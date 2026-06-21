"""
LBS Root-Cause Agent
====================
Orchestration layer over the SQL engine (LBS_Engine.sql). It:
  1. resolves the reporting dates,
  2. calls the deterministic procs as "tools" (numbers come ONLY from SQL),
  3. lets a local LLM (Ollama) plan the focus, then narrate the structured
     results — the LLM never invents a figure.

Deps:  pip install pyodbc requests
Assumes LBS_Engine.sql objects already exist in the SputnikCube schema.
"""

from __future__ import annotations
import json
import re
import datetime as dt
from dataclasses import dataclass, field

import pyodbc
import requests

# --------------------------------------------------------------------------- #
# Config  (values come from config.py <- .env; never hardcode secrets)
# --------------------------------------------------------------------------- #
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CONN_STR, OLLAMA_URL, OLLAMA_MODEL

ZSCORE_ALERT = 2.0


# --------------------------------------------------------------------------- #
# DB layer
# --------------------------------------------------------------------------- #
def _rows(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, r)) for r in cursor.fetchall()]


class DB:
    def __init__(self, conn_str: str = CONN_STR):
        self.cn = pyodbc.connect(conn_str, autocommit=True)

    def proc(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self.cn.cursor()
        cur.execute(sql, params)
        return _rows(cur)

    def scalar(self, sql: str, params: tuple = ()):
        cur = self.cn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


# --------------------------------------------------------------------------- #
# Tools  (thin, typed wrappers around the procs — the agent's only DB access)
# --------------------------------------------------------------------------- #
class Tools:
    def __init__(self, db: DB):
        self.db = db

    def latest_date(self) -> dt.date:
        return self.db.scalar("SELECT MAX(BusinessDate) FROM SputnikCube.FactLBS")

    def prior_date(self, cur: dt.date) -> dt.date:
        return self.db.scalar("SELECT SputnikCube.fnPriorLoadedDate(?)", (cur,))

    def prior_month_end(self, cur: dt.date) -> dt.date:
        return self.db.scalar("SELECT SputnikCube.fnPriorMonthEnd(?)", (cur,))

    def total_delta(self, cur: dt.date, prior: dt.date) -> dict:
        sql = """
          SELECT PriorTotal   = SUM(CASE WHEN BusinessDate=? THEN LBS END),
                 CurrentTotal = SUM(CASE WHEN BusinessDate=? THEN LBS END)
          FROM SputnikCube.vwFactLBS_Enriched
          WHERE BusinessDate IN (?,?) AND LineItem <> 'Unmatched'"""
        r = self.db.proc(sql, (prior, cur, prior, cur))[0]
        p, c = r["PriorTotal"] or 0, r["CurrentTotal"] or 0
        return {"prior": p, "current": c, "delta": c - p}

    def top_movers(self, dimension: str, cur: dt.date, prior: dt.date,
                   top_n: int = 15, filter_col: str | None = None,
                   filter_val: str | None = None) -> list[dict]:
        return self.db.proc(
            "EXEC SputnikCube.usp_TopMovers @Dimension=?, @CurDate=?, @PriorDate=?, "
            "@TopN=?, @FilterCol=?, @FilterVal=?",
            (dimension, cur, prior, top_n, filter_col, filter_val))

    def drill_path(self, cur: dt.date, prior: dt.date, direction: str = "UP") -> list[dict]:
        return self.db.proc(
            "EXEC SputnikCube.usp_DrillPath @CurDate=?, @PriorDate=?, @Direction=?",
            (cur, prior, direction))

    def series(self, dimension: str, value: str, as_of: dt.date,
               resolution: str = "DAILY") -> list[dict]:
        return self.db.proc(
            "EXEC SputnikCube.usp_Series @Dimension=?, @DimValue=?, @AsOf=?, @Resolution=?",
            (dimension, value, as_of, resolution))

    def dim_values(self, dimension: str, top: int = 30) -> list[str]:
        """Distinct values of a whitelisted dimension — vocabulary for the planner so it
        can map a user's words to an EXACT filter value (e.g. 'FIF')."""
        if dimension not in set(FILTERABLE_DIMS):
            return []                                # whitelist guard (col is interpolated)
        rows = self.db.proc(
            f"SELECT DISTINCT TOP (?) [{dimension}] AS v "
            f"FROM SputnikCube.vwFactLBS_Enriched "
            f"WHERE LineItem <> 'Unmatched' AND [{dimension}] IS NOT NULL "
            f"ORDER BY [{dimension}]", (top,))
        return [r["v"] for r in rows if r["v"]]


# --------------------------------------------------------------------------- #
# Formatting  (engine owns the figures; the LLM only ever sees final strings)
# --------------------------------------------------------------------------- #
def fmt_gbp(amount, signed: bool = False) -> str:
    """Signed GBP with the scale word baked in (e.g. '+£1.11bn', '-£605.7m').
    The LLM must never see a raw integer, so it can never misstate magnitude."""
    a = float(amount or 0)
    mag = abs(a)
    if   mag >= 1e12: body = f"£{mag/1e12:.2f}tn"
    elif mag >= 1e9:  body = f"£{mag/1e9:.2f}bn"
    elif mag >= 1e6:  body = f"£{mag/1e6:.1f}m"
    elif mag >= 1e3:  body = f"£{mag/1e3:.0f}k"
    else:             body = f"£{mag:,.0f}"
    if a < 0:      return "-" + body
    return ("+" + body) if signed else body


def _narration_view(ev: dict) -> dict:
    """Re-cast the evidence with every figure pre-formatted to a string. The
    narrator copies these verbatim — model choice can no longer affect a number."""
    def movers(rows):
        return [{"name": r["Dim"],
                 "prior":   fmt_gbp(r.get("PriorAmt")),
                 "current": fmt_gbp(r.get("CurrentAmt")),
                 "change":  fmt_gbp(r.get("Delta"), signed=True)} for r in rows]

    t = ev["total"]
    focus_key = next((k for k in ev if k.startswith("by_") and k != "by_line_item"), None)
    view = {
        "dates":         {"current": ev["current_date"], "prior": ev["prior_date"]},
        "scope":         (f"{ev['filter']['col']} = {ev['filter']['val']}"
                          if ev.get("filter") else "whole book"),
        "total_move":    fmt_gbp(t["delta"], signed=True),
        "total_prior":   fmt_gbp(t["prior"]),
        "total_current": fmt_gbp(t["current"]),
        "by_line_item":  movers(ev["by_line_item"]),
        "reconciles":    ev["reconciles"],
    }
    if focus_key:
        view[focus_key] = movers(ev[focus_key])
    if ev.get("drill_path"):
        view["drill_path"] = [{"level": p["Dimension"], "name": p["Value"],
                               "change": fmt_gbp(p["Delta"], signed=True)}
                              for p in ev["drill_path"]]
    a = ev.get("anomaly")
    if a:
        view["anomaly"] = {
            "dimension":  a["dimension"], "value": a["value"],
            "zscore":     round(a["zscore"], 1) if a.get("zscore") is not None else None,
            "is_anomaly": bool(a.get("is_anomaly")),
        }
    return view


# Hard guard: every £-figure the narrator prints must be one the engine handed it.
# Instructions alone don't stop a small model corrupting a digit (e.g. £1.11bn -> £11.11bn),
# so we verify after generation and fall back to the deterministic template if it fabricated one.
_MONEY_RE = re.compile(r"£\s?\d[\d,]*(?:\.\d+)?\s?(?:tn|bn|m|k)?", re.IGNORECASE)


def _money_tokens(text: str) -> set[str]:
    return {m.lstrip("+-").replace(" ", "").lower() for m in _MONEY_RE.findall(text)}


def _allowed_money(view: dict) -> set[str]:
    out: set[str] = set()
    def add(s):
        if isinstance(s, str) and "£" in s:
            out.add(s.lstrip("+-").replace(" ", "").lower())
    for k in ("total_move", "total_prior", "total_current"):
        add(view.get(k))
    for val in view.values():
        if isinstance(val, list):
            for row in val:
                if isinstance(row, dict):
                    for f in ("prior", "current", "change"):
                        add(row.get(f))
    return out


# --------------------------------------------------------------------------- #
# LLM layer (Ollama)
# --------------------------------------------------------------------------- #
def ollama(system: str, user: str, force_json: bool = False) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "options": {"temperature": 0},   # deterministic: fewer rambles / refusals / digit slips
    }
    if force_json:
        payload["format"] = "json"
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["message"]["content"]
    except Exception as e:                       # graceful fallback to template
        return f"__LLM_UNAVAILABLE__:{e}"


