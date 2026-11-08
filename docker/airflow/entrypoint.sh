#!/usr/bin/env bash
# Один контейнер для локального запуска: миграция метаданных, пользователь, планировщик и веб-интерфейс.
set -euo pipefail

airflow db migrate
airflow users create --role Admin --username "${AIRFLOW_ADMIN_USER}" --password "${AIRFLOW_ADMIN_PASSWORD}" \
    --firstname Admin --lastname Freight --email admin@example.com || true

airflow scheduler &
exec airflow webserver --port 8080
