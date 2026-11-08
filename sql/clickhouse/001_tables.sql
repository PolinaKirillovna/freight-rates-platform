-- Аналитический слой в ClickHouse: копия рыночных строк core и витрина маршрута на MergeTree.
-- Все таблицы секционированы по месяцу: пересчёт месяца = DROP PARTITION + INSERT (идемпотентно),
-- ReplacingMergeTree(_version) дополнительно схлопывает дубли, если вставка была повторена.

CREATE DATABASE IF NOT EXISTS {database};

CREATE TABLE IF NOT EXISTS {database}.fact_market_rows
(
    price_month         Date,
    price_date          Date,
    price_id            UInt64,
    body_group_code     Int16,
    loading_date        Date,
    loading_city_gcid   UInt32,
    unloading_city_gcid UInt32,
    weight_category     LowCardinality(String),
    volume_category     LowCardinality(String),
    organization_id     Nullable(UInt32),
    price_type          UInt8,
    is_fact             Bool,
    complex             Bool,
    outlier_mark        Bool,
    price_original      Float64,
    distance            Float64,
    price_rub           Float64,
    price_rub_per_km    Float64,
    _version            UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(price_month)
ORDER BY (price_month, loading_city_gcid, unloading_city_gcid, body_group_code, weight_category, price_id, price_date);

CREATE TABLE IF NOT EXISTS {database}.route_month_base
(
    month               Date,
    loading_city_gcid   UInt32,
    unloading_city_gcid UInt32,
    body_group_code     Int16,
    weight_category     LowCardinality(String),
    trips_count         UInt32,
    carriers_count      UInt32,
    p25_trip            Float64,
    median_trip         Float64,
    p75_trip            Float64,
    avg_trip            Float64,
    sum_trip            Float64,
    p25_km              Float64,
    median_km           Float64,
    p75_km              Float64,
    cv_rate             Nullable(Float64),
    volume_ftl_share    Float64,
    last_price_date     Date,
    _version            UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(month)
ORDER BY (month, loading_city_gcid, unloading_city_gcid, body_group_code, weight_category);

CREATE TABLE IF NOT EXISTS {database}.dim_city
(
    gc_id       UInt32,
    city_name   String,
    region_name Nullable(String),
    latitude    Nullable(Float64),
    longitude   Nullable(Float64)
)
ENGINE = MergeTree ORDER BY gc_id;

CREATE TABLE IF NOT EXISTS {database}.dim_body_group
(
    body_group_code Int16,
    body_group      String
)
ENGINE = MergeTree ORDER BY body_group_code;

CREATE TABLE IF NOT EXISTS {database}.snapshot
(
    as_of Date
)
ENGINE = MergeTree ORDER BY tuple();
