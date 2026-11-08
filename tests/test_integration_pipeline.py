"""End-to-end checks against a live warehouse (RUN_INTEGRATION=1, after `python -m etl.cli all`)."""
import uuid
from datetime import date

import pytest

from common.config import SQL_DIR, load_settings
from common.db import connect
from etl import steps

pytestmark = pytest.mark.integration

CHECKSUM_SQL = """
SELECT (SELECT count(*) FROM raw.prices),
       (SELECT count(*) FROM core.fact_price),
       (SELECT count(*) FROM core.fact_price_body_group),
       (SELECT count(*) FROM core.fact_price_date),
       (SELECT count(*) FROM dq.quarantine_prices),
       (SELECT count(*) FROM mart.route_month_base),
       (SELECT round(sum(sum_trip)::numeric, 2) FROM mart.route_month_base),
       (SELECT round(sum(median_trip + p25_km + p75_km)::numeric, 4) FROM mart.mart_route_card),
       (SELECT sum(trips_count) FROM mart.mart_route_card)
"""


@pytest.fixture(scope="module")
def settings():
    return load_settings()


def _checksum(settings):
    """Counts compare exactly; float sums compare with a relative tolerance (summation order may differ)."""
    with connect(settings.dwh) as conn:
        row = conn.execute(CHECKSUM_SQL).fetchone()
    return [v if isinstance(v, int) else pytest.approx(float(v), rel=1e-12) for v in row]


def test_rerun_of_a_window_does_not_change_anything(settings):
    before = _checksum(settings)
    batch = uuid.uuid4()
    for _ in range(2):  # two consecutive re-runs of the same window
        steps.step_extract(settings, batch, date(2026, 9, 20), date(2026, 11, 15))
        steps.step_transform(settings, batch, date(2026, 9, 20), date(2026, 11, 15))
        steps.step_marts(settings, batch)
    assert _checksum(settings) == [b.expected if hasattr(b, 'expected') else b for b in before]


def test_full_mart_refresh_equals_incremental(settings):
    before = _checksum(settings)
    steps.step_marts(settings, uuid.uuid4(), full_refresh=True)
    assert _checksum(settings) == [b.expected if hasattr(b, 'expected') else b for b in before]


def test_smoke_checks_have_no_violations(settings):
    sql = (SQL_DIR / "checks" / "smoke.sql").read_text(encoding="utf-8")
    with connect(settings.dwh) as conn:
        rows = conn.execute(sql).fetchall()
    violations = {name: n for name, n in rows if n}
    assert violations == {}


def test_city_duplicates_do_not_multiply_deals(settings):
    with connect(settings.dwh) as conn:
        dup_gcids = conn.execute("SELECT count(*) FROM core.dim_city WHERE duplicates > 1").fetchone()[0]
        joined, plain = conn.execute(
            "SELECT (SELECT count(*) FROM core.fact_price f JOIN core.dim_city c ON c.gc_id = f.loading_city_gcid),"
            "       (SELECT count(*) FROM core.fact_price)"
        ).fetchone()
    assert dup_gcids >= 2          # Москва and Казань have duplicate gc_id rows in the source
    assert joined == plain


def test_clickhouse_layer_matches_postgres(settings, tmp_path):
    chdb_executor = pytest.importorskip("tests.chdb_executor")
    from etl.clickhouse import sync_clickhouse

    ch = chdb_executor.ChdbExecutor(str(tmp_path))
    first = sync_clickhouse(settings, full_refresh=True, ch=ch)
    second = sync_clickhouse(settings, full_refresh=True, ch=ch)  # idempotent re-sync
    assert first["parity_ok"] and second["parity_ok"]
    count = ch.query_df(f"SELECT count() AS n FROM {settings.clickhouse.database}.fact_market_rows FINAL")["n"][0]
    assert count == first["rows_synced"]
