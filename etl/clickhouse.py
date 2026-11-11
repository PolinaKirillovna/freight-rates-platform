"""Аналитический слой ClickHouse (MergeTree): синхронизировать строки core, посчитать карточку
маршрута средствами ClickHouse и сверить результат с витриной PostgreSQL.

Идемпотентность: каждый месяц, требующий пересчёта, — это секция. Синхронизация удаляет секции месяца и
вставляет их заново; ReplacingMergeTree(_version) + FINAL дополнительно поглощает повторную вставку.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Protocol

import numpy as np
import pandas as pd

from common.config import SQL_DIR, Settings
from common.db import connect, fetch_df
from common.logging import get_logger

log = get_logger(__name__)

CONSUMER = "clickhouse"
PARITY_COLUMNS = ["trips_count", "carriers_count", "p25_trip", "median_trip", "p75_trip", "avg_trip",
                  "p25_km", "median_km", "p75_km", "cv_rate"]
KEY = ["month", "loading_city_gcid", "unloading_city_gcid", "body_group_code", "weight_category"]

MARKET_ROWS_SQL = """
SELECT d.price_month, d.price_date, f.price_id, bg.body_group_code, f.loading_date,
       f.loading_city_gcid, f.unloading_city_gcid, f.weight_category, f.volume_category,
       f.organization_id, f.price_type, f.is_fact, f.complex, f.outlier_mark,
       f.price_original, f.distance, f.price_rub, f.price_rub_per_km
