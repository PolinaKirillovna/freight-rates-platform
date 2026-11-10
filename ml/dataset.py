"""Набор данных для обучения и скоринга из слоя core."""
from __future__ import annotations

from datetime import date

import pandas as pd

from common.db import fetch_df

DATASET_SQL = """
SELECT f.price_id,
       f.loading_date,
       f.loading_city_gcid,
       f.unloading_city_gcid,
       f.price_type,
       f.distance,
       f.total_weight,
       f.total_volume,
       g.body_group,
       f.weight_category,
       f.volume_category,
       f.outlier_mark,
       f.price_rub,
       lc.city_name   AS origin_city,
       lc.region_name AS origin_region,
       lc.latitude    AS origin_lat,
       lc.longitude   AS origin_lon,
       uc.city_name   AS dest_city,
       uc.region_name AS dest_region,
       uc.latitude    AS dest_lat,
       uc.longitude   AS dest_lon
FROM core.fact_price f
JOIN core.dim_city lc       ON lc.gc_id = f.loading_city_gcid      -- dim_city уже без дублей по gc_id
JOIN core.dim_city uc       ON uc.gc_id = f.unloading_city_gcid
JOIN core.dim_body_group g  ON g.body_group_code = f.primary_body_group_code
WHERE f.is_fact
  AND f.price_type IN (1, 2, 3, 4, 5, 9)
  AND NOT f.complex
  AND f.distance > 0
  AND f.price_rub > 0
  AND f.loading_date >= %(start)s
ORDER BY f.loading_date, f.price_id
"""


def load_dataset(conn, start: date) -> pd.DataFrame:
    df = fetch_df(conn, DATASET_SQL, {"start": start})
    df["loading_date"] = pd.to_datetime(df["loading_date"])
    for col in ("distance", "total_weight", "total_volume", "price_rub", "origin_lat", "origin_lon", "dest_lat", "dest_lon"):
        df[col] = df[col].astype("float64")
    df["outlier_mark"] = df["outlier_mark"].astype(bool)
    return df
