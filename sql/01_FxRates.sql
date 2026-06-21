/* ============================================================================
   FX rates (Phase 3) — synthetic, swap-ready stub.
   ----------------------------------------------------------------------------
   One row per (BusinessDate, Currency): RateToGBP = GBP value of 1 unit of the
   currency. GBP = 1.0; USD/EUR drift daily so FX isolation has signal. Replace
   this table's load with a real feed later — the attribution code is unchanged.

   Run AFTER 00_Setup_SputnikCube.sql. Rebuildable.
   ============================================================================ */
USE SputnikCube;
GO
IF OBJECT_ID('SputnikCube.DimFxRate') IS NOT NULL DROP TABLE SputnikCube.DimFxRate;
GO
CREATE TABLE SputnikCube.DimFxRate (
    BusinessDate date         NOT NULL,
    Currency     varchar(8)   NOT NULL,
    RateToGBP    float        NOT NULL,
    CONSTRAINT PK_DimFxRate PRIMARY KEY (BusinessDate, Currency)
);
GO
;WITH dts AS ( SELECT DISTINCT BusinessDate FROM SputnikCube.FactLBS ),
ccy AS ( SELECT Currency FROM (VALUES ('GBP'),('USD'),('EUR')) c(Currency) )
INSERT SputnikCube.DimFxRate (BusinessDate, Currency, RateToGBP)
SELECT d.BusinessDate, c.Currency,
       CASE c.Currency
         WHEN 'GBP' THEN 1.0
         WHEN 'USD' THEN ROUND(0.79 * (1 + 0.03 * SIN(0.30 * DATEDIFF(DAY,'2025-06-01',d.BusinessDate))), 5)
         WHEN 'EUR' THEN ROUND(0.86 * (1 + 0.025 * COS(0.20 * DATEDIFF(DAY,'2025-06-01',d.BusinessDate))), 5)
         ELSE 1.0 END
FROM dts d CROSS JOIN ccy c;
GO
SELECT Currencies = COUNT(DISTINCT Currency), Rows = COUNT(*),
       Latest = MAX(BusinessDate) FROM SputnikCube.DimFxRate;
GO
