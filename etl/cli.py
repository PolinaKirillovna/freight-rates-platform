"""Точка входа командной строки.

    python -m etl.cli all --start 2023-01-01 --end 2027-12-31     # полный пересчёт: ETL -> витрины -> ClickHouse -> ML
    python -m etl.cli etl --start 2026-10-01 --end 2026-10-31     # повтор одного окна (идемпотентно)
    python -m etl.cli marts --full-refresh
"""
from __future__ import annotations

import argparse
import uuid
from datetime import date

from common.config import load_settings
from common.logging import get_logger, setup_logging
from etl import steps

log = get_logger("etl.cli")


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="etl", description="Freight rates data platform pipeline")
    parser.add_argument(
        "command",
        choices=["migrate", "extract", "transform", "etl", "marts", "clickhouse", "ml", "all"],
    )
    parser.add_argument("--start", type=date.fromisoformat, help="начало окна (loading_date, включительно)")
    parser.add_argument("--end", type=date.fromisoformat, help="конец окна (loading_date, включительно)")
    parser.add_argument("--full-refresh", action="store_true", help="пересчитать все месяцы витрин, а не только требующие пересчёта")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse(argv)
    settings = load_settings()
    setup_logging(settings.log_level)
    start = args.start or settings.window_start
    end = args.end or settings.window_end
    if start > end:
        raise SystemExit("--start must not be after --end")
    batch_id = uuid.uuid4()
    log.info("pipeline command", extra={"command": args.command, "batch_id": str(batch_id),
                                         "window_start": start, "window_end": end, "as_of": settings.as_of})

    cmd = args.command
    if cmd in {"migrate", "etl", "all"}:
        steps.step_migrate(settings, batch_id)
    if cmd in {"extract", "etl", "all"}:
        steps.step_extract(settings, batch_id, start, end)
    if cmd in {"transform", "etl", "all"}:
        steps.step_transform(settings, batch_id, start, end)
    if cmd in {"marts", "etl", "all"}:
        steps.step_marts(settings, batch_id, full_refresh=args.full_refresh)
    if cmd in {"clickhouse", "etl", "all"}:
        steps.step_clickhouse(settings, batch_id, full_refresh=args.full_refresh)
    if cmd in {"ml", "all"}:
        steps.step_ml(settings, batch_id)
    log.info("pipeline command finished", extra={"command": cmd, "batch_id": str(batch_id)})


if __name__ == "__main__":
    main()