PLANNER_SYS = (
    "You route a question about leverage balance sheet (LBS) moves into a query plan. "
    "A CONVERSATION SO FAR may be given — use it to resolve follow-ups like 'that "
    "counterparty', 'drill into it', 'what about FIF', 'same but month-end'. "
    "Reply with JSON ONLY:\n"
    "{\"focus\": one of [LineItem,Business,SubBusiness,SubDivision,LegalEntity,"
    "Counterparty,Currency,ISIN],\n"
    " \"direction\": one of [UP,DOWN,ABS],\n"
    " \"resolution\": one of [DAILY,MONTHEND],\n"
    " \"filter_col\": one of the focus values above or null,\n"
    " \"filter_val\": a string (e.g. 'Citadel LLC', 'FIF - Fixed Income Financing') or null}\n"
    "Use filter_col/filter_val to scope to a name the user is now asking about (often "
    "carried from the previous turn). Pick a focus DIFFERENT from filter_col. "
    "Defaults: focus=Business, direction=ABS, resolution=DAILY, filter_col=null, filter_val=null."
)

NARRATOR_SYS = (
    "You are a markets treasury analyst writing leverage balance-sheet (LBS) commentary, "
    "in a multi-turn chat. Answer the CURRENT QUESTION, using CONVERSATION SO FAR for "
    "context when it is a follow-up.\n"
    "CRITICAL — NUMBERS: You must NEVER write a digit yourself. Each figure in FACTS has "
    "a token in square brackets, e.g. [MOVE], [L1], [P3]. Whenever you state a value, "
    "write its token ALONE. The token already contains the sign and currency (e.g. [L1] "
    "becomes '+£605.7m'), so do NOT put '+', '£', or any symbol before or after a token — "
    "write '[L1]', never '+£[L1]'. Writing any literal number (e.g. '1.1bn', '£500m', "
    "'2.8') is forbidden and will be rejected. Names (counterparties, desks, ISINs) you "
    "may write as plain text.\n"
    "STYLE: ONE concise paragraph, at most 4 sentences. Lead with the total move [MOVE], "
    "then the top 2-3 drivers, then the drill path to the names. Mention the anomaly if one "
    "is flagged. State each figure once — do NOT repeat figures or restate earlier answers. "
    "A leading '+' adds to leverage, '-' reduces."
)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
FOCUS_DIMS = ["LineItem", "Business", "SubBusiness", "SubDivision", "LegalEntity",
              "Counterparty", "Currency", "ISIN"]