FROM core.fact_price_date d
JOIN core.fact_price f             USING (price_id)
JOIN core.fact_price_body_group bg USING (price_id)
WHERE d.price_month = ANY(%(months)s)
"""


class ClickHouseExecutor(Protocol):
    def command(self, sql: str, params: dict | None = None) -> None: ...
    def insert_df(self, table: str, df: pd.DataFrame) -> None: ...
    def query_df(self, sql: str, params: dict | None = None) -> pd.DataFrame: ...


class ConnectExecutor:
    """Рабочий исполнитель поверх clickhouse-connect (интерфейс HTTP)."""

    def __init__(self, settings: Settings):
        import clickhouse_connect

        ch = settings.clickhouse
        self.client = clickhouse_connect.get_client(host=ch.host, port=ch.port, username=ch.user, password=ch.password)

    def command(self, sql: str, params: dict | None = None) -> None:
        self.client.command(sql, parameters=params)

    def insert_df(self, table: str, df: pd.DataFrame) -> None:
        self.client.insert_df(table, df)

    def query_df(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        return self.client.query_df(sql, parameters=params)


def _sql(name: str, database: str) -> str:
    return (SQL_DIR / "clickhouse" / name).read_text(encoding="utf-8").replace("{database}", database)


def _statements(script: str) -> list[str]:
    no_comments = "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def ensure_schema(ch: ClickHouseExecutor, database: str) -> None:
    for name in ("001_tables.sql", "002_route_card_view.sql"):
        for statement in _statements(_sql(name, database)):
            ch.command(statement)


def _partition_ids(months: list[date]) -> list[str]:
    return [m.strftime("%Y%m") for m in months]


def sync_clickhouse(settings: Settings, full_refresh: bool = False, ch: ClickHouseExecutor | None = None) -> dict:
    db = settings.clickhouse.database
    ch = ch or ConnectExecutor(settings)
    ensure_schema(ch, db)

    with connect(settings.dwh) as conn:
        if full_refresh:
            months = [r[0] for r in conn.execute("SELECT DISTINCT price_month FROM core.fact_price_date ORDER BY 1")]
        else:
            months = [r[0] for r in conn.execute(
                "SELECT month FROM etl.dirty_months WHERE consumer = %s ORDER BY month", (CONSUMER,))]
        rows = fetch_df(conn, MARKET_ROWS_SQL, {"months": months}) if months else pd.DataFrame()
        cities = fetch_df(conn, "SELECT gc_id, city_name, region_name, latitude, longitude FROM core.dim_city ORDER BY gc_id")
        groups = fetch_df(conn, "SELECT body_group_code, body_group FROM core.dim_body_group ORDER BY 1")

    # Небольшие измерения и дата снимка заменяются целиком при каждой синхронизации.
    for table, frame in ((f"{db}.dim_city", cities), (f"{db}.dim_body_group", groups),
                         (f"{db}.snapshot", pd.DataFrame({"as_of": [settings.as_of]}))):
        ch.command(f"TRUNCATE TABLE {table}")
        ch.insert_df(table, frame)

    version = int(time.time() * 1000)
    if months:
        for partition in _partition_ids(months):
            ch.command(f"ALTER TABLE {db}.fact_market_rows DROP PARTITION ID '{partition}'")
            ch.command(f"ALTER TABLE {db}.route_month_base DROP PARTITION ID '{partition}'")
        if not rows.empty:
            rows["organization_id"] = rows["organization_id"].astype(object).where(rows["organization_id"].notna(), None)
            rows["_version"] = version
            for col in ("price_original", "distance", "price_rub", "price_rub_per_km"):
                rows[col] = rows[col].astype("float64")
            ch.insert_df(f"{db}.fact_market_rows", rows)
        ch.command(_strip_comments(_sql("route_month_base.sql", db)),
                   {"as_of": settings.as_of, "months": months, "version": version})

    parity = check_parity(settings, ch, months) if months else {"cells_compared": 0}
    with connect(settings.dwh) as conn:
        conn.execute("DELETE FROM etl.dirty_months WHERE consumer = %s AND month = ANY(%s)", (CONSUMER, months))
        conn.commit()

    metrics = {"months_synced": len(months), "rows_synced": len(rows), **parity}
    log.info("clickhouse synced", extra=metrics)
    return metrics


def _strip_comments(script: str) -> str:
    return "\n".join(line for line in script.splitlines() if not line.strip().startswith("--")).strip()


def check_parity(settings: Settings, ch: ClickHouseExecutor, months: list[date], rel_tol: float = 1e-6) -> dict:
    """Сверить route_month_base в ClickHouse с таким же в PostgreSQL по синхронизированным месяцам."""
    cols = ", ".join(KEY + PARITY_COLUMNS)
    with connect(settings.dwh) as conn:
        pg = fetch_df(conn, f"SELECT {cols} FROM mart.route_month_base WHERE month = ANY(%(m)s)", {"m": months})
    chdf = ch.query_df(
        f"SELECT {cols} FROM {settings.clickhouse.database}.route_month_base FINAL WHERE month IN {{months:Array(Date)}}",
        {"months": months},
    )
    for frame in (pg, chdf):
        frame["month"] = pd.to_datetime(frame["month"]).dt.date
        frame["weight_category"] = frame["weight_category"].astype(str)
        for col in ("loading_city_gcid", "unloading_city_gcid", "body_group_code"):
            frame[col] = frame[col].astype("int64")
    merged = pg.merge(chdf, on=KEY, how="outer", suffixes=("_pg", "_ch"), indicator=True)
    only_one_side = int((merged["_merge"] != "both").sum())
    both = merged[merged["_merge"] == "both"]
    mismatched_cells = 0
    if not both.empty:
        bad = pd.Series(False, index=both.index)
        for col in PARITY_COLUMNS:
            a = both[f"{col}_pg"].astype("float64")
            b = both[f"{col}_ch"].astype("float64")
            same_null = a.isna() & b.isna()
            close = np.isclose(a, b, rtol=rel_tol, atol=1e-6)
            bad |= ~(same_null | close)
        mismatched_cells = int(bad.sum())
    result = {
        "parity_cells_pg": len(pg),
        "parity_cells_clickhouse": len(chdf),
        "parity_cells_missing_on_one_side": only_one_side,
        "parity_cells_mismatched": mismatched_cells,
        "parity_ok": only_one_side == 0 and mismatched_cells == 0,
    }
    if not result["parity_ok"]:
        log.warning("clickhouse parity check failed", extra=result)
    return result
