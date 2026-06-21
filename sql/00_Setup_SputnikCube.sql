/* ============================================================================
   SputnikCube — LOCAL SYNTHETIC DEMO setup
   ----------------------------------------------------------------------------
   Builds the database + [SputnikCube] schema (faithful to docs/Schema_v2.sql),
   seeds dimensions with realistic (but FAKE) values, a DimDate calendar, and a
   synthetic FactLBS spanning ~1 year of business days with a deliberate anomaly
   on the latest date so the root-cause story has a driver to find.

   NOTHING here is real data (fictional bank). Safe to drop/rebuild at will.
   Run AFTER this: sql/LBS_Engine.sql   (builds the views/procs the agent calls)
   ============================================================================ */
SET NOCOUNT ON;
GO
IF DB_ID('SputnikCube') IS NULL
    CREATE DATABASE SputnikCube;
GO
USE SputnikCube;
GO
IF SCHEMA_ID('SputnikCube') IS NULL
    EXEC('CREATE SCHEMA SputnikCube');
GO

/* ---- clean slate (demo is fully rebuildable) -------------------------------*/
IF OBJECT_ID('SputnikCube.FactLBS')                     IS NOT NULL DROP TABLE SputnikCube.FactLBS;
IF OBJECT_ID('SputnikCube.DimLBSAsset')                 IS NOT NULL DROP TABLE SputnikCube.DimLBSAsset;
IF OBJECT_ID('SputnikCube.DimLBSBalanceClassification') IS NOT NULL DROP TABLE SputnikCube.DimLBSBalanceClassification;
IF OBJECT_ID('SputnikCube.DimLBSCpNode')                IS NOT NULL DROP TABLE SputnikCube.DimLBSCpNode;
IF OBJECT_ID('SputnikCube.DimLBSEntity')                IS NOT NULL DROP TABLE SputnikCube.DimLBSEntity;
IF OBJECT_ID('SputnikCube.DimLBSNetting')               IS NOT NULL DROP TABLE SputnikCube.DimLBSNetting;
IF OBJECT_ID('SputnikCube.DimLBSSDS')                   IS NOT NULL DROP TABLE SputnikCube.DimLBSSDS;
IF OBJECT_ID('SputnikCube.DimLBSSecurity')              IS NOT NULL DROP TABLE SputnikCube.DimLBSSecurity;
IF OBJECT_ID('SputnikCube.DimLBSProduct')               IS NOT NULL DROP TABLE SputnikCube.DimLBSProduct;
IF OBJECT_ID('SputnikCube.DimLBSTDB')                   IS NOT NULL DROP TABLE SputnikCube.DimLBSTDB;
IF OBJECT_ID('SputnikCube.DimLBSSapAccount')            IS NOT NULL DROP TABLE SputnikCube.DimLBSSapAccount;
IF OBJECT_ID('SputnikCube.DimDate')                     IS NOT NULL DROP TABLE SputnikCube.DimDate;
GO

/* ============================================================
   1. TABLES  (subset of cols sufficient for the engine)
   ============================================================ */
CREATE TABLE SputnikCube.DimLBSAsset (
    AssetKey int NOT NULL PRIMARY KEY,
    LBSCategory varchar(255) NULL, LBSSubCategory varchar(255) NULL,
    AssetSubClass varchar(255) NULL, AssetLiability varchar(255) NULL );

CREATE TABLE SputnikCube.DimLBSBalanceClassification (
    BalanceClassificationKey int NOT NULL PRIMARY KEY,
    BalanceClassification varchar(255) NULL, BalanceSourceName varchar(255) NULL,
    SourceSystemName varchar(255) NULL, TradingSystemName varchar(255) NULL );

CREATE TABLE SputnikCube.DimLBSCpNode (
    CpNodeKey int NOT NULL PRIMARY KEY,
    BookCode varchar(255) NULL, CPNode varchar(255) NULL, MainTrader varchar(512) NULL,
    MasterBookNodeName varchar(255) NULL,
    ProdLevel2BG varchar(255) NULL, ProdLevel3SB varchar(255) NULL, ProdLevel4SD varchar(255) NULL,
    ProdLevel5PG varchar(255) NULL, SourceBookCode varchar(255) NULL, ReportingCluster varchar(255) NULL );

