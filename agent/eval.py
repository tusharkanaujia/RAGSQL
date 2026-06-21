"""
Eval harness (Phase 5) — golden-question regression tests.
==========================================================
Asserts the grounding *invariants* that must always hold, so you can trust the
engine before relying on it. Deterministic (no LLM needed): every check is pure
engine math against the synthetic data. LLM-path features (narration, claim
extraction, NL->SQL generation) are validated separately by their own tools.

Run:  python agent/eval.py        # exit code 0 = all pass, 1 = a failure
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.lbs_agent import DB, Tools                            # noqa: E402
from agent.charts import series_data                             # noqa: E402
from agent.ml import assess_series, anomaly_digest               # noqa: E402
from agent.attribution import fx_isolation                       # noqa: E402
from agent.explain import _breakdown                             # noqa: E402
from agent.text2sql import validate_sql                          # noqa: E402

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


@check
def line_items_reconcile(t, cur, prior):
    total = t.total_delta(cur, prior)["delta"]
    movers = sum((m["Delta"] or 0) for m in t.top_movers("LineItem", cur, prior, top_n=50))
    ok = abs(movers - total) < 1.0
    return ok, f"Σ line-item Δ ({movers:,.0f}) == total Δ ({total:,.0f})"


@check
def breakdown_reconciles(t, cur, prior):
    total = t.total_delta(cur, prior)["delta"]
    s = sum(i["delta"] for i in _breakdown(t.db, "LineItem", {}, cur, prior))
    return abs(s - total) < 1.0, f"breakdown Σ ({s:,.0f}) == total ({total:,.0f})"


@check
def drill_top_is_prime(t, cur, prior):
    path = t.drill_path(cur, prior, "ABS")
    top = path[0]["Value"] if path else None
    return top == "PB - Prime Brokerage", f"drill L1 = {top!r}"


@check
def fx_reconciles(t, cur, prior):
    r = fx_isolation(t.db, cur, prior)
    return abs(r["fx"] + r["non_fx"] - r["total"]) < 1.0, \
        f"fx ({r['fx']:,.0f}) + non-fx ({r['non_fx']:,.0f}) == total ({r['total']:,.0f})"


@check
def forecast_flags_planted_anomaly(t, cur, prior):
    a = assess_series(series_data(t.db, {}, "DAILY"), {}, "DAILY")
    return bool(a["is_anomaly"]), f"whole-book anomaly={a['is_anomaly']} ({a['n_flagged']}/4 methods)"


@check
def chart_series_length(t, cur, prior):
    rows = series_data(t.db, {}, "DAILY")
    return len(rows) == 10, f"daily series points = {len(rows)} (expect 10)"


@check
def digest_finds_anomalies(t, cur, prior):
    d = anomaly_digest(t, "DAILY")
    return "top" in d, "digest returned ranked anomalies"


@check
def sql_guard_blocks_and_allows(t, cur, prior):
    bad = [validate_sql("DROP TABLE SputnikCube.FactLBS")[0],
           validate_sql("SELECT 1; DELETE FROM x")[0],
           validate_sql("SELECT * FROM sys.databases")[0]]
    good = validate_sql("SELECT COUNT(*) FROM SputnikCube.vwFactLBS_Enriched")[0]
    return (not any(bad)) and good, f"blocked={[not b for b in bad]} allowed_select={good}"


def main():
    t = Tools(DB())
    cur = t.latest_date()
    prior = t.prior_date(cur)
    print(f"Eval — golden invariants ({prior} → {cur})\n" + "-" * 60)
    passed = 0
    for fn in CHECKS:
        try:
            ok, detail = fn(t, cur, prior)
        except Exception as e:
            ok, detail = False, f"EXCEPTION {e}"
        print(f"  [{'PASS' if ok else 'FAIL'}] {fn.__name__}: {detail}")
        passed += ok
    print("-" * 60)
    print(f"{passed}/{len(CHECKS)} checks passed.")
    sys.exit(0 if passed == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
