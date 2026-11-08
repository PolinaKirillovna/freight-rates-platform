CREATE TABLE IF NOT EXISTS ml.model_runs (
    run_id         uuid PRIMARY KEY,
    trained_at     timestamptz NOT NULL,
    as_of          date        NOT NULL,
    train_start    date        NOT NULL,
    train_end_excl date        NOT NULL,
    test_start     date        NOT NULL,
    test_end_incl  date        NOT NULL,
    champion_model varchar(64) NOT NULL,
    params         jsonb       NOT NULL
);

CREATE TABLE IF NOT EXISTS ml.model_metrics (
    run_id      uuid        NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    model_name  varchar(64) NOT NULL,
    split       varchar(32) NOT NULL,     -- train / test / test_unfiltered
    n_rows      integer     NOT NULL,
    r2          double precision,
    mape_pct    double precision,
    rmse_rub    double precision,
    mae_rub     double precision,
    PRIMARY KEY (run_id, model_name, split)
);

CREATE TABLE IF NOT EXISTS ml.feature_importance (
    run_id     uuid        NOT NULL REFERENCES ml.model_runs (run_id) ON DELETE CASCADE,
    model_name varchar(64) NOT NULL,
    feature    varchar(64) NOT NULL,
    importance double precision NOT NULL,
    PRIMARY KEY (run_id, model_name, feature)
);

-- Пакетный скоринг: факт против прогноза. Для будущих заявок фактические поля пустые.
CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    price_id            bigint      NOT NULL,
    model_name          varchar(64) NOT NULL,
    split               varchar(16) NOT NULL CHECK (split IN ('test', 'future')),
    loading_date        date        NOT NULL,
    loading_city_gcid   integer     NOT NULL,
    unloading_city_gcid integer     NOT NULL,
    body_group          varchar(32) NOT NULL,
    weight_category     varchar(4)  NOT NULL,
    distance            double precision NOT NULL,
    outlier_mark        boolean     NOT NULL,
    actual_price_rub    double precision,
    predicted_price_rub double precision NOT NULL,
    abs_error_rub       double precision,
    ape_pct             double precision,
    is_champion         boolean     NOT NULL,
    run_id              uuid        NOT NULL,
    scored_at           timestamptz NOT NULL,
    PRIMARY KEY (price_id, model_name),
    CHECK (split = 'test' OR (actual_price_rub IS NULL AND abs_error_rub IS NULL AND ape_pct IS NULL))
);
CREATE INDEX IF NOT EXISTS ml_predictions_date_idx ON mart.ml_price_predictions (loading_date, model_name);
