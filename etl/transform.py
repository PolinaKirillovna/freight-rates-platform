"""Преобразование: окно raw -> core (проверенные, приведённые к рублям, категоризованные факты).

Вся запись — одна транзакция: измерения обновляются upsert'ом, окно фактов заменяется
(DELETE + COPY), карантин за окно заменяется, а затронутые изменением месяцы помечаются
грязными для потребителей ниже по течению (витрины PostgreSQL, ClickHouse).
"""
from __future__ import annotations

import uuid
from datetime import date

import numpy as np
import pandas as pd

from common.config import Settings
from common.db import connect, copy_df, fetch_df
from common.logging import get_logger
from etl import dq
from etl.rules import (
    OTHER_GROUP,
    RUB,
    UNKNOWN_GROUP,
    body_group_code,
    volume_category,
    weight_category,
)

log = get_logger(__name__)

DIRTY_MONTH_CONSUMERS = ("pg_mart", "clickhouse")

FACT_COLUMNS = [
    "price_id", "created_at", "loading_date", "end_loading_date", "loading_city_gcid", "unloading_city_gcid",
    "organization_id", "price_type", "is_fact", "complex", "outlier_mark", "same_city_route",
    "distance", "total_weight", "total_volume", "body_type", "primary_body_group_code",
    "weight_category", "volume_category", "weight_category_filled", "volume_category_filled",
    "currency_code", "price_original", "nearest_rate_date", "fx_rate", "price_rub", "price_rub_per_km", "_batch_id",
]


# ----------------------------------------------------------------------------- чистые преобразования

