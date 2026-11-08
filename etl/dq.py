"""Правила качества данных по сделкам. Каждое правило — векторный предикат, возвращающий True для плохих строк.

Отбракованные строки не теряются молча: каждая пара (сделка, сработавшее правило) идёт в dq.quarantine_prices.
Маршруты внутри одного города (loading = unloading) сохраняются намеренно — исходный job оставляет их с
расстоянием — и только помечаются в core (same_city_route).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from etl.rules import EUR, KNOWN_CURRENCIES, RUB, RUB_MIN_VALID_PRICE, USD


@dataclass(frozen=True)
class DqContext:
    known_gcids: set[int]
    rate_keys: set[tuple[str, object]]      # пары (currency_code, rate_date), присутствующие в курсах валют


@dataclass(frozen=True)
class Rule:
    code: str
    description: str
    predicate: Callable[[pd.DataFrame, DqContext], pd.Series]


def _missing_fx_rate(df: pd.DataFrame, ctx: DqContext) -> pd.Series:
    foreign = df["currency_code"].isin([USD, EUR])
    keys = pd.Series(list(zip(df["currency_code"], df["nearest_rate_date"], strict=False)), index=df.index)
    has_rate = keys.map(lambda k: k in ctx.rate_keys) & df["nearest_rate_date"].notna()
    return foreign & ~has_rate


RULES: list[Rule] = [
    Rule("NON_POSITIVE_PRICE", "price <= 0", lambda d, c: d["price"] <= 0),
    Rule(
        "RUB_PRICE_TOO_LOW",
        f"RUB price in (0; {RUB_MIN_VALID_PRICE:.0f}]",
        lambda d, c: (d["currency_code"] == RUB) & (d["price"] > 0) & (d["price"] <= RUB_MIN_VALID_PRICE),
    ),
    Rule("NON_POSITIVE_DISTANCE", "distance <= 0", lambda d, c: d["distance"] <= 0),
    Rule("NEGATIVE_WEIGHT", "total_weight < 0", lambda d, c: d["total_weight"] < 0),
    Rule("NEGATIVE_VOLUME", "total_volume < 0", lambda d, c: d["total_volume"] < 0),
    Rule("UNKNOWN_LOADING_CITY", "loading_city_gcid not in cities",
         lambda d, c: ~d["loading_city_gcid"].isin(c.known_gcids)),
    Rule("UNKNOWN_UNLOADING_CITY", "unloading_city_gcid not in cities",
         lambda d, c: ~d["unloading_city_gcid"].isin(c.known_gcids)),
    Rule("UNKNOWN_CURRENCY", "currency_code not in (643, 840, 978)",
         lambda d, c: ~d["currency_code"].isin(KNOWN_CURRENCIES)),
    Rule("MISSING_FX_RATE", "foreign currency without an exact rate on nearest_rate_date", _missing_fx_rate),
]


@dataclass
class DqResult:
    clean: pd.DataFrame
    quarantine: pd.DataFrame          # price_id, reason_code, details + контекстные столбцы
    stats: dict[str, int]


def validate(prices: pd.DataFrame, ctx: DqContext) -> DqResult:
    bad_any = pd.Series(False, index=prices.index)
    frames = []
    stats: dict[str, int] = {}
    for rule in RULES:
        mask = rule.predicate(prices, ctx).fillna(True).astype(bool)   # NULL в проверяемом поле = брак
        stats[rule.code] = int(mask.sum())
        if mask.any():
            rejected = prices.loc[mask, ["id", "loading_date", "currency_code", "price", "distance"]].copy()
            rejected["reason_code"] = rule.code
            rejected["details"] = rule.description
            frames.append(rejected)
        bad_any |= mask

    quarantine = (
        pd.concat(frames, ignore_index=True).rename(columns={"id": "price_id"})
        if frames
        else pd.DataFrame(columns=["price_id", "loading_date", "currency_code", "price", "distance", "reason_code", "details"])
    )
    stats["rows_checked"] = len(prices)
    stats["rows_rejected"] = int(bad_any.sum())
    stats["rows_clean"] = int((~bad_any).sum())
    return DqResult(clean=prices.loc[~bad_any].copy(), quarantine=quarantine, stats=stats)