CREATE TABLE SputnikCube.DimLBSEntity (
    EntityKey int NOT NULL PRIMARY KEY,
    BankLevyGroup varchar(255) NULL, BankLevyStatus varchar(255) NULL,
    LegalEntity varchar(255) NULL, LegalEntityName varchar(512) NULL,
    ReportingCategory varchar(255) NULL, SourceEntityCode varchar(255) NULL );

CREATE TABLE SputnikCube.DimLBSNetting (
    NettingKey int NOT NULL PRIMARY KEY,
    NettingAgreementStrength int NULL, NettingSetId varchar(255) NULL );

CREATE TABLE SputnikCube.DimLBSSDS (
    SDSKey int NOT NULL PRIMARY KEY,
    Counterpartyname varchar(255) NULL, ClientHouseIndicator varchar(255) NULL,
    CounterpartyCCPFlag bit NULL, IsCounterpartyInternal bit NULL,
    CounterpartyIndustryCode int NULL );

CREATE TABLE SputnikCube.DimLBSSecurity (
    SecurityKey int NOT NULL PRIMARY KEY,
    CountryOfRisk varchar(255) NULL, Cusip varchar(255) NULL,
    HqlaClassification varchar(255) NULL, ISIN varchar(255) NULL,
    IssuerName varchar(255) NULL, SecurityCurrency varchar(255) NULL,
    SecurityName varchar(512) NULL, InstrumentMaturityDate date NULL );

CREATE TABLE SputnikCube.DimLBSProduct (
    ProductKey int NOT NULL PRIMARY KEY,
    CEMProduct varchar(255) NULL, OTCETDFlag varchar(255) NULL, TTSProductType varchar(255) NULL );

CREATE TABLE SputnikCube.DimLBSTDB (
    TDBKey int NOT NULL PRIMARY KEY,
    TDBProductType varchar(255) NULL, TDBSubType varchar(255) NULL );

CREATE TABLE SputnikCube.DimLBSSapAccount (
    SapAccountKey int NOT NULL PRIMARY KEY,
    SapAccount varchar(255) NULL, SapAcctDescription varchar(255) NULL );

CREATE TABLE SputnikCube.DimDate (
    DateKey int NOT NULL PRIMARY KEY,
    BusinessDate date NOT NULL,
    [Day] int NULL, [Month] int NULL, MonthName varchar(20) NULL,
    [Quarter] int NULL, [Year] int NULL, WeekDayName varchar(20) NULL );

CREATE TABLE SputnikCube.FactLBS (
    BusinessDate date NOT NULL,
    AssetKey int NOT NULL, BalanceClassificationKey int NOT NULL,
    CpNodeKey int NOT NULL, EntityKey int NOT NULL, MeasureTypeKey int NOT NULL,
    NettingKey int NOT NULL, ProductKey int NOT NULL, RunNameKey int NOT NULL,
    SapAccountKey int NOT NULL, SDSKey int NOT NULL, SecurityKey int NOT NULL,
    TDBKey int NOT NULL, TradeKey int NOT NULL,
    GBPIFRSBalanceSheetAmount float NULL,
    [import history id] int NOT NULL );
GO

/* ============================================================
   2. DIMENSION SEED DATA  (-1 = Unmatched everywhere)
   ============================================================ */
INSERT SputnikCube.DimLBSAsset (AssetKey,LBSCategory,LBSSubCategory,AssetLiability) VALUES
 (-1,'Unmatched','Unmatched','Unmatched'),
 ( 1,'Derivatives','Replacement Cost','Assets'),
 ( 2,'Derivatives','Potential Future Exposure','Assets'),
 ( 3,'Derivatives','Cash Collateral','Liabilities'),
 ( 4,'Derivatives','WCDS','Assets'),
 ( 5,'SFTs','IFRS Reverse Repos','Assets'),
 ( 6,'SFTs','E-C Addon','Assets'),
 ( 7,'SFTs','Cash Prime Brokerage','Assets'),
 ( 8,'SFTs','Short Credits','Liabilities'),
 ( 9,'Margin Loans','Cash Prime Brokerage','Assets'),
 (10,'Trading Portfolio','Trading Portfolio Assets','Assets'),
 (11,'Trading Portfolio','Trading Portfolio Liabilities','Liabilities'),
 (12,'Trading Portfolio','Financial Investments','Assets'),
 (13,'Loans','Financing Solutions','Assets'),
 (14,'Loans','Secure Hub Feed','Assets'),
 (15,'Other Adjustments','COU ISDA Collateral','Liabilities'),
 (16,'Other Adjustments','Nostro','Assets'),
 (17,'Other Adjustments','Settlement Balances','Assets');

