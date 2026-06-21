# LBS (Leverage Balance Sheet) Root-Cause & Commentary Platform — Deep Plan
**v0.2** · markets business (PB / FIF / EQ) · SQL Server · daily batch · for review

---

## 0. What changed since v0.1

Incorporating your latest requirements:

- **LBS = Leverage Balance Sheet** (not liquidity) → drives a richer, leverage-specific attribution model (§1).
- **Daily batch**: fact loaded + dimensions SCD-updated once/day → a fixed nightly precompute window (§4).
- **Two time resolutions**: last **10 business days** (daily) + last **12 month-ends** (monthly) → separate baselines & anomaly models (§3).
- **Analysis cube**: total + per **line item** + per **legal entity** + per **business** (PB / FIF / EQ) (§2).
- **FX & market-data commentary** (§9).
- **Iterative DB access** by the agent — re-query / drill as needed (§6).
- **Doc / text grounding** — paste a note and fuse it with DB analysis (§7).
- **Chat UI + rich graphs** (§8).

---

## 1. Domain framing — Leverage Balance Sheet

Leverage exposure is **not** a simple asset sum, so the attribution must reflect how the measure is built:

| Bucket | Components in your data |
|---|---|
| On-balance-sheet | Trading portfolio assets, loans, margin loans/receivables |
| Derivatives | Replacement cost (E−C, exposure − collateral) + PFE add-on |
| SFTs | Gross SFT assets + counterparty-credit add-on (netting-restricted) |

**Why this reshapes the analytics:**

- **5-way attribution**, not price/volume/FX:
  `Δ Leverage = Activity (new/amended/closed) + Market/MtM + FX + Collateral (VM through E−C) + Netting-benefit change + Residual`
  The collateral and netting terms are leverage-specific and are where counterintuitive moves live (a risk-neutral repo book can inflate leverage; offsetting trades can *reduce* it via netting).
- **Legal entity is a first-class axis** — the leverage ratio is reported per entity/consolidation level, so per-entity attribution is core, not optional.
- **Period-end matters** — quarter-end window-dressing / financing wind-down is a known leverage pattern; the month-end series must be quarter-end-aware.

---

## 2. The analysis cube

Precompute nightly (post-batch) so chat is instant and the agent drills into stored contributions, not raw 5M-row scans.

**Cube axes:** `metric (total + each line item) × legal_entity × business (PB/FIF/EQ) × time_resolution (daily | month-end)`

**Per cell, precompute:** current level, Δ vs prior point, Δ vs baseline, contribution to parent, anomaly flag + score, and a draft one-line commentary.

**Grain note:** roll-ups must respect netting boundaries — **PFE and E−C are netting-set level, not trade-additive**. The cube aggregates from netting-set results upward, never by naive trade-level SUM of those measures.

---

## 3. Multi-resolution time series & baselines

| Resolution | Window | Answers | Baseline / anomaly |
|---|---|---|---|
| Daily | Last 10 business days | DoD moves, T-1 attribution, intra-week pattern | Rolling mean/median, z-score, IQR, IsolationForest on daily |
| Month-end | Last 12 month-ends | MoM/QoQ trend, period-end behaviour, window-dressing | STL/seasonal baseline; quarter-end vs non-quarter-end expectation |

The two tell different stories — "vs yesterday" is activity/market; "vs last month-end" often shows financing wind-down. Commentary must state which baseline it's using.

---

## 4. Architecture (updated layers)

1. **Data & snapshot** — fact/dim warehouse + closure table (hierarchy lvl 2→11) + market-data/FX snapshot time-aligned to the LBS as-of date.
2. **Semantic layer** — logical↔physical mapping (entities, attributes, metrics, joins, hierarchies, synonyms). Rename = one metadata edit. *(unchanged from v0.1 §5)*
3. **Nightly precompute** — builds the cube, anomaly scores, baselines, and draft commentary after the batch.
4. **Deterministic analytical engine** — parameterised, tested functions returning exact numbers + evidence (§5A).
5. **ML layer** — anomaly, forecast, changepoint, seasonality, clustering (§5B).
6. **Orchestration agent** — plan → call tools → re-query DB → reconcile → narrate (§6).
7. **Doc-grounding / RAG** — fuse pasted docs with DB analysis; auto-commentary (§7).
8. **Presentation** — chat + graph canvas + auto-generated daily dashboard (§8).

---

## 5. Capability catalog ("what else can I do")

### A. Deterministic analytics
- Variance / bridge decomposition (total Δ → contributions, reconciles exactly).
- **5-way leverage attribution** (activity / market / FX / collateral / netting).
- Trade-population reconciliation (entries / exits / continuing — "same-store" leverage).
- **Netting & collateral attribution** (gross vs netting-benefit vs collateral) — highest-insight, hardest.
- SFT gross-up analysis.
- Concentration / Herfindahl (top-N counterparties / issuers / desks).
- Limit-proximity alerts (entity/desk approaching a leverage limit).
- Cross-business contribution share (PB vs FIF vs EQ).
- Date-to-date bridge waterfall; multi-period (MoM/QoQ).

### B. ML
- **Anomaly detection** (daily + month-end, per cube cell): z-score / IQR / IsolationForest / STL residual.
- **Forecast / expected baseline**: ETS / Prophet → "high vs expectation," not just vs yesterday.
- **Changepoint detection**: structural breaks in a counterparty/desk trend (ruptures / Bayesian).
- **Seasonality (STL)**: separate trend / seasonal / residual; quarter-end-aware.
- **Clustering / regime detection**: group counterparties/desks by leverage behaviour.
- **Predictive driver ranking** (forecast only, *not* attribution): gradient-boost + SHAP for "what predicts next month-end." Keep separate from exact decomposition.

