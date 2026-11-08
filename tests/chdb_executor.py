"""In-process ClickHouse (chdb) executor used by the integration test of the ClickHouse layer."""
from __future__ import annotations

import os
import re
import tempfile
from datetime import date

import pandas as pd


def _literal(value) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    if isinstance(value, date):
        return f"toDate('{value.isoformat()}')"
    if isinstance(value, str):
        return "'" + value.replace("'", "\\'") + "'"
    return str(value)


def _bind(sql: str, params: dict | None) -> str:
    if not params:
        return sql
    return re.sub(r"\{(\w+):[^}]+\}", lambda m: _literal(params[m.group(1)]), sql)


class ChdbExecutor:
    def __init__(self, path: str | None = None):
        from chdb import session

        self.dir = path or tempfile.mkdtemp(prefix="chdb_")
        self.sess = session.Session(self.dir)

    def command(self, sql: str, params: dict | None = None) -> None:
        self.sess.query(_bind(sql, params))

    def insert_df(self, table: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        cols = self.sess.query(f"SELECT name FROM system.columns WHERE concat(database, '.', table) = '{table}' ORDER BY position",
                               "CSV").bytes().decode().split()
        cols = [c.strip('"') for c in cols]
        frame = df.copy()
        for c in frame.columns:
            if frame[c].dtype == bool:
                frame[c] = frame[c].astype(int)
        path = os.path.join(self.dir, "insert.csv")
        frame[[c for c in cols if c in frame.columns]].to_csv(path, index=False, header=True, na_rep="\\N")
        present = ", ".join(c for c in cols if c in frame.columns)
        self.sess.query(
            f"INSERT INTO {table} ({present}) SELECT * FROM file('{path}', 'CSVWithNames') "
            "SETTINGS input_format_csv_empty_as_default = 1"
        )

    def query_df(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        return self.sess.query(_bind(sql, params), "DataFrame")