INSERT SputnikCube.DimLBSBalanceClassification (BalanceClassificationKey,BalanceClassification,SourceSystemName) VALUES
 ( 1,'Gross','Aggregator'),
 ( 2,'Netting - ISDA','Aggregator'),
 ( 3,'Netting - GMRA','Aggregator'),
 ( 4,'Netting - Settlement','Aggregator');

INSERT SputnikCube.DimLBSCpNode (CpNodeKey,ProdLevel2BG,ProdLevel3SB,ProdLevel4SD,SourceBookCode) VALUES
 (-1,'Unmatched','Unmatched','Unmatched','Unmatched'),
 ( 1,'Markets','Equities','Prime','EQ-PRM-01'),
 ( 2,'Markets','Equities','Equity Derivatives','EQ-EQD-01'),
 ( 3,'Markets','Equities','Cash Equities','EQ-CSH-01'),
 ( 4,'Markets','Fixed Income Financing','Fixed Income Financing','FIF-REPO-01'),
 ( 5,'Markets','Macro','Rates','MAC-RTS-01'),
 ( 6,'Markets','Credit','FI Credit','CRD-FIC-01'),
 ( 7,'Markets','Securitized Products','Securitized Products Trading','SP-TRD-01'),
 ( 8,'Banking','Corporate Banking','Lending','BNK-LND-01');

INSERT SputnikCube.DimLBSEntity (EntityKey,BankLevyStatus,LegalEntityName,ReportingCategory) VALUES
 (-1,'Unmatched','Unmatched','Unmatched'),
 ( 1,'UK','Northwind Bank PLC','C'),
 ( 2,'UK','Northwind Capital Securities Limited','RC'),
 ( 3,'Non-UK','Northwind Bank Ireland PLC','CEu'),
 ( 4,'Non-UK','Northwind Capital Inc','SC'),
 ( 5,'UK','Northwind Bank UK PLC','C');

INSERT SputnikCube.DimLBSNetting (NettingKey,NettingAgreementStrength,NettingSetId) VALUES
 (-1,-1,'Unmatched'),
 ( 1, 5,'NS-ISDA-0001'),
 ( 2, 5,'NS-ISDA-0002'),
 ( 3, 1,'NS-GMRA-0001'),
 ( 4, 5,'NS-GMRA-0002'),
 ( 5, 1,'NS-CSA-0001');

INSERT SputnikCube.DimLBSSDS (SDSKey,Counterpartyname,ClientHouseIndicator,CounterpartyCCPFlag) VALUES
 (-1,'Unmatched','Unmatched',0),
 ( 1,'LCH Ltd','House',1),
 ( 2,'ICE Clear Europe','House',1),
 ( 3,'Citadel LLC','Client',0),
 ( 4,'Millennium Management','Client',0),
 ( 5,'BlackRock','Client',0),
 ( 6,'Bank of America','House',0),
 ( 7,'Bluecrest Capital','Client',0),
 ( 8,'Goldman Sachs','House',0);

INSERT SputnikCube.DimLBSSecurity (SecurityKey,ISIN,IssuerName,SecurityCurrency,CountryOfRisk,HqlaClassification) VALUES
 (-1,'Unmatched','Unmatched','Unmatched','Unmatched','Unmatched'),
 ( 1,'GB00BH4HKS39','UK Gilt 1.5% 2047','GBP','United Kingdom','Level 1'),
 ( 2,'US912828YK06','US Treasury 1.75% 2029','USD','United States','Level 1'),
 ( 3,'DE0001102333','German Bund 0% 2030','EUR','Germany','Level 1'),
 ( 4,'US0378331005','Apple Inc','USD','United States','Non-HQLA'),
 ( 5,'GB0002374006','Diageo PLC','GBP','United Kingdom','Non-HQLA'),
 ( 6,'US88160R1014','Tesla Inc','USD','United States','Non-HQLA'),
 ( 7,'FR0000131104','BNP Paribas SA','EUR','France','Non-HQLA'),
 ( 8,'GB00B03MLX29','Shell PLC','GBP','United Kingdom','Non-HQLA');