# Columns a user may filter a chart/series by — all exist in vwFactLBS_Enriched.
FILTERABLE_DIMS = FOCUS_DIMS + ["LBSSubCategory", "CountryOfRisk", "IssuerName",
                                "BalanceClassification", "AssetLiability",
                                "BankLevyStatus", "ClientHouseIndicator", "NettingSetId"]


@dataclass
class Plan:
    focus: str = "Business"
    direction: str = "ABS"
    resolution: str = "DAILY"
    filter_col: str | None = None
    filter_val: str | None = None


def _vocab_text(vocab: dict | None) -> str:
    if not vocab:
        return ""
    parts = []
    for k in ("Business", "LineItem", "Currency", "Counterparty"):
        vals = vocab.get(k) or []
        if vals:
            parts.append(f"  {k}: " + " | ".join(vals[:20]))
    if not parts:
        return ""
    return ("VALID VALUES (when filtering, copy filter_val EXACTLY from these):\n"
            + "\n".join(parts) + "\n\n")


def plan_question(question: str, history: str = "", vocab: dict | None = None) -> Plan:
    user = (_vocab_text(vocab) +
            (f"CONVERSATION SO FAR:\n{history}\n\n" if history else "") +
            f"CURRENT QUESTION: {question}")
    raw = ollama(PLANNER_SYS, user, force_json=True)
    if raw.startswith("__LLM_UNAVAILABLE__"):
        return Plan()
    try:
        d = json.loads(raw)
    except Exception:
        return Plan()
    focus = d.get("focus") if d.get("focus") in FOCUS_DIMS else "Business"
    direction = d.get("direction") if d.get("direction") in ("UP", "DOWN", "ABS") else "ABS"
    resolution = d.get("resolution") if d.get("resolution") in ("DAILY", "MONTHEND") else "DAILY"
    fcol = d.get("filter_col") if d.get("filter_col") in FOCUS_DIMS else None
    fval = d.get("filter_val") or None

    # vocab repair: trust the VALUE over the column. The planner often files a name under
    # the wrong dimension (e.g. 'Citadel LLC' as LegalEntity, not Counterparty); if we know
    # which dimension actually contains the value, route the filter there.
    if fval and vocab:
        belongs = [dim for dim, vals in vocab.items() if fval in (vals or [])]
        if belongs and fcol not in belongs:
            fcol = belongs[0]
        elif not belongs and fcol is None:
            fval = None

    if not fcol:                 # need both halves or neither
        fval = None
    if fcol == focus:            # filtering on the focus dim is degenerate — drop it
        fcol = fval = None
    return Plan(focus, direction, resolution, fcol, fval)


