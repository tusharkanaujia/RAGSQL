"""
Project the LBS star schema into a Neo4j property graph.
=========================================================
The SQL engine answers aggregation/drill questions. Neo4j is for the long tail of
*multi-hop relational* questions the star schema is awkward at — legal-entity /
netting / collateral chains:  "which entities is counterparty X booked at, through
which netting sets?", "what counterparties do entities A and B share?".

Grounding is preserved: every amount on a graph edge is SUM(LBS) projected straight
from SQL for the latest BusinessDate, so the numbers are the same ones SQL would give.

Model
-----
Nodes :Counterparty(name, clientHouse) :LegalEntity(name, bankLevyStatus)
      :NettingSet(id, strength) :Security(isin, issuer, currency, country)
      :Business(name)
Edges (cp)-[:FACES {lbs}]->(le)        counterparty exposure booked at entity
      (cp)-[:NETS_UNDER {lbs}]->(ns)   counterparty's exposure under a netting set
      (ns)-[:BOOKED_AT {lbs}]->(le)    netting set booked at entity
      (cp)-[:TRADES {lbs}]->(b)        counterparty active in a business
      (cp)-[:HOLDS {lbs}]->(sec)       counterparty position in a security

Run:  python graph/build_graph.py
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD   # noqa: E402
from agent.lbs_agent import DB                              # noqa: E402
from neo4j import GraphDatabase                             # noqa: E402

# --- SQL projections (latest BusinessDate, filtered, aggregated, signed SUM) --- #
Q_NODES_CP = """
  SELECT Counterparty AS name, MAX(ClientHouseIndicator) AS clientHouse
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched' AND Counterparty IS NOT NULL
  GROUP BY Counterparty"""
Q_NODES_LE = """
  SELECT LegalEntity AS name, MAX(BankLevyStatus) AS bankLevyStatus
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched' AND LegalEntity IS NOT NULL
  GROUP BY LegalEntity"""
Q_NODES_NS = """
  SELECT e.NettingSetId AS id, MAX(n.NettingAgreementStrength) AS strength
  FROM SputnikCube.vwFactLBS_Enriched e
  LEFT JOIN SputnikCube.DimLBSNetting n ON n.NettingSetId = e.NettingSetId
  WHERE e.BusinessDate=? AND e.LineItem<>'Unmatched'
    AND e.NettingSetId IS NOT NULL AND e.NettingSetId<>'Unmatched'
  GROUP BY e.NettingSetId"""
Q_NODES_SEC = """
  SELECT ISIN AS isin, MAX(IssuerName) AS issuer, MAX(Currency) AS currency,
         MAX(CountryOfRisk) AS country
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched' AND ISIN IS NOT NULL AND ISIN<>'Unmatched'
  GROUP BY ISIN"""
Q_NODES_BIZ = """
  SELECT Business AS name FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched' AND Business IS NOT NULL
  GROUP BY Business"""

Q_FACES = """
  SELECT Counterparty AS cp, LegalEntity AS le, lbs=SUM(LBS)
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched'
    AND Counterparty IS NOT NULL AND LegalEntity IS NOT NULL
  GROUP BY Counterparty, LegalEntity"""
Q_NETS = """
  SELECT Counterparty AS cp, NettingSetId AS ns, lbs=SUM(LBS)
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched'
    AND Counterparty IS NOT NULL AND NettingSetId IS NOT NULL AND NettingSetId<>'Unmatched'
  GROUP BY Counterparty, NettingSetId"""
Q_BOOKED = """
  SELECT NettingSetId AS ns, LegalEntity AS le, lbs=SUM(LBS)
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched'
    AND NettingSetId IS NOT NULL AND NettingSetId<>'Unmatched' AND LegalEntity IS NOT NULL
  GROUP BY NettingSetId, LegalEntity"""
Q_TRADES = """
  SELECT Counterparty AS cp, Business AS b, lbs=SUM(LBS)
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched'
    AND Counterparty IS NOT NULL AND Business IS NOT NULL
  GROUP BY Counterparty, Business"""
Q_HOLDS = """
  SELECT Counterparty AS cp, ISIN AS isin, lbs=SUM(LBS)
  FROM SputnikCube.vwFactLBS_Enriched
  WHERE BusinessDate=? AND LineItem<>'Unmatched'
    AND Counterparty IS NOT NULL AND ISIN IS NOT NULL AND ISIN<>'Unmatched'
  GROUP BY Counterparty, ISIN"""

CONSTRAINTS = [
    "CREATE CONSTRAINT cp_name  IF NOT EXISTS FOR (n:Counterparty) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT le_name  IF NOT EXISTS FOR (n:LegalEntity)  REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT ns_id    IF NOT EXISTS FOR (n:NettingSet)   REQUIRE n.id   IS UNIQUE",
    "CREATE CONSTRAINT sec_isin IF NOT EXISTS FOR (n:Security)     REQUIRE n.isin IS UNIQUE",
    "CREATE CONSTRAINT biz_name IF NOT EXISTS FOR (n:Business)     REQUIRE n.name IS UNIQUE",
]


def _w(tx, cypher, rows):
    tx.run(cypher, rows=rows)


def build():
    if not NEO4J_PASSWORD:
        raise SystemExit("NEO4J_PASSWORD is empty — set it in .env to enable the graph.")
    db = DB()
    asof = db.scalar("SELECT MAX(BusinessDate) FROM SputnikCube.FactLBS")
    print(f"Projecting SputnikCube -> Neo4j for {asof} ...")

    pull = lambda q: db.proc(q, (asof,))
    nodes = {"cp": pull(Q_NODES_CP), "le": pull(Q_NODES_LE), "ns": pull(Q_NODES_NS),
             "sec": pull(Q_NODES_SEC), "biz": pull(Q_NODES_BIZ)}
    edges = {"faces": pull(Q_FACES), "nets": pull(Q_NETS), "booked": pull(Q_BOOKED),
             "trades": pull(Q_TRADES), "holds": pull(Q_HOLDS)}

    drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    drv.verify_connectivity()
    with drv.session() as s:
        s.run("MATCH (n) DETACH DELETE n")                      # rebuildable
        for c in CONSTRAINTS:
            s.run(c)
        # nodes
        s.execute_write(_w, "UNWIND $rows AS r MERGE (n:Counterparty {name:r.name}) "
                            "SET n.clientHouse=r.clientHouse", nodes["cp"])
        s.execute_write(_w, "UNWIND $rows AS r MERGE (n:LegalEntity {name:r.name}) "
                            "SET n.bankLevyStatus=r.bankLevyStatus", nodes["le"])
        s.execute_write(_w, "UNWIND $rows AS r MERGE (n:NettingSet {id:r.id}) "
                            "SET n.strength=r.strength", nodes["ns"])
        s.execute_write(_w, "UNWIND $rows AS r MERGE (n:Security {isin:r.isin}) "
                            "SET n.issuer=r.issuer, n.currency=r.currency, n.country=r.country",
                        nodes["sec"])
        s.execute_write(_w, "UNWIND $rows AS r MERGE (n:Business {name:r.name})", nodes["biz"])
        # edges
        s.execute_write(_w, "UNWIND $rows AS r MATCH (c:Counterparty {name:r.cp}),(l:LegalEntity {name:r.le}) "
                            "MERGE (c)-[e:FACES]->(l) SET e.lbs=r.lbs", edges["faces"])
        s.execute_write(_w, "UNWIND $rows AS r MATCH (c:Counterparty {name:r.cp}),(n:NettingSet {id:r.ns}) "
                            "MERGE (c)-[e:NETS_UNDER]->(n) SET e.lbs=r.lbs", edges["nets"])
        s.execute_write(_w, "UNWIND $rows AS r MATCH (n:NettingSet {id:r.ns}),(l:LegalEntity {name:r.le}) "
                            "MERGE (n)-[e:BOOKED_AT]->(l) SET e.lbs=r.lbs", edges["booked"])
        s.execute_write(_w, "UNWIND $rows AS r MATCH (c:Counterparty {name:r.cp}),(b:Business {name:r.b}) "
                            "MERGE (c)-[e:TRADES]->(b) SET e.lbs=r.lbs", edges["trades"])
        s.execute_write(_w, "UNWIND $rows AS r MATCH (c:Counterparty {name:r.cp}),(s2:Security {isin:r.isin}) "
                            "MERGE (c)-[e:HOLDS]->(s2) SET e.lbs=r.lbs", edges["holds"])

        counts = s.run("MATCH (n) RETURN labels(n)[0] AS lbl, count(*) AS c "
                       "ORDER BY lbl").data()
        rels = s.run("MATCH ()-[e]->() RETURN type(e) AS t, count(*) AS c ORDER BY t").data()
    drv.close()
    print("Nodes:", {r["lbl"]: r["c"] for r in counts})
    print("Edges:", {r["t"]: r["c"] for r in rels})
    print("Graph build complete.")


if __name__ == "__main__":
    build()