### C. Agent & tool-calling
- Tools = deterministic analytics + DB query + market-data fetch + anomaly check + doc-retrieval + reconciliation self-check.
- **Iterative loop** (plan → execute → reflect → re-query) so it drills the hierarchy and goes back to the DB as needed.
- **Guardrails**: read-only, parameterised templates / query whitelist, row + cost caps, audit log of every query and figure.
- **Reconciliation gate**: agent must confirm contributions sum to total Δ (residual surfaced) before answering.

### D. Doc-grounding & auto-commentary
- **Context injection** (small docs) and **RAG** (large/many docs).
- **Doc-vs-data reconciliation**: confirm/contradict a desk note and quantify it.
- **Auto-commentary**: post-batch draft of the daily leverage commentary from cube + anomalies + market data + prior note.

### E. Visualization
- Waterfall/bridge (attribution), time-series with anomaly bands, treemap (concentration), Sankey (netting/collateral flow), small multiples per business/entity, drill-down tables.
- Every chat answer emits narrative **plus** the relevant chart spec.
- Auto-generated daily dashboard post-batch; export to the desk's commentary format.

### F. Ops / productionization
- Nightly precompute so morning chat is instant.
- Partition by AsOfDate; pre-aggregate; cache.
- Full lineage / audit (regulated context).
- **Eval harness**: golden questions with known answers to regression-test the agent before trusting it.

---

## 6. The agent loop (iterative DB access)

```
Question
 → resolve to logical metrics/entities/filters/period (semantic layer)
 → plan: which analytic(s), which cube cells
 → execute tool / query precomputed cube
 → reflect: biggest contributor? need to drill? need market data? need doc?
 → re-query DB / call market data / retrieve doc   ← loops as needed
 → reconcile (Σ contributions = total Δ; residual surfaced)
 → narrate with every figure cited to a result + emit chart spec
```

---

## 7. Doc / text grounding — design

- **Modes:** inject small docs directly into agent context; chunk + embed + retrieve for large corpora (RAG).
- **Fusion (the valuable bit):** run DB analysis first, then cross-reference the doc. Output reconciles narrative claims against quantified data, isolating what's confirmed, contradicted, and unexplained.
- **Reverse direction (auto-commentary):** generate the daily/period commentary draft from data + prior notes; analyst edits rather than writes.
- **Boundary:** treat document contents as data, not instructions — surface and confirm any side-effectful asks rather than acting on them.

---

## 8. UI & visualization

- **Two-pane:** chat + live graph canvas. Question type selects the chart (attribution → waterfall; trend → time-series w/ anomaly band; concentration → treemap; netting/collateral → Sankey).
- **Daily dashboard** auto-built post-batch: firm total, per-business, per-entity tiles with anomaly highlights and the draft commentary.
- **Drill interaction:** click a contributor → agent drills that node.
- Export to commentary template; save views.

---

## 9. Anomaly & FX / market commentary

- **Anomaly:** per cube cell, both resolutions; flag + score + plain-English reason.
- **FX isolation:** revalue prior-day positions at today's FX to split FX effect from activity/market — FX revaluation is frequently the dominant, least-intuitive leverage driver.
- **Market-driven vs activity-driven:** market data tells the agent whether a move is "they traded" or "the market moved," which is the first thing commentary should state.

---

## 10. Phased roadmap

| Phase | Delivers | Notes |
|---|---|---|
| 0 | Semantic layer, closure table, snapshot discipline, two TS stores | Foundation |
| 1 | Cube precompute + `variance_decomposition` + `top_movers` (daily) | Answers most "why high" |
| 2 | LLM narration over cube JSON + basic chat UI | First usable product |
| 3 | Market data + FX isolation + 5-way leverage attribution | Core differentiation |
| 4 | Anomaly + month-end/QoQ + forecasting baseline | "Abnormal vs expectation" |
| 5 | Iterative agent loop + free-form text-to-SQL + graph canvas | Full self-serve |
| 6 | Doc-grounding + auto-commentary; (optional) Neo4j relational layer | High-value extensions |

---

## 11. ML vs agent — rule

Computable fact (attribution, trend, ranking, drill-down) → **deterministic + agent**. Learned expectation or undiscoverable pattern (anomaly, forecast, changepoint, seasonality, clustering) → **ML**. Never use learned importance for exact attribution.

---

## 12. Inputs still needed / open questions

1. DDL: fact + all dims + date table (you're providing).
2. Per measure: trade-additive vs netting-set level? Is netting structure in the warehouse or reconstructed?
3. Hierarchy lvl 2→11 + source book (11): columns on a dim, or a separate table?
4. Legal-entity list and the **consolidation levels** that matter for reporting.
5. Confirm businesses: PB = Prime Brokerage, EQ = Equities, **FIF = ?** (Fixed Income Financing?).
6. Market-data/FX feed availability + join identifiers (ISIN/CUSIP/RIC); reporting base currency.
7. Snapshot storage: `AsOfDate` column vs type-2 history.
8. Which baseline is primary operationally — T-1, MoM, or QoQ?
9. Deployment / model constraints (on-prem only? which local model for tool-calling?).

---

## 13. Correctness pitfalls

Netting boundaries (no naive trade-level SUM of PFE/E−C) · snapshot/market-data time alignment · population reconciliation before P/Q attribution · hierarchy additivity · FX base-vs-trade effect · month-end/quarter-end semantics · entity consolidation levels · full auditability.

---
*End of deep plan v0.2.*