def gather_evidence(tools: Tools, plan: Plan) -> dict:
    cur = tools.latest_date()
    prior = (tools.prior_month_end(cur) if plan.resolution == "MONTHEND"
             else tools.prior_date(cur))
    fcol, fval = plan.filter_col, plan.filter_val

    total = tools.total_delta(cur, prior)        # whole-book total (always unfiltered)
    by_line = tools.top_movers("LineItem", cur, prior, top_n=8,
                               filter_col=fcol, filter_val=fval)
    by_focus = tools.top_movers(plan.focus, cur, prior, top_n=8,
                                filter_col=fcol, filter_val=fval)
    path = tools.drill_path(cur, prior, plan.direction)

    # time series + anomaly on the single biggest mover in the focus dimension
    anomaly = None
    if by_focus:
        biggest = max(by_focus, key=lambda r: abs(r["Delta"] or 0))
        ser = tools.series(plan.focus, biggest["Dim"], cur, plan.resolution)
        if ser:
            latest = ser[-1]
            anomaly = {"dimension": plan.focus, "value": biggest["Dim"],
                       "zscore": latest.get("ZScore"),
                       "is_anomaly": latest.get("IsAnomaly"),
                       "series": [{"date": str(s["BusinessDate"]), "amt": s["Amt"]}
                                  for s in ser]}

    # reconciliation guard — only meaningful when the slice is the whole book
    recon_ok = True if fcol else \
        abs(sum((r["Delta"] or 0) for r in by_line) - total["delta"]) < 1.0

    return {"plan": plan.__dict__, "current_date": str(cur), "prior_date": str(prior),
            "filter": ({"col": fcol, "val": fval} if fcol else None),
            "total": total, "by_line_item": by_line, f"by_{plan.focus.lower()}": by_focus,
            "drill_path": path, "anomaly": anomaly, "reconciles": recon_ok}


# --------------------------------------------------------------------------- #
# Token legend  (the LLM references [TOKENS]; we substitute exact figures)
# --------------------------------------------------------------------------- #
# Match a token, ALSO eating any sign/currency the model wrongly typed in front of it
# (e.g. '+£[L1]' -> the substituted value already carries its own sign + '£').
_TOKEN_RE = re.compile(r"[+\-]?£?\[([A-Z]{1,6}\d*)\]")


def _facts_legend(view: dict) -> tuple[dict, str]:
    """Return (token -> exact string, human-readable legend for the prompt)."""
    facts: dict[str, str] = {}
    lines: list[str] = []

    def F(tok, label, val):
        facts[tok] = val
        lines.append(f"[{tok}] {label} = {val}")

    F("MOVE", f"total LBS move ({view['scope']})", view["total_move"])
    F("PRIOR", f"prior total ({view['dates']['prior']})", view["total_prior"])
    F("CUR", f"current total ({view['dates']['current']})", view["total_current"])

    lines.append("Line-item drivers:")
    for i, r in enumerate(view.get("by_line_item", [])[:6], 1):
        F(f"L{i}", r["name"], r["change"])

    focus_key = next((k for k in view if k.startswith("by_") and k != "by_line_item"), None)
    if focus_key:
        lines.append(f"{focus_key.replace('by_', '')} drivers:")
        for i, r in enumerate(view[focus_key][:6], 1):
            F(f"F{i}", r["name"], r["change"])

    if view.get("drill_path"):
        lines.append("Drill path to the names:")
        for i, p in enumerate(view["drill_path"], 1):
            F(f"P{i}", f"{p['level']} / {p['name']}", p["change"])

    a = view.get("anomaly")
    if a and a.get("is_anomaly"):
        lines.append(f"ANOMALY FLAGGED: {a['value']} ({a['dimension']}), "
                     f"z-score [Z]; write [Z] for the z-score value.")
        facts["Z"] = str(a["zscore"])
    return facts, "\n".join(lines)


