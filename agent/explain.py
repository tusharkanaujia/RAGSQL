"""
Iterative deep root-cause (Phase 5).
====================================
The fixed drill walks one path; this walks it *iteratively*, surfacing the
**unexplained residual** at every level — the project rule "always surface the
residual rather than hiding it". Deterministic (numbers from SQL), multi-filter so it
can descend LineItem → Business → Counterparty → Currency → ISIN under the running
scope.

CLI:  python agent/explain.py "root cause of today's move"
      python agent/explain.py "deep dive into Prime Brokerage"
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.lbs_agent import DB, Tools, fmt_gbp, FILTERABLE_DIMS    # noqa: E402
from agent.charts import extract_filters, _label                   # noqa: E402

_LEVELS = ["LineItem", "Business", "Counterparty", "Currency", "ISIN"]
_WORDS = ("deep dive", "deep-dive", "root cause", "root-cause", "break it down",
          "break down", "full breakdown", "explain in detail", "decompose",
          "walk me through", "drill all", "drill down fully", "fully explain")


def _breakdown(db: DB, group_dim: str, filters: dict, cur, prior) -> list[dict]:
    """Day-over-day delta grouped by group_dim under arbitrary whitelisted filters."""
    if group_dim not in FILTERABLE_DIMS:
        raise ValueError(group_dim)
    for c in filters:
        if c not in FILTERABLE_DIMS:
            raise ValueError(c)
    conds = "".join(f" AND v.[{c}] = ? " for c in filters)
    sql = (f"SELECT g = v.[{group_dim}], bd = v.BusinessDate, amt = SUM(v.LBS) "
           f"FROM SputnikCube.vwFactLBS_Enriched v "
           f"WHERE v.BusinessDate IN (?, ?) AND v.LineItem <> 'Unmatched' {conds} "
           f"GROUP BY v.[{group_dim}], v.BusinessDate")
    rows = db.proc(sql, tuple([cur, prior] + list(filters.values())))
    agg: dict[str, dict] = {}
    for r in rows:
        k = r["g"] if r["g"] is not None else "(none)"
        d = agg.setdefault(k, {"p": 0.0, "c": 0.0})
        d["c" if str(r["bd"]) == str(cur) else "p"] = float(r["amt"] or 0)
    items = [{"name": k, "delta": v["c"] - v["p"]} for k, v in agg.items()]
    items.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return items


def deep_explain(tools: Tools, filters: dict | None = None) -> str:
    cur = tools.latest_date()
    prior = tools.prior_date(cur)
    if not prior:
        return "No prior business day to compare."
    filters = dict(filters or {})
    scoped_total = sum(i["delta"] for i in _breakdown(tools.db, "LineItem", filters, cur, prior))
    scope = f", scope {_label(filters)}" if filters else ""
    lines = [f"Root-cause ({prior} → {cur}){scope}: total {fmt_gbp(scoped_total, signed=True)}."]

    parent = scoped_total
    indent = "  "
    for lvl in _LEVELS:
        if lvl in filters:
            continue
        if parent == 0 or abs(parent) < abs(scoped_total) * 0.05:
            break                              # remaining contribution negligible
        items = [i for i in _breakdown(tools.db, lvl, filters, cur, prior) if i["name"] != "(none)"]
        if not items:
            break
        top = items[:2]
        shown = sum(i["delta"] for i in top)
        for i in top:
            pct = (i["delta"] / parent * 100) if parent else 0
            lines.append(f"{indent}↳ {lvl}: {i['name']} {fmt_gbp(i['delta'], signed=True)} "
                         f"({pct:.0f}% of parent)")
        resid = parent - shown
        if abs(resid) > abs(parent) * 0.02:
            lines.append(f"{indent}  · residual ({lvl}): {fmt_gbp(resid, signed=True)} "
                         f"across {len(items) - len(top)} other(s)")
        lead = top[0]
        filters[lvl] = lead["name"]            # descend into the largest
        parent = lead["delta"]
        indent += "  "
    return "\n".join(lines)


def is_explain_request(question: str) -> bool:
    return any(w in question.lower() for w in _WORDS)


def explain_answer(question: str, tools: Tools):
    if not is_explain_request(question):
        return None
    return deep_explain(tools, extract_filters(question, tools)), None, None


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "root cause of today's move"
    r = explain_answer(q, Tools(DB()))
    print(r[0] if r else "[not a deep-dive request]")
