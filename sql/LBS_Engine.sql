/* ============================================================================
   LBS Analytical Engine  (SQL Server / SputnikCube)
   Deterministic layer the agent calls. Build order matters (deps top-down).
   Measure: GBPIFRSBalanceSheetAmount is already correctly signed -> SUM as-is.
   ============================================================================ */


/* =========================== 1. CALENDAR ================================== */
GO
CREATE OR ALTER VIEW SputnikCube.vwLoadedDates AS
WITH d AS ( SELECT DISTINCT BusinessDate FROM SputnikCube.FactLBS )
SELECT
    d.BusinessDate,
    dd.[Year], dd.[Month], dd.[Quarter],
    IsMonthEnd   = CASE WHEN d.BusinessDate =
                        MAX(d.BusinessDate) OVER (PARTITION BY dd.[Year], dd.[Month])
                        THEN 1 ELSE 0 END,
    IsQuarterEnd = CASE WHEN d.BusinessDate =
                        MAX(d.BusinessDate) OVER (PARTITION BY dd.[Year], dd.[Quarter])
                        THEN 1 ELSE 0 END
FROM d
JOIN SputnikCube.DimDate dd ON dd.BusinessDate = d.BusinessDate;
GO
CREATE OR ALTER FUNCTION SputnikCube.fnPriorLoadedDate(@d date) RETURNS date AS
BEGIN
    RETURN (SELECT MAX(BusinessDate) FROM SputnikCube.FactLBS WHERE BusinessDate < @d);
END
GO
CREATE OR ALTER FUNCTION SputnikCube.fnPriorMonthEnd(@d date) RETURNS date AS
BEGIN
    RETURN (SELECT MAX(BusinessDate) FROM SputnikCube.vwLoadedDates
            WHERE IsMonthEnd = 1 AND BusinessDate < @d);
END
GO
CREATE OR ALTER FUNCTION SputnikCube.fnPriorQuarterEnd(@d date) RETURNS date AS
BEGIN
    RETURN (SELECT MAX(BusinessDate) FROM SputnikCube.vwLoadedDates
            WHERE IsQuarterEnd = 1 AND BusinessDate < @d);
END
GO


/* =========================== 2. CORE VIEWS ================================ */
GO
CREATE OR ALTER VIEW SputnikCube.vwCpNodeBusiness AS
SELECT
    cp.CpNodeKey, cp.ProdLevel2BG, cp.ProdLevel3SB, cp.ProdLevel4SD,
    Business =
        CASE WHEN cp.ProdLevel4SD = 'Prime'                  THEN 'PB - Prime Brokerage'
             WHEN cp.ProdLevel3SB = 'Equities'               THEN 'EQ - Equities'
             WHEN cp.ProdLevel3SB = 'Fixed Income Financing' THEN 'FIF - Fixed Income Financing'
             ELSE COALESCE(cp.ProdLevel3SB, 'Unmapped') END
FROM SputnikCube.DimLBSCpNode cp;
GO
CREATE OR ALTER VIEW SputnikCube.vwFactLBS AS   -- signed measure, ready to SUM
SELECT
    f.BusinessDate, f.CpNodeKey, f.EntityKey, f.SDSKey, f.SecurityKey,
    f.NettingKey, f.ProductKey,
    a.LBSCategory, a.LBSSubCategory, a.AssetLiability,
    bc.BalanceClassification,
    LBS = f.GBPIFRSBalanceSheetAmount
FROM SputnikCube.FactLBS f
JOIN SputnikCube.DimLBSAsset a ON a.AssetKey = f.AssetKey
JOIN SputnikCube.DimLBSBalanceClassification bc
     ON bc.BalanceClassificationKey = f.BalanceClassificationKey;
GO
CREATE OR ALTER VIEW SputnikCube.vwFactLBS_Enriched AS
SELECT
    v.BusinessDate,
    v.LBSCategory          AS LineItem,
    v.LBSSubCategory,
    v.AssetLiability,
    v.BalanceClassification,
    b.Business,
    b.ProdLevel3SB         AS SubBusiness,
    b.ProdLevel4SD         AS SubDivision,
    e.LegalEntityName      AS LegalEntity,
    e.BankLevyStatus,
    sds.Counterpartyname   AS Counterparty,
    sds.ClientHouseIndicator,
    sec.ISIN,
    sec.IssuerName,
    sec.SecurityCurrency   AS Currency,
    sec.CountryOfRisk,
    net.NettingSetId,
    v.LBS
