-- Карточка маршрута и коридор рыночных ставок (эталонный запрос).
-- Зерно: месяц (из price_dates) × город отправления × город назначения × группа кузова × весовая категория.
-- Параметры: %(as_of)s — дата снимка; %(months)s — массив месяцев для пересчёта (NULL = все месяцы).
WITH price_month AS (
    -- Одна строка на (сделка, месяц): долгосрочная заявка попадает в каждый свой месяц ровно один раз.
    SELECT d.price_id, d.price_month AS month, max(d.price_date) AS last_price_date
    FROM core.fact_price_date d
    WHERE d.price_date <= %(as_of)s::date                       -- плановые месяцы не входят в историю рынка
      AND (%(months)s::date[] IS NULL OR d.price_month = ANY (%(months)s::date[]))
    GROUP BY d.price_id, d.price_month
),
market AS (
    SELECT pm.month, pm.last_price_date,
           f.price_id, f.loading_city_gcid, f.unloading_city_gcid,
           bg.body_group_code, f.weight_category, f.volume_category,
           f.organization_id, f.price_type, f.price_rub, f.price_rub_per_km
    FROM price_month pm
    JOIN core.fact_price f             ON f.price_id = pm.price_id
    JOIN core.fact_price_body_group bg ON bg.price_id = f.price_id   -- уникально по (сделка, группа)
    WHERE f.is_fact
      AND f.price_type IN (1, 2, 3, 4, 5, 9)
      AND NOT f.complex
      AND NOT f.outlier_mark
      AND f.price_original > 0
      AND f.distance > 0
      AND f.price_rub IS NOT NULL
)
SELECT month, loading_city_gcid, unloading_city_gcid, body_group_code, weight_category,
       count(DISTINCT price_id)                                                AS trips_count,
       count(DISTINCT organization_id) FILTER (WHERE price_type IN (3, 9))     AS carriers_count,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub)                 AS p25_trip,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub)                 AS median_trip,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub)                 AS p75_trip,
       avg(price_rub)                                                          AS avg_trip,
       sum(price_rub)                                                          AS sum_trip,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub_per_km)          AS p25_km,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub_per_km)          AS median_km,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub_per_km)          AS p75_km,
       CASE WHEN count(*) >= 2 AND avg(price_rub) <> 0
            THEN stddev_samp(price_rub) / avg(price_rub) END                   AS cv_rate,
       avg((volume_category = 'FTL')::int)::double precision                   AS volume_ftl_share,
       max(last_price_date)                                                    AS last_price_date
FROM market
GROUP BY month, loading_city_gcid, unloading_city_gcid, body_group_code, weight_category
