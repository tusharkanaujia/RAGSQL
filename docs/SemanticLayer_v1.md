# LBS Platform — Semantic Layer + Core Attribution (Phase 0–1 Starter)
**v1** · bound to the SputnikCube demo schema · SQL Server

This is the foundation brick: a logical→physical mapping and the first deterministic
decomposition. Everything downstream (agent, ML, UI) calls these.

---

## 1. Semantic layer (logical → physical)

```yaml
fact:
  lbs:
    physical_table: SputnikCube.FactLBS
    date_column: BusinessDate
    grain: [BusinessDate, AssetKey, BalanceClassificationKey, CpNodeKey, EntityKey,
            MeasureTypeKey, NettingKey, ProductKey, RunNameKey, SapAccountKey,
            SDSKey, SecurityKey, TDBKey, TradeKey]
    measures:
      lbs_usage:
        sql: SUM(SputnikCube.FactLBS.GBPIFRSBalanceSheetAmount)
        synonyms: [lbs, leverage, balance sheet usage, exposure]
        # OPEN: sign convention on GBPIFRSBalanceSheetAmount (see notes §4)

entities:
  asset:                       # the LBS line-item taxonomy
    table: SputnikCube.DimLBSAsset
    key: AssetKey
    join: FactLBS.AssetKey = DimLBSAsset.AssetKey
    attributes:
      line_item:    { column: LBSCategory, synonyms: [line item, category, product type] }
      sub_category: { column: LBSSubCategory }
      asset_subclass: { column: AssetSubClass }
      asset_liability: { column: AssetLiability }   # 'Assets' | 'Liabilities'

  cpnode:                      # desk / book hierarchy (levels 2->10 + source book = 11)
    table: SputnikCube.DimLBSCpNode
    key: CpNodeKey
    join: FactLBS.CpNodeKey = DimLBSCpNode.CpNodeKey
    attributes:
      business_group: { column: ProdLevel2BG }      # Banking | Markets
      sub_business:   { column: ProdLevel3SB }       # Equities | Macro | Credit | Fixed Income Financing | ...
      sub_division:   { column: ProdLevel4SD }       # Prime | Rates | Equity Derivatives | ...
      product_group:  { column: ProdLevel5PG }
      product:        { column: ProdLevel6PR }
      level7:  { column: ProdLevel7 }
      level8:  { column: ProdLevel8 }
      level9:  { column: ProdLevel9 }
      level10: { column: ProdLevel10 }
      source_book_code: { column: SourceBookCode }   # level 11
      main_trader: { column: MainTrader }
      trader:      { column: Trader }
      reporting_cluster: { column: ReportingCluster }
    derived:
      business:    # PB/FIF/EQ as selectable peers (see view in §2)
        logic: |
          CASE WHEN ProdLevel4SD = 'Prime' THEN 'PB - Prime Brokerage'
               WHEN ProdLevel3SB = 'Equities' THEN 'EQ - Equities'
               WHEN ProdLevel3SB = 'Fixed Income Financing' THEN 'FIF - Fixed Income Financing'
               ELSE COALESCE(ProdLevel3SB, 'Unmapped') END

  entity:                      # legal entity (per-entity leverage reporting)
    table: SputnikCube.DimLBSEntity
    key: EntityKey
    join: FactLBS.EntityKey = DimLBSEntity.EntityKey
    attributes:
      legal_entity_name: { column: LegalEntityName, synonyms: [legal entity, entity] }
      legal_entity:      { column: LegalEntity }
      bank_levy_group:   { column: BankLevyGroup }
      bank_levy_status:  { column: BankLevyStatus }  # UK | Non-UK
      reporting_category:{ column: ReportingCategory }

  counterparty:                # SDS
    table: SputnikCube.DimLBSSDS
    key: SDSKey
    join: FactLBS.SDSKey = DimLBSSDS.SDSKey
    attributes:
      counterparty_name: { column: Counterpartyname, synonyms: [counterparty, cpty, client] }
      client_house:      { column: ClientHouseIndicator }   # House | Client
      ccp_flag:          { column: CounterpartyCCPFlag }
      is_internal:       { column: IsCounterpartyInternal }
      cpty_legal_entity: { column: LegalEntitySDSNameSiteName }

  security:
    table: SputnikCube.DimLBSSecurity
    key: SecurityKey
    join: FactLBS.SecurityKey = DimLBSSecurity.SecurityKey
    attributes:
      isin:            { column: ISIN, synonyms: [isin, instrument] }
      issuer_name:     { column: IssuerName }
      security_ccy:    { column: SecurityCurrency, synonyms: [currency, ccy] }
      country_of_risk: { column: CountryOfRisk }
      maturity_date:   { column: InstrumentMaturityDate }
      hqla_classification: { column: HqlaClassification }
      security_type:   { column: SecurityType }

  netting:
    table: SputnikCube.DimLBSNetting
    key: NettingKey
    join: FactLBS.NettingKey = DimLBSNetting.NettingKey
    attributes:
      netting_set_id:   { column: NettingSetId }
      agreement_strength: { column: NettingAgreementStrength }  # 1 | 5 (-1 unmatched)

  product:
    table: SputnikCube.DimLBSProduct
    key: ProductKey
    join: FactLBS.ProductKey = DimLBSProduct.ProductKey
    attributes:
      cem_product:    { column: CEMProduct }       # FX | EQ | IR | COM
      notional_ccy:   { column: NotionalCCYCode }
      otc_etd_flag:   { column: OTCETDFlag }        # OTC | ETD | ND
      tts_product_type: { column: TTSProductType }

  tdb:
    table: SputnikCube.DimLBSTDB
    key: TDBKey
    join: FactLBS.TDBKey = DimLBSTDB.TDBKey
    attributes:
      tdb_product_type: { column: TDBProductType }
      tdb_sub_type:     { column: TDBSubType }

  sap_account:
    table: SputnikCube.DimLBSSapAccount
    key: SapAccountKey
    join: FactLBS.SapAccountKey = DimLBSSapAccount.SapAccountKey
    attributes:
      sap_l2: { column: Level2Description }
      sap_l3: { column: Level3Description }
      sap_l4: { column: Level4Description }
      sap_l5: { column: Level5Description }
      sap_account: { column: SapAccount }

  balance_classification:
    table: SputnikCube.DimLBSBalanceClassification
    key: BalanceClassificationKey
    join: FactLBS.BalanceClassificationKey = DimLBSBalanceClassification.BalanceClassificationKey
    attributes:
      balance_classification: { column: BalanceClassification }
      source_system: { column: SourceSystemName }
      trading_system: { column: TradingSystemName }

  date:
    table: SputnikCube.DimDate
    join: FactLBS.BusinessDate = DimDate.BusinessDate
    attributes:
      month: { column: MonthYear }
      quarter: { column: QuarterName }
      year: { column: Year }
```

