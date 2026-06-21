# SETUP — run the LBS Root-Cause Platform end-to-end

A single, ordered runbook. Do the steps top to bottom. Windows commands shown
(PowerShell / cmd). The **core** (steps 1–6) is all you need; **Ollama** (7),
**Neo4j** (8) and the **web UI** (9) are optional add-ons.

> What it is: ask plain-English questions about a Leverage Balance Sheet
> ("why is LBS high today?", "show USD TPA trend", "is the whole book abnormal?",
> "netting chain for Citadel LLC"). A deterministic SQL engine computes every number;
> a local LLM only narrates. Ships with **synthetic demo data** (a fictional bank).

---

## 0. Install prerequisites (once per PC)
| Need | Where | Required? |
|------|-------|-----------|
| Git | https://git-scm.com | yes |
| Python 3.10+ | https://python.org (tick "Add Python to PATH") | yes |
| ODBC Driver 17 or 18 for SQL Server | search "ODBC Driver 17 for SQL Server download" | yes |
| `sqlcmd` (or SSMS / Azure Data Studio) | ships with the ODBC tools / SSMS | yes (to load the DB) |
| Ollama + a model | https://ollama.com, then `ollama pull llama3.1` | optional (narration) |
| Java 17 or 21 **or** Docker | https://adoptium.net / https://docker.com | optional (Neo4j graph) |

---

## 1. Get the code
```powershell
git clone https://github.com/tusharkanaujia/RAGSQL
cd RAGSQL
```

## 2. Python environment + dependencies
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Configure the connection — `.env`
```powershell
copy .env.example .env
notepad .env
```
Set `LBS_CONN_STR` to **your** SQL Server. Windows login:
```
LBS_CONN_STR=DRIVER={ODBC Driver 17 for SQL Server};SERVER=YOURSERVER\INSTANCE;DATABASE=SputnikCube;Trusted_Connection=yes;
```
SQL username/password instead:
```
LBS_CONN_STR=DRIVER={ODBC Driver 17 for SQL Server};SERVER=YOURSERVER;DATABASE=SputnikCube;UID=youruser;PWD=yourpass;
```

## 4. Build / connect the database
> ⚠️ `sql/00_Setup_SputnikCube.sql` **DROPS and recreates tables with fake data.**
> Run it ONLY for the demo DB. **Never** run it against real data.

**Demo data (throwaway `SputnikCube`):**
```powershell
sqlcmd -S "YOURSERVER\INSTANCE" -E -b -I -i sql/00_Setup_SputnikCube.sql
sqlcmd -S "YOURSERVER\INSTANCE" -E -d SputnikCube -b -I -i sql/LBS_Engine.sql
sqlcmd -S "YOURSERVER\INSTANCE" -E -d SputnikCube -b -I -i sql/01_FxRates.sql
```
**Real existing `SputnikCube` (skip 00_Setup):**
```powershell
sqlcmd -S "YOURSERVER\INSTANCE" -E -d SputnikCube -b -I -i sql/LBS_Engine.sql
sqlcmd -S "YOURSERVER\INSTANCE" -E -d SputnikCube -b -I -i sql/01_FxRates.sql
```
*(SQL auth: replace `-E` with `-U youruser -P yourpass`. No sqlcmd? Open each `.sql`
in SSMS / Azure Data Studio and Execute — `00_Setup` against `master`, the other two
against `SputnikCube`.)*

## 5. Sanity check
```powershell
$env:PYTHONIOENCODING="utf-8"      # so the £ sign renders
python agent/eval.py               # expect: "8/8 checks passed"
```

## 6. Run the chat (core)
```powershell
python agent/lbs_agent.py          # interactive — type /help for commands
python agent/lbs_agent.py --demo   # scripted demo, no typing
```
Ask things like:
- `why is LBS high today?`               (root-cause drill)
- `is the whole book abnormal today?`    (forecast vs expectation + anomaly)
- `show USD TPA trend`                   (chart spec)
- `how much of the move is FX?`          (FX isolation)
- `netting chain for Citadel LLC`        (graph — needs step 8)
- `/digest`  `/commentary`  `/explain`  `/sql how many counterparties`  `/eval`

---

## 7. (Optional) Ollama — narrated answers
Without it, answers use a deterministic template (still correct). With it:
```powershell
ollama pull llama3.1     # one-time; keep `ollama serve` running
```
`.env` already points at `http://localhost:11434`. Nothing else to do.

## 8. (Optional) Neo4j — relational "chain" questions
Pick **one**. Then set `NEO4J_PASSWORD` in `.env` and run `python graph/build_graph.py`.

**8a. Docker (simplest):**
```powershell
docker run -d --name lbs-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/lbsgraph123 -e NEO4J_PLUGINS='["apoc"]' neo4j:5
# later: docker stop lbs-neo4j  /  docker start lbs-neo4j
```
**8b. No Docker — Neo4j Community Server (needs Java 17/21):**
```powershell
# download + unzip the Community Windows zip from https://neo4j.com/deployment-center/
$env:JAVA_HOME="C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot"   # your JDK path
& "C:\path\to\neo4j-community-5.26.5\bin\neo4j-admin.bat" dbms set-initial-password lbsgraph123
& "C:\path\to\neo4j-community-5.26.5\bin\neo4j.bat" console          # keep this window open
```
*(or `bin\neo4j.bat windows-service install` then `neo4j start`, from an Admin terminal,
to run on boot.)* Full details: **`graph/SETUP_NO_DOCKER.md`**.

**Then, for either:**
```powershell
# .env -> NEO4J_PASSWORD=lbsgraph123
python graph/build_graph.py        # load the graph from SQL
python graph/lbs_graph.py --demo   # try the multi-hop queries
```
Only ONE Neo4j may run at a time (both use ports 7474/7687). To switch: stop one,
start the other, run `build_graph.py` once.

## 9. (Optional) Web UI
```powershell
pip install flask
python ui/app.py                   # -> http://localhost:5000
```
Left = chat with saved-conversation switcher; right = chart canvas for trend questions.

---

## Troubleshooting
- **`£` shows as `?`/garbled** → `set PYTHONIOENCODING=utf-8` (cmd) or
  `$env:PYTHONIOENCODING="utf-8"` (PowerShell) before running.
- **Can't connect to SQL** → check the SQL Server service is running, the `SERVER\INSTANCE`
  name, and that ODBC Driver 17/18 is installed. Test: `sqlcmd -S "YOURSERVER\INSTANCE" -E -Q "SELECT 1"`.
- **`pyodbc` install fails (Linux/CI)** → install `unixodbc-dev` first.
- **Graph questions say nothing / fall back to SQL** → Neo4j isn't running, or
  `NEO4J_PASSWORD` is blank in `.env`, or you didn't run `build_graph.py`.
- **Neo4j port already in use** → another Neo4j (Docker or local) is up; stop it first.
- **Reset the demo data** → re-run the three `sql/` scripts in step 4.

## What's where
`sql/` engine + data · `agent/` chat, tools, forecast/anomaly, FX, docs, text-to-SQL,
eval · `graph/` Neo4j layer · `ui/` web app · `docs/` design notes · `ROADMAP.md`
phases · this file = how to run it.
