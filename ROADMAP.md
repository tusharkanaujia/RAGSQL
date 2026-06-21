# Roadmap — LBS Root-Cause Platform

Natural-language root-cause analysis + commentary over a Leverage Balance Sheet
data warehouse (SQL Server star schema, ~5M rows/day, one pre-signed measure).

**Architecture principle (holds across every phase):**
> The deterministic SQL engine computes the **numbers**. ML/agents produce only
> **expectations, flags, prose, and chart specs** — never the reported figures.
> Every figure in any answer traces back to a SQL result. This separation is what
> makes the output trustworthy in a regulated setting.

Legend: ✅ done · 🟡 in progress · ◻️ planned

---

## Phase 0 — Foundation ✅
- ✅ Synthetic `SputnikCube` (rebuildable) + deterministic engine: calendar,
  top-movers, drill-path (Business→SubDivision→Counterparty→Currency→ISIN),
  time-series with z-score, nightly cube.
- ✅ Grounded chat: history-aware planner, token-substitution narration, hard guard
  rejecting any fabricated figure, deterministic template fallback.
- ✅ In-session conversation memory (follow-up resolution).
- ✅ Optional Neo4j graph layer for multi-hop relational questions (netting/entity
  chains).

## Phase 1 — Make it a product ✅
*Goal: durable conversations + a visual surface.*
- ✅ **1a. Persistent chat history** (SQLite, `agent/store.py`): save / list / resume /
  rename / delete conversations across restarts; auto-titled from the first question;
  graph turns persisted too.
- ✅ 1b. **Chart-spec tool** (`agent/charts.py`): trend questions ("show USD TPA trend",
  "plot derivatives over time", "Citadel month-end") → a grounded time series with
  z-score **anomaly bands** + a **Vega-Lite** spec; auto-routed in chat.
- ✅ 1c. **Two-pane web UI** (`ui/`, Flask + Vega-Lite): chat pane + live chart canvas;
  conversation switcher with saved history; the three routes (chart/graph/SQL) tagged
  per message. *Follow-on ◻️: richer chart types (waterfall/treemap/Sankey) and
  click-a-contributor → drill.*

## Phase 2 — Smart baselines (ML) 🟡
*Goal: "high vs **expectation**", not just vs yesterday.* (`agent/ml.py`)
- ✅ Forecast baseline (Holt level+trend, pure-Python — no heavy deps) → expected value
  + residual band + "surprise" in σ.
- ✅ Multi-method anomaly ensemble (z-score + IQR + MAD + forecast-residual), daily &
  month-end; flagged when ≥2 methods agree.
- ✅ Changepoint detection (largest material mean-shift).
- ✅ Auto-routed in chat ("is X abnormal / vs expectation") with an actual-vs-expected chart.
- ✅ Anomaly → **auto-explanation** (flag auto-triggers the drill: "Driven by SFTs +£605.7m…").
- ✅ **Morning digest** (`/digest`): scans dimensions, ranks today's outliers vs expectation.
- ◻️ Seasonality/STL (quarter-end window-dressing aware) + IsolationForest (needs sklearn).

## Phase 3 — Market-data / FX enrichment 🟡
*Goal: the analytical crown jewel — explain WHY, not just WHERE.*
- ✅ Stub FX feed (`sql/01_FxRates.sql`, swap-ready) + **FX isolation** (`agent/attribution.py`):
  splits a move into FX vs non-FX (activity+market), per currency, reconciling exactly;
  auto-routed ("how much of the move is FX") with an FX-vs-non-FX bar chart.
- ◻️ Full **5-way attribution** (activity / market / collateral / netting) — needs
  trade-level price×qty×fx components the fact table doesn't carry yet. (Netting and
  collateral are already available as line-item / balance-classification splits.)
- ◻️ Real price + market-data feed (replace the stub).

## Phase 4 — Document grounding & commentary ✅  (`agent/docs_ground.py`)
- ✅ **Document grounding** (`/doc <path>`): the LLM extracts a note's claims, the engine
  reconciles each vs SQL (confirmed / partly / contradicted / unverified) with amount
  checks. Document treated as data, not instructions (prompt-injection guard).
- ✅ **Auto-commentary** (`/commentary`, or "draft the daily commentary"): deterministic
  daily/month-end draft from total move + top line items + drill path + anomaly digest.
- ◻️ RAG over large doc corpora (embeddings) — current is single-doc context injection.

## Phase 5 — Agentic & self-serve 🟡
- ✅ Iterative deep root-cause (`agent/explain.py`, `/explain`): drills LineItem → Business
  → Counterparty → Currency → ISIN, surfacing the **residual at each level**.
- ✅ Grounded text-to-SQL (`agent/text2sql.py`, `/sql`): read-only SELECT, strictly
  validated (SELECT-only, keyword + object whitelist, row/time caps) — for the
  "how many / list / average" long tail.
- ✅ Eval harness (`agent/eval.py`, `/eval`): 8 golden invariants (reconciliation, drill,
  FX, anomaly, series, digest, SQL guard) — **8/8 green**.
- ◻️ LLM-driven multi-agent loop (planner · analysts · critic) — the current loop is
  deterministic; an LLM ReAct controller is the future option.

## Cross-cutting ◻️
- ◻️ Semantic layer as code (powers safe text-to-SQL + renames).
- ◻️ Audit & lineage (every query + figure logged) — regulated context.
- ◻️ Access control / row-level security per desk/entity.
- ◻️ Concentration (Herfindahl/top-N) + limit-proximity tools.
- ◻️ Nightly precompute expansion for instant morning chat.

---

### Suggested build order
1. **Phase 1a → 1b → 1c** (persistent history → chart tool → UI) — turns the engine
   into a usable product.
2. **Phase 2** (forecast + anomaly) — makes "why is it high" genuinely smart.
3. **Phase 3** (attribution + FX) — needs a feed; highest analytical value.
4. **Phase 4** (docs + auto-commentary).
5. **Phase 5** (agentic + text-to-SQL + eval).
