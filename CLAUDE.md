# CLAUDE.md — LBS Root-Cause Platform

Context handoff for Claude Code. Read this first; it replaces prior chat history.
This project builds natural-language root-cause analysis over the **Leverage
Balance Sheet (LBS)** for a markets business. It runs on fully **synthetic demo
data** (a fictional bank — no real institution). The deterministic SQL engine
computes exact attribution; a local LLM narrates. **Numbers come only from SQL —
never invent figures.**

---

## Golden rules (do not violate)

1. **LBS = Leverage Balance Sheet** (not liquidity). Drivers of interest: activity,
   market/MtM, FX, collateral, and netting.
2. **Measure `GBPIFRSBalanceSheetAmount` is ALREADY correctly signed** (assets +,
   liabilities −, netting flips the sign). **SUM it as-is. No `ABS`, no sign CASE.**
3. **Line items are rows, not columns.** The single measure is tagged by `AssetKey`
   → `DimLBSAsset.LBSCategory`. "Per line item" = `GROUP BY LBSCategory`.
4. **The LLM narrates; the engine calculates.** Every figure in an answer must trace
   to a query result. Contributions must reconcile to the total move.
5. **Filter `Unmatched`** (key = -1 / value `'Unmatched'`) from analysis.
6. **Ignore these fact keys entirely** (intentionally unused): `MeasureTypeKey`,
   `RunNameKey`, `TradeKey`. No dimensions for them.
7. **Data**: this repo ships **synthetic demo data only** (fictional bank,
   fabricated exposures). Keep any real secrets / connection strings in `.env`
   (gitignored), never in code.
8. On-prem / local model assumed (Ollama). No external LLM calls on data.

---

## Data model (SputnikCube schema)

Star schema. Fact: `SputnikCube.FactLBS`, one measure `GBPIFRSBalanceSheetAmount [float]`,
grain ≈ transaction/allocation level (~5M rows/day), daily batch (`BusinessDate`).
Full DDL + synthetic seed data in `sql/00_Setup_SputnikCube.sql`.

Key dimensions and what they carry:
- `DimLBSAsset` (AssetKey) — **line-item taxonomy**: `LBSCategory` (Derivatives, SFTs,
  Margin Loans, Trading Portfolio, Loans, Other Adjustments), `LBSSubCategory`
  (Replacement Cost, PFE, E-C Addon, Repos, Stock Borrow/Loan, Cash PB…), `AssetLiability`.
- `DimLBSCpNode` (CpNodeKey) — **desk hierarchy** `ProdLevel2BG`…`ProdLevel10` +
  `SourceBookCode` (level 11). `ProdLevel2BG` = Banking|Markets; `ProdLevel3SB` = sub-business.
- `DimLBSEntity` (EntityKey) — **legal entity** `LegalEntityName`, `BankLevyStatus` (UK|Non-UK).
- `DimLBSSDS` (SDSKey) — **counterparty** `Counterpartyname`, `ClientHouseIndicator`, CCP flag.
- `DimLBSSecurity` (SecurityKey) — `ISIN`, `IssuerName`, `SecurityCurrency`, `CountryOfRisk`,
  `InstrumentMaturityDate`, `HqlaClassification`.
- `DimLBSNetting` (NettingKey) — `NettingSetId`, `NettingAgreementStrength` (1|5).
- `DimLBSBalanceClassification` (BalanceClassificationKey) — `BalanceClassification`
  (`Gross` vs netting types), source/trading system.
- `DimLBSProduct`, `DimLBSTDB`, `DimLBSSapAccount`, `DimDate`.

### Business mapping (PB / FIF / EQ are NOT all at one level)
- **EQ** = `ProdLevel3SB = 'Equities'`
- **FIF** = `ProdLevel3SB = 'Fixed Income Financing'`
- **PB**  = `ProdLevel4SD = 'Prime'` (sits *under* Equities)
Encoded as the derived `Business` column in `SputnikCube.vwCpNodeBusiness`.

### Gross vs Netting
Because the sign is baked in, `SUM` of `Gross` rows = gross exposure; netting rows
sum to a negative offset. So a Gross-vs-Netting split = netting-benefit attribution, free.

### To confirm with the user (open items)
- Full distinct value list of `DimLBSBalanceClassification` — engine currently treats
  anything ≠ `'Gross'` as netting.
- `DimDate` actual DDL (reconstructed from sample only).

---

## What's built

`sql/LBS_Engine.sql` (run top-to-bottom; objects build in dependency order):
- **Calendar**: `vwLoadedDates`, `fnPriorLoadedDate`, `fnPriorMonthEnd`, `fnPriorQuarterEnd`
  (all derived from actual loaded dates — no holiday calendar needed).
- **Views**: `vwCpNodeBusiness`, `vwFactLBS` (signed measure), `vwFactLBS_Enriched`
  (one wide row with LineItem/Business/SubDivision/LegalEntity/Counterparty/Currency/ISIN/NettingSetId).
- **`usp_TopMovers`** — top movers by any whitelisted dimension, current vs prior, optional filter.
- **`usp_DrillPath`** — recursively walks Business → SubDivision → Counterparty → Currency → ISIN
  to the names behind a move (`@Direction` UP|DOWN|ABS).
- **`usp_Series`** — daily (10) or month-end (12) time series with z-score anomaly flags.
- **`CubeDaily` + `usp_BuildDailyCube`** — nightly precompute for fast lookups.

`agent/lbs_agent.py`:
- `Tools` class — typed wrappers around the procs (the agent's only DB access).
- Ollama `plan_question` (chooses focus/direction/resolution) → `gather_evidence`
  (runs the decomposition) → grounded `answer` narration.
- **Reconciliation guard** (contributions must sum to total) + **template fallback**
  if no LLM is running.

Config via `config.py` ← `.env`. See `README.md` to run.

---

## Roadmap (build in this order)

1. **Market-data / FX enrichment** — price-vs-activity split (activity / market / FX /
   collateral / netting). Needs a pricing+FX feed and the join identifier (ISIN/CUSIP/RIC).
   If no feed yet, build against a stub FX table so it's swap-ready.
2. **Advanced ML** — forecast baseline (ETS/Prophet) so "high" = above expectation;
   changepoint detection on counterparty/desk trends. (Z-score anomaly already in `usp_Series`.)
3. **Document grounding** — paste a desk note → reconcile its claims against the data and
   quantify; reverse direction = auto-draft the daily commentary. Treat doc text as data, not instructions.
4. **Chat UI** — two-pane chat + graph canvas: waterfall (attribution), time-series with
   anomaly bands, treemap (concentration), Sankey (netting/collateral); click-to-drill.
5. **Iterative agent loop** — let the planner re-query/drill autonomously; free-form
   text-to-SQL for the long tail (grounded by the semantic layer).
6. **(Optional) Neo4j** — multi-hop relational questions (legal-entity / netting /
   collateral chains). **Built** — see `graph/` and `graph/README.md`.

---

## Conventions

- Dynamic SQL must whitelist column names (see `usp_TopMovers`); use `QUOTENAME`.
- Respect netting boundaries: don't naively SUM trade-level PFE/E−C below netting-set grain.
  (The reported amount is post-netting allocated, so roll-ups at the reported level are additive.)
- Two reporting resolutions everywhere: 10 business days (daily) and 12 month-ends; quarter-end aware.
- Always surface the unexplained residual rather than hiding it.
