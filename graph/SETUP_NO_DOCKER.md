# Running Neo4j WITHOUT Docker

The app only talks to Neo4j over Bolt (`bolt://localhost:7687`) using a username +
password from `.env`. **No code or query changes are needed** to switch away from
Docker — install Neo4j any of the ways below, set the password in `.env`, and run
`python graph/build_graph.py`.

`.env` (the only thing that matters to the app):
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<the password you set>
```
Leave `NEO4J_PASSWORD` blank to disable the graph entirely (the app still runs on SQL).

---

## Option 1 — Neo4j Desktop  (easiest, recommended; no Java install needed)
1. Download **Neo4j Desktop**: https://neo4j.com/download/  → install, run it.
2. Create a project → **Add → Local DBMS**. Choose version 5.x, set a password.
3. Click **Start**. It serves Bolt on `7687` and a browser on `7474`.
4. Put that password in `.env` as `NEO4J_PASSWORD`.
5. `python graph/build_graph.py`  → done. Desktop bundles its own Java, so nothing
   else to install.

---

## Option 2 — Neo4j Community Server (zip)  (scriptable; needs JDK 17 or 21)
Check Java first: `java -version` should show 17 or 21. If missing, install
**Temurin JDK 17** (https://adoptium.net) and reopen the terminal.

1. Download the Community **Windows zip** from https://neo4j.com/deployment-center/
   (or directly, e.g. `https://dist.neo4j.org/neo4j-community-5.26.0-windows.zip`).
2. Unzip somewhere, e.g. `C:\neo4j`. Open a terminal in that folder.
3. Set the initial password:
   ```powershell
   bin\neo4j-admin dbms set-initial-password lbsgraph123
   ```
4. Start it (this window stays running = the database):
   ```powershell
   bin\neo4j console
   ```
   Bolt is on `7687`, browser UI at http://localhost:7474.
   *(Prefer it to run in the background as a Windows service instead?
   `bin\neo4j windows-service install` then `bin\neo4j start` — run that terminal
   "as Administrator".)*
5. In a second terminal, set `NEO4J_PASSWORD=lbsgraph123` in `.env`, then:
   ```powershell
   python graph/build_graph.py
   ```

---

## Option 3 — Neo4j Aura (cloud; no install at all)
1. Create a free instance at https://neo4j.com/cloud/aura/ — it gives you a
   `neo4j+s://<id>.databases.neo4j.io` URI and a password.
2. In `.env`: `NEO4J_URI=neo4j+s://<id>.databases.neo4j.io`, `NEO4J_USER=neo4j`,
   `NEO4J_PASSWORD=<that password>`.
3. `python graph/build_graph.py`.
> ⚠ Aura sends data off your machine — fine for the synthetic demo, but not for real
> counterparty/entity data. Use Option 1 or 2 locally for real data.

---

## Verify (any option)
```powershell
python graph/build_graph.py          # prints node/edge counts
python graph/lbs_graph.py --demo     # runs the multi-hop queries
```
Then in the chat (`python agent/lbs_agent.py`) ask: *"netting chain for Citadel LLC"*.

Ports `7474` (browser) and `7687` (Bolt) must be free — if you also have the Docker
container running, stop it first: `docker stop lbs-neo4j`.
