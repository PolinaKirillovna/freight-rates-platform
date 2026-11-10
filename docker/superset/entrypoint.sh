#!/usr/bin/env bash
# Инициализация метаданных Superset, создание подключений, наборов данных, графиков и дашборда,
# затем веб-сервер. Миграция метаданных дожидается базы с повторами: кратковременная
# недоступность PostgreSQL на старте не должна останавливать контейнер насовсем.
set -euo pipefail

for attempt in $(seq 1 30); do
    if superset db upgrade; then
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "ERROR: metadata database is still unavailable after ${attempt} attempts" >&2
        exit 1
    fi
    echo "Metadata database is not ready (attempt ${attempt}/30), retrying in 10 s"
    sleep 10
done

superset fab create-admin \
    --username "${SUPERSET_ADMIN_USER}" --password "${SUPERSET_ADMIN_PASSWORD}" \
    --firstname Admin --lastname Freight --email admin@example.com || true
superset init
python /app/bootstrap/bootstrap.py || echo "WARNING: dashboard provisioning failed, Superset will start without it"

exec gunicorn --bind 0.0.0.0:8088 --workers 2 --worker-class gthread --threads 4 --timeout 120 \
    "superset.app:create_app()"
