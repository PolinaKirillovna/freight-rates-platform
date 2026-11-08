"""Pipeline configuration from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = PROJECT_ROOT / "sql"


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _date(name: str, default: str) -> date:
    return date.fromisoformat(_env(name, default))


def _bool(name: str, default: str) -> bool:
    return _env(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PostgresDsn:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password} application_name=freight-rates"
        )


@dataclass(frozen=True)
class ClickHouseSettings:
    enabled: bool
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class Settings:
    source: PostgresDsn
    dwh: PostgresDsn
    clickhouse: ClickHouseSettings
    as_of: date
    window_start: date
    window_end: date
    random_seed: int
    artifacts_dir: Path
    log_level: str
    extra: dict = field(default_factory=dict)


def load_settings() -> Settings:
    pg_host = _env("POSTGRES_HOST", "localhost")
    pg_port = int(_env("POSTGRES_PORT", "5432"))
    return Settings(
        source=PostgresDsn(
            host=_env("SOURCE_DB_HOST", pg_host),
            port=int(_env("SOURCE_DB_PORT", str(pg_port))),
            dbname=_env("SOURCE_DB_NAME", "deals_bigdata"),
            user=_env("SOURCE_DB_USER", "source_reader"),
            password=_env("SOURCE_DB_PASSWORD", "source_reader"),
        ),
        dwh=PostgresDsn(
            host=_env("DWH_DB_HOST", pg_host),
            port=int(_env("DWH_DB_PORT", str(pg_port))),
            dbname=_env("DWH_DB_NAME", "dwh"),
            user=_env("DWH_DB_USER", "dwh"),
            password=_env("DWH_DB_PASSWORD", "dwh"),
        ),
        clickhouse=ClickHouseSettings(
            enabled=_bool("CLICKHOUSE_ENABLED", "true"),
            host=_env("CLICKHOUSE_HOST", "localhost"),
            port=int(_env("CLICKHOUSE_HTTP_PORT", "8123")),
            user=_env("CLICKHOUSE_USER", "default"),
            password=_env("CLICKHOUSE_PASSWORD", ""),
            database=_env("CLICKHOUSE_DB", "freight"),
        ),
        as_of=_date("AS_OF_DATE", "2026-10-26"),
        window_start=_date("PIPELINE_START_DATE", "2023-01-01"),
        window_end=_date("PIPELINE_END_DATE", "2027-12-31"),
        random_seed=int(_env("RANDOM_SEED", "42")),
        artifacts_dir=Path(_env("ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts"))),
        log_level=_env("LOG_LEVEL", "INFO"),
    )
