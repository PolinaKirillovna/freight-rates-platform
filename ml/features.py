"""Конвейер признаков для модели цены рейса.

Разрешены только атрибуты, известные до назначения цены рейса. Всё производное от целевой
переменной (цена с НДС, цена за км / за тонну той же сделки, маржинальные компоненты, ночные
групповые агрегаты из price_group_stats, флаг выброса) запрещено и проверяется assert_no_leakage.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "price_rub"

CATEGORICAL_FEATURES = [
    "origin_region", "dest_region", "origin_city", "dest_city",
    "body_group", "weight_category", "volume_category", "price_type",
]
NUMERIC_FEATURES = [
    "distance", "total_weight", "total_volume",
    "origin_lat", "origin_lon", "dest_lat", "dest_lon",
    "year", "month", "day_of_week", "day_of_year",
    "month_sin", "month_cos", "dow_sin", "dow_cos", "doy_sin", "doy_cos",
]
FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES

FORBIDDEN_FEATURES = {
    "price", "price_rub", "price_original", "price_with_vat", "price_rub_per_km", "price_per_km",
    "price_per_ton", "fx_rate", "customer_cost", "transportation_cost", "carrier_cost", "profit",
    "avg_price", "min_km_price", "max_km_price", "price_group_id", "outliers_amount",
    "clear_items_amount", "outlier_mark",
}

PERIODS = {"month": 12.0, "dow": 7.0, "doy": 365.25}


def add_calendar_features(df: pd.DataFrame, date_col: str = "loading_date") -> pd.DataFrame:
    out = df.copy()
    d = pd.to_datetime(out[date_col])
    out["year"] = d.dt.year.astype("int32")
    out["month"] = d.dt.month.astype("int32")
    out["day_of_week"] = d.dt.dayofweek.astype("int32")          # понедельник = 0
    out["day_of_year"] = d.dt.dayofyear.astype("int32")
    for name, t, period in (("month", out["month"], PERIODS["month"]),
                            ("dow", out["day_of_week"], PERIODS["dow"]),
                            ("doy", out["day_of_year"], PERIODS["doy"])):
        angle = 2 * np.pi * t / period
        out[f"{name}_sin"] = np.sin(angle)
        out[f"{name}_cos"] = np.cos(angle)
    return out


def assert_no_leakage(columns) -> None:
    leaked = sorted(set(columns) & FORBIDDEN_FEATURES)
    if leaked:
        raise ValueError(f"Target leakage: forbidden features in the model input: {leaked}")


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Вернуть матрицу признаков. NaN в категориях становится литералом 'NA' (константа, а не обученная статистика)."""
    enriched = add_calendar_features(df)
    X = enriched[FEATURES].copy()
    for col in CATEGORICAL_FEATURES:
        X[col] = X[col].astype("string").fillna("NA").astype(str)
    for col in NUMERIC_FEATURES:
        X[col] = X[col].astype("float64")
    assert_no_leakage(X.columns)
    return X
