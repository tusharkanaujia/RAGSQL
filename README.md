# LBS Root-Cause Platform

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![SQL Server](https://img.shields.io/badge/SQL%20Server-T--SQL-CC2927?logo=microsoftsqlserver&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-5-008CC1?logo=neo4j&logoColor=white)
![Ollama](https://img.shields.io/badge/LLM-Ollama%20(local)-000000?logo=ollama&logoColor=white)
![Grounded](https://img.shields.io/badge/numbers-only%20from%20SQL-2ea44f)

Natural-language root-cause analysis over the Leverage Balance Sheet (LBS) for a
markets business (PB / FIF / EQ). A deterministic SQL engine computes the exact
attribution; a local LLM (Ollama) narrates the results. Numbers come only from SQL.

> Runs on **fully synthetic demo data** (a fictional bank, fabricated exposures) —
> nothing here is real. See `sql/00_Setup_SputnikCube.sql`.

## Layout
```
sql/LBS_Engine.sql     deterministic engine (calendar, views, top-movers, drill-down, time-series, cube)
agent/lbs_agent.py     orchestration: wraps procs as tools, plans, narrates (grounded)
agent/store.py         persistent conversation history (SQLite)
agent/charts.py        chart tool: trend questions -> Vega-Lite specs (anomaly bands)
ui/                    optional Flask web UI (two-pane chat + chart canvas)
config.py              reads connection / Ollama / Neo4j settings from .env
graph/                 optional Neo4j layer for multi-hop relational questions
docs/                  design plan + semantic layer
```

Three answer routes, auto-selected by the question:
**trend/plot** -> chart spec (`agent/charts.py`) · **relational** (netting/entity
chains) -> Neo4j graph · **everything else** -> the SQL engine. All grounded in SQL.

## Prerequisites
- Python 3.10+ and the **ODBC Driver 17 for SQL Server**
- Access to the `SputnikCube` database
- (Optional) [Ollama](https://ollama.com) running locally for narration — the
  agent falls back to a template narrative if it is not available

## Setup
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy .env.example .env            # then edit .env with your server + database
```

All settings (SQL connection, Ollama, Neo4j) come from `.env` via `config.py` —
no hardcoded credentials.

## Database

### Option A — local synthetic demo (no real data needed)
Builds a self-contained `SputnikCube` with realistic *fake* data and a deliberate
anomaly on the latest date, so the whole stack runs offline:
```bash
sqlcmd -S "localhost\MSSQLSERVER2019" -E -b -I -i sql/00_Setup_SputnikCube.sql
sqlcmd -S "localhost\MSSQLSERVER2019" -E -d SputnikCube -b -I -i sql/LBS_Engine.sql
```
(`00_Setup_SputnikCube.sql` is fully rebuildable — re-run it any time to reset.)

### Option B — real data
Run `sql/LBS_Engine.sql` top-to-bottom against an existing `SputnikCube` (VS Code
mssql extension or SSMS). Objects build in dependency order. Uncomment the
smoke-test block at the bottom to validate against a real `BusinessDate`.

## Run
Interactive chat — with **memory** (follow-ups) and **persistent history** (saved to
a local SQLite `chat_history.db`, so you can resume past chats):
```bash
python agent/lbs_agent.py
#   you> Why is my LBS high today?
#   you> Drill into that counterparty        # follow-up uses prior context
#   you> Now show me the month-end picture
#
# conversation commands:
#   /new [title]   /list   /open <id>   /title <text>   /delete <id>
#   /history   /reset   /graph <q>   /help   /exit
```
Each conversation is saved and auto-titled from its first question; `/list` then
`/open <id>` resumes one with its history restored.

### Web UI (two-pane chat + chart canvas)
```bash
pip install flask
python ui/app.py          # -> http://localhost:5000
```
Left pane = chat with a conversation switcher (saved history); right pane renders the
Vega-Lite chart for trend questions. Each reply is tagged by route (chart / graph /
sql). Vega-Lite is loaded from a CDN in the browser (a library fetch — no data leaves
the host); vendor it locally for an air-gapped deployment.
Scripted multi-turn demo (proves memory + grounding, no typing):
```bash
python agent/lbs_agent.py --demo
```
On Windows, prefix with `set PYTHONIOENCODING=utf-8` (cmd) or
`$env:PYTHONIOENCODING="utf-8"` (PowerShell) so the `£` sign renders.

## How numbers stay correct (grounding)
The engine owns every figure; the LLM only writes prose. Each figure is
pre-formatted (`+£1.11bn`) and handed to the narrator as a `[TOKEN]`; the model
references tokens and the engine substitutes the exact string. A guard then
**rejects** any narration that prints a literal figure of its own, falling back to
a deterministic template — so a wrong number can never reach the user, regardless
of which local model is used.

## Graph layer (Neo4j) — optional, for multi-hop relational questions
The SQL engine handles aggregation/drill. Neo4j handles the relational long tail —
**legal-entity / netting / collateral chains** ("which entities is Citadel booked
at, through which netting sets?", "what counterparties do two entities share?").
Amounts are projected from SQL, so graph answers stay grounded.

### 1. Get a Neo4j to connect to (pick one)
**A. Docker (easiest, used here):**
```bash
docker run -d --name lbs-neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/lbsgraph123 -e NEO4J_PLUGINS='["apoc"]' neo4j:5
# Browser UI: http://localhost:7474   (login neo4j / lbsgraph123)
# stop/start later:  docker stop lbs-neo4j   /   docker start lbs-neo4j
```
**B. Neo4j Desktop** — install, create a local DBMS, set a password, start it.
**C. Neo4j Aura (cloud free tier)** — create an instance; it gives you a
`neo4j+s://xxxx.databases.neo4j.io` URI and a password.

### 2. Point the app at it
The connection is just three env vars in `.env` (already templated in `.env.example`):
```
NEO4J_URI=bolt://localhost:7687        # Aura: neo4j+s://<id>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=lbsgraph123             # whatever you set above; empty = graph disabled
```
`config.py` reads these; `graph/build_graph.py` and `graph/lbs_graph.py` open a Bolt
driver with `GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))`.

### 3. Install the driver, build the graph, query it
```bash
pip install neo4j                      # already in requirements.txt
python graph/build_graph.py           # projects SputnikCube -> Neo4j (rebuildable)
python graph/lbs_graph.py --demo      # canned multi-hop queries
python graph/lbs_graph.py "netting chain for Citadel LLC"
```
In the chat (`python agent/lbs_agent.py`), relational questions **auto-route** to the
graph; force it with `/graph <question>`. If Neo4j isn't running, the chat silently
stays on the SQL path — the graph is fully optional.

Rebuild the graph whenever new data lands (it reads the latest `BusinessDate`):
re-run `python graph/build_graph.py`.

## Moving to another machine
```bash
git clone https://github.com/tusharkanaujia/RAGSQL && cd RAGSQL
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env            # set your local connection strings
```
Secrets live in `.env` (gitignored), so they never travel through the repo.

## Data
This repo ships **synthetic demo data only** — a fictional bank with fabricated
exposures (`sql/00_Setup_SputnikCube.sql`). No real institution, counterparties,
or positions are included. `.gitignore` excludes `.env` and any data extracts; if
you point the engine at real data, keep that data and your `.env` out of git.

## Build status
Done:
- SQL engine (calendar, views, top-movers, drill-path, time-series, cube).
- Local synthetic demo DB (`sql/00_Setup_SputnikCube.sql`).
- Grounded **multi-turn chat** with memory: history-aware planner (resolves
  follow-up references + maps words to real filter values), token-substitution
  narration, and a hard guard that guarantees figures come only from SQL.

Known limits (mostly the small local model): on harder follow-ups the 8B planner
sometimes picks a less-useful drill direction or misses a filter; it then still
returns correct, grounded numbers via the template. A larger local model improves
the prose/routing without changing the guarantees.

Next: market-data / FX enrichment, forecast + changepoint ML, document grounding,
and the chat UI with waterfall / treemap / time-series charts.
