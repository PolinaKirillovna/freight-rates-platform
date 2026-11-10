"""Шаги конвейера с общей сигнатурой, используются из командной строки и из DAG Airflow.

Каждый шаг — наследник PipelineStep: шаблонный метод ``run()`` журналирует запуск через
``run_journal`` и делегирует саму работу абстрактному ``_execute()``. Функции ``step_*``
остаются тонкими обёртками, поэтому CLI, DAG и тесты вызывают шаги без изменений.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import date

from common.config import Settings
from common.db import apply_migrations, run_journal


class PipelineStep(ABC):
    """Шаблонный метод: записать запуск в etl.run_log, затем выполнить тело конкретного шага."""

    name: str

    def run(self, settings: Settings, batch_id: uuid.UUID, *args, **kwargs) -> dict:
        with self._journal(settings, batch_id, *args, **kwargs) as m:
            m.update(self._execute(settings, batch_id, *args, **kwargs))
        return m

    def _journal(self, settings: Settings, batch_id: uuid.UUID, *args, **kwargs):
        return run_journal(settings.dwh, self.name, batch_id, self._window(*args, **kwargs))

    def _window(self, *args, **kwargs) -> tuple | None:
        return None

    @abstractmethod
    def _execute(self, settings: Settings, batch_id: uuid.UUID, *args, **kwargs) -> dict:
        ...


class MigrateStep(PipelineStep):
    name = "migrate"

    def _journal(self, settings: Settings, batch_id: uuid.UUID, *args, **kwargs):
        # Таблицы run_log может ещё не быть при самой первой миграции.
        return super()._journal(settings, batch_id) if _journal_ready(settings) else _noop()

    def _execute(self, settings: Settings, batch_id: uuid.UUID) -> dict:
        return {"files": apply_migrations(settings.dwh)}


class ExtractStep(PipelineStep):
    name = "extract"

    def _window(self, start: date, end: date) -> tuple:
        return (start, end)

    def _execute(self, settings: Settings, batch_id: uuid.UUID, start: date, end: date) -> dict:
        from etl.extract import extract_window
        return extract_window(settings, start, end, batch_id)


class TransformStep(PipelineStep):
    name = "transform"

    def _window(self, start: date, end: date) -> tuple:
        return (start, end)

    def _execute(self, settings: Settings, batch_id: uuid.UUID, start: date, end: date) -> dict:
        from etl.transform import transform_window
        return transform_window(settings, start, end, batch_id)


class MartsStep(PipelineStep):
    name = "marts"

    def _execute(self, settings: Settings, batch_id: uuid.UUID, full_refresh: bool = False) -> dict:
        from etl.marts import refresh_marts
        return refresh_marts(settings, full_refresh=full_refresh)


class ClickhouseStep(PipelineStep):
    name = "clickhouse"

    def run(self, settings: Settings, batch_id: uuid.UUID, full_refresh: bool = False) -> dict:
        if not settings.clickhouse.enabled:
            return {"skipped": True}
        return super().run(settings, batch_id, full_refresh=full_refresh)

    def _execute(self, settings: Settings, batch_id: uuid.UUID, full_refresh: bool = False) -> dict:
        from etl.clickhouse import sync_clickhouse
        return sync_clickhouse(settings, full_refresh=full_refresh)


class MlStep(PipelineStep):
    name = "ml"

    def _execute(self, settings: Settings, batch_id: uuid.UUID) -> dict:
        from ml.train import train_and_score
        return train_and_score(settings)


# Тонкие обёртки, чтобы CLI, DAG и тесты вызывали шаги без изменений.

def step_migrate(settings: Settings, batch_id: uuid.UUID) -> dict:
    return MigrateStep().run(settings, batch_id)


def step_extract(settings: Settings, batch_id: uuid.UUID, start: date, end: date) -> dict:
    return ExtractStep().run(settings, batch_id, start, end)


def step_transform(settings: Settings, batch_id: uuid.UUID, start: date, end: date) -> dict:
    return TransformStep().run(settings, batch_id, start, end)


def step_marts(settings: Settings, batch_id: uuid.UUID, full_refresh: bool = False) -> dict:
    return MartsStep().run(settings, batch_id, full_refresh=full_refresh)


def step_clickhouse(settings: Settings, batch_id: uuid.UUID, full_refresh: bool = False) -> dict:
    return ClickhouseStep().run(settings, batch_id, full_refresh=full_refresh)


def step_ml(settings: Settings, batch_id: uuid.UUID) -> dict:
    return MlStep().run(settings, batch_id)


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
