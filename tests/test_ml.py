from datetime import date

import numpy as np
import pandas as pd
import pytest

from ml.features import FEATURES, FORBIDDEN_FEATURES, TARGET, add_calendar_features, assert_no_leakage, build_features
from ml.metrics import regression_report
from ml.models import CatBoostModel, tree_baseline
from ml.split import TimeSplit
from ml.train import CHAMPION_MODEL, clean_training_target

AS_OF = date(2026, 10, 26)


def test_split_boundaries_are_fixed_by_snapshot_date():
    s = TimeSplit.from_as_of(AS_OF)
    assert (s.train_start, s.train_end, s.test_start, s.test_end) == (
        date(2026, 7, 26), date(2026, 10, 5), date(2026, 10, 5), date(2026, 10, 26))


def test_boundary_day_belongs_only_to_test_and_future_is_separate():
    s = TimeSplit.from_as_of(AS_OF)
    days = pd.Series(pd.to_datetime(["2026-07-25", "2026-07-26", "2026-10-04", "2026-10-05", "2026-10-26", "2026-10-27"]))
    m = s.masks(days)
    assert m["train"].tolist() == [False, True, True, False, False, False]
    assert m["test"].tolist() == [False, False, False, True, True, False]
    assert m["future"].tolist() == [False, False, False, False, False, True]
    assert not (m["train"] & m["test"]).any()


def test_champion_is_fixed_to_catboost_not_chosen_on_test():
    # CatBoost — заранее определённая основная модель; тест не должен участвовать в выборе модели.
    assert CHAMPION_MODEL == "catboost"
    assert CHAMPION_MODEL == CatBoostModel(seed=0, params={}).name


def test_cyclic_features_are_continuous_over_new_year():
    df = add_calendar_features(pd.DataFrame({"loading_date": pd.to_datetime(["2025-12-31", "2026-01-01", "2026-07-02"])}))
    dec31, jan1, jul2 = df[["doy_sin", "doy_cos"]].to_numpy()
    assert np.linalg.norm(dec31 - jan1) < 0.05            # соседи на окружности
    assert np.linalg.norm(dec31 - jul2) > 1.9             # противоположная сторона года
    assert np.allclose(df["month_sin"] ** 2 + df["month_cos"] ** 2, 1.0)


def _sample(n=10) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "loading_date": pd.date_range("2026-08-01", periods=n, freq="D"),
        "origin_region": ["г. Москва"] * n, "dest_region": [None] * n,
        "origin_city": ["Москва"] * n, "dest_city": ["Казань"] * n,
        "body_group": ["Тент"] * n, "weight_category": ["FTL"] * n, "volume_category": ["FTL"] * n,
        "price_type": [3] * n, "distance": rng.uniform(100, 2000, n), "total_weight": [20.0] * n,
        "total_volume": [80.0] * n, "origin_lat": [55.7] * n, "origin_lon": [37.6] * n,
        "dest_lat": [55.8] * n, "dest_lon": [49.1] * n,
        "price_rub": rng.uniform(1e4, 2e5, n), "outlier_mark": [False] * n, "price_group_id": [1] * n,
    })


def test_feature_matrix_has_no_target_derivatives():
    X = build_features(_sample())
    assert list(X.columns) == FEATURES
    assert not set(X.columns) & FORBIDDEN_FEATURES
    assert (X["dest_region"] == "NA").all()


@pytest.mark.parametrize("leak", ["price_with_vat", "price_rub_per_km", "avg_price", "price_group_id", "outlier_mark", "profit"])
def test_leakage_guard_rejects_forbidden_columns(leak):
    with pytest.raises(ValueError):
        assert_no_leakage(FEATURES + [leak])


def test_train_cleaning_uses_flag_and_train_statistics_only():
    train = _sample(200)
    train.loc[0, "outlier_mark"] = True
    train.loc[1, "price_rub"] = train.loc[1, "distance"] * 1e5        # абсурдная цена за км, без флага выброса
    kept, info = clean_training_target(train)
    assert 0 not in kept.index and 1 not in kept.index
    assert info["train_rows_flagged_outliers"] == 1 and info["train_rows_iqr_removed"] >= 1


def test_regression_report():
    r = regression_report([100, 200, 300], [110, 190, 300])
    assert r["mae_rub"] == pytest.approx(20 / 3)
    assert r["mape_pct"] == pytest.approx((10 + 5 + 0) / 3)
    assert r["rmse_rub"] == pytest.approx(np.sqrt(200 / 3))
    assert r["r2"] == pytest.approx(1 - 200 / 20000)


def test_log_target_model_predicts_rubles_not_logs():
    df = _sample(60)
    X, y = build_features(df), df[TARGET]
    model = tree_baseline(seed=0, params={"max_depth": 3}, target_transform="log")
    model.fit(X, y, X, y)
    pred = model.predict(X)
    assert pred.min() > 1000                                    # рубли, а не логарифмы (~10)
    assert np.exp(model._predict(X)) == pytest.approx(pred)      # прогноз — экспонента обучения на ln(y)
    assert model._predict(X).max() < 20                         # внутренняя шкала — логарифмическая