def _apply_tokens(text: str, facts: dict) -> str:
    text = _TOKEN_RE.sub(lambda m: facts.get(m.group(1), ""), text)
    # strip brackets the model wrapped around a (now substituted) figure: [+£502.5m] -> +£502.5m
    text = re.sub(r"\[\s*([+\-]?£[^\[\]]*?)\s*\]", r"\1", text)
    # belt-and-braces: collapse any double sign/currency artifacts that slipped through
    text = re.sub(r"[+\-]?£\s*([+\-])£", r"\1£", text)   # +£+£.. / +£-£.. -> +£.. / -£..
    text = re.sub(r"£\s*£", "£", text)                    # ££ -> £
    text = re.sub(r"\+{2,}", "+", text)                   # ++ -> +
    return text.strip()


# --------------------------------------------------------------------------- #
# Deterministic narrative (LLM-free; also the safety fallback)
# --------------------------------------------------------------------------- #
def _template_narrative(ev: dict) -> str:
    t = ev["total"]
    scope = (f" within {ev['filter']['col']} = {ev['filter']['val']}"
             if ev.get("filter") else "")
    lines = [f"LBS moved {fmt_gbp(t['delta'], signed=True)} from {ev['prior_date']} to "
             f"{ev['current_date']} ({fmt_gbp(t['prior'])} -> {fmt_gbp(t['current'])})."]
    if ev["by_line_item"]:
        top = ev["by_line_item"][0]
        lines.append(f"Largest line-item move{scope}: {top['Dim']} "
                     f"{fmt_gbp(top['Delta'], signed=True)}.")
    if ev["drill_path"]:
        crumb = " > ".join(f"{p['Value']} ({fmt_gbp(p['Delta'], signed=True)})"
                           for p in ev["drill_path"])
        lines.append(f"Drill path: {crumb}.")
    if ev["anomaly"] and ev["anomaly"].get("is_anomaly"):
        a = ev["anomaly"]
        lines.append(f"Anomaly: {a['value']} z-score {a['zscore']:.1f}.")
    return " ".join(lines)


def _narrate(question: str, view: dict, history: str = "") -> str:
    """LLM commentary via token substitution; deterministic fallback on any breach."""
    facts, legend = _facts_legend(view)
    user = ((f"CONVERSATION SO FAR:\n{history}\n\n" if history else "") +
            f"CURRENT QUESTION:\n{question}\n\n"
            f"SCOPE: {view['scope']}\n\nFACTS (reference tokens, never write digits):\n{legend}")
    raw = ollama(NARRATOR_SYS, user)
    if raw.startswith("__LLM_UNAVAILABLE__"):
        return None                                  # caller uses the template
    # The model must reference figures via [TOKEN]s ONLY — a literal £-figure in the raw
    # output is a violation (even if it coincidentally matches some value), so reject it.
    if _money_tokens(raw):
        return None
    text = _apply_tokens(raw, facts)
    # defense in depth: post-substitution must contain only evidence figures
    if _money_tokens(text) - _allowed_money(view):
        return None
    text = text.split("\n\n")[0].strip()   # keep it to one paragraph (model sometimes rambles)
    # a real grounded answer cites at least one figure; if none rendered (e.g. the model
    # refused or went off-topic), it isn't an answer — fall back to the template.
    if not _money_tokens(text):
        return None
    return text


# --------------------------------------------------------------------------- #
# Conversation  (memory: each turn keeps a grounded digest for follow-ups)
# --------------------------------------------------------------------------- #
@dataclass
class Turn:
    question: str
    digest: str            # deterministic one-liner — safe, grounded context
    answer: str


