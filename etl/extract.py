"""Extract: source PostgreSQL -> raw layer of the warehouse.

Deals (and their child rows) are re-extracted by a loading_date window and atomically replace
the same window in raw (DELETE + INSERT in one transaction). Dictionaries are small and are
replaced as full snapshots. Data is streamed with COPY TO/FROM STDOUT — nothing goes row by row.
"""
from __future__ import annotations

import uuid
from datetime import date

import psycopg

from common.config import Settings
from common.db import connect
from common.logging import get_logger

log = get_logger(__name__)

PRICE_COLUMNS = [
    "id", "created_at", "updated_at", "organization_id", "loading_date", "end_loading_date",
    "loading_city_gcid", "unloading_city_gcid", "distance", "total_weight", "total_volume", "body_type",
    "price", "currency_code", "complex", "price_type", "is_fact", "nearest_rate_date", "outlier_mark",
    "weight_category", "volume_category",
]

DICTIONARIES = {
    "cities": "SELECT id, gc_id, name, full_name_local, latitude, longitude FROM cities",
    "body_types": "SELECT id, name, std_id FROM body_types",
    "exchange_rates": "SELECT id, num_code, value, setting_date FROM exchange_rates",
}


def _stream(src: psycopg.Connection, dst: psycopg.Connection, select_sql: str, params: tuple, target: str) -> int:
    """Pipe `COPY (select) TO STDOUT` from source into `COPY target FROM STDIN` on the warehouse."""
    query = psycopg.ClientCursor(src).mogrify(select_sql, params) if params else select_sql
    with src.cursor() as scur, dst.cursor() as dcur:
        with scur.copy(f"COPY ({query}) TO STDOUT") as out, dcur.copy(f"COPY {target} FROM STDIN") as inp:
            for block in out:
                inp.write(block)
    table = target.split(" ", 1)[0]
    return dst.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def extract_window(settings: Settings, start: date, end: date, batch_id: uuid.UUID) -> dict:
    window = (start, end)
    price_cols = ", ".join(PRICE_COLUMNS)
    with connect(settings.source) as src, connect(settings.dwh) as dwh:
        src.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")  # consistent snapshot

        dwh.execute(
            "CREATE TEMP TABLE stg_prices (LIKE raw.prices INCLUDING DEFAULTS) ON COMMIT DROP;"
            "ALTER TABLE stg_prices ALTER COLUMN _batch_id DROP NOT NULL;"
            "CREATE TEMP TABLE stg_price_body_types (LIKE raw.price_body_types) ON COMMIT DROP;"
            "CREATE TEMP TABLE stg_price_dates (LIKE raw.price_dates) ON COMMIT DROP;"
        )
        n_prices = _stream(
            src, dwh,
            f"SELECT {price_cols} FROM prices WHERE loading_date BETWEEN %s AND %s",
            window, f"stg_prices ({price_cols})",
        )
        n_pbt = _stream(
            src, dwh,
            "SELECT pbt.price_id, pbt.body_type_id FROM price_body_types pbt "
            "JOIN prices p ON p.id = pbt.price_id WHERE p.loading_date BETWEEN %s AND %s",
            window, "stg_price_body_types (price_id, body_type_id)",
        )
        n_pd = _stream(
            src, dwh,
            "SELECT pd.price_id, pd.price_date FROM price_dates pd "
            "JOIN prices p ON p.id = pd.price_id WHERE p.loading_date BETWEEN %s AND %s",
            window, "stg_price_dates (price_id, price_date)",
        )

        # Replace the window. A deal whose loading_date moved into this window from another one
        # is also replaced (match by id), so a re-extract never leaves two versions of one deal.
        dwh.execute("UPDATE stg_prices SET _batch_id = %s", (batch_id,))
        dwh.execute(
            "CREATE TEMP TABLE replaced_ids ON COMMIT DROP AS "
            "SELECT id FROM raw.prices WHERE loading_date BETWEEN %s AND %s "
            "UNION SELECT id FROM stg_prices",
            window,
        )
        dwh.execute("DELETE FROM raw.price_body_types t USING replaced_ids r WHERE t.price_id = r.id")
        dwh.execute("DELETE FROM raw.price_dates t USING replaced_ids r WHERE t.price_id = r.id")
        deleted = dwh.execute("DELETE FROM raw.prices t USING replaced_ids r WHERE t.id = r.id").rowcount
        dwh.execute("INSERT INTO raw.prices SELECT * FROM stg_prices")
        dwh.execute("INSERT INTO raw.price_body_types SELECT * FROM stg_price_body_types")
        dwh.execute("INSERT INTO raw.price_dates SELECT * FROM stg_price_dates")

        dict_rows = {}
        for table, select_sql in DICTIONARIES.items():
            dwh.execute(f"TRUNCATE raw.{table}")
            cols = select_sql.split("SELECT ", 1)[1].split(" FROM", 1)[0]
            dict_rows[table] = _stream(src, dwh, select_sql, (), f"raw.{table} ({cols})")

        dwh.commit()
        src.rollback()

    metrics = {
        "rows_read_prices": n_prices,
        "rows_read_price_body_types": n_pbt,
        "rows_read_price_dates": n_pd,
        "rows_replaced_prices": deleted,
        **{f"rows_read_{k}": v for k, v in dict_rows.items()},
    }
    log.info("raw window extracted", extra=metrics)
    return metrics
