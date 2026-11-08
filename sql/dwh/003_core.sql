-- core: очищенный слой. Сюда попадают только строки, прошедшие проверки качества;
-- бизнес-фильтры витрин (факт, прямой маршрут, выбросы) хранятся флагами и применяются выше.

-- Справочник городов без дублей gc_id: на каждый gc_id одна строка с минимальным cities.id.
CREATE TABLE IF NOT EXISTS core.dim_city (
    gc_id           integer PRIMARY KEY,
    source_city_id  bigint       NOT NULL,
    city_name       varchar(255) NOT NULL,
    region_name     varchar(255),
    full_name_local varchar(255) NOT NULL,
    latitude        double precision,
    longitude       double precision,
    duplicates      integer      NOT NULL          -- сколько строк источника схлопнуто в эту
);

-- Бизнес-группы кузова. Код группы = std_id для четырёх стандартных групп,
-- 0 = «Прочие», -1 = «Не указан».
CREATE TABLE IF NOT EXISTS core.dim_body_group (
    body_group_code smallint PRIMARY KEY,
    body_group      varchar(32) NOT NULL UNIQUE
);
INSERT INTO core.dim_body_group (body_group_code, body_group) VALUES
    (56, 'Тент'), (4, 'Рефрижератор'), (2, 'Изотерм'), (15, 'Борт'), (0, 'Прочие'), (-1, 'Не указан')
ON CONFLICT (body_group_code) DO UPDATE SET body_group = EXCLUDED.body_group;

CREATE TABLE IF NOT EXISTS core.dim_body_type (
    body_type_id    integer PRIMARY KEY,
    name            varchar(255) NOT NULL,
    std_id          integer,
    body_group_code smallint NOT NULL REFERENCES core.dim_body_group (body_group_code)
);

CREATE TABLE IF NOT EXISTS core.exchange_rate (
    currency_code varchar(3)       NOT NULL,
    rate_date     date             NOT NULL,
    rate          double precision NOT NULL,
    PRIMARY KEY (currency_code, rate_date)
);

CREATE TABLE IF NOT EXISTS core.fact_price (
    price_id                 bigint PRIMARY KEY,
    created_at               timestamp(0),
    loading_date             date             NOT NULL,
    end_loading_date         date,
    loading_city_gcid        integer          NOT NULL REFERENCES core.dim_city (gc_id),
    unloading_city_gcid      integer          NOT NULL REFERENCES core.dim_city (gc_id),
    organization_id          integer,
    price_type               smallint         NOT NULL,
    is_fact                  boolean          NOT NULL,
    complex                  boolean          NOT NULL,
    outlier_mark             boolean          NOT NULL,
    same_city_route          boolean          NOT NULL,
    distance                 double precision NOT NULL CHECK (distance > 0),
    total_weight             double precision NOT NULL CHECK (total_weight >= 0),
    total_volume             double precision NOT NULL CHECK (total_volume >= 0),
    body_type                integer,
    primary_body_group_code  smallint         NOT NULL REFERENCES core.dim_body_group (body_group_code),
    weight_category          varchar(4)       NOT NULL,
    volume_category          varchar(4)       NOT NULL,
    weight_category_filled   boolean          NOT NULL,   -- категория была пустой и рассчитана пайплайном
    volume_category_filled   boolean          NOT NULL,
    currency_code            varchar(3)       NOT NULL,
    price_original           double precision NOT NULL CHECK (price_original > 0),
    nearest_rate_date        date,
    fx_rate                  double precision NOT NULL,
    price_rub                double precision NOT NULL CHECK (price_rub > 0),
    price_rub_per_km         double precision NOT NULL,
    _batch_id                uuid             NOT NULL,
    _loaded_at               timestamptz      NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fact_price_loading_date_idx ON core.fact_price (loading_date);

-- Связь сделки с группами кузова: не более одной строки на пару (сделка, группа).
CREATE TABLE IF NOT EXISTS core.fact_price_body_group (
    price_id        bigint   NOT NULL REFERENCES core.fact_price (price_id) ON DELETE CASCADE,
    body_group_code smallint NOT NULL REFERENCES core.dim_body_group (body_group_code),
    PRIMARY KEY (price_id, body_group_code)
);

-- Помесячная развёртка долгосрочных заявок (зерно месячной статистики).
CREATE TABLE IF NOT EXISTS core.fact_price_date (
    price_id    bigint NOT NULL REFERENCES core.fact_price (price_id) ON DELETE CASCADE,
    price_date  date   NOT NULL,
    price_month date   NOT NULL GENERATED ALWAYS AS (date_trunc('month', price_date::timestamp)::date) STORED,
    PRIMARY KEY (price_id, price_date)
);
CREATE INDEX IF NOT EXISTS fact_price_date_month_idx ON core.fact_price_date (price_month, price_id);
