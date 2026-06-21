"""
Forecast baseline + multi-method anomaly detection (Phase 2).
=============================================================
Answers "is X abnormal / above expectation?" — not just "vs yesterday". For a
grounded time series (from the chart tool's SQL query) it computes:

  - a **Holt** (level+trend) one-step-ahead forecast -> *expected* latest value
    + a "surprise" in σ of the forecast residuals;
  - a **4-method anomaly ensemble** on the window — z-score, IQR, MAD, and
    forecast-residual — flagged when ≥2 agree;
  - a simple **changepoint** (largest mean-shift split), if significant.

Pure-Python (statistics only) so it runs on short series (10 daily / 12 month-end)
with no heavy ML deps. Numbers come from SQL; this layer only adds expectation/flags.

CLI:  python agent/ml.py "is USD TPA abnormal today"
"""
from __future__ import annotations
import os
import sys
import statistics as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.lbs_agent import DB, Tools, fmt_gbp                      # noqa: E402
from agent.charts import (series_data, extract_filters, _resolution,  # noqa: E402
                          _label, _write_spec)

_FORECAST_WORDS = ("abnormal", "anomaly", "anomalous", "unusual", "unexpected",
                   "expectation", "forecast", "outlier", "off-trend", "off trend",
                   "as expected", "spike", "out of line", "elevated vs")


def _std(xs) -> float:
    return st.pstdev(xs) if len(xs) > 1 else 0.0


# --------------------------------------------------------------------------- #
# Forecast (Holt linear) + anomaly methods
# --------------------------------------------------------------------------- #
def holt(values: list[float], alpha: float = 0.5, beta: float = 0.3):
    """One-step-ahead forecasts (aligned to values[1:]) + next-step forecast."""
    if len(values) < 2:
        return [], (values[-1] if values else 0.0)
    level, trend = values[0], values[1] - values[0]
    forecasts = []
    for i in range(1, len(values)):
        forecasts.append(level + trend)            # forecast for point i (pre-actual)
        prev = level
        level = alpha * values[i] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev) + (1 - beta) * trend
    return forecasts, level + trend


def _methods(values: list[float], resid_std: float, surprise: float | None) -> dict:
    x = values[-1]
    out: dict[str, dict] = {}
    # z-score
    mean, sd = st.mean(values), _std(values)
    z = (x - mean) / sd if sd else 0.0
    out["zscore"] = {"flag": abs(z) >= 2, "score": round(z, 2)}
    # IQR (Tukey)
    if len(values) >= 4:
        q = st.quantiles(values, n=4)
        iqr = q[2] - q[0]
        lo, hi = q[0] - 1.5 * iqr, q[2] + 1.5 * iqr
        out["iqr"] = {"flag": x < lo or x > hi, "score": round((x - st.median(values)) / iqr, 2) if iqr else 0.0}
    # MAD (modified z)
    med = st.median(values)
    mad = st.median([abs(v - med) for v in values])
    mz = 0.6745 * (x - med) / mad if mad else 0.0
    out["mad"] = {"flag": abs(mz) >= 3.5, "score": round(mz, 2)}
    # forecast residual
    if surprise is not None:
        out["forecast"] = {"flag": abs(surprise) >= 2, "score": round(surprise, 2)}
    return out


def changepoint(values: list[float], dates: list[str]):
    """Largest mean-shift split; returned only if the shift is material."""
    n = len(values)
    if n < 6:
        return None
    overall = _std(values) or 1.0
    best = None
    for i in range(2, n - 2):
        a, b = values[:i], values[i:]
        diff = abs(st.mean(b) - st.mean(a))
        if best is None or diff > best[1]:
            best = (i, diff)
    i, diff = best
    if diff >= 1.5 * overall:
        return {"date": dates[i], "before": st.mean(values[:i]), "after": st.mean(values[i:])}
    return None


