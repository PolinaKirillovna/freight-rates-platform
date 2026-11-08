"""Mart layer refresh in PostgreSQL.

Only the months marked dirty by the core step are recomputed (DELETE + INSERT of those months in
one transaction), then the thin materialized view with MoM/YoY and reliability is refreshed.
"""
from __future__ import annotations

from datetime import date

from common.config import SQL_DIR, Settings
from common.db import connect, set_setting
from common.logging import get_logger

log = get_logger(__name__)

CONSUMER = "pg_mart"
BASE_COLUMNS = (
    "month, loading_city_gcid, unloading_city_gcid, body_group_code, weight_category, trips_count, carriers_count, "
    "p25_trip, median_trip, p75_trip, avg_trip, sum_trip, p25_km, median_km, p75_km, cv_rate, volume_ftl_share, "
    "last_price_date"
)


def route_card_query() -> str:
    return (SQL_DIR / "mart" / "route_month_base.sql").read_text(encoding="utf-8")


def refresh_marts(settings: Settings, full_refresh: bool = False) -> dict:
    with connect(settings.dwh) as conn:
        set_setting(conn, "as_of", settings.as_of.isoformat())
        if full_refresh:
            months: list[date] | None = None
            conn.execute("TRUNCATE mart.route_month_base")
        else:
            months = [r[0] for r in conn.execute(
                "SELECT month FROM etl.dirty_months WHERE consumer = %s ORDER BY month FOR UPDATE", (CONSUMER,)
            ).fetchall()]
            if not months:
                log.info("no dirty months, mart refresh skipped")
            conn.execute("DELETE FROM mart.route_month_base WHERE month = ANY(%s)", (months,))

        inserted = 0
        if months is None or months:
            inserted = conn.execute(
                f"INSERT INTO mart.route_month_base ({BASE_COLUMNS}) {route_card_query()}",
                {"as_of": settings.as_of, "months": months},
            ).rowcount
        conn.execute("DELETE FROM etl.dirty_months WHERE consumer = %s", (CONSUMER,))
        conn.commit()

    # REFRESH ... CONCURRENTLY keeps the view readable for BI while it rebuilds,
    # but it requires the view to be populated once; the first refresh is a plain one.
    with connect(settings.dwh, autocommit=True) as conn:
        populated = conn.execute(
            "SELECT ispopulated FROM pg_matviews WHERE schemaname = 'mart' AND matviewname = 'mart_route_card'"
        ).fetchone()[0]
        conn.execute(f"REFRESH MATERIALIZED VIEW {'CONCURRENTLY ' if populated else ''}mart.mart_route_card")
        card_rows = conn.execute("SELECT count(*) FROM mart.mart_route_card").fetchone()[0]
        reliability = dict(conn.execute(
            "SELECT reliability_level, count(*) FROM mart.mart_route_card GROUP BY 1"
        ).fetchall())

    metrics = {
        "months_recomputed": "all" if months is None else len(months),
        "rows_upserted_base": inserted,
        "route_card_rows": card_rows,
        "reliability": reliability,
    }
    log.info("marts refreshed", extra=metrics)
    return metrics
