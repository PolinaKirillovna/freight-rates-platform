-- Smoke-проверки хранилища: каждая строка — название проверки и число нарушений (ожидается 0).
SELECT 'core: deal duplicated inside one body group' AS check_name, count(*) AS violations
FROM (SELECT price_id, body_group_code FROM core.fact_price_body_group GROUP BY 1, 2 HAVING count(*) > 1) t
UNION ALL
SELECT 'core: quarantined deal also present in core', count(*)
FROM dq.quarantine_prices q JOIN core.fact_price f ON f.price_id = q.price_id
UNION ALL
SELECT 'core: non-positive or missing price_rub', count(*)
FROM core.fact_price WHERE price_rub IS NULL OR price_rub <= 0
UNION ALL
SELECT 'core: RUB price at or below 1000', count(*)
FROM core.fact_price WHERE currency_code = '643' AND price_original <= 1000
UNION ALL
SELECT 'core: foreign currency without exact rate', count(*)
FROM core.fact_price f LEFT JOIN core.exchange_rate r
       ON r.currency_code = f.currency_code AND r.rate_date = f.nearest_rate_date
WHERE f.currency_code <> '643' AND r.rate IS NULL
UNION ALL
SELECT 'dim_city: gc_id not unique', count(*) - count(DISTINCT gc_id) FROM core.dim_city
UNION ALL
SELECT 'raw vs core+quarantine: lost deals', count(*)
FROM raw.prices p
WHERE NOT EXISTS (SELECT 1 FROM core.fact_price f WHERE f.price_id = p.id)
  AND NOT EXISTS (SELECT 1 FROM dq.quarantine_prices q WHERE q.price_id = p.id)
UNION ALL
SELECT 'mart: future dates in market history', count(*)
FROM mart.route_month_base WHERE last_price_date > (SELECT value::date FROM etl.settings WHERE key = 'as_of')
UNION ALL
SELECT 'mart: corridor out of order', count(*)
FROM mart.route_month_base WHERE NOT (p25_trip <= median_trip AND median_trip <= p75_trip AND p25_km <= median_km AND median_km <= p75_km)
UNION ALL
SELECT 'mart: cv_rate present for a single trip', count(*)
FROM mart.route_month_base WHERE trips_count < 2 AND cv_rate IS NOT NULL
UNION ALL
SELECT 'mart: trips differ from independent recount', abs(
    (SELECT sum(trips_count) FROM mart.route_month_base)
  - (SELECT count(*) FROM (
        SELECT DISTINCT d.price_month, f.price_id, bg.body_group_code
        FROM core.fact_price f
        JOIN core.fact_price_date d USING (price_id)
        JOIN core.fact_price_body_group bg USING (price_id)
        WHERE f.is_fact AND NOT f.complex AND NOT f.outlier_mark
          AND d.price_date <= (SELECT value::date FROM etl.settings WHERE key = 'as_of')) t))
UNION ALL
SELECT 'mart: MoM without previous month cell', count(*)
FROM mart.mart_route_card c
WHERE c.mom_pct IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM mart.route_month_base b
    WHERE b.loading_city_gcid = c.loading_city_gcid AND b.unloading_city_gcid = c.unloading_city_gcid
      AND b.body_group_code = c.body_group_code AND b.weight_category = c.weight_category
      AND b.month = (c.month - interval '1 month')::date)
UNION ALL
SELECT 'ml: future prediction with actual value', count(*)
FROM mart.ml_price_predictions WHERE split = 'future' AND (actual_price_rub IS NOT NULL OR ape_pct IS NOT NULL)
UNION ALL
SELECT 'ml: test prediction outside test window', count(*)
FROM mart.ml_price_predictions WHERE split = 'test' AND NOT loading_date BETWEEN DATE '2026-10-05' AND DATE '2026-10-26';
