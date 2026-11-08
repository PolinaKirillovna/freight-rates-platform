"""Ежедневный конвейер: источник -> raw -> core -> витрины PostgreSQL -> ClickHouse -> обучение и скоринг.

Окно дат погрузки по умолчанию скользящее: [начало интервала - LOOKBACK_DAYS; конец интервала + FORWARD_DAYS].
Отставание назад подхватывает поздние правки источника (например, ночную разметку выбросов),
вперёд — плановые заявки для скоринга. Каждый шаг идемпотентен, поэтому повтор задачи безопасен.

Ручная дозагрузка за любой период:
    airflow dags trigger freight_rates_pipeline --conf '{"start_date": "2023-01-01", "end_date": "2027-12-31"}'
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta

from airflow.decorators import dag, task
from airflow.models.param import Param
from airflow.operators.python import get_current_context

LOOKBACK_DAYS = int(os.getenv("PIPELINE_LOOKBACK_DAYS", "14"))
FORWARD_DAYS = int(os.getenv("PIPELINE_FORWARD_DAYS", "150"))


def _context_window() -> tuple[uuid.UUID, date, date]:
    ctx = get_current_context()
    params = ctx["params"]
    batch_id = uuid.uuid5(uuid.NAMESPACE_URL, f"freight_rates_pipeline/{ctx['run_id']}")
    if params.get("start_date") and params.get("end_date"):
        start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    else:
        start = (ctx["data_interval_start"] - timedelta(days=LOOKBACK_DAYS)).date()
        end = (ctx["data_interval_end"] + timedelta(days=FORWARD_DAYS)).date()
    return batch_id, start, end


def _settings():
    """Внутри Airflow обработчики логов задачи сохраняются: логгеры проекта передают события им."""
    import logging

    from common.config import load_settings

    settings = load_settings()
    logging.getLogger().setLevel(settings.log_level)
    return settings


@dag(
    dag_id="freight_rates_pipeline",
    description="ETL, контроль качества, витрины рыночных ставок, ClickHouse и прогноз тарифа",
    schedule="@daily",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=2)},
    params={
        "start_date": Param(None, type=["null", "string"], format="date", description="начало окна (loading_date)"),
        "end_date": Param(None, type=["null", "string"], format="date", description="конец окна (loading_date)"),
        "full_refresh": Param(False, type="boolean", description="пересчитать все месяцы витрин"),
    },
    tags=["freight", "etl", "ml"],
)
def freight_rates_pipeline():
    @task
    def migrate() -> None:
        from etl import steps

        batch_id, _, _ = _context_window()
        steps.step_migrate(_settings(), batch_id)

    @task
    def extract() -> dict:
        from etl import steps

        batch_id, start, end = _context_window()
        return steps.step_extract(_settings(), batch_id, start, end)

    @task
    def transform() -> dict:
        from etl import steps

        batch_id, start, end = _context_window()
        return steps.step_transform(_settings(), batch_id, start, end)

    @task
    def build_marts() -> dict:
        from etl import steps

        batch_id, _, _ = _context_window()
        full = bool(get_current_context()["params"].get("full_refresh"))
        return steps.step_marts(_settings(), batch_id, full_refresh=full)

    @task
    def sync_clickhouse() -> dict:
        from etl import steps

        batch_id, _, _ = _context_window()
        full = bool(get_current_context()["params"].get("full_refresh"))
        result = steps.step_clickhouse(_settings(), batch_id, full_refresh=full)
        if result.get("parity_ok") is False:
            raise ValueError(f"ClickHouse mart differs from PostgreSQL: {result}")
        return result

    @task
    def train_and_score() -> dict:
        from etl import steps

        batch_id, _, _ = _context_window()
        return steps.step_ml(_settings(), batch_id)

    m, e, t, mart = migrate(), extract(), transform(), build_marts()
    m >> e >> t >> mart >> [sync_clickhouse(), train_and_score()]


freight_rates_pipeline()