class Conversation:
    """Stateful chat over the LBS engine. Holds per-turn history so the planner and
    narrator can resolve follow-ups ('drill into that counterparty', 'now month-end')."""

    def __init__(self, db: DB | None = None, max_history: int = 4,
                 store=None, conversation_id: int | None = None):
        self.tools = Tools(db or DB())
        self.turns: list[Turn] = []
        self.max_history = max_history
        self.store = store                       # optional ChatStore for persistence
        self.conversation_id = conversation_id
        # one-time vocabulary so the planner can map words -> exact filter values
        self.vocab = {dim: self.tools.dim_values(dim)
                      for dim in ("Business", "LineItem", "Currency",
                                  "Counterparty", "LegalEntity")}
        if store and conversation_id and store.exists(conversation_id):
            self.load_conversation(conversation_id)

    def _history(self) -> str:
        recent = self.turns[-self.max_history:]
        return "\n".join(f"Q{i+1}: {t.question}\nA{i+1}: {t.digest}"
                         for i, t in enumerate(recent))

    # --- persistence-aware turn recording -------------------------------- #
    def _record(self, question: str, digest: str, answer: str, source: str):
        if self.store and self.conversation_id:
            if not self.turns:                   # first turn titles the conversation
                title = question if len(question) <= 60 else question[:57] + "..."
                self.store.rename(self.conversation_id, title)
            self.store.add_turn(self.conversation_id, question, digest, answer, source)
        self.turns.append(Turn(question, digest, answer))

    def ask(self, question: str) -> str:
        history = self._history()
        plan = plan_question(question, history, self.vocab)
        ev = gather_evidence(self.tools, plan)
        view = _narration_view(ev)

        narration = _narrate(question, view, history)
        if narration is None:
            narration = _template_narrative(ev)
        if not ev["reconciles"]:
            narration += ("\n\n[!] Line-item contributions did not reconcile to the "
                          "total — investigate.")

        self._record(question, _template_narrative(ev), narration, "sql")
        return narration

    def add_external(self, question: str, answer: str, source: str = "graph"):
        """Record a turn answered outside ask() (e.g. the graph layer) so memory +
        persistence stay coherent."""
        self._record(question, answer.split("\n")[0], answer, source)

    # --- conversation lifecycle (persistence) ---------------------------- #
    def new_conversation(self, title: str = "Untitled") -> int | None:
        self.turns = []
        if self.store:
            self.conversation_id = self.store.create(title)
        return self.conversation_id

    def load_conversation(self, conversation_id: int):
        self.conversation_id = conversation_id
        self.turns = [Turn(t["question"], t["digest"], t["answer"])
                      for t in self.store.turns(conversation_id)] if self.store else []

    def reset(self):
        """Clear in-memory planner context (does not delete the stored conversation)."""
        self.turns.clear()


def answer(question: str, db: DB | None = None) -> str:
    """One-shot convenience wrapper (no memory)."""
    return Conversation(db).ask(question)


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
_DEMO_TURNS = [
    "Why is my LBS high today? What drove it?",
    "Drill into that counterparty — what line items is it in?",
    "What about Fixed Income Financing instead?",
    "Show me the month-end picture for the whole book.",
]


def run_demo():
    print("=== LBS chat — scripted multi-turn demo (proves memory + grounding) ===\n")
    convo = Conversation()
    for q in _DEMO_TURNS:
        print(f"you> {q}")
        print(f"lbs> {convo.ask(q)}\n")


def _graph_answer(question: str) -> str | None:
    """Try the Neo4j relational layer; None if not relational / graph unavailable.
    Lazy import keeps the graph optional and avoids a circular import."""
    try:
        from graph.lbs_graph import answer_relational
        return answer_relational(question)
    except Exception:
        return None


def _chart_answer(question: str, tools: "Tools"):
    """Try the chart tool; returns (summary, spec, path) for a trend question, else None."""
    try:
        from agent.charts import chart_answer
        return chart_answer(question, tools)
    except Exception:
        return None


def _forecast_answer(question: str, tools: "Tools"):
    """Try the forecast/anomaly layer; (verdict, spec, path) or None."""
    try:
        from agent.ml import forecast_answer
        return forecast_answer(question, tools)
    except Exception:
        return None


def _digest_answer(question: str, tools: "Tools"):
    """Try the morning anomaly digest; (text, None, None) or None."""
    try:
        from agent.ml import digest_answer
        return digest_answer(question, tools)
    except Exception:
        return None


def _fx_answer(question: str, tools: "Tools"):
    """Try FX-isolation attribution; (text, spec, path) or None."""
    try:
        from agent.attribution import fx_answer
        return fx_answer(question, tools)
    except Exception:
        return None


def _commentary_answer(question: str, tools: "Tools"):
    """Try auto-commentary ('draft the daily commentary'); (text, None, None) or None."""
    try:
        from agent.docs_ground import commentary_answer
        return commentary_answer(question, tools)
    except Exception:
        return None


def _explain_answer(question: str, tools: "Tools"):
    """Try iterative deep root-cause ('deep dive / root cause'); (text, None, None) or None."""
    try:
        from agent.explain import explain_answer
        return explain_answer(question, tools)
    except Exception:
        return None


