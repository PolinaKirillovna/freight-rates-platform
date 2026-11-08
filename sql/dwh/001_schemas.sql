-- Слои хранилища: raw (сырая копия источника) -> core (очищенные факты и измерения)
-- -> mart (витрины для BI) + dq (карантин) + etl (служебное состояние) + ml (модель и прогнозы).
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS dq;
CREATE SCHEMA IF NOT EXISTS etl;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ml;
