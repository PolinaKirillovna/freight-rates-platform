-- Тот же расчёт коридора, что и sql/mart/route_month_base.sql, но средствами ClickHouse.
-- quantileExactInclusive интерполирует так же, как percentile_cont в PostgreSQL.
-- Параметры: {as_of:Date}, {months:Array(Date)}, {version:UInt64}
INSERT INTO {database}.route_month_base
SELECT
    month,
    loading_city_gcid_m                                           AS loading_city_gcid,
    unloading_city_gcid_m                                         AS unloading_city_gcid,
    body_group_code_m                                             AS body_group_code,
    weight_category_m                                             AS weight_category,
    uniqExact(price_id_m)                                         AS trips_count,
    uniqExactIf(organization_id_m, price_type_m IN (3, 9))        AS carriers_count,
    quantileExactInclusive(0.25)(price_rub_m)                     AS p25_trip,
    quantileExactInclusive(0.50)(price_rub_m)                     AS median_trip,
    quantileExactInclusive(0.75)(price_rub_m)                     AS p75_trip,
    avg(price_rub_m)                                              AS avg_trip,
    sum(price_rub_m)                                              AS sum_trip,
    quantileExactInclusive(0.25)(price_rub_per_km_m)              AS p25_km,
    quantileExactInclusive(0.50)(price_rub_per_km_m)              AS median_km,
    quantileExactInclusive(0.75)(price_rub_per_km_m)              AS p75_km,
    if(count() >= 2 AND avg(price_rub_m) != 0, stddevSamp(price_rub_m) / avg(price_rub_m), NULL) AS cv_rate,
    avg(volume_category_m = 'FTL')                                AS volume_ftl_share,
    max(last_price_date_m)                                        AS last_price_date,
    {version:UInt64}                                              AS _version
FROM
(
    -- одна строка на (месяц, сделка, группа кузова); алиасы с суффиксом _m не затеняют исходные столбцы в WHERE
    SELECT price_month AS month, price_id AS price_id_m, body_group_code AS body_group_code_m,
           any(loading_city_gcid) AS loading_city_gcid_m, any(unloading_city_gcid) AS unloading_city_gcid_m,
           any(weight_category) AS weight_category_m, any(volume_category) AS volume_category_m,
           any(organization_id) AS organization_id_m, any(price_type) AS price_type_m,
           any(price_rub) AS price_rub_m, any(price_rub_per_km) AS price_rub_per_km_m,
           max(price_date) AS last_price_date_m
    FROM {database}.fact_market_rows FINAL
    WHERE price_date <= {as_of:Date}
      AND price_month IN {months:Array(Date)}
      AND is_fact AND price_type IN (1, 2, 3, 4, 5, 9)
      AND NOT complex AND NOT outlier_mark
      AND price_original > 0 AND distance > 0
    GROUP BY price_month, price_id, body_group_code
)
GROUP BY month, loading_city_gcid_m, unloading_city_gcid_m, body_group_code_m, weight_category_m
