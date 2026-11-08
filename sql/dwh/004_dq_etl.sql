-- Карантин: каждая отбракованная сделка с кодом причины (одна строка на причину).
CREATE TABLE IF NOT EXISTS dq.quarantine_prices (
    price_id       bigint      NOT NULL,
    reason_code    varchar(40) NOT NULL,
    loading_date   date        NOT NULL,
    currency_code  varchar(3),
    price          double precision,
    distance       double precision,
    details        text,
    _batch_id      uuid        NOT NULL,
    _quarantined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (price_id, reason_code)
);
CREATE INDEX IF NOT EXISTS quarantine_loading_date_idx ON dq.quarantine_prices (loading_date);

-- Журнал запусков шагов: что прочитано, отбраковано и записано.
CREATE TABLE IF NOT EXISTS etl.run_log (
    run_id      bigserial PRIMARY KEY,
    batch_id    uuid        NOT NULL,
    step        varchar(40) NOT NULL,
    window_start date,
    window_end   date,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz,
    status      varchar(16) NOT NULL,
    metrics     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error       text
);
CREATE INDEX IF NOT EXISTS run_log_step_idx ON etl.run_log (step, started_at DESC);

-- Месяцы, которые нужно пересчитать потребителям core (витрина PostgreSQL, ClickHouse).
-- Шаг очистки записывает месяцы, потребитель удаляет свои строки после успешного пересчёта.
CREATE TABLE IF NOT EXISTS etl.dirty_months (
    consumer varchar(32) NOT NULL,
    month    date        NOT NULL,
    marked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, month)
);

-- Параметры снимка, общие для SQL-представлений.
CREATE TABLE IF NOT EXISTS etl.settings (
    key   varchar(64) PRIMARY KEY,
    value text        NOT NULL
);