INSERT SputnikCube.DimLBSProduct (ProductKey,CEMProduct,OTCETDFlag) VALUES
 (1,'EQ','OTC'),(2,'IR','OTC'),(3,'FX','OTC'),(4,'COM','ETD');
INSERT SputnikCube.DimLBSTDB (TDBKey,TDBProductType,TDBSubType) VALUES (1,'Financing','Repo');
INSERT SputnikCube.DimLBSSapAccount (SapAccountKey,SapAccount,SapAcctDescription) VALUES (1,'100000','Trading Assets');
GO

/* ============================================================
   3. DimDate  (every calendar day in the demo window)
   ============================================================ */
;WITH n AS (
    SELECT TOP (430) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
    FROM sys.all_objects
), cal AS (
    SELECT d = DATEADD(DAY, i, '2025-06-01') FROM n
)
INSERT SputnikCube.DimDate (DateKey,BusinessDate,[Day],[Month],MonthName,[Quarter],[Year],WeekDayName)
SELECT CONVERT(int, FORMAT(d,'yyyyMMdd')), d,
       DAY(d), MONTH(d), DATENAME(MONTH,d), DATEPART(QUARTER,d), YEAR(d), DATENAME(WEEKDAY,d)
FROM cal
WHERE d <= '2026-07-15';
GO

/* ============================================================
   4. SYNTHETIC FactLBS
      slice = a realistic (desk x line-item x counterparty x security x netting)
      combo with a baked, already-signed base amount. Daily value = base * gentle
      trend * +-6% deterministic noise, plus a concentrated anomaly on the latest
      business date (EQ / Prime / Citadel / Apple) so root-cause has a driver.
   ============================================================ */
-- business dates = weekdays in [2025-06-02, 2026-06-19], DayIdx ascending from 0
;WITH n AS (
    SELECT TOP (430) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
    FROM sys.all_objects
), days AS (
    SELECT d = DATEADD(DAY, i, '2025-06-02') FROM n
), bdays AS (
    SELECT d FROM days
    WHERE d <= '2026-06-19' AND DATEPART(WEEKDAY, d) NOT IN (1,7)   -- drop Sun/Sat (US@@DATEFIRST default)
)
SELECT d, DayIdx = ROW_NUMBER() OVER (ORDER BY d) - 1,
       IsLatest = CASE WHEN d = (SELECT MAX(d) FROM bdays) THEN 1 ELSE 0 END
INTO #dates
FROM bdays;

-- 33 curated slices: (SliceId, Asset, CpNode, Entity, SDS, Security, Netting, BalClass, BaseAmt)
CREATE TABLE #slice (
    SliceId int, AssetKey int, CpNodeKey int, EntityKey int, SDSKey int,
    SecurityKey int, NettingKey int, BalClassKey int, BaseAmt float );
