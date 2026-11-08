"""Pipeline steps with a shared signature, used by the CLI and by the Airflow DAG."""
from __future__ import annotations

import uuid
from datetime import date

from common.config import Settings
from common.db import apply_migrations, run_journal


def step_migrate(settings: Settings, batch_id: uuid.UUID) -> dict:
    with run_journal(settings.dwh, "migrate", batch_id) if _journal_ready(settings) else _noop() as m:
        files = apply_migrations(settings.dwh)
        m["files"] = files
    return {"files": files}


def step_extract(settings: Settings, batch_id: uuid.UUID, start: date, end: date) -> dict:
    from etl.extract import extract_window
    with run_journal(settings.dwh, "extract", batch_id, (start, end)) as m:
        m.update(extract_window(settings, start, end, batch_id))
    return m


def step_transform(settings: Settings, batch_id: uuid.UUID, start: date, end: date) -> dict:
    from etl.transform import transform_window
    with run_journal(settings.dwh, "transform", batch_id, (start, end)) as m:
        m.update(transform_window(settings, start, end, batch_id))
    return m


def step_marts(settings: Settings, batch_id: uuid.UUID, full_refresh: bool = False) -> dict:
    from etl.marts import refresh_marts
    with run_journal(settings.dwh, "marts", batch_id) as m:
        m.update(refresh_marts(settings, full_refresh=full_refresh))
    return m


def step_clickhouse(settings: Settings, batch_id: uuid.UUID, full_refresh: bool = False) -> dict:
    if not settings.clickhouse.enabled:
        return {"skipped": True}
    from etl.clickhouse import sync_clickhouse
    with run_journal(settings.dwh, "clickhouse", batch_id) as m:
        m.update(sync_clickhouse(settings, full_refresh=full_refresh))
    return m


def step_ml(settings: Settings, batch_id: uuid.UUID) -> dict:
    from ml.train import train_and_score
    with run_journal(settings.dwh, "ml", batch_id) as m:
        m.update(train_and_score(settings))
    return m


# --------------------------------------------------------------------------------------- helpers

def _journal_ready(settings: Settings) -> bool:
    from common.db import connect
    with connect(settings.dwh, autocommit=True) as conn:
        return conn.execute("SELECT to_regclass('etl.run_log') IS NOT NULL").fetchone()[0]


class _noop:
    def __enter__(self) -> dict:
        return {}

    def __exit__(self, *exc) -> bool:
        return False
