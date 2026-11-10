"""Доменные правила, общие для ETL, витрин и ML: категории, группы кузова, коды валют."""
from __future__ import annotations

import numpy as np
import pandas as pd

RUB, USD, EUR = "643", "840", "978"
KNOWN_CURRENCIES = {RUB, USD, EUR}
FACT_PRICE_TYPES = (1, 2, 3, 4, 5, 9)
CARRIER_PRICE_TYPES = (3, 9)
RUB_MIN_VALID_PRICE = 1000.0          # проверка источника: рублёвая цена <= 1000 — не цена рейса

# Стандартизированные группы кузова: код = std_id родительского типа; 0 — «Прочие», -1 — «Не указан».
BODY_GROUPS = {56: "Тент", 4: "Рефрижератор", 2: "Изотерм", 15: "Борт", 0: "Прочие", -1: "Не указан"}
STANDARD_GROUP_CODES = {56, 4, 2, 15}
OTHER_GROUP, UNKNOWN_GROUP = 0, -1

WEIGHT_BINS = [  # (нижняя граница включительно, категория), проверяется сверху вниз по весу, округлённому до 0,1 т
    (30.0, "G20T"),
    (10.0, "FTL"),
    (5.0, "10T"),
    (2.5, "5T"),
    (0.5, "L5T"),
]


def round_half_up(values: pd.Series, decimals: int = 1) -> pd.Series:
    """Округление как round(numeric, n) в PostgreSQL: половина — от нуля, устойчиво к шуму float (9,95 -> 10,0)."""
    factor = 10 ** decimals
    arr = values.astype("float64").to_numpy()
    rounded = np.sign(arr) * np.floor(np.abs(arr) * factor + 0.5 + 1e-9) / factor
    return pd.Series(rounded, index=values.index)


def weight_category(weight_t: pd.Series) -> pd.Series:
    w = round_half_up(weight_t, 1)
    conditions = [w >= lower for lower, _ in WEIGHT_BINS]
    choices = [cat for _, cat in WEIGHT_BINS]
    return pd.Series(np.select(conditions, choices, default="OTH"), index=weight_t.index)


def volume_category(volume_m3: pd.Series) -> pd.Series:
    v = volume_m3.astype("float64")
    return pd.Series(np.where((v > 0) & (v <= 120), "FTL", "OTH"), index=volume_m3.index)


def body_group_code(std_id: pd.Series) -> pd.Series:
    """Сопоставить body_types.std_id с кодом бизнес-группы; всё нестандартное — «Прочие»."""
    std = pd.to_numeric(std_id, errors="coerce")
    return pd.Series(np.where(std.isin(list(STANDARD_GROUP_CODES)), std, OTHER_GROUP), index=std_id.index).astype("int16")


def split_city_region(full_name_local: pd.Series) -> pd.DataFrame:
    """'Казань, Республика Татарстан' -> город 'Казань', регион 'Республика Татарстан'."""
    parts = full_name_local.fillna("").str.split(",", n=1, expand=True)
    if parts.shape[1] == 1:
        parts[1] = None
    city = parts[0].str.strip().replace("", None)
    region = parts[1].str.strip().replace("", None)
    return pd.DataFrame({"city_name": city, "region_name": region}, index=full_name_local.index)
