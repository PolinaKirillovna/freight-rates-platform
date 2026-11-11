# Проверка проекта

Команды выполняются из корня репозитория на Mac с работающим Docker Desktop. Для полного стека с Airflow выделите Docker не менее 8 ГБ памяти.

## Запуск с чистыми томами

> Команда `down -v` удаляет сохранённые БД и артефакты Docker-томов. Используйте её только для осознанной проверки «с нуля».

```bash
docker compose --profile airflow down -v
docker compose --profile airflow up --build
```

Во втором окне терминала:

```bash
docker compose --profile airflow ps -a
docker compose logs pipeline --tail 100
```

Ожидается `pipeline: Exited (0)`, работающие PostgreSQL/ClickHouse и доступность интерфейсов Superset/Airflow. При отдельном запуске без Airflow достаточно `docker compose up --build`.

## Автотесты

```bash
make test
make test-integration
make smoke
```

Фактический прогон (11.11.2026, пересобранный образ `pipeline`):

- `make test` — **55 passed, 5 skipped** (5 интеграционных тестов пропускаются без `RUN_INTEGRATION=1`);
- `make test-integration` — **60 passed** (режим включает модульные тесты, поэтому числа нельзя складывать);
- `make smoke` — **14 инвариантов, 0 нарушений**.

До добавления теста фиксации champion (`test_champion_is_fixed_to_catboost_not_chosen_on_test`) было **54 passed / 5 skipped** и **59 passed** соответственно. Для фактического состава тестов запустите `pytest --collect-only -q` в том же окружении.

## Ограничения CI

GitHub Actions выполняет линтер и тесты, затем интеграционный прогон на **60 000 строках**, с `CLICKHOUSE_ENABLED=false`. Успешный CI не заменяет локальную проверку полного Docker-стека с ClickHouse, Airflow и Superset на 300 000 строках.

## Проверка идемпотентности

Сохраните результат запроса до и после повтора одного окна — все пять чисел должны совпадать:

```bash
Q="SELECT (SELECT count(*) FROM core.fact_price),(SELECT count(*) FROM dq.quarantine_prices),(SELECT count(*) FROM mart.route_month_base),(SELECT round(sum(sum_trip)) FROM mart.route_month_base),(SELECT count(*) FROM mart.ml_price_predictions)"
docker compose exec -T postgres psql -U postgres -d dwh -tAc "$Q"
make rerun-window START=2026-10-01 END=2026-10-31
docker compose exec -T postgres psql -U postgres -d dwh -tAc "$Q"
```

Для более строгой проверки дополнительно сравните P25/P50/P75 и суммы по выбранным ячейкам до/после обновления.

## Проверка моделей

```bash
docker compose run --rm --no-deps pipeline cat /app/artifacts/ml/metrics.md
make analyze
```

Проверьте границы `train/test/future`, отсутствие утечек из цены и тот факт, что для `future` `actual_price_rub` остаётся `NULL`.

## Проверка интерфейсов

- PostgreSQL: данные в `raw`, `core`, `dq`, `mart`; карантин и числа соответствуют README.
- ClickHouse: сопоставьте несколько ячеек `freight.route_card` с `mart.mart_route_card` PostgreSQL.
- Superset: откройте `http://localhost:8088/superset/dashboard/freight-rates/` и убедитесь, что смена маршрута и кузова меняет графики.
- Airflow: `http://localhost:8080`, DAG `freight_rates_pipeline`; ручной запуск за октябрь 2026 и проверка логов задач.

## SQL для ручной верификации

```sql
-- Коридор ставок по маршруту
SELECT month, route, body_group, weight_category,
       round(p25_trip) AS p25, round(median_trip) AS p50, round(p75_trip) AS p75,
       round(median_km::numeric, 1) AS rub_per_km, trips_count, carriers_count,
       round(mom_pct::numeric, 1) AS mom, round(yoy_pct::numeric, 1) AS yoy, reliability_level
FROM mart.mart_route_card
WHERE route = 'Москва → Санкт-Петербург' AND body_group = 'Тент' AND weight_category = 'FTL'
ORDER BY month DESC
LIMIT 12;

-- Самые надёжные направления последнего месяца
SELECT route, body_group, weight_category, round(median_trip) AS p50, trips_count, carriers_count
FROM mart.v_route_network
WHERE reliability_level = 'HIGH'
ORDER BY trips_count DESC;

-- Почему отбракованы строки
SELECT reason_code, sum(rows_quarantined) FROM mart.v_dq_quarantine_summary GROUP BY 1;

-- Рейсы, закрытые заметно выше прогноза
SELECT price_id, loading_date, body_group, round(actual_price_rub) AS fact,
       round(predicted_price_rub) AS forecast, round(ape_pct::numeric, 1) AS error_pct
FROM mart.ml_price_predictions
WHERE is_champion AND split = 'test' AND actual_price_rub > predicted_price_rub * 1.3
ORDER BY ape_pct DESC
LIMIT 20;

-- Журнал запусков
SELECT step, status, window_start, window_end, finished_at - started_at AS took, metrics
FROM etl.run_log ORDER BY run_id DESC LIMIT 10;
```

Пример карточки маршрута Москва → Санкт-Петербург, тент, FTL:

| Месяц | P25 | P50 | P75 | ₽/км | Рейсов | Перевозчиков | MoM, % | YoY, % | Надёжность |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-10 | 78 828 | 88 023 | 96 230 | 111,3 | 47 | 18 | 0,1 | 18,3 | HIGH |
| 2026-09 | 76 737 | 87 946 | 101 327 | 111,2 | 32 | 13 | 4,3 | 20,8 | HIGH |
| 2026-08 | 75 923 | 84 345 | 95 057 | 106,6 | 33 | 11 | 5,1 | 10,4 | MEDIUM |

Через ClickHouse:

```sql
SELECT month, route, median_trip, trips_count, reliability_level
FROM freight.route_card
WHERE route = 'Москва → Санкт-Петербург' AND body_group = 'Тент' AND weight_category = 'FTL'
ORDER BY month DESC LIMIT 3;
```

## Что тестируется автоматически

- **Модульные (55)** — без базы: граничные значения категорий, каждая проверка качества, пересчёт валют, схлопывание групп кузова, разбор региона, границы разбиения по времени, циклические признаки, запрет полей-утечек, фиксация CatBoost как champion, метрики.
- **Интеграционные (5)** — на поднятом хранилище: повтор окна, равенство полного и частичного пересчёта, smoke-проверки ([`sql/checks/smoke.sql`](../sql/checks/smoke.sql), 14 инвариантов — от потерянных сделок до независимого пересчёта числа рейсов), отсутствие размножения сделок справочником городов, совпадение ClickHouse с PostgreSQL (на встроенном ClickHouse — chdb).
- **CI** (`.github/workflows/ci.yml`) — линтер, модульные тесты и полный прогон конвейера с интеграционными тестами на PostgreSQL 17.

## Запуск без Docker

Нужны Python 3.11+ и PostgreSQL с базами `deals_bigdata` и `dwh`. Подключения задаются переменными из `.env.example`.

```bash
pip install -r requirements-dev.txt
export POSTGRES_HOST=localhost CLICKHOUSE_ENABLED=false
python -m etl.cli all                                       # полный прогон
python -m etl.cli etl --start 2026-10-01 --end 2026-10-31   # повтор окна
python -m etl.cli marts --full-refresh                      # пересчёт всех месяцев витрины
pytest -q                                                   # модульные тесты
RUN_INTEGRATION=1 pytest -q                                 # плюс интеграционные
```