FROM SputnikCube.vwFactLBS v
LEFT JOIN SputnikCube.vwCpNodeBusiness b ON b.CpNodeKey   = v.CpNodeKey
LEFT JOIN SputnikCube.DimLBSEntity     e ON e.EntityKey   = v.EntityKey
LEFT JOIN SputnikCube.DimLBSSDS      sds ON sds.SDSKey    = v.SDSKey
LEFT JOIN SputnikCube.DimLBSSecurity sec ON sec.SecurityKey = v.SecurityKey
LEFT JOIN SputnikCube.DimLBSNetting  net ON net.NettingKey  = v.NettingKey;
GO


/* =========================== 3. TOP MOVERS (workhorse tool) =============== */
GO
CREATE OR ALTER PROCEDURE SputnikCube.usp_TopMovers
    @Dimension  sysname,                 -- whitelisted column in vwFactLBS_Enriched
    @CurDate    date,
    @PriorDate  date = NULL,              -- default = prior loaded date
    @TopN       int  = 15,
    @FilterCol  sysname = NULL,           -- optional single equality filter
    @FilterVal  nvarchar(255) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    IF @PriorDate IS NULL SET @PriorDate = SputnikCube.fnPriorLoadedDate(@CurDate);

    DECLARE @allowed TABLE (c sysname);
    INSERT @allowed VALUES ('LineItem'),('LBSSubCategory'),('BalanceClassification'),
        ('Business'),('SubBusiness'),('SubDivision'),('LegalEntity'),('BankLevyStatus'),
        ('Counterparty'),('ClientHouseIndicator'),('ISIN'),('IssuerName'),
        ('Currency'),('CountryOfRisk'),('NettingSetId');
    IF NOT EXISTS (SELECT 1 FROM @allowed WHERE c = @Dimension)
        BEGIN RAISERROR('Dimension not allowed: %s',16,1,@Dimension); RETURN; END
    IF @FilterCol IS NOT NULL AND NOT EXISTS (SELECT 1 FROM @allowed WHERE c = @FilterCol)
        BEGIN RAISERROR('Filter column not allowed: %s',16,1,@FilterCol); RETURN; END

    DECLARE @sql nvarchar(max) = N'
      WITH agg AS (
        SELECT ' + QUOTENAME(@Dimension) + N' AS Dim, BusinessDate, Amt = SUM(LBS)
        FROM SputnikCube.vwFactLBS_Enriched
        WHERE BusinessDate IN (@CurDate,@PriorDate) AND LineItem <> ''Unmatched'''
        + CASE WHEN @FilterCol IS NOT NULL
               THEN N' AND ' + QUOTENAME(@FilterCol) + N' = @FilterVal' ELSE N'' END + N'
        GROUP BY ' + QUOTENAME(@Dimension) + N', BusinessDate )
      SELECT TOP (@TopN)
        Dim,
        PriorAmt   = ISNULL(SUM(CASE WHEN BusinessDate=@PriorDate THEN Amt END),0),
        CurrentAmt = ISNULL(SUM(CASE WHEN BusinessDate=@CurDate   THEN Amt END),0),
        Delta      = ISNULL(SUM(CASE WHEN BusinessDate=@CurDate   THEN Amt END),0)
                   - ISNULL(SUM(CASE WHEN BusinessDate=@PriorDate THEN Amt END),0)
      FROM agg GROUP BY Dim
      ORDER BY ABS(ISNULL(SUM(CASE WHEN BusinessDate=@CurDate THEN Amt END),0)
                 - ISNULL(SUM(CASE WHEN BusinessDate=@PriorDate THEN Amt END),0)) DESC;';

    EXEC sp_executesql @sql,
        N'@CurDate date,@PriorDate date,@TopN int,@FilterVal nvarchar(255)',
        @CurDate,@PriorDate,@TopN,@FilterVal;
END
GO


/* =========================== 4. DRILL-DOWN PATH ========================== */
GO
CREATE OR ALTER PROCEDURE SputnikCube.usp_DrillPath
    @CurDate   date,
    @PriorDate date = NULL,
    @Direction varchar(4) = 'UP'        -- UP | DOWN | ABS
AS
BEGIN
    SET NOCOUNT ON;
    IF @PriorDate IS NULL SET @PriorDate = SputnikCube.fnPriorLoadedDate(@CurDate);

    DECLARE @path   TABLE (Lvl int, Dimension sysname, Value nvarchar(255), Delta float);
    DECLARE @levels TABLE (Lvl int, Col sysname);
    INSERT @levels VALUES (1,'Business'),(2,'SubDivision'),(3,'Counterparty'),(4,'Currency'),(5,'ISIN');

    DECLARE @order nvarchar(20) =
        CASE @Direction WHEN 'UP' THEN N'Delta DESC'
                        WHEN 'DOWN' THEN N'Delta ASC' ELSE N'ABS(Delta) DESC' END;
    DECLARE @whereExtra nvarchar(max) = N'';
    DECLARE @lvl int = 1, @maxlvl int = (SELECT MAX(Lvl) FROM @levels);
    DECLARE @col sysname, @sql nvarchar(max), @val nvarchar(255), @delta float;

    WHILE @lvl <= @maxlvl
    BEGIN
        SET @col = (SELECT Col FROM @levels WHERE Lvl = @lvl);
        SET @sql = N'
          SELECT TOP (1) @val_out = Dim, @delta_out = Delta FROM (
            SELECT Dim,
              Delta = ISNULL(SUM(CASE WHEN BusinessDate=@CurDate   THEN Amt END),0)
                    - ISNULL(SUM(CASE WHEN BusinessDate=@PriorDate THEN Amt END),0)
            FROM (
              SELECT ' + QUOTENAME(@col) + N' AS Dim, BusinessDate, Amt = SUM(LBS)
              FROM SputnikCube.vwFactLBS_Enriched
              WHERE BusinessDate IN (@CurDate,@PriorDate) AND LineItem <> ''Unmatched'''
              + @whereExtra + N'
              GROUP BY ' + QUOTENAME(@col) + N', BusinessDate
            ) a GROUP BY Dim
          ) b ORDER BY ' + @order + N';';

        SET @val = NULL; SET @delta = NULL;
        EXEC sp_executesql @sql,
            N'@CurDate date,@PriorDate date,@val_out nvarchar(255) OUTPUT,@delta_out float OUTPUT',
            @CurDate,@PriorDate,@val OUTPUT,@delta OUTPUT;

        IF @val IS NULL BREAK;                       -- cannot drill into NULL
        INSERT @path VALUES (@lvl,@col,@val,@delta);
        SET @whereExtra = @whereExtra + N' AND ' + QUOTENAME(@col) + N' = '
                          + QUOTENAME(@val,'''');     -- safe string literal
        SET @lvl += 1;
    END

    SELECT Lvl, Dimension, Value, Delta FROM @path ORDER BY Lvl;
END
GO


/* =========================== 5. TIME SERIES + ANOMALY ==================== */
GO
CREATE OR ALTER PROCEDURE SputnikCube.usp_Series
    @Dimension  sysname,                 -- e.g. 'Business'
    @DimValue   nvarchar(255),           -- e.g. 'EQ - Equities'
    @AsOf       date,
    @Resolution varchar(8) = 'DAILY',    -- DAILY | MONTHEND
    @Points     int = NULL               -- default 10 daily / 12 month-end
AS
BEGIN
    SET NOCOUNT ON;
    IF @Points IS NULL SET @Points = CASE WHEN @Resolution='MONTHEND' THEN 12 ELSE 10 END;

    DECLARE @allowed TABLE (c sysname);
    INSERT @allowed VALUES ('LineItem'),('Business'),('SubBusiness'),('SubDivision'),
        ('LegalEntity'),('Counterparty'),('Currency'),('CountryOfRisk'),
        ('IssuerName'),('ISIN'),('BalanceClassification');
    IF NOT EXISTS (SELECT 1 FROM @allowed WHERE c = @Dimension)
        BEGIN RAISERROR('Dimension not allowed: %s',16,1,@Dimension); RETURN; END

    DECLARE @sql nvarchar(max) = N'
      WITH dts AS (
        SELECT TOP (@Points) BusinessDate FROM SputnikCube.vwLoadedDates
        WHERE BusinessDate <= @AsOf '
        + CASE WHEN @Resolution='MONTHEND' THEN N' AND IsMonthEnd = 1 ' ELSE N'' END + N'
        ORDER BY BusinessDate DESC
      ),
      ser AS (
        SELECT d.BusinessDate, Amt = ISNULL(SUM(v.LBS),0)
        FROM dts d
        LEFT JOIN SputnikCube.vwFactLBS_Enriched v
          ON v.BusinessDate = d.BusinessDate
         AND v.' + QUOTENAME(@Dimension) + N' = @DimValue
         AND v.LineItem <> ''Unmatched''
        GROUP BY d.BusinessDate
      )
      SELECT BusinessDate, Amt,
        WindowMean = AVG(Amt) OVER (),
        WindowStd  = STDEV(Amt) OVER (),
        ZScore     = CASE WHEN STDEV(Amt) OVER () > 0
                          THEN (Amt - AVG(Amt) OVER ()) / STDEV(Amt) OVER () END,
        IsAnomaly  = CASE WHEN STDEV(Amt) OVER () > 0
                          AND ABS((Amt - AVG(Amt) OVER ()) / STDEV(Amt) OVER ()) >= 2
                          THEN 1 ELSE 0 END
      FROM ser ORDER BY BusinessDate;';

    EXEC sp_executesql @sql,
        N'@DimValue nvarchar(255),@AsOf date,@Points int',
        @DimValue,@AsOf,@Points;
END
GO


/* =========================== 6. NIGHTLY CUBE PRECOMPUTE ================== */
GO
IF OBJECT_ID('SputnikCube.CubeDaily') IS NULL
CREATE TABLE SputnikCube.CubeDaily (
    BusinessDate          date          NOT NULL,
    LineItem              varchar(255)  NOT NULL,
    Business              varchar(255)  NOT NULL,
    LegalEntity           varchar(512)  NOT NULL,
    BalanceClassification varchar(255)  NOT NULL,
    LBS                   float         NULL
);
GO
CREATE OR ALTER PROCEDURE SputnikCube.usp_BuildDailyCube @CurDate date
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM SputnikCube.CubeDaily WHERE BusinessDate = @CurDate;
    INSERT SputnikCube.CubeDaily
        (BusinessDate, LineItem, Business, LegalEntity, BalanceClassification, LBS)
    SELECT BusinessDate,
           LineItem,
           COALESCE(Business,'Unmapped'),
           COALESCE(LegalEntity,'Unknown'),
           BalanceClassification,
           SUM(LBS)
    FROM SputnikCube.vwFactLBS_Enriched
    WHERE BusinessDate = @CurDate AND LineItem <> 'Unmatched'
    GROUP BY BusinessDate, LineItem,
             COALESCE(Business,'Unmapped'),
             COALESCE(LegalEntity,'Unknown'),
             BalanceClassification;
END
GO


/* =========================== 7. SMOKE TESTS ============================== */
-- DECLARE @cur date = (SELECT MAX(BusinessDate) FROM SputnikCube.FactLBS);
-- DECLARE @prior date = SputnikCube.fnPriorLoadedDate(@cur);
-- EXEC SputnikCube.usp_TopMovers @Dimension='LineItem', @CurDate=@cur, @PriorDate=@prior;
-- EXEC SputnikCube.usp_TopMovers @Dimension='Business', @CurDate=@cur, @PriorDate=@prior;
-- EXEC SputnikCube.usp_TopMovers @Dimension='Counterparty', @CurDate=@cur, @PriorDate=@prior,
--      @FilterCol='Business', @FilterVal='EQ - Equities', @TopN=10;
-- EXEC SputnikCube.usp_DrillPath @CurDate=@cur, @Direction='UP';
-- EXEC SputnikCube.usp_Series @Dimension='Business', @DimValue='EQ - Equities',
--      @AsOf=@cur, @Resolution='DAILY';
-- EXEC SputnikCube.usp_BuildDailyCube @CurDate=@cur;