def enrich_prices(clean: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    """Привести к рублям по точному курсу на nearest_rate_date и дозаполнить пустые категории.

    У `rates` столбцы currency_code, rate_date, rate. Рубль всегда конвертируется по 1,0.
    Строки без точного курса должны быть уже отбракованы проверками качества (правило MISSING_FX_RATE).
    """
    df = clean.copy()
    merged = df.merge(
        rates.rename(columns={"rate_date": "nearest_rate_date"}),
        how="left", on=["currency_code", "nearest_rate_date"], validate="many_to_one",
    )
    merged.index = df.index
    fx = np.where(df["currency_code"] == RUB, 1.0, merged["rate"])
    if np.isnan(fx.astype(float)).any():
        raise ValueError("Rows without an exact FX rate reached enrichment; DQ must quarantine them first")
    df["fx_rate"] = fx.astype(float)
    df["price_original"] = df["price"].astype(float)
    df["price_rub"] = df["price_original"] * df["fx_rate"]
    df["price_rub_per_km"] = df["price_rub"] / df["distance"]

    derived_w = weight_category(df["total_weight"])
    derived_v = volume_category(df["total_volume"])
    df["weight_category_filled"] = df["weight_category"].isna()
    df["volume_category_filled"] = df["volume_category"].isna()
    df["weight_category"] = df["weight_category"].fillna(derived_w)
    df["volume_category"] = df["volume_category"].fillna(derived_v)
    df["same_city_route"] = df["loading_city_gcid"] == df["unloading_city_gcid"]
    return df


def body_type_dimension(body_types: pd.DataFrame) -> pd.DataFrame:
    dim = body_types.rename(columns={"id": "body_type_id"})[["body_type_id", "name", "std_id"]].copy()
    dim["body_group_code"] = body_group_code(dim["std_id"])
    dim["std_id"] = dim["std_id"].astype("Int64")
    return dim


def resolve_body_groups(prices: pd.DataFrame, links: pd.DataFrame, body_dim: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Одна строка на (сделка, группа кузова) + основная группа каждой сделки.

    Связи берутся из price_body_types; сделка без связей откатывается к prices.body_type, затем к
    «Не указан». Несколько дочерних типов одной группы (например, 56 и 61) схлопываются в одну строку,
    поэтому цена не учитывается дважды внутри группы. Неизвестные id типов попадают в «Прочие».
    """
    group_by_type = dict(zip(body_dim["body_type_id"], body_dim["body_group_code"], strict=False))

    def to_group(type_ids: pd.Series) -> pd.Series:
        mapped = type_ids.map(group_by_type)
        mapped = mapped.where(type_ids.isna() | mapped.notna(), OTHER_GROUP)
        return mapped.fillna(UNKNOWN_GROUP).astype("int16")

    ids = prices["id"]
    linked = links[links["price_id"].isin(ids)][["price_id", "body_type_id"]]
    linked = linked.assign(body_group_code=to_group(linked["body_type_id"]))

    without_links = prices.loc[~ids.isin(linked["price_id"]), ["id", "body_type"]]
    fallback = pd.DataFrame({
        "price_id": without_links["id"].to_numpy(),
        "body_group_code": to_group(without_links["body_type"]).to_numpy(),
    })
    groups = (
        pd.concat([linked[["price_id", "body_group_code"]], fallback], ignore_index=True)
        .drop_duplicates()
        .sort_values(["price_id", "body_group_code"])
        .reset_index(drop=True)
    )

    primary = to_group(prices["body_type"])
    primary.index = prices["id"].to_numpy()
    first_link = linked.sort_values(["price_id", "body_type_id"]).drop_duplicates("price_id").set_index("price_id")["body_group_code"]
    no_main_type = prices["body_type"].isna().to_numpy()
    primary[no_main_type] = first_link.reindex(primary.index[no_main_type]).fillna(UNKNOWN_GROUP).to_numpy()
    return groups, primary.astype("int16")


def build_price_dates(prices: pd.DataFrame, raw_dates: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    dates = raw_dates[raw_dates["price_id"].isin(prices["id"])][["price_id", "price_date"]]
    missing = prices.loc[~prices["id"].isin(dates["price_id"]), ["id", "loading_date"]]
    fallback = missing.rename(columns={"id": "price_id", "loading_date": "price_date"})
    return pd.concat([dates, fallback], ignore_index=True).drop_duplicates(), len(fallback)


# ----------------------------------------------------------------------------- database step

def _read_inputs(conn, start: date, end: date) -> dict[str, pd.DataFrame]:
    window = {"start": start, "end": end}
    return {
        "prices": fetch_df(conn, "SELECT * FROM raw.prices WHERE loading_date BETWEEN %(start)s AND %(end)s", window),
        "links": fetch_df(
            conn,
            "SELECT l.price_id, l.body_type_id FROM raw.price_body_types l JOIN raw.prices p ON p.id = l.price_id "
            "WHERE p.loading_date BETWEEN %(start)s AND %(end)s", window),
        "dates": fetch_df(
            conn,
            "SELECT d.price_id, d.price_date FROM raw.price_dates d JOIN raw.prices p ON p.id = d.price_id "
            "WHERE p.loading_date BETWEEN %(start)s AND %(end)s", window),
        "cities": fetch_df(conn, "SELECT gc_id FROM raw.cities"),
        "body_types": fetch_df(conn, "SELECT id, name, std_id FROM raw.body_types"),
        "rates": fetch_df(
            conn,
            "SELECT DISTINCT ON (num_code, setting_date) num_code AS currency_code, setting_date AS rate_date, value AS rate "
            "FROM raw.exchange_rates ORDER BY num_code, setting_date, id DESC"),
    }


def _upsert_dimensions(conn, body_dim: pd.DataFrame) -> None:
    conn.execute("""
        INSERT INTO core.dim_city (gc_id, source_city_id, city_name, region_name, full_name_local, latitude, longitude, duplicates)
        SELECT DISTINCT ON (gc_id)
               gc_id, id,
               coalesce(nullif(btrim(split_part(full_name_local, ',', 1)), ''), name),
               CASE WHEN strpos(full_name_local, ',') > 0
                    THEN nullif(btrim(substr(full_name_local, strpos(full_name_local, ',') + 1)), '') END,
               full_name_local, latitude, longitude,
               count(*) OVER (PARTITION BY gc_id)
        FROM raw.cities
        ORDER BY gc_id, id                          -- каноническая строка = минимальный cities.id на gc_id
        ON CONFLICT (gc_id) DO UPDATE SET
            source_city_id = EXCLUDED.source_city_id, city_name = EXCLUDED.city_name,
            region_name = EXCLUDED.region_name, full_name_local = EXCLUDED.full_name_local,
            latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, duplicates = EXCLUDED.duplicates
    """)
    conn.execute("CREATE TEMP TABLE stg_body_type (LIKE core.dim_body_type) ON COMMIT DROP")
    copy_df(conn, "stg_body_type", body_dim, ["body_type_id", "name", "std_id", "body_group_code"])
    conn.execute("""
        INSERT INTO core.dim_body_type SELECT * FROM stg_body_type
        ON CONFLICT (body_type_id) DO UPDATE SET name = EXCLUDED.name, std_id = EXCLUDED.std_id,
                                                 body_group_code = EXCLUDED.body_group_code
    """)
    conn.execute("""
        INSERT INTO core.exchange_rate (currency_code, rate_date, rate)
        SELECT DISTINCT ON (num_code, setting_date) num_code, setting_date, value
        FROM raw.exchange_rates ORDER BY num_code, setting_date, id DESC
        ON CONFLICT (currency_code, rate_date) DO UPDATE SET rate = EXCLUDED.rate
    """)


def transform_window(settings: Settings, start: date, end: date, batch_id: uuid.UUID) -> dict:
    with connect(settings.dwh) as conn:
        data = _read_inputs(conn, start, end)
        prices = data["prices"]
        ctx = dq.DqContext(
            known_gcids=set(data["cities"]["gc_id"].astype(int)),
            rate_keys=set(zip(data["rates"]["currency_code"], data["rates"]["rate_date"], strict=False)),
        )
        result = dq.validate(prices, ctx)
        clean = enrich_prices(result.clean, data["rates"])

        body_dim = body_type_dimension(data["body_types"])
        groups, primary = resolve_body_groups(clean, data["links"], body_dim)
        clean["primary_body_group_code"] = clean["id"].map(primary).astype("int16")
        price_dates, dates_fallback = build_price_dates(clean, data["dates"])

        fact = clean.rename(columns={"id": "price_id"})
        fact["_batch_id"] = str(batch_id)
        for col in ("organization_id", "body_type"):
            fact[col] = fact[col].astype("Int64")
        quarantine = result.quarantine.assign(_batch_id=str(batch_id))

        _upsert_dimensions(conn, body_dim)

        conn.execute(
            "CREATE TEMP TABLE replaced_ids ON COMMIT DROP AS "
            "SELECT price_id FROM core.fact_price WHERE loading_date BETWEEN %(s)s AND %(e)s "
            "UNION SELECT id FROM raw.prices WHERE loading_date BETWEEN %(s)s AND %(e)s",
            {"s": start, "e": end},
        )
        conn.execute("CREATE INDEX ON replaced_ids (price_id)")
        conn.execute(
            "CREATE TEMP TABLE touched_months ON COMMIT DROP AS "
            "SELECT DISTINCT d.price_month AS month FROM core.fact_price_date d JOIN replaced_ids r USING (price_id)"
        )
        deleted = conn.execute("DELETE FROM core.fact_price f USING replaced_ids r WHERE f.price_id = r.price_id").rowcount
        conn.execute(
            "DELETE FROM dq.quarantine_prices q WHERE q.loading_date BETWEEN %(s)s AND %(e)s "
            "OR q.price_id IN (SELECT price_id FROM replaced_ids)",
            {"s": start, "e": end},
        )

        n_fact = copy_df(conn, "core.fact_price", fact, FACT_COLUMNS)
        n_groups = copy_df(conn, "core.fact_price_body_group", groups, ["price_id", "body_group_code"])
        n_dates = copy_df(conn, "core.fact_price_date", price_dates, ["price_id", "price_date"])
        n_quarantine = copy_df(
            conn, "dq.quarantine_prices", quarantine,
            ["price_id", "reason_code", "loading_date", "currency_code", "price", "distance", "details", "_batch_id"],
        )

        conn.execute(
            "INSERT INTO touched_months SELECT DISTINCT d.price_month FROM core.fact_price_date d "
            "JOIN core.fact_price f USING (price_id) WHERE f._batch_id = %s",
            (batch_id,),
        )
        dirty = conn.execute(
            "INSERT INTO etl.dirty_months (consumer, month) "
            "SELECT c, t.month FROM (SELECT DISTINCT month FROM touched_months) t CROSS JOIN unnest(%s::text[]) c "
            "ON CONFLICT (consumer, month) DO UPDATE SET marked_at = now()",
            (list(DIRTY_MONTH_CONSUMERS),),
        ).rowcount
        conn.commit()

    metrics = {
        "rows_read": len(prices),
        "rows_quarantined": result.stats["rows_rejected"],
        "quarantine_records": n_quarantine,
        "rows_loaded_core": n_fact,
        "rows_replaced_core": deleted,
        "body_group_links": n_groups,
        "price_dates": n_dates,
        "price_dates_fallback": dates_fallback,
        "weight_category_filled": int(fact["weight_category_filled"].sum()),
        "volume_category_filled": int(fact["volume_category_filled"].sum()),
        "same_city_routes": int(fact["same_city_route"].sum()),
        "dirty_month_marks": dirty,
        "dq_rule_hits": {k: v for k, v in result.stats.items() if k.isupper()},
    }
    log.info("core window loaded", extra=metrics)
    return metrics