def assess_series(rows: list[dict], filters: dict, resolution: str) -> dict:
    values = [float(r["Amt"] or 0) for r in rows]
    dates = [str(r["BusinessDate"]) for r in rows]
    forecasts, _next = holt(values)
    expected = forecasts[-1] if forecasts else None
    residuals = [values[i + 1] - forecasts[i] for i in range(len(forecasts))]
    resid_std = _std(residuals)
    actual = values[-1] if values else 0.0
    surprise = ((actual - expected) / resid_std) if (expected is not None and resid_std) else None
    methods = _methods(values, resid_std, surprise)
    n_flagged = sum(1 for m in methods.values() if m["flag"])
    return {
        "label": _label(filters), "resolution": resolution,
        "latest_date": dates[-1] if dates else None,
        "actual": actual, "expected": expected, "resid_std": resid_std,
        "surprise_sigma": surprise,
        "direction": "above" if (surprise or 0) >= 0 else "below",
        "methods": methods, "n_flagged": n_flagged, "is_anomaly": n_flagged >= 2,
        "changepoint": changepoint(values, dates),
        "forecasts": forecasts, "values": values, "dates": dates,
    }


# --------------------------------------------------------------------------- #
# Verdict text (grounded) + chart spec with an expected line
# --------------------------------------------------------------------------- #
def verdict(a: dict) -> str:
    if a["expected"] is None:
        return f"{a['label']}: not enough history to form an expectation."
    sigma = a["surprise_sigma"]
    s = (f"{a['label']} — latest {a['latest_date']}: actual {fmt_gbp(a['actual'])} vs "
         f"expected {fmt_gbp(a['expected'])} (Holt)")
    if sigma is not None:
        s += f" → {abs(sigma):.1f}σ {a['direction']} expectation."
    flagged = [f"{k} {v['score']}" for k, v in a["methods"].items() if v["flag"]]
    if a["is_anomaly"]:
        s += f" ⚠ ANOMALY — {a['n_flagged']}/{len(a['methods'])} methods agree ({', '.join(flagged)})."
    else:
        s += f" Within normal range ({a['n_flagged']}/{len(a['methods'])} methods flag it)."
    cp = a["changepoint"]
    if cp:
        s += (f" Trend shifted around {cp['date']} "
              f"({fmt_gbp(cp['before'])} → {fmt_gbp(cp['after'])} avg).")
    return s


def forecast_spec(a: dict, filters: dict) -> dict:
    res = "month-end" if a["resolution"] == "MONTHEND" else "daily"
    fc = a["forecasts"]
    vals, dates = a["values"], a["dates"]
    mean, sd = st.mean(vals), _std(vals)
    values = []
    for i, d in enumerate(dates):
        exp = fc[i - 1] if i >= 1 else None          # one-step-ahead aligns to dates[1:]
        z = (vals[i] - mean) / sd if sd else 0.0
        values.append({"date": d, "amt": vals[i], "expected": exp,
                       "lo": mean - 2 * sd, "hi": mean + 2 * sd,
                       "anomaly": 1 if abs(z) >= 2 else 0})
    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "title": f"{a['label']} — actual vs expected ({res})",
        "width": 720, "height": 360, "data": {"values": values},
        "encoding": {"x": {"field": "date", "type": "temporal", "title": "Business date"}},
        "layer": [
            {"mark": {"type": "area", "opacity": 0.12, "color": "#888"},
             "encoding": {"y": {"field": "lo", "type": "quantitative", "title": "LBS (GBP)"},
                          "y2": {"field": "hi"}}},
            {"mark": {"type": "line", "color": "#1f77b4"},
             "encoding": {"y": {"field": "amt", "type": "quantitative"}}},
            {"mark": {"type": "line", "color": "#ff7f0e", "strokeDash": [5, 4]},
             "encoding": {"y": {"field": "expected", "type": "quantitative"}}},
            {"mark": {"type": "point", "filled": True, "size": 70},
             "encoding": {"y": {"field": "amt", "type": "quantitative"},
                          "color": {"field": "anomaly", "type": "nominal",
                                    "scale": {"domain": [0, 1], "range": ["#1f77b4", "#d62728"]},
                                    "legend": None}}},
        ],
    }


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def is_forecast_request(question: str) -> bool:
    return any(w in question.lower() for w in _FORECAST_WORDS)


