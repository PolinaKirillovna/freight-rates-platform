from datetime import date

import pandas as pd
import pytest

from etl import dq
from etl.transform import body_type_dimension, build_price_dates, enrich_prices, resolve_body_groups

D = date(2026, 9, 12)


def make_prices(**overrides) -> pd.DataFrame:
    base = {
        "id": 1, "loading_date": date(2026, 9, 15), "loading_city_gcid": 1001, "unloading_city_gcid": 1003,
        "distance": 897.0, "total_weight": 20.0, "total_volume": 82.0, "body_type": 56, "price": 84500.0,
        "currency_code": "643", "nearest_rate_date": D, "weight_category": "FTL", "volume_category": "FTL",
    }
    rows = overrides.pop("rows", [{}])
    return pd.DataFrame([{**base, **{"id": i + 1}, **row} for i, row in enumerate(rows)])


CTX = dq.DqContext(known_gcids={1001, 1003}, rate_keys={("840", D), ("978", D), ("643", D)})
RATES = pd.DataFrame({"currency_code": ["643", "840", "978"], "rate_date": [D, D, D], "rate": [1.0, 90.0, 100.0]})


@pytest.mark.parametrize("row, reason", [
    ({"price": 0.0}, "NON_POSITIVE_PRICE"),
    ({"price": -5.0}, "NON_POSITIVE_PRICE"),
    ({"price": 1000.0}, "RUB_PRICE_TOO_LOW"),
    ({"distance": 0.0}, "NON_POSITIVE_DISTANCE"),
    ({"total_weight": -1.0}, "NEGATIVE_WEIGHT"),
    ({"total_volume": -0.5}, "NEGATIVE_VOLUME"),
    ({"loading_city_gcid": 9999}, "UNKNOWN_LOADING_CITY"),
    ({"unloading_city_gcid": 9999}, "UNKNOWN_UNLOADING_CITY"),
    ({"currency_code": "156"}, "UNKNOWN_CURRENCY"),
    ({"currency_code": "840", "price": 2480.0, "nearest_rate_date": None}, "MISSING_FX_RATE"),
    ({"currency_code": "978", "price": 900.0, "nearest_rate_date": date(2026, 9, 14)}, "MISSING_FX_RATE"),  # понедельник: курса нет
])
def test_invalid_rows_go_to_quarantine_with_reason(row, reason):
    result = dq.validate(make_prices(rows=[row]), CTX)
    assert result.clean.empty
    assert reason in set(result.quarantine["reason_code"])


def test_valid_rows_pass_and_foreign_small_prices_are_not_rub_rule():
    prices = make_prices(rows=[{}, {"currency_code": "840", "price": 900.0}, {"loading_city_gcid": 1003, "unloading_city_gcid": 1003}])
    result = dq.validate(prices, CTX)
    assert len(result.clean) == 3              # маршрут внутри города сохраняется, цена 900 USD валидна
    assert result.quarantine.empty


def test_row_failing_several_rules_gets_one_record_per_reason():
    result = dq.validate(make_prices(rows=[{"price": 0.0, "distance": 0.0}]), CTX)
    assert sorted(result.quarantine["reason_code"]) == ["NON_POSITIVE_DISTANCE", "NON_POSITIVE_PRICE"]
    assert result.stats["rows_rejected"] == 1


def test_currency_conversion_uses_exact_rate_and_fills_categories():
    prices = make_prices(rows=[{}, {"currency_code": "840", "price": 1000.0, "weight_category": None, "total_weight": 9.95},
                               {"currency_code": "978", "price": 1000.0, "volume_category": None, "total_volume": 0.0}])
    out = enrich_prices(prices, RATES)
    assert out["price_rub"].tolist() == [84500.0, 90000.0, 100000.0]
    assert out.loc[1, "weight_category"] == "FTL" and bool(out.loc[1, "weight_category_filled"])
    assert out.loc[2, "volume_category"] == "OTH" and bool(out.loc[2, "volume_category_filled"])
    assert out.loc[0, "price_rub_per_km"] == pytest.approx(84500 / 897)


def test_enrichment_refuses_rows_without_rate():
    prices = make_prices(rows=[{"currency_code": "840", "nearest_rate_date": date(2026, 9, 14)}])
    with pytest.raises(ValueError):
        enrich_prices(prices, RATES)


BODY_TYPES = pd.DataFrame({
    "id": [1, 2, 4, 15, 46, 56, 57, 61, 50],
    "name": ["бортовая", "изотерм", "реф", "борт", "реф мульти", "тент", "тент бок", "тент полн", "самосвал"],
    "std_id": [15, 2, 4, 15, 4, 56, 56, 56, 50],
})


def test_body_groups_are_deduplicated_and_fall_back():
    prices = make_prices(rows=[{"body_type": 56}, {"body_type": 56}, {"body_type": None}, {"body_type": 4}, {"body_type": 777}])
    links = pd.DataFrame({
        "price_id": [1, 1, 1, 2, 2, 4],
        "body_type_id": [56, 61, 15, 57, 1, 46],   # 56+61 -> одна строка «Тент»; 15 -> «Борт»
    })
    groups, primary = resolve_body_groups(prices, links, body_type_dimension(BODY_TYPES))
    as_sets = groups.groupby("price_id")["body_group_code"].apply(set).to_dict()
    assert as_sets == {1: {56, 15}, 2: {56, 15}, 3: {-1}, 4: {4}, 5: {0}}
    assert not groups.duplicated().any()
    assert primary.to_dict() == {1: 56, 2: 56, 3: -1, 4: 4, 5: 0}


def test_price_dates_fallback_to_loading_date():
    prices = make_prices(rows=[{}, {}])
    raw_dates = pd.DataFrame({"price_id": [1, 1], "price_date": [date(2026, 9, 15), date(2026, 10, 15)]})
    dates, fallback = build_price_dates(prices, raw_dates)
    assert fallback == 1
    assert sorted(dates.loc[dates["price_id"] == 2, "price_date"]) == [date(2026, 9, 15)]
