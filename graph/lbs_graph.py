"""
Neo4j query layer for multi-hop relational LBS questions.
=========================================================
The SQL engine owns aggregation/drill. This module answers the relational long tail
(legal-entity / netting / collateral chains) with Cypher, and routes a free-text
question to the right traversal. Amounts were projected from SQL by build_graph.py,
so they stay grounded.

Run:  python graph/build_graph.py        # build/refresh the graph first
      python graph/lbs_graph.py --demo   # canned multi-hop queries
      python graph/lbs_graph.py          # ask one relational question, argv
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD   # noqa: E402
from agent.lbs_agent import fmt_gbp                          # noqa: E402

try:
    from neo4j import GraphDatabase
except ImportError:                                          # graph is optional
    GraphDatabase = None

_DRIVER = None


def get_driver():
    """Cached driver, or None if the graph is not configured/reachable."""
    global _DRIVER
    if _DRIVER is not None:
        return _DRIVER
    if not (GraphDatabase and NEO4J_PASSWORD):
        return None
    try:
        d = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        d.verify_connectivity()
        _DRIVER = d
        return d
    except Exception:
        return None


class GraphTools:
    """Typed multi-hop traversals — the only graph access the router uses."""

    def __init__(self, driver):
        self.d = driver

    def _q(self, cypher, **params):
        with self.d.session() as s:
            return s.run(cypher, **params).data()

    def names(self, label):
        key = "id" if label == "NettingSet" else ("isin" if label == "Security" else "name")
        return [r["v"] for r in self._q(f"MATCH (n:{label}) RETURN n.{key} AS v")]

    def counterparty_overview(self, cp):
        return self._q(
            """MATCH (c:Counterparty {name:$cp})
               OPTIONAL MATCH (c)-[f:FACES]->(le:LegalEntity)
               WITH c, collect(DISTINCT {name:le.name, lbs:f.lbs}) AS entities
               OPTIONAL MATCH (c)-[u:NETS_UNDER]->(ns:NettingSet)
               WITH c, entities, collect(DISTINCT {id:ns.id, strength:ns.strength, lbs:u.lbs}) AS nsets
               OPTIONAL MATCH (c)-[t:TRADES]->(b:Business)
               WITH c, entities, nsets, collect(DISTINCT {name:b.name, lbs:t.lbs}) AS biz
               OPTIONAL MATCH (c)-[h:HOLDS]->(s:Security)
               RETURN entities, nsets, biz,
                      collect(DISTINCT {isin:s.isin, issuer:s.issuer, ccy:s.currency, lbs:h.lbs}) AS secs""",
            cp=cp)

    def netting_chain(self, cp):
        """The headline 2-hop: counterparty -> netting set -> legal entity, with amounts."""
        return self._q(
            """MATCH (c:Counterparty {name:$cp})-[u:NETS_UNDER]->(ns:NettingSet)-[b:BOOKED_AT]->(le:LegalEntity)
               RETURN ns.id AS nettingSet, ns.strength AS strength, le.name AS legalEntity,
                      b.lbs AS lbs
               ORDER BY abs(coalesce(b.lbs,0)) DESC""",
            cp=cp)

    def entity_counterparties(self, le):
        return self._q(
            """MATCH (c:Counterparty)-[f:FACES]->(:LegalEntity {name:$le})
               RETURN c.name AS counterparty, c.clientHouse AS clientHouse, f.lbs AS lbs
               ORDER BY abs(coalesce(f.lbs,0)) DESC""",
            le=le)

    def shared_counterparties(self, a, b):
        return self._q(
            """MATCH (c:Counterparty)-[fa:FACES]->(:LegalEntity {name:$a})
               MATCH (c)-[fb:FACES]->(:LegalEntity {name:$b})
               RETURN c.name AS counterparty, fa.lbs AS lbsA, fb.lbs AS lbsB
               ORDER BY abs(coalesce(fa.lbs,0))+abs(coalesce(fb.lbs,0)) DESC""",
            a=a, b=b)


# --------------------------------------------------------------------------- #
# Free-text router  (relational questions only; returns None otherwise)
# --------------------------------------------------------------------------- #
_REL_WORDS = ("netting", "chain", "booked", "through which", "through what", "shared",
              "in common", "common counterpart", "both entit", "linked", "connect",
              "path", "which entit", "which legal", "facing", "counterparties of",
              "booked at", "relationship")


def _find(question, candidates):
    """Names from the graph that appear in the question (longest first)."""
    ql = question.lower()
    hits = [c for c in candidates if c and c.lower() in ql]
    return sorted(set(hits), key=len, reverse=True)


def is_relational(question: str) -> bool:
    ql = question.lower()
    return any(w in ql for w in _REL_WORDS)


def answer_relational(question: str) -> str | None:
    """Route a relational question to a graph traversal. None = not for the graph."""
    if not is_relational(question):
        return None
    drv = get_driver()
    if drv is None:
        return None
    g = GraphTools(drv)
    cps = _find(question, g.names("Counterparty"))
    les = _find(question, g.names("LegalEntity"))
    ql = question.lower()

    if ("shared" in ql or "common" in ql or "both" in ql) and len(les) >= 2:
        rows = g.shared_counterparties(les[0], les[1])
        if not rows:
            return f"[graph] No counterparties are booked at both {les[0]} and {les[1]}."
        body = "\n".join(f"  - {r['counterparty']}: {fmt_gbp(r['lbsA'], signed=True)} at "
                         f"{les[0]}, {fmt_gbp(r['lbsB'], signed=True)} at {les[1]}" for r in rows)
        return f"[graph] Counterparties shared by {les[0]} and {les[1]}:\n{body}"

    if cps and ("netting" in ql or "chain" in ql or "booked" in ql or "through" in ql):
        rows = g.netting_chain(cps[0])
        if not rows:
            return f"[graph] No netting-set -> legal-entity chain found for {cps[0]}."
        body = "\n".join(f"  - {cps[0]} -> netting set {r['nettingSet']} "
                         f"(strength {r['strength']}) -> {r['legalEntity']}: "
                         f"{fmt_gbp(r['lbs'], signed=True)}" for r in rows)
        return f"[graph] Netting chain for {cps[0]}:\n{body}"

    if les and ("which counterpart" in ql or "counterparties" in ql or "facing" in ql):
        rows = g.entity_counterparties(les[0])
        body = "\n".join(f"  - {r['counterparty']} ({r['clientHouse']}): "
                         f"{fmt_gbp(r['lbs'], signed=True)}" for r in rows)
        return f"[graph] Counterparties booked at {les[0]}:\n{body}"

    if cps:
        o = g.counterparty_overview(cps[0])[0]
        ents = ", ".join(f"{e['name']} ({fmt_gbp(e['lbs'], signed=True)})"
                         for e in o["entities"] if e["name"])
        nss = ", ".join(f"{n['id']} (str {n['strength']})" for n in o["nsets"] if n["id"])
        biz = ", ".join(b["name"] for b in o["biz"] if b["name"])
        secs = ", ".join(f"{s['issuer']}/{s['ccy']}" for s in o["secs"] if s["isin"])
        return (f"[graph] {cps[0]} relationships:\n"
                f"  legal entities: {ents or '—'}\n"
                f"  netting sets:   {nss or '—'}\n"
                f"  businesses:     {biz or '—'}\n"
                f"  securities:     {secs or '—'}")

    if les:
        rows = g.entity_counterparties(les[0])
        body = "\n".join(f"  - {r['counterparty']}: {fmt_gbp(r['lbs'], signed=True)}" for r in rows)
        return f"[graph] Counterparties booked at {les[0]}:\n{body}"

    return ("[graph] This looks relational but I couldn't spot a known counterparty or "
            "legal entity in it. Try naming one, e.g. 'netting chain for Citadel LLC'.")


# --------------------------------------------------------------------------- #
def _demo():
    drv = get_driver()
    if drv is None:
        raise SystemExit("Graph not reachable. Set NEO4J_PASSWORD and run build_graph.py first.")
    g = GraphTools(drv)
    cp = next((c for c in g.names("Counterparty") if "Citadel" in c), g.names("Counterparty")[0])
    les = g.names("LegalEntity")
    qs = [
        f"What is the netting chain for {cp}?",
        f"Which counterparties are booked at {les[0]}?",
        f"What counterparties are shared by {les[0]} and {les[1]}?",
        f"Show me all relationships for {cp}",
    ]
    for q in qs:
        print(f"\n>>> {q}")
        print(answer_relational(q))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
    else:
        q = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) \
            or "netting chain for Citadel LLC"
        print(answer_relational(q) or "[not a relational question]")
