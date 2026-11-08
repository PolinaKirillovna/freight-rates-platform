-- mart: слой для BI.
-- route_month_base  — тяжёлые агрегаты по ячейке, пересчитываются только затронутые месяцы;
-- mart_route_card   — материализованное представление поверх базы: динамика MoM/YoY,
--                     уровень надёжности, человекочитаемые измерения. Обновляется целиком и быстро.

CREATE TABLE IF NOT EXISTS mart.route_month_base (
    month               date        NOT NULL,
    loading_city_gcid   integer     NOT NULL,
    unloading_city_gcid integer     NOT NULL,
    body_group_code     smallint    NOT NULL,
    weight_category     varchar(4)  NOT NULL,
    trips_count         integer     NOT NULL,
    carriers_count      integer     NOT NULL,
    p25_trip            double precision NOT NULL,
    median_trip         double precision NOT NULL,
    p75_trip            double precision NOT NULL,
    avg_trip            double precision NOT NULL,
    sum_trip            double precision NOT NULL,
    p25_km              double precision NOT NULL,
    median_km           double precision NOT NULL,
    p75_km              double precision NOT NULL,
    cv_rate             double precision,
    volume_ftl_share    double precision NOT NULL,   -- доля рейсов с объёмной категорией FTL (атрибут, не зерно)
    last_price_date     date        NOT NULL,
    _refreshed_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (month, loading_city_gcid, unloading_city_gcid, body_group_code, weight_category)
);
CREATE INDEX IF NOT EXISTS route_month_base_route_idx
    ON mart.route_month_base (loading_city_gcid, unloading_city_gcid, body_group_code, weight_category, month);

CREATE MATERIALIZED VIEW IF NOT EXISTS mart.mart_route_card AS
WITH snapshot AS (
    SELECT coalesce((SELECT value::date FROM etl.settings WHERE key = 'as_of'), current_date) AS as_of
)
SELECT
    b.month,
    b.loading_city_gcid,
    lc.city_name                                   AS loading_city,
    lc.region_name                                 AS loading_region,
    b.unloading_city_gcid,
    uc.city_name                                   AS unloading_city,
    uc.region_name                                 AS unloading_region,
    lc.city_name || ' → ' || uc.city_name          AS route,
    b.body_group_code,
    g.body_group,
    b.weight_category,
    b.p25_trip, b.median_trip, b.p75_trip, b.avg_trip,
    b.p25_km,   b.median_km,   b.p75_km,
    b.trips_count,
    b.carriers_count,
    b.cv_rate,
    CASE WHEN pm.median_trip IS NULL OR pm.median_trip = 0 THEN NULL
         ELSE (b.median_trip / pm.median_trip - 1) * 100 END AS mom_pct,
    CASE WHEN py.median_trip IS NULL OR py.median_trip = 0 THEN NULL
         ELSE (b.median_trip / py.median_trip - 1) * 100 END AS yoy_pct,
    (s.as_of - b.last_price_date)                  AS freshness_days,
    CASE
        WHEN b.trips_count >= 30 AND b.carriers_count >= 5 AND (s.as_of - b.last_price_date) <= 30 THEN 'HIGH'
        WHEN b.trips_count >= 10 THEN 'MEDIUM'
        ELSE 'LOW_DATA'
    END                                            AS reliability_level,
    b.volume_ftl_share,
    b.last_price_date,
    s.as_of
FROM mart.route_month_base b
CROSS JOIN snapshot s
JOIN core.dim_city lc        ON lc.gc_id = b.loading_city_gcid
JOIN core.dim_city uc        ON uc.gc_id = b.unloading_city_gcid
JOIN core.dim_body_group g   ON g.body_group_code = b.body_group_code
LEFT JOIN mart.route_month_base pm
       ON pm.loading_city_gcid = b.loading_city_gcid AND pm.unloading_city_gcid = b.unloading_city_gcid
      AND pm.body_group_code = b.body_group_code AND pm.weight_category = b.weight_category
      AND pm.month = (b.month - interval '1 month')::date
LEFT JOIN mart.route_month_base py
       ON py.loading_city_gcid = b.loading_city_gcid AND py.unloading_city_gcid = b.unloading_city_gcid
      AND py.body_group_code = b.body_group_code AND py.weight_category = b.weight_category
      AND py.month = (b.month - interval '12 months')::date
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS mart_route_card_pk
    ON mart.mart_route_card (month, loading_city_gcid, unloading_city_gcid, body_group_code, weight_category);
CREATE INDEX IF NOT EXISTS mart_route_card_route_idx
    ON mart.mart_route_card (route, body_group, weight_category, month);

-- Сводка по сети маршрутов за последний месяц снимка (сценарий тендера и бюджетирования).
CREATE OR REPLACE VIEW mart.v_route_network AS
SELECT *
FROM mart.mart_route_card
WHERE month = date_trunc('month', as_of)::date;

-- Качество данных: сколько строк отбраковано по каждой причине и месяцу погрузки.
CREATE OR REPLACE VIEW mart.v_dq_quarantine_summary AS
SELECT date_trunc('month', loading_date)::date AS loading_month,
       reason_code,
       count(*)                                AS rows_quarantined
FROM dq.quarantine_prices
GROUP BY 1, 2;