---

## 2. Derived `Business` view (PB / FIF / EQ as peers)

```sql
CREATE OR ALTER VIEW SputnikCube.vwCpNodeBusiness AS
SELECT
    cp.CpNodeKey,
    cp.ProdLevel2BG,
    cp.ProdLevel3SB,
    cp.ProdLevel4SD,
    Business =
        CASE
            WHEN cp.ProdLevel4SD = 'Prime'                   THEN 'PB - Prime Brokerage'
            WHEN cp.ProdLevel3SB = 'Equities'                THEN 'EQ - Equities'
            WHEN cp.ProdLevel3SB = 'Fixed Income Financing'  THEN 'FIF - Fixed Income Financing'
            ELSE COALESCE(cp.ProdLevel3SB, 'Unmapped')
        END
FROM SputnikCube.DimLBSCpNode cp;
```

---

## 3. Core attribution — "why did LBS move vs prior day?"

The reusable pattern: aggregate current and prior, pivot, delta, order by |delta|.
Swap the grouped dimension to answer line-item / business / entity questions.
Sum of the per-row Deltas reconciles exactly to the total move.

```sql
DECLARE @Cur   date = '2024-09-30';
DECLARE @Prior date = '2024-09-27';   -- prior business day (parameterise from DimDate)

/* ---- A) By LINE ITEM (DimLBSAsset.LBSCategory) ---- */
WITH agg AS (
    SELECT a.LBSCategory, f.BusinessDate,
           Amt = SUM(f.GBPIFRSBalanceSheetAmount)
    FROM SputnikCube.FactLBS f
    JOIN SputnikCube.DimLBSAsset a ON a.AssetKey = f.AssetKey
    WHERE f.BusinessDate IN (@Cur, @Prior)
    GROUP BY a.LBSCategory, f.BusinessDate
)
SELECT
    LineItem   = LBSCategory,
    PriorAmt   = ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0),
    CurrentAmt = ISNULL(SUM(CASE WHEN BusinessDate=@Cur   THEN Amt END),0),
    Delta      = ISNULL(SUM(CASE WHEN BusinessDate=@Cur   THEN Amt END),0)
               - ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0)
FROM agg
GROUP BY LBSCategory
ORDER BY ABS(ISNULL(SUM(CASE WHEN BusinessDate=@Cur THEN Amt END),0)
           - ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0)) DESC;


/* ---- B) By BUSINESS (PB / FIF / EQ via derived view) ---- */
WITH agg AS (
    SELECT b.Business, f.BusinessDate,
           Amt = SUM(f.GBPIFRSBalanceSheetAmount)
    FROM SputnikCube.FactLBS f
    JOIN SputnikCube.vwCpNodeBusiness b ON b.CpNodeKey = f.CpNodeKey
    WHERE f.BusinessDate IN (@Cur, @Prior)
    GROUP BY b.Business, f.BusinessDate
)
SELECT
    Business,
    PriorAmt   = ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0),
    CurrentAmt = ISNULL(SUM(CASE WHEN BusinessDate=@Cur   THEN Amt END),0),
    Delta      = ISNULL(SUM(CASE WHEN BusinessDate=@Cur   THEN Amt END),0)
               - ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0)
FROM agg
GROUP BY Business
ORDER BY ABS(ISNULL(SUM(CASE WHEN BusinessDate=@Cur THEN Amt END),0)
           - ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0)) DESC;


/* ---- C) By LEGAL ENTITY (DimLBSEntity.LegalEntityName) ---- */
WITH agg AS (
    SELECT e.LegalEntityName, f.BusinessDate,
           Amt = SUM(f.GBPIFRSBalanceSheetAmount)
    FROM SputnikCube.FactLBS f
    JOIN SputnikCube.DimLBSEntity e ON e.EntityKey = f.EntityKey
    WHERE f.BusinessDate IN (@Cur, @Prior)
    GROUP BY e.LegalEntityName, f.BusinessDate
)
SELECT
    LegalEntity = LegalEntityName,
    PriorAmt    = ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0),
    CurrentAmt  = ISNULL(SUM(CASE WHEN BusinessDate=@Cur   THEN Amt END),0),
    Delta       = ISNULL(SUM(CASE WHEN BusinessDate=@Cur   THEN Amt END),0)
                - ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0)
FROM agg
GROUP BY LegalEntityName
ORDER BY ABS(ISNULL(SUM(CASE WHEN BusinessDate=@Cur THEN Amt END),0)
           - ISNULL(SUM(CASE WHEN BusinessDate=@Prior THEN Amt END),0)) DESC;


/* ---- Reconciliation: total move (per-row Deltas above must sum to this) ---- */
SELECT
    PriorTotal   = SUM(CASE WHEN BusinessDate=@Prior THEN GBPIFRSBalanceSheetAmount END),
    CurrentTotal = SUM(CASE WHEN BusinessDate=@Cur   THEN GBPIFRSBalanceSheetAmount END),
    TotalDelta   = SUM(CASE WHEN BusinessDate=@Cur   THEN GBPIFRSBalanceSheetAmount END)
                 - SUM(CASE WHEN BusinessDate=@Prior THEN GBPIFRSBalanceSheetAmount END)
FROM SputnikCube.FactLBS
WHERE BusinessDate IN (@Cur, @Prior);
```

To drill (e.g. into the top business), add a `WHERE` on that business and re-group by the next level (`ProdLevel4SD`, then counterparty, then security) — this is exactly the loop the agent will automate.

---

## 4. Notes & assumptions to confirm

- **Sign convention** on `GBPIFRSBalanceSheetAmount` — are liabilities stored negative, or all positive with `AssetLiability` carrying the sign? The sample slice was all zeros, so this is unconfirmed and changes whether you sum or net by `AssetLiability`.
- **Netting additivity** — these queries sum the *reported, netting-allocated* amount on the fact, so roll-ups are additive. The netting subtlety only bites when we later attribute *within* derivative/SFT exposure below netting-set grain.
- **Prior business day** — hardcoded here; will be derived from `DimDate` (skip weekends/holidays) in the real engine.
- **Unmatched (-1)** keys are included; add `WHERE key <> -1` to exclude.
