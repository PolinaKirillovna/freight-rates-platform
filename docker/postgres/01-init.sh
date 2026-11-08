#!/usr/bin/env bash
# Первичная инициализация сервера PostgreSQL (выполняется один раз, на пустом томе):
# роли, базы источника и хранилища, служебные базы Airflow и Superset, генерация данных источника.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE ${SOURCE_DB_USER} LOGIN PASSWORD '${SOURCE_DB_PASSWORD}';
CREATE ROLE ${DWH_DB_USER} LOGIN PASSWORD '${DWH_DB_PASSWORD}';
CREATE ROLE ${BI_DB_USER} LOGIN PASSWORD '${BI_DB_PASSWORD}';
CREATE ROLE ${AIRFLOW_DB_USER} LOGIN PASSWORD '${AIRFLOW_DB_PASSWORD}';
CREATE ROLE ${SUPERSET_DB_USER} LOGIN PASSWORD '${SUPERSET_DB_PASSWORD}';
CREATE DATABASE deals_bigdata;
CREATE DATABASE dwh OWNER ${DWH_DB_USER};
CREATE DATABASE airflow OWNER ${AIRFLOW_DB_USER};
CREATE DATABASE superset OWNER ${SUPERSET_DB_USER};
GRANT CONNECT ON DATABASE dwh TO ${BI_DB_USER};
SQL

echo "Generating source data: ${SOURCE_N_PRICES} deals"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname deals_bigdata -q -f /source/schema.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname deals_bigdata -q \
     -v n_prices="${SOURCE_N_PRICES}" -f /source/synthetic.sql > /dev/null

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname deals_bigdata <<SQL
GRANT CONNECT ON DATABASE deals_bigdata TO ${SOURCE_DB_USER};
GRANT USAGE ON SCHEMA public TO ${SOURCE_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${SOURCE_DB_USER};
SQL
echo "Source database is ready"
