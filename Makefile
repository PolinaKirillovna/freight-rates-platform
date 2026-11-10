# Все команды выполняются на своей машине, в корне репозитория.
.PHONY: up down reset logs airflow test test-integration backfill rerun-window psql smoke analyze

up:                ## поднять всё: PostgreSQL, ClickHouse, конвейер, Superset
	docker compose up --build

airflow:           ## то же + Airflow
	docker compose --profile airflow up --build

down:
	docker compose --profile airflow down

reset:             ## удалить тома и начать с нуля
	docker compose --profile airflow down -v

logs:
	docker compose logs -f pipeline

backfill:          ## полный пересчёт: ETL -> витрины -> ClickHouse -> ML
	docker compose run --rm pipeline python -m etl.cli all

rerun-window:      ## повтор окна, пример: make rerun-window START=2026-10-01 END=2026-10-31
	docker compose run --rm pipeline python -m etl.cli etl --start $(START) --end $(END)

analyze:           ## отчёт о достижимой точности модели (нужен полный прогон конвейера)
	docker compose run --rm pipeline python -m ml.analysis

test:              ## модульные тесты (без базы)
	docker compose run --rm --no-deps pipeline pytest -q

test-integration:  ## интеграционные тесты на поднятом хранилище
	docker compose run --rm -e RUN_INTEGRATION=1 pipeline pytest -q

smoke:             ## smoke-проверки хранилища (все нули = порядок)
	docker compose exec -T postgres psql -U postgres -d dwh -f - < sql/checks/smoke.sql

psql:
	docker compose exec postgres psql -U postgres -d dwh
