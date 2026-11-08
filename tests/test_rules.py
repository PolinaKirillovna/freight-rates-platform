import pandas as pd
import pytest

from etl.rules import body_group_code, round_half_up, split_city_region, volume_category, weight_category


@pytest.mark.parametrize("weight, expected", [
    (38.0, "G20T"), (30.0, "G20T"), (29.96, "G20T"),          # rounds up to 30.0
    (29.94, "FTL"), (10.0, "FTL"), (9.95, "FTL"),             # 9.95 -> 10.0
    (9.94, "10T"), (5.0, "10T"),
    (4.9, "5T"), (2.5, "5T"), (2.45, "5T"),
    (2.44, "L5T"), (0.5, "L5T"), (0.45, "L5T"),
    (0.44, "OTH"), (0.0, "OTH"),
])
def test_weight_category_boundaries(weight, expected):
    assert weight_category(pd.Series([weight])).iloc[0] == expected


@pytest.mark.parametrize("volume, expected", [(0.0, "OTH"), (0.1, "FTL"), (120.0, "FTL"), (120.01, "OTH"), (170.0, "OTH")])
def test_volume_category_boundaries(volume, expected):
    assert volume_category(pd.Series([volume])).iloc[0] == expected


def test_round_half_up_matches_postgres():
    assert round_half_up(pd.Series([9.95, 29.95, 0.45, 2.449999])).tolist() == [10.0, 30.0, 0.5, 2.4]


def test_body_groups_follow_business_mapping():
    # std_id values as they come from body_types for the ids listed in the business rules
    by_id = {56: 56, 57: 56, 58: 56, 59: 56, 60: 56, 61: 56, 4: 4, 46: 4, 47: 4, 48: 4, 2: 2, 1: 15, 15: 15,
             3: 3, 5: 5, 50: 50, 65: 65, 68: 68, 26: 3}
    codes = body_group_code(pd.Series(list(by_id.values())))
    result = dict(zip(by_id.keys(), codes, strict=False))
    assert {k for k, v in result.items() if v == 56} == {56, 57, 58, 59, 60, 61}
    assert {k for k, v in result.items() if v == 4} == {4, 46, 47, 48}
    assert {k for k, v in result.items() if v == 2} == {2}
    assert {k for k, v in result.items() if v == 15} == {1, 15}
    assert {k for k, v in result.items() if v == 0} == {3, 5, 50, 65, 68, 26}


def test_split_city_region():
    parsed = split_city_region(pd.Series(["Казань, Республика Татарстан", "Москва, г. Москва", "Казань г, Татарстан", "Тверь"]))
    assert parsed["city_name"].tolist() == ["Казань", "Москва", "Казань г", "Тверь"]
    assert parsed["region_name"].tolist() == ["Республика Татарстан", "г. Москва", "Татарстан", None]
