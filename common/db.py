"""PostgreSQL helpers: connections, bulk COPY of DataFrames, migrations and run journal."""
from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import psycopg

from common.config import SQL_DIR, PostgresDsn
from common.logging import get_logger

log = get_logger(__name__)


def connect(dsn: PostgresDsn, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn.conninfo, autocommit=autocommit)


def fetch_df(conn: psycopg.Connection, query: str, params: dict | Sequence | None = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def copy_df(conn: psycopg.Connection, table: str, df: pd.DataFrame, columns: Sequence[str] | None = None) -> int:
    """Bulk-load a DataFrame with COPY ... FROM STDIN (CSV). No per-row INSERTs."""
    columns = list(columns or df.columns)
    if df.empty:
        return 0
    buffer = io.StringIO()
    frame = df[columns].replace({np.nan: None})
    frame.to_csv(buffer, index=False, header=False, na_rep="")
    buffer.seek(0)
    cols = ", ".join(f'"{c}"' for c in columns)
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '')") as copy:
            while chunk := buffer.read(1 << 20):
                copy.write(chunk)
    return len(df)


def apply_migrations(dsn: PostgresDsn) -> list[str]:
    """Apply sql/dwh/*.sql in order. Every file is idempotent (IF NOT EXISTS), applied list is journaled."""
    applied: list[str] = []
    with connect(dsn) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations "
            "(filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in sorted((SQL_DIR / "dwh").glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO public.schema_migrations (filename) VALUES (%s) "
                "ON CONFLICT (filename) DO UPDATE SET applied_at = now()",
                (path.name,),
            )
            applied.append(path.name)
        conn.commit()
    log.info("migrations applied", extra={"files": applied})
    return applied


def set_setting(conn: psycopg.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO etl.settings (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )


@contextmanager
def run_journal(dsn: PostgresDsn, step: str, batch_id: uuid.UUID, window: tuple | None = None) -> Iterator[dict]:
    """Record a step run in etl.run_log; the yielded dict collects metrics."""
    metrics: dict = {}
    started = datetime.now(UTC)
    start, end = window if window else (None, None)
    with connect(dsn, autocommit=True) as conn:
        run_id = conn.execute(
            "INSERT INTO etl.run_log (batch_id, step, window_start, window_end, started_at, status) "
            "VALUES (%s, %s, %s, %s, %s, 'running') RETURNING run_id",
            (batch_id, step, start, end, started),
        ).fetchone()[0]
    log.info("step started", extra={"step": step, "batch_id": str(batch_id), "window_start": start, "window_end": end})
    try:
        yield metrics
    except Exception as exc:
        with connect(dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE etl.run_log SET status = 'failed', finished_at = now(), metrics = %s, error = %s WHERE run_id = %s",
                (json.dumps(metrics, default=str), repr(exc), run_id),
            )
        log.exception("step failed", extra={"step": step, "batch_id": str(batch_id)})
        raise
    with connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE etl.run_log SET status = 'success', finished_at = now(), metrics = %s WHERE run_id = %s",
            (json.dumps(metrics, default=str), run_id),
        )
    elapsed = (datetime.now(UTC) - started).total_seconds()
    log.info("step finished", extra={"step": step, "seconds": round(elapsed, 2), **metrics})
