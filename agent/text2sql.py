"""
Grounded text-to-SQL (Phase 5) — the long tail.
================================================
For aggregate questions the fixed procs don't cover ("how many counterparties are
CCPs?", "average LBS per business", "list legal entities with USD exposure"), the
LLM writes a *read-only* SELECT against the semantic view, which is then **strictly
validated** before running. Numbers come from the executed query, so it stays grounded.

Guardrails (all must pass, else the query is rejected — never executed):
  - must be a single statement starting with SELECT (no WITH/CTE, no ';' chains);
  - no DDL/DML/exec keywords (insert/update/delete/drop/alter/exec/sp_/xp_/into/…);
  - may only read whitelisted objects (the enriched view + the FX table);
  - results are row-capped and the cursor is time-limited.

CLI:  python agent/text2sql.py "how many counterparties are CCPs"
"""
from __future__ import annotations
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.lbs_agent import DB, Tools, fmt_gbp, ollama          # noqa: E402

ALLOWED_OBJECTS = {"sputnikcube.vwfactlbs_enriched", "sputnikcube.dimfxrate"}
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|exec|execute|merge|truncate|grant|"
    r"revoke|into|backup|restore|openrowset|openquery|sp_\w*|xp_\w*)\b", re.I)
_OBJ = re.compile(r"\b(?:from|join)\s+([a-z0-9_\.\[\]]+)", re.I)
_ROW_CAP = 200

SCHEMA = (
    "View SputnikCube.vwFactLBS_Enriched — one row per allocation, columns:\n"
    "  BusinessDate (date), LineItem, LBSSubCategory, AssetLiability, "
    "BalanceClassification, Business, SubBusiness, SubDivision, LegalEntity, "
    "BankLevyStatus, Counterparty, ClientHouseIndicator, ISIN, IssuerName, Currency, "
    "CountryOfRisk, NettingSetId, LBS (float, the SIGNED measure — SUM it as-is).\n"
    "Also SputnikCube.DimFxRate(BusinessDate, Currency, RateToGBP).")

GEN_SYS = (
    "You write ONE read-only Microsoft T-SQL SELECT to answer a question about a "
    "leverage balance sheet. Output SQL ONLY (no prose, no markdown fences).\n" + SCHEMA +
    "\nRules: SELECT only (no WITH/CTE, no semicolons, no INSERT/UPDATE/DELETE/EXEC). "
    "Read ONLY from SputnikCube.vwFactLBS_Enriched (join DimFxRate only if needed). "
    "Always filter LineItem <> 'Unmatched'. Use TOP (n) for rankings. SUM(LBS) for amounts.")

_TRIGGERS = ("how many", "count ", "number of", "list ", "which ", "average ",
             "avg ", "median ", "what is the average", "how much total",
             "per business", "per currency", "per counterparty", "group by", " per ")


def is_sql_request(question: str) -> bool:
    ql = question.lower()
    return any(t in ql for t in _TRIGGERS)


def _clean(sql: str) -> str:
    sql = re.sub(r"```(?:sql)?", "", sql, flags=re.I).strip().strip("`").strip()
    return sql


def validate_sql(sql: str) -> tuple[bool, str]:
    s = _clean(sql)
    if not s:
        return False, "empty"
    body = s.rstrip(";").strip()
    if ";" in body:
        return False, "multiple statements not allowed"
    if not re.match(r"(?is)^\s*select\b", body):
        return False, "must start with SELECT (no WITH/CTE/DDL)"
    if _FORBIDDEN.search(body):
        return False, "forbidden keyword present"
    for raw in _OBJ.findall(body):
        obj = raw.lower().replace("[", "").replace("]", "")
        if obj not in ALLOWED_OBJECTS:
            return False, f"object not allowed: {raw}"
    return True, body


def run_sql(db: DB, sql: str, cap: int = _ROW_CAP):
    db.cn.timeout = 15
    cur = db.cn.cursor()
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchmany(cap)
    return cols, [list(r) for r in rows]


def _fmt_table(cols, rows, max_rows=15) -> str:
    def cell(col, v):
        if v is None:
            return ""
        if isinstance(v, (int, float)) and re.search(r"lbs|amt|gbp|sum|delta|total|exposure",
                                                     col, re.I):
            return fmt_gbp(v, signed=True)
        return str(v)
    out = ["  " + " | ".join(cols)]
    for r in rows[:max_rows]:
        out.append("  " + " | ".join(cell(c, v) for c, v in zip(cols, r)))
    if len(rows) > max_rows:
        out.append(f"  … (+{len(rows) - max_rows} more rows)")
    return "\n".join(out)


def text2sql_answer(question: str, tools: Tools, force: bool = False):
    """Return (text, None, None) for an aggregate question, or None if not one."""
    if not force and not is_sql_request(question):
        return None
    cur = tools.latest_date()
    prior = tools.prior_date(cur)
    user = (f"Latest BusinessDate = '{cur}', prior business day = '{prior}'.\n"
            f"QUESTION: {question}")
    raw = ollama(GEN_SYS, user)
    if raw.startswith("__LLM_UNAVAILABLE__"):
        return "[sql] no local LLM available to generate the query.", None, None
    ok, payload = validate_sql(raw)
    if not ok:
        return (f"[sql] generated query rejected by guard — {payload}.\n"
                f"  (raw: {_clean(raw)[:140]})", None, None)
    sql = payload
    try:
        cols, rows = run_sql(tools.db, sql)
    except Exception as e:
        return f"[sql] query failed: {str(e)[:160]}\n  SQL: {sql}", None, None
    if not rows:
        return f"[sql] no rows.\n  SQL: {sql}", None, None
    head = (f"[sql] {rows[0][0]}" if len(cols) == 1 and len(rows) == 1
            else f"[sql] {len(rows)} row(s):")
    return f"{head}\n{_fmt_table(cols, rows)}\n  SQL: {sql}", None, None


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "how many counterparties are CCPs"
    r = text2sql_answer(q, Tools(DB()), force=True)
    print(r[0] if r else "[not a SQL-style question]")
