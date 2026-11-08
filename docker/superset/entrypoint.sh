#!/usr/bin/env bash
# Superset metadata init + provisioning of databases, datasets, charts and the dashboard, then the web server.
set -euo pipefail

superset db upgrade
superset fab create-admin \
    --username "${SUPERSET_ADMIN_USER}" --password "${SUPERSET_ADMIN_PASSWORD}" \
    --firstname Admin --lastname Freight --email admin@example.com || true
superset init
python /app/bootstrap/bootstrap.py || echo "WARNING: dashboard provisioning failed, Superset will start without it"

exec gunicorn --bind 0.0.0.0:8088 --workers 2 --worker-class gthread --threads 4 --timeout 120 \
    "superset.app:create_app()"
