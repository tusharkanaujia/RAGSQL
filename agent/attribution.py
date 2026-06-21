"""
FX isolation attribution (Phase 3).
===================================
Splits a day-over-day (or month-end) LBS move into **FX** vs **non-FX** and shows
which currencies drove the FX part. The split reconciles exactly to the total move.

Method (per currency, rate = GBP per 1 unit of currency):
    GBP = Local x Rate, so for each currency
      local_prior   = GBP_prior / rate_prior
      fx_effect     = local_prior x (rate_cur - rate_prior)      # held local, FX moved
      non_fx_effect = ΔGBP - fx_effect                            # activity + market
    Σ fx_effect + Σ non_fx_effect == ΔGBP   (exact)

Scope note: a *full* 5-way split (activity / market / collateral / netting) needs
trade-level price×quantity×fx components, which the fact table doesn't carry. FX
isolation is the part that's exactly computable from price-free data; netting and
collateral are separately available as line-item / balance-classification splits.

CLI:  python agent/attribution.py "how much of today's move is FX"
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.lbs_agent import DB, Tools, fmt_gbp, FILTERABLE_DIMS    # noqa: E402
from agent.charts import extract_filters, _resolution, _label, _write_spec  # noqa: E402

_FX_WORDS = ("fx", "currency effect", "currency impact", "exchange rate",
             "due to fx", "fx impact", "fx isolation", "fx move", "from fx",
             "because of fx", "currency moves explain")


def fx_isolation(db: DB, cur, prior, filters: dict | None = None) -> dict:
    filters = filters or {}
    for c in filters:
        if c not in FILTERABLE_DIMS:
            raise ValueError(f"filter column not allowed: {c}")
    conds = "".join(f" AND v.[{c}] = ? " for c in filters)
    sql = f"""
      SELECT ccy = v.Currency, bd = v.BusinessDate, GBP = SUM(v.LBS),
             Rate = MAX(COALESCE(fx.RateToGBP, 1.0))
      FROM SputnikCube.vwFactLBS_Enriched v
      LEFT JOIN SputnikCube.DimFxRate fx
        ON fx.BusinessDate = v.BusinessDate AND fx.Currency = v.Currency
      WHERE v.BusinessDate IN (?, ?) AND v.LineItem <> 'Unmatched'
            AND v.Currency IS NOT NULL {conds}
      GROUP BY v.Currency, v.BusinessDate;"""
    rows = db.proc(sql, tuple([cur, prior] + list(filters.values())))

    agg: dict[str, dict] = {}
    for r in rows:
        d = agg.setdefault(r["ccy"], {})
        if str(r["bd"]) == str(cur):
            d["cur_gbp"], d["cur_rate"] = float(r["GBP"] or 0), float(r["Rate"] or 1)
        else:
            d["prior_gbp"], d["prior_rate"] = float(r["GBP"] or 0), float(r["Rate"] or 1)

    by_ccy, tot_fx, tot_nonfx, tot_d = [], 0.0, 0.0, 0.0
    for ccy, d in agg.items():
        cg, pg = d.get("cur_gbp", 0.0), d.get("prior_gbp", 0.0)
        cr, pr = d.get("cur_rate", 1.0), d.get("prior_rate", 1.0)
        dgbp = cg - pg
        local_prior = pg / pr if pr else 0.0
        fx = local_prior * (cr - pr)
        nonfx = dgbp - fx
        tot_fx += fx; tot_nonfx += nonfx; tot_d += dgbp
        by_ccy.append({"ccy": ccy, "dgbp": dgbp, "fx": fx, "nonfx": nonfx})
    by_ccy.sort(key=lambda x: abs(x["fx"]), reverse=True)
    return {"total": tot_d, "fx": tot_fx, "non_fx": tot_nonfx, "by_currency": by_ccy}


def verdict(res: dict, cur, prior, filters: dict) -> str:
    scope = f" for {_label(filters)}" if filters else ""
    s = (f"LBS{scope} moved {fmt_gbp(res['total'], signed=True)} ({prior} → {cur}): "
         f"FX explains {fmt_gbp(res['fx'], signed=True)}, "
         f"non-FX (activity + market) {fmt_gbp(res['non_fx'], signed=True)}.")
    fxs = [c for c in res["by_currency"] if abs(c["fx"]) > 1]
    if fxs:
        s += " Largest FX: " + ", ".join(
            f"{c['ccy']} {fmt_gbp(c['fx'], signed=True)}" for c in fxs[:3]) + "."
    return s


def fx_spec(res: dict, filters: dict) -> dict:
    values = []
    for c in res["by_currency"]:
        values.append({"ccy": c["ccy"], "component": "FX", "value": round(c["fx"])})
        values.append({"ccy": c["ccy"], "component": "non-FX", "value": round(c["nonfx"])})
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": f"FX vs non-FX attribution{(' — ' + _label(filters)) if filters else ''}",
        "width": 720, "height": 360, "data": {"values": values},
        "mark": "bar",
        "encoding": {
            "x": {"field": "ccy", "type": "nominal", "title": "Currency"},
            "xOffset": {"field": "component"},
            "y": {"field": "value", "type": "quantitative", "title": "ΔLBS (GBP)"},
            "color": {"field": "component", "type": "nominal",
                      "scale": {"domain": ["FX", "non-FX"], "range": ["#ff7f0e", "#1f77b4"]}},
            "tooltip": [{"field": "ccy"}, {"field": "component"},
                        {"field": "value", "type": "quantitative", "format": ",.0f"}],
        },
    }


def is_fx_request(question: str) -> bool:
    ql = question.lower()
    return any(w in ql for w in _FX_WORDS)


def fx_answer(question: str, tools: Tools):
    if not is_fx_request(question):
        return None
    filters = extract_filters(question, tools)
    cur = tools.latest_date()
    prior = (tools.prior_month_end(cur) if _resolution(question) == "MONTHEND"
             else tools.prior_date(cur))
    if not prior:
        return "No prior date to compare.", None, None
    res = fx_isolation(tools.db, cur, prior, filters)
    spec = fx_spec(res, filters)
    path = _write_spec(spec, {**filters, "_": "fx"})
    return verdict(res, cur, prior, filters), spec, path


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "how much of today's move is FX"
    r = fx_answer(q, Tools(DB()))
    if r is None:
        print("[not an FX question] e.g. python agent/attribution.py \"how much is FX\"")
    else:
        print(r[0])
        if r[2]:
            print(f"[chart spec saved: {r[2]}]")
