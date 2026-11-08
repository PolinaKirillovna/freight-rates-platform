-- Представления для BI и права роли только для чтения (bi_reader создаётся при инициализации сервера).

CREATE OR REPLACE VIEW mart.v_model_quality AS
SELECT r.run_id, r.trained_at, r.as_of, r.champion_model, m.model_name, m.split,
       m.n_rows, m.r2, m.mape_pct, m.rmse_rub, m.mae_rub,
       (m.model_name = r.champion_model) AS is_champion
FROM ml.model_runs r
JOIN ml.model_metrics m USING (run_id)
WHERE r.run_id = (SELECT run_id FROM ml.model_runs ORDER BY trained_at DESC LIMIT 1);

CREATE OR REPLACE VIEW mart.v_feature_importance AS
SELECT f.model_name, f.feature, f.importance
FROM ml.feature_importance f
WHERE f.run_id = (SELECT run_id FROM ml.model_runs ORDER BY trained_at DESC LIMIT 1);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_reader') THEN
        GRANT USAGE ON SCHEMA mart, core, dq, ml TO bi_reader;
        GRANT SELECT ON ALL TABLES IN SCHEMA mart, core, dq, ml TO bi_reader;
        ALTER DEFAULT PRIVILEGES IN SCHEMA mart, core, dq, ml GRANT SELECT ON TABLES TO bi_reader;
    END IF;
END
$$;
