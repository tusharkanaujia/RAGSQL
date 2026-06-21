"""
Chart tool (Phase 1b).
======================
Turns a trend question — "show USD TPA trend", "plot derivatives over time",
"chart Citadel month-end" — into:
  1. a grounded time series from SQL (with z-score anomaly bands), and
  2. a Vega-Lite spec a UI can render.

Grounding: the series is SUM(LBS) straight from `vwFactLBS_Enriched`; the summary
text is built deterministically with the engine's own formatter. The LLM is not
involved, so no figure can drift.

CLI:  python agent/charts.py "USD TPA trend"
"""
from __future__ import annotations
import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.lbs_agent import DB, Tools, fmt_gbp, FILTERABLE_DIMS   # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "charts_out")

# Dimensions the chart router will try to detect in a question, broad -> narrow.
_CHART_DIMS = ["Currency", "LBSSubCategory", "LineItem", "Business", "SubDivision",
               "Counterparty", "LegalEntity", "IssuerName", "CountryOfRisk", "ISIN"]

# Abbreviations users say that aren't literal column values.
_ALIASES = {
    "tpa": ("LBSSubCategory", "Trading Portfolio Assets"),
    "trading portfolio asset": ("LBSSubCategory", "Trading Portfolio Assets"),
    "tpl": ("LBSSubCategory", "Trading Portfolio Liabilities"),
    "rc": ("LBSSubCategory", "Replacement Cost"),
    "pfe": ("LBSSubCategory", "Potential Future Exposure"),
}

_CHART_WORDS = ("trend", "over time", "time series", "timeseries", "plot", "chart",
                "history of", "movement", "how has", "evolution", "graph of",
                "graph for", "trajectory")

_VOCAB: dict[str, list[str]] | None = None


def _vocab(tools: Tools) -> dict[str, list[str]]:
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = {d: tools.dim_values(d, top=200) for d in _CHART_DIMS}
    return _VOCAB


# --------------------------------------------------------------------------- #
# Data + spec
# --------------------------------------------------------------------------- #
def series_data(db: DB, filters: dict, resolution: str = "DAILY",
                points: int | None = None, as_of=None) -> list[dict]:
    """Time series of SUM(LBS) under whitelisted equality filters, with z-score."""
    for c in filters:
        if c not in FILTERABLE_DIMS:
            raise ValueError(f"filter column not allowed: {c}")
    if as_of is None:
        as_of = db.scalar("SELECT MAX(BusinessDate) FROM SputnikCube.FactLBS")
    if points is None:
        points = 12 if resolution == "MONTHEND" else 10
    me = " AND IsMonthEnd = 1 " if resolution == "MONTHEND" else ""
    conds = "".join(f" AND v.[{c}] = ? " for c in filters)
    sql = f"""
      WITH dts AS (
        SELECT TOP (?) BusinessDate FROM SputnikCube.vwLoadedDates
        WHERE BusinessDate <= ? {me} ORDER BY BusinessDate DESC ),
      ser AS (
        SELECT d.BusinessDate, Amt = ISNULL(SUM(v.LBS), 0)
        FROM dts d
        LEFT JOIN SputnikCube.vwFactLBS_Enriched v
          ON v.BusinessDate = d.BusinessDate AND v.LineItem <> 'Unmatched' {conds}
        GROUP BY d.BusinessDate )
      SELECT BusinessDate, Amt,
        WindowMean = AVG(Amt) OVER (),
        WindowStd  = STDEV(Amt) OVER (),
        ZScore = CASE WHEN STDEV(Amt) OVER () > 0
                      THEN (Amt - AVG(Amt) OVER ()) / STDEV(Amt) OVER () END,
        IsAnomaly = CASE WHEN STDEV(Amt) OVER () > 0
                         AND ABS((Amt - AVG(Amt) OVER ()) / STDEV(Amt) OVER ()) >= 2
                         THEN 1 ELSE 0 END
      FROM ser ORDER BY BusinessDate;"""
    return db.proc(sql, tuple([points, as_of] + list(filters.values())))


def _label(filters: dict) -> str:
    return " · ".join(filters.values()) if filters else "Whole book"