def _explain_drivers(tools: Tools, filters: dict, resolution: str) -> str:
    """Auto-explanation: when something is anomalous, drill to the top drivers of the
    day-over-day (or month-end) move so the flag says WHY, not just THAT."""
    try:
        cur = tools.latest_date()
        prior = (tools.prior_month_end(cur) if resolution == "MONTHEND"
                 else tools.prior_date(cur))
        if not prior:
            return ""
        fcol = next(iter(filters)) if len(filters) == 1 else None
        fval = filters.get(fcol) if fcol else None
        breakdown = "Business" if fcol == "LineItem" else "LineItem"
        movers = tools.top_movers(breakdown, cur, prior, top_n=3,
                                  filter_col=fcol, filter_val=fval)
        top = [m for m in movers if (m["Delta"] or 0)][:2]
        if not top:
            return ""
        return ("Driven by " +
                ", ".join(f"{m['Dim']} {fmt_gbp(m['Delta'], signed=True)}" for m in top) + ".")
    except Exception:
        return ""


def forecast_answer(question: str, tools: Tools):
    """Return (verdict_text, vega_spec, spec_path) for an expectation/anomaly question,
    or None if it isn't one."""
    if not is_forecast_request(question):
        return None
    filters = extract_filters(question, tools)
    resolution = _resolution(question)
    rows = series_data(tools.db, filters, resolution)
    if len(rows) < 4:
        return f"{_label(filters)}: not enough history to assess.", None, None
    a = assess_series(rows, filters, resolution)
    text = verdict(a)
    if a["is_anomaly"]:                              # auto-explanation
        why = _explain_drivers(tools, filters, resolution)
        if why:
            text += " " + why
    spec = forecast_spec(a, filters)
    path = _write_spec(spec, {**filters, "_": "forecast"})
    return text, spec, path


# --------------------------------------------------------------------------- #
# Morning anomaly digest — scan dimensions, rank today's outliers vs expectation
# --------------------------------------------------------------------------- #
_DIGEST_WORDS = ("digest", "what's unusual", "whats unusual", "what is unusual",
                 "anomaly report", "anomalies today", "what stands out",
                 "scan for anomalies", "morning report", "what changed")


def is_digest_request(question: str) -> bool:
    return any(w in question.lower() for w in _DIGEST_WORDS)


def anomaly_digest(tools: Tools, resolution: str = "DAILY",
                   dims=("Business", "LineItem", "Currency", "Counterparty"),
                   top: int = 6) -> str:
    found = []
    for dim in dims:
        for val in tools.dim_values(dim, top=50):
            rows = series_data(tools.db, {dim: val}, resolution)
            if len(rows) < 4:
                continue
            a = assess_series(rows, {dim: val}, resolution)
            if a["is_anomaly"] and a["surprise_sigma"] is not None:
                found.append((abs(a["surprise_sigma"]), dim, val, a))
    found.sort(key=lambda x: x[0], reverse=True)
    res = "month-end" if resolution == "MONTHEND" else "daily"
    if not found:
        return f"Anomaly digest ({res}): nothing materially off expectation."
    lines = [f"Anomaly digest ({res}) — top {min(top, len(found))} vs expectation:"]
    for sigma, dim, val, a in found[:top]:
        lines.append(f"  • {val} ({dim}): {fmt_gbp(a['actual'])} vs exp "
                     f"{fmt_gbp(a['expected'])} — {sigma:.1f}σ {a['direction']}, "
                     f"{a['n_flagged']}/{len(a['methods'])} methods.")
    return "\n".join(lines)


def digest_answer(question: str, tools: Tools):
    if not is_digest_request(question):
        return None
    return anomaly_digest(tools, _resolution(question)), None, None


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "is the whole book abnormal today"
    res = forecast_answer(q, Tools(DB()))
    if res is None:
        print("[not a forecast/anomaly request] e.g. python agent/ml.py \"is USD TPA abnormal\"")
    else:
        text, _spec, path = res
        print(text)
        if path:
            print(f"[chart spec saved: {path}]")