def _text2sql_answer(question: str, tools: "Tools", force: bool = False):
    """Try grounded text-to-SQL (aggregate long-tail); (text, None, None) or None."""
    try:
        from agent.text2sql import text2sql_answer
        return text2sql_answer(question, tools, force=force)
    except Exception:
        return None


def route_question(convo: "Conversation", question: str) -> dict:
    """Single routing entry point (UI + REPL): digest -> forecast -> chart -> graph -> SQL.
    Records the turn + returns {text, source, spec, path}. Numbers come from SQL."""
    co = _commentary_answer(question, convo.tools)    # "draft the daily commentary"
    if co is not None:
        text, spec, path = co
        convo.add_external(question, text, source="commentary")
        return {"text": text, "source": "commentary", "spec": spec, "path": path}
    eo = _explain_answer(question, convo.tools)       # "deep dive / root cause"
    if eo is not None:
        text, spec, path = eo
        convo.add_external(question, text, source="explain")
        return {"text": text, "source": "explain", "spec": spec, "path": path}
    da = _digest_answer(question, convo.tools)        # "anomaly digest / what's unusual"
    if da is not None:
        text, spec, path = da
        convo.add_external(question, text, source="digest")
        return {"text": text, "source": "digest", "spec": spec, "path": path}
    xa = _fx_answer(question, convo.tools)             # "how much of the move is FX"
    if xa is not None:
        text, spec, path = xa
        convo.add_external(question, text, source="fx")
        return {"text": text, "source": "fx", "spec": spec, "path": path}
    fa = _forecast_answer(question, convo.tools)     # "is X abnormal / vs expectation"
    if fa is not None:
        text, spec, path = fa
        convo.add_external(question, text, source="forecast")
        return {"text": text, "source": "forecast", "spec": spec, "path": path}
    ca = _chart_answer(question, convo.tools)         # "show X trend"
    if ca is not None:
        summary, spec, path = ca
        convo.add_external(question, summary, source="chart")
        return {"text": summary, "source": "chart", "spec": spec, "path": path}
    g = _graph_answer(question)                       # relational (netting/entity chains)
    if g is not None:
        convo.add_external(question, g, source="graph")
        return {"text": g, "source": "graph", "spec": None, "path": None}
    ta = _text2sql_answer(question, convo.tools)      # aggregate long-tail (how many/list/avg)
    if ta is not None:
        text, spec, path = ta
        convo.add_external(question, text, source="sql-gen")
        return {"text": text, "source": "sql-gen", "spec": None, "path": None}
    return {"text": convo.ask(question), "source": "sql", "spec": None, "path": None}


_HELP = """Commands:
  /new [title]     start a new saved conversation
  /list            list saved conversations
  /open <id>       resume a saved conversation (loads its history)
  /title <text>    rename the current conversation
  /delete <id>     delete a saved conversation
  /history         show this conversation's turns
  /reset           clear in-memory context (keeps the saved conversation)
  /graph <q>       force the question to the Neo4j graph layer
  /digest [month]  scan dimensions for today's anomalies vs expectation
  /doc <path>      reconcile a desk note's claims against the data
  /commentary [m]  auto-draft the daily (or month-end) LBS commentary
  /explain [scope] iterative deep root-cause with residuals
  /sql <question>  grounded read-only text-to-SQL for the long tail
  /eval            run the golden-invariant checks
  /help            show this help
  /exit            quit
"""