def vega_spec(rows: list[dict], filters: dict, resolution: str) -> dict:
    title = f"{_label(filters)} — LBS ({'month-end' if resolution == 'MONTHEND' else 'daily'})"
    values = []
    for r in rows:
        mean = float(r["WindowMean"] or 0)
        std = float(r["WindowStd"] or 0)
        values.append({
            "date": str(r["BusinessDate"]),
            "amt": float(r["Amt"] or 0),
            "lo": mean - 2 * std,
            "hi": mean + 2 * std,
            "anomaly": int(r["IsAnomaly"] or 0),
            "z": round(float(r["ZScore"]), 2) if r["ZScore"] is not None else None,
        })
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": title, "width": 720, "height": 360,
        "data": {"values": values},
        "encoding": {"x": {"field": "date", "type": "temporal", "title": "Business date"}},
        "layer": [
            {"mark": {"type": "area", "opacity": 0.15, "color": "#888"},
             "encoding": {"y": {"field": "lo", "type": "quantitative", "title": "LBS (GBP)"},
                          "y2": {"field": "hi"}}},
            {"mark": {"type": "line", "color": "#1f77b4", "point": False},
             "encoding": {"y": {"field": "amt", "type": "quantitative"}}},
            {"mark": {"type": "point", "filled": True, "size": 70},
             "encoding": {
                 "y": {"field": "amt", "type": "quantitative"},
                 "color": {"field": "anomaly", "type": "nominal",
                           "scale": {"domain": [0, 1], "range": ["#1f77b4", "#d62728"]},
                           "legend": None},
                 "tooltip": [{"field": "date", "type": "temporal"},
                             {"field": "amt", "type": "quantitative", "format": ",.0f", "title": "LBS"},
                             {"field": "z", "type": "quantitative", "title": "z-score"}]}},
        ],
    }


def summarize(rows: list[dict], filters: dict, resolution: str) -> str:
    """Deterministic, grounded one-paragraph summary of the series."""
    if not rows:
        return f"No data for {_label(filters)}."
    first, last = rows[0], rows[-1]
    res = "month-end" if resolution == "MONTHEND" else "daily"
    move = (last["Amt"] or 0) - (first["Amt"] or 0)
    arrow = "up" if move > 0 else ("down" if move < 0 else "flat")
    anoms = [r for r in rows if r["IsAnomaly"]]
    s = (f"{_label(filters)} — {res}, {len(rows)} points "
         f"({first['BusinessDate']} → {last['BusinessDate']}). "
         f"Latest {fmt_gbp(last['Amt'])} (window mean {fmt_gbp(last['WindowMean'])}); "
         f"{arrow} {fmt_gbp(move, signed=True)} over the window.")
    if anoms:
        a = max(anoms, key=lambda r: abs(r["ZScore"] or 0))
        s += (f" ⚠ {len(anoms)} anomaly point(s); largest {a['BusinessDate']} "
              f"({fmt_gbp(a['Amt'])}, z={a['ZScore']:.1f}).")
    return s


# --------------------------------------------------------------------------- #
# Free-text router
# --------------------------------------------------------------------------- #
def is_chart_request(question: str) -> bool:
    return any(w in question.lower() for w in _CHART_WORDS)


def extract_filters(question: str, tools: Tools) -> dict:
    ql = question.lower()
    filters: dict[str, str] = {}
    for alias, (col, val) in _ALIASES.items():            # abbreviations first
        if re.search(rf"\b{re.escape(alias)}\b", ql):
            filters.setdefault(col, val)
    for dim, vals in _vocab(tools).items():               # then literal vocab values
        if dim in filters:
            continue
        hit = sorted([v for v in vals if v and v.lower() in ql], key=len, reverse=True)
        if hit:
            filters[dim] = hit[0]
    return filters


def _resolution(question: str) -> str:
    ql = question.lower()
    return "MONTHEND" if any(w in ql for w in ("month-end", "month end", "monthly",
                                               "month", "quarter")) else "DAILY"


def _write_spec(spec: dict, filters: dict) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (_label(filters)).lower()).strip("-") or "whole-book"
    path = os.path.join(OUT_DIR, f"{slug}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)
    with open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh, indent=2)
    return path


def chart_answer(question: str, tools: Tools):
    """Return (summary_text, spec_path) for a trend question, or None if it isn't one."""
    if not is_chart_request(question):
        return None
    filters = extract_filters(question, tools)
    resolution = _resolution(question)
    rows = series_data(tools.db, filters, resolution)
    spec = vega_spec(rows, filters, resolution)
    path = _write_spec(spec, filters)
    return summarize(rows, filters, resolution), path


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "USD TPA trend"
    res = chart_answer(q, Tools(DB()))
    if res is None:
        print("[not a chart request] try e.g.  python agent/charts.py \"USD TPA trend\"")
    else:
        summary, path = res
        print(summary)
        print(f"[chart spec saved: {path}]")
