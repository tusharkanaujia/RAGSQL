"""
Offline tests — no database / no LLM required.
==============================================
These run anywhere (including GitHub Actions, which has no SQL Server): they exercise
the pure functions that guard correctness — the GBP formatter, the token substitution
used for grounded narration, and the text-to-SQL safety validator.

The full data-dependent invariants live in agent/eval.py (needs the SputnikCube DB).

Run:  python tests/test_offline.py     # exit 0 = all pass
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.lbs_agent import fmt_gbp, _apply_tokens, _money_tokens, _allowed_money  # noqa: E402
from agent.text2sql import validate_sql                                            # noqa: E402

_fails: list[str] = []


def eq(name, got, exp):
    if got != exp:
        _fails.append(f"{name}: got {got!r}, expected {exp!r}")


def ok(name, cond):
    if not cond:
        _fails.append(f"{name}: expected truthy")


# --- GBP formatter (scale baked in; the LLM never invents a magnitude) -------
eq("fmt_bn", fmt_gbp(1106187075, signed=True), "+£1.11bn")
eq("fmt_m", fmt_gbp(-180000000), "-£180.0m")
eq("fmt_k", fmt_gbp(952000), "£952k")
eq("fmt_tn", fmt_gbp(-1234567890123, signed=True), "-£1.23tn")

# --- token substitution (incl. double-prefix cleanup) -----------------------
eq("tok_basic", _apply_tokens("total [MOVE] now", {"MOVE": "+£1.11bn"}), "total +£1.11bn now")
eq("tok_prefix", _apply_tokens("total +£[MOVE]", {"MOVE": "+£1.11bn"}), "total +£1.11bn")
eq("tok_doublecur", _apply_tokens("££[P]", {"P": "£9.94bn"}), "£9.94bn")
eq("tok_spacing", _apply_tokens("a [X] b", {"X": "+£5m"}), "a +£5m b")

# --- grounding guard helpers ------------------------------------------------
ok("money_tokens_finds", _money_tokens("up +£605.7m today") == {"£605.7m"})
view = {"total_move": "+£1.11bn", "by_line_item": [{"change": "+£605.7m"}]}
ok("allowed_money", {"£1.11bn", "£605.7m"} <= _allowed_money(view))

# --- text-to-SQL safety validator -------------------------------------------
ok("sql_block_drop", not validate_sql("DROP TABLE SputnikCube.FactLBS")[0])
ok("sql_block_multi", not validate_sql("SELECT 1; DELETE FROM x")[0])
ok("sql_block_obj", not validate_sql("SELECT * FROM sys.databases")[0])
ok("sql_block_update", not validate_sql("UPDATE SputnikCube.vwFactLBS_Enriched SET LBS=0")[0])
ok("sql_allow_select", validate_sql("SELECT COUNT(*) FROM SputnikCube.vwFactLBS_Enriched")[0])
ok("sql_allow_fenced", validate_sql("```sql\nSELECT Currency FROM SputnikCube.vwFactLBS_Enriched\n```")[0])


if __name__ == "__main__":
    total = "see source"
    if _fails:
        print("FAILED:")
        for f in _fails:
            print("  -", f)
        sys.exit(1)
    print("All offline tests passed.")
    sys.exit(0)
