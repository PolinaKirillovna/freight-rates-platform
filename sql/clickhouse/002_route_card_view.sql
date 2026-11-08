-- Карточка маршрута в ClickHouse: те же правила MoM/YoY и уровня надёжности, что и в PostgreSQL.
CREATE OR REPLACE VIEW {database}.route_card AS
SELECT
    b.month                                      AS month,
    b.loading_city_gcid                          AS loading_city_gcid,
    lc.city_name                                 AS loading_city,
    lc.region_name                               AS loading_region,
    b.unloading_city_gcid                        AS unloading_city_gcid,
    uc.city_name                                 AS unloading_city,
    uc.region_name                               AS unloading_region,
    concat(lc.city_name, ' → ', uc.city_name)    AS route,
    b.body_group_code                            AS body_group_code,
    g.body_group                                 AS body_group,
    b.weight_category                            AS weight_category,
    b.p25_trip AS p25_trip, b.median_trip AS median_trip, b.p75_trip AS p75_trip, b.avg_trip AS avg_trip,
    b.p25_km AS p25_km, b.median_km AS median_km, b.p75_km AS p75_km,
    b.trips_count                                AS trips_count,
    b.carriers_count                             AS carriers_count,
    b.cv_rate                                    AS cv_rate,
    if(pm.median_trip IS NULL OR pm.median_trip = 0, NULL, (b.median_trip / pm.median_trip - 1) * 100) AS mom_pct,
    if(py.median_trip IS NULL OR py.median_trip = 0, NULL, (b.median_trip / py.median_trip - 1) * 100) AS yoy_pct,
    dateDiff('day', b.last_price_date, s.as_of)  AS freshness_days,
    multiIf(b.trips_count >= 30 AND b.carriers_count >= 5 AND dateDiff('day', b.last_price_date, s.as_of) <= 30, 'HIGH',
            b.trips_count >= 10, 'MEDIUM',
            'LOW_DATA')                          AS reliability_level,
    s.as_of                                      AS as_of
FROM (SELECT * FROM {database}.route_month_base FINAL) AS b
CROSS JOIN (SELECT max(as_of) AS as_of FROM {database}.snapshot) AS s
LEFT JOIN (SELECT * FROM {database}.route_month_base FINAL) AS pm
       ON  pm.loading_city_gcid = b.loading_city_gcid AND pm.unloading_city_gcid = b.unloading_city_gcid
       AND pm.body_group_code = b.body_group_code AND pm.weight_category = b.weight_category
       AND pm.month = addMonths(b.month, -1)
LEFT JOIN (SELECT * FROM {database}.route_month_base FINAL) AS py
       ON  py.loading_city_gcid = b.loading_city_gcid AND py.unloading_city_gcid = b.unloading_city_gcid
       AND py.body_group_code = b.body_group_code AND py.weight_category = b.weight_category
       AND py.month = addMonths(b.month, -12)
LEFT JOIN {database}.dim_city AS lc ON lc.gc_id = b.loading_city_gcid
LEFT JOIN {database}.dim_city AS uc ON uc.gc_id = b.unloading_city_gcid
LEFT JOIN {database}.dim_body_group AS g ON g.body_group_code = b.body_group_code
SETTINGS join_use_nulls = 1;