INSERT #slice VALUES
 ( 1, 5,1,2,3,4,1,1, 1200000000),   -- EQ/Prime  RevRepo  Citadel  Apple   (anomaly target)
 ( 2, 9,1,2,4,6,5,1,  850000000),   -- EQ/Prime  MarginLoan Millennium Tesla
 ( 3, 7,1,2,5,5,3,1,  600000000),   -- EQ/Prime  CashPB   BlackRock Diageo
 ( 4, 1,2,1,3,4,1,1,  450000000),   -- EQ/EqDeriv RC      Citadel  Apple
 ( 5, 2,2,1,6,6,1,1,  300000000),   -- EQ/EqDeriv PFE     BofA     Tesla
 ( 6, 3,2,1,3,4,2,2, -180000000),   -- EQ/EqDeriv CashCollat netting (ISDA)
 ( 7,10,3,2,5,5,5,1,  500000000),   -- EQ/CashEq TradingPort Assets
 ( 8,11,3,2,5,8,5,1, -220000000),   -- EQ/CashEq TradingPort Liab
 ( 9, 6,1,2,7,4,1,1,  250000000),   -- EQ/Prime  E-C Addon Bluecrest Apple
 (10, 5,1,2,3,4,3,3, -400000000),   -- EQ/Prime  RevRepo netting offset (GMRA)
 (11, 5,4,1,8,1,3,1, 1500000000),   -- FIF       RevRepo  GS       Gilt
 (12, 5,4,1,6,2,4,1, 1100000000),   -- FIF       RevRepo  BofA     UST
 (13, 8,4,3,8,3,4,1, -350000000),   -- FIF       ShortCredits liab
 (14, 5,4,1,8,1,3,3, -700000000),   -- FIF       RevRepo netting offset (GMRA)
 (15,13,4,1,6,2,5,1,  400000000),   -- FIF       Loans FinancingSolutions
 (16, 1,5,1,1,1,1,1,  900000000),   -- Macro/Rates RC LCH Gilt
 (17, 2,5,1,1,1,1,1,  650000000),   -- Macro/Rates PFE LCH Gilt
 (18, 3,5,1,1,1,2,2, -500000000),   -- Macro/Rates CashCollat netting (ISDA)
 (19,12,5,1,2,2,5,1,  300000000),   -- Macro/Rates Financial Investments
 (20, 1,6,4,2,7,1,1,  400000000),   -- Credit RC ICE BNP
 (21, 4,6,4,6,7,2,1,  150000000),   -- Credit WCDS BofA BNP
 (22, 8,6,4,8,3,4,1, -200000000),   -- Credit ShortCredits liab
 (23,13,7,4,5,6,5,1,  350000000),   -- SecProd Loans BlackRock Tesla
 (24,14,7,4,7,6,5,1,  200000000),   -- SecProd Loans SecureHubFeed
 (25,13,8,5,6,5,5,1,  250000000),   -- Banking Loans BofA Diageo
 (26,16,8,5,6,5,5,1,   80000000),   -- Banking Nostro
 (27,15,5,1,1,1,2,2, -300000000),   -- Other COU ISDA Collateral netting
 (28,17,3,2,5,4,4,4, -120000000),   -- Other Settlement Balances netting
 (29,16,1,2,3,4,5,1,   90000000),   -- Other Nostro EQ/Prime
 (30, 9,1,2,3,4,5,1,  700000000),   -- EQ/Prime MarginLoan Citadel Apple (anomaly target)
 (31, 7,1,2,4,6,3,1,  500000000),   -- EQ/Prime CashPB Millennium Tesla
 (32,-1,-1,-1,-1,-1,-1,1, 50000000),-- Unmatched (must be filtered out)
 (33,-1, 1, 2, 3, 4, 1,1, 30000000);-- Unmatched

INSERT SputnikCube.FactLBS
 (BusinessDate,AssetKey,BalanceClassificationKey,CpNodeKey,EntityKey,MeasureTypeKey,
  NettingKey,ProductKey,RunNameKey,SapAccountKey,SDSKey,SecurityKey,TDBKey,TradeKey,
  GBPIFRSBalanceSheetAmount,[import history id])
SELECT
  t.d, s.AssetKey, s.BalClassKey, s.CpNodeKey, s.EntityKey, -1 /*MeasureType*/,
  s.NettingKey, 1 /*Product*/, 1 /*RunName*/, 1 /*Sap*/, s.SDSKey, s.SecurityKey,
  1 /*TDB*/, s.SliceId /*degenerate trade id*/,
  /* base * trend * (+-6% deterministic noise) + concentrated latest-date anomaly */
  ROUND(
     s.BaseAmt
     * (1 + 0.0005 * t.DayIdx)
     * (1 + ((ABS(CHECKSUM(s.SliceId * 101, t.d)) % 121) - 60) / 1000.0)
   + CASE WHEN t.IsLatest = 1 AND s.SliceId = 1  THEN  600000000
          WHEN t.IsLatest = 1 AND s.SliceId = 30 THEN  500000000
          ELSE 0 END, 0),
  1
FROM #dates t
CROSS JOIN #slice s;

DROP TABLE #dates; DROP TABLE #slice;
GO

/* ---- quick sanity readout -------------------------------------------------*/
SELECT FactRows = COUNT(*),
       DistinctDates = COUNT(DISTINCT BusinessDate),
       FirstDate = MIN(BusinessDate), LastDate = MAX(BusinessDate)
FROM SputnikCube.FactLBS;
GO
