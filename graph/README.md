# Graph layer (Neo4j) — multi-hop relational questions

The SQL engine (`sql/LBS_Engine.sql`) answers **aggregation / drill-down** ("why is
LBS high", "top movers", "drill to the names"). This graph layer answers the
**relational long tail** the star schema is awkward at — legal-entity / netting /
collateral chains:

- "Which legal entities is Citadel booked at, and *through which netting sets*?"
- "What counterparties do two legal entities *share*?"
- "Show all relationships for a counterparty."

**Grounding is preserved.** Every amount on a graph edge is `SUM(GBPIFRSBalanceSheetAmount)`
projected straight from SQL for the latest `BusinessDate` — the same number SQL would
give. The graph adds *relationships*, not new figures.

The graph is **optional**: if Neo4j isn't configured/reachable, the chat silently
stays on the SQL path.

---

## Files
| File | Role |
|---|---|
| `build_graph.py` | Projects `SputnikCube` (latest date) → Neo4j property graph. Rebuildable. |
| `lbs_graph.py`   | `GraphTools` (Cypher traversals) + `answer_relational()` free-text router. |

Connection settings live in the project `.env` (read via `config.py`):
```
NEO4J_URI=bolt://localhost:7687     # Aura: neo4j+s://<id>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=lbsgraph123          # empty password disables the graph
```

---

## Graph model
```
Nodes
  (:Counterparty {name, clientHouse})
  (:LegalEntity  {name, bankLevyStatus})
  (:NettingSet   {id, strength})            # strength 1 | 5
  (:Security     {isin, issuer, currency, country})
  (:Business     {name})

Edges  (each carries  lbs = SUM(LBS) from SQL)
  (cp)-[:FACES]->(le)        counterparty exposure booked at a legal entity
  (cp)-[:NETS_UNDER]->(ns)   counterparty exposure under a netting set
  (ns)-[:BOOKED_AT]->(le)    netting set booked at a legal entity
  (cp)-[:TRADES]->(b)        counterparty active in a business
  (cp)-[:HOLDS]->(sec)       counterparty position in a security
```
The headline multi-hop is `(:Counterparty)-[:NETS_UNDER]->(:NettingSet)-[:BOOKED_AT]->(:LegalEntity)`.

---

## 1. Get a Neo4j to connect to

You only need a running Neo4j. Pick whichever fits — **no code changes**, just the
three `.env` vars.

### Option A — Docker (least setup)
```bash
docker run -d --name lbs-neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/lbsgraph123 -e NEO4J_PLUGINS='["apoc"]' neo4j:5
```
- Browser UI: <http://localhost:7474> (login `neo4j` / `lbsgraph123`)
- Lifecycle: `docker stop lbs-neo4j` / `docker start lbs-neo4j` (data persists);
  `docker rm -f lbs-neo4j` to wipe and start fresh.
- Or use `docker compose up -d` with the `docker-compose.yml` in this folder.

> **No Docker?** See [SETUP_NO_DOCKER.md](SETUP_NO_DOCKER.md) for Neo4j Desktop /
> Community Server / Aura — the app is identical, only `.env` changes.

### Option B — Neo4j Desktop (recommended if you don't have Docker)
1. Install Neo4j Desktop (Windows GUI). It **bundles Java** — nothing else to install.
2. Create a local DBMS, set a password, click **Start**.
3. Keep `NEO4J_URI=bolt://localhost:7687`; put the password in `.env`.

### Option C — Neo4j Community Server (zip, no Docker)
1. Install a **JDK 17 or 21** (required by Neo4j 5) and put `java` on `PATH`.
2. Download the Community Server zip, unzip.
3. `bin\neo4j-admin dbms set-initial-password <yourpassword>`
4. `bin\neo4j console`  → serves Bolt on `7687`, browser on `7474`.

### Option D — Neo4j Aura (cloud, zero install)
1. Create a free instance; it gives a `neo4j+s://<id>.databases.neo4j.io` URI + password.
2. Set `NEO4J_URI` to that URI (note the `neo4j+s://` TLS scheme) and the password in `.env`.

> ⚠️ **Data sensitivity.** Aura sends data off-machine. Fine for this **synthetic demo**,
> but it conflicts with the project's on-prem / private golden rule for *real*
> counterparty / legal-entity data. Use a local option (A/B/C) for real data.

---

## 2. Things to check (any option)
- **Ports** `7474` (HTTP browser) and `7687` (Bolt) must be free.
- **Password** — Neo4j forces a non-default password on first start; whatever you set
  goes into `NEO4J_PASSWORD`. Empty = graph disabled.
- **Java** — only Option C needs you to install a JDK; Docker and Desktop bundle it.
- **APOC** — included in the Docker run for convenience, but **not required**: all
  queries here are plain Cypher.
- **Driver** — `pip install neo4j` (already in `requirements.txt`).

---

## 3. Build and query
```bash
pip install neo4j                       # once
python graph/build_graph.py            # project SputnikCube -> Neo4j (re-run when new data lands)
python graph/lbs_graph.py --demo       # canned multi-hop queries
python graph/lbs_graph.py "netting chain for Citadel LLC"
```

In the chat, relational questions **auto-route** to the graph; force it with `/graph`:
```bash
python agent/lbs_agent.py
#   you> which netting sets link Citadel LLC to which legal entities?   (-> graph)
#   you> why is LBS high today?                                         (-> SQL)
#   you> /graph counterparties shared by Northwind Bank PLC and Northwind Capital Inc
```

The router (`answer_relational`) handles: netting chains, counterparties at an entity,
counterparties shared by two entities, and a full counterparty relationship overview.
It returns `None` for non-relational questions so they fall through to the SQL engine.

---

## 4. Refresh / rebuild
`build_graph.py` reads the **latest `BusinessDate`** and rebuilds the whole graph
(`MATCH (n) DETACH DELETE n` first). Re-run it after each nightly load. To extend the
model (e.g. add `ISSUED_BY` issuer nodes, or per-date snapshots), add projection
queries + `MERGE`s in `build_graph.py` and matching traversals in `lbs_graph.py`.