def chat_repl():
    print("LBS root-cause chat. Ask about leverage balance-sheet moves.")
    print("Auto-routed: 'is X abnormal/vs expectation' -> forecast+anomaly · 'show X")
    print("trend' -> chart · netting/entity chains -> graph · everything else -> SQL.")
    print(_HELP)

    store = None
    try:
        from agent.store import ChatStore
        store = ChatStore()
    except Exception as e:
        print(f"(history disabled: {e})\n")

    convo = Conversation(store=store)
    convo.new_conversation()                      # start fresh; titled on first question
    if store:
        print(f"(new conversation #{convo.conversation_id} — /list to see past ones)\n")

    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q in ("/exit", "/quit"):
            break
        if q in ("/help", "/?"):
            print(_HELP); continue
        if q == "/reset":
            convo.reset(); print("(in-memory context cleared)\n"); continue
        if q == "/history":
            if not convo.turns:
                print("  (no turns yet)\n")
            for i, t in enumerate(convo.turns, 1):
                print(f"  {i}. Q: {t.question}\n     {t.digest}")
            print(); continue
        if q == "/digest" or q.startswith("/digest "):
            res = "MONTHEND" if "month" in q.lower() else "DAILY"
            try:
                from agent.ml import anomaly_digest
                print(anomaly_digest(convo.tools, res) + "\n")
            except Exception as e:
                print(f"(digest unavailable: {e})\n")
            continue
        if q.startswith("/doc "):
            path = q[5:].strip()
            try:
                from agent.docs_ground import ground_document
                with open(path, encoding="utf-8") as fh:
                    rep = ground_document(convo.tools, fh.read())
                print(rep + "\n"); convo.add_external(f"/doc {path}", rep, source="doc")
            except FileNotFoundError:
                print(f"  file not found: {path}\n")
            except Exception as e:
                print(f"  doc grounding failed: {e}\n")
            continue
        if q == "/commentary" or q.startswith("/commentary "):
            res = "MONTHEND" if "month" in q.lower() else "DAILY"
            try:
                from agent.docs_ground import auto_commentary
                c = auto_commentary(convo.tools, res)
                print(c + "\n"); convo.add_external("/commentary", c, source="commentary")
            except Exception as e:
                print(f"  commentary failed: {e}\n")
            continue
        if q == "/explain" or q.startswith("/explain "):
            scope = q[len("/explain "):].strip() if q.startswith("/explain ") else ""
            try:
                from agent.explain import deep_explain
                from agent.charts import extract_filters
                filt = extract_filters(scope, convo.tools) if scope else {}
                txt = deep_explain(convo.tools, filt)
                print(txt + "\n"); convo.add_external(q, txt, source="explain")
            except Exception as e:
                print(f"  explain failed: {e}\n")
            continue
        if q.startswith("/sql "):
            try:
                from agent.text2sql import text2sql_answer
                r = text2sql_answer(q[5:].strip(), convo.tools, force=True)
                txt = r[0] if r else "[sql] no result"
                print(txt + "\n"); convo.add_external(q, txt, source="sql-gen")
            except Exception as e:
                print(f"  sql failed: {e}\n")
            continue
        if q == "/eval":
            try:
                import subprocess
                print(subprocess.run([sys.executable, os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "agent", "eval.py")], capture_output=True, text=True).stdout)
            except Exception as e:
                print(f"  eval failed: {e}\n")
            continue

        # --- persistence commands ---------------------------------------- #
        if store and (q == "/new" or q.startswith("/new ")):
            title = q[5:].strip() or "Untitled"
            cid = convo.new_conversation(title)
            print(f"(started conversation #{cid})\n"); continue
        if store and q == "/list":
            rows = store.list()
            if not rows:
                print("  (no saved conversations)\n")
            for c in rows:
                mark = "*" if c.id == convo.conversation_id else " "
                print(f" {mark}#{c.id}  {c.title}  ({c.turn_count} turns, {c.updated_at})")
            print(); continue
        if store and q.startswith("/open "):
            arg = q[6:].strip()
            if arg.isdigit() and store.exists(int(arg)):
                convo.load_conversation(int(arg))
                print(f"(opened #{arg} — {len(convo.turns)} turns restored)\n")
            else:
                print("  no such conversation id\n")
            continue
        if store and q.startswith("/title "):
            title = q[7:].strip()
            if title and convo.conversation_id:
                store.rename(convo.conversation_id, title)
                print(f"(renamed to: {title})\n")
            continue
        if store and q.startswith("/delete "):
            arg = q[8:].strip()
            if arg.isdigit() and store.exists(int(arg)):
                store.delete(int(arg))
                print(f"(deleted #{arg})")
                if int(arg) == convo.conversation_id:
                    convo.new_conversation(); print(f"(now on new #{convo.conversation_id})")
                print()
            else:
                print("  no such conversation id\n")
            continue

        # --- forced graph ------------------------------------------------- #
        if q.startswith("/graph "):
            gq = q[len("/graph "):].strip()
            g = _graph_answer(gq)
            if g is not None:
                print(g + "\n"); convo.add_external(gq, g, source="graph")
            else:
                print("[graph] not a relational question, or graph unavailable.\n")
            continue

        # --- auto-route: forecast -> chart -> graph -> SQL ---------------- #
        r = route_question(convo, q)            # records the turn (memory + persistence)
        out = f"lbs> {r['text']}"
        if r.get("path"):
            out += f"\n     [chart spec: {r['path']}]"
        print(out + "\n")

    if store:
        store.close()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_demo()
    else:
        chat_repl()
