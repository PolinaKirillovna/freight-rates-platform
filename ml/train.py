"""Train the models on a chronological split, evaluate, and batch-score test + future deals.

Run: python -m etl.cli ml   (or the `ml` task of the Airflow DAG)
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from common.config import Settings
from common.db import connect, copy_df
from common.logging import get_logger
from ml.dataset import load_dataset
from ml.features import FEATURES, TARGET, build_features
from ml.metrics import regression_report
from ml.models import CatBoostModel, ridge_baseline, tree_baseline
from ml.split import TimeSplit

log = get_logger(__name__)

VALIDATION_DAYS = 14          # tail of train used only for CatBoost early stopping
IQR_K = 3.0                   # train-only fence on ln(price per km)

PREDICTION_COLUMNS = [
    "price_id", "model_name", "split", "loading_date", "loading_city_gcid", "unloading_city_gcid", "body_group",
    "weight_category", "distance", "outlier_mark", "actual_price_rub", "predicted_price_rub", "abs_error_rub",
    "ape_pct", "is_champion", "run_id", "scored_at",
]


def clean_training_target(train: pd.DataFrame, k: float = IQR_K) -> tuple[pd.DataFrame, dict]:
    """Drop flagged outliers, then apply an IQR fence on ln(price/km) computed on train only."""
    unflagged = train[~train["outlier_mark"]]
    log_km = np.log(unflagged[TARGET] / unflagged["distance"])
    q1, q3 = np.percentile(log_km, [25, 75])
    low, high = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
    kept = unflagged[(log_km >= low) & (log_km <= high)]
    return kept, {
        "train_rows_raw": len(train),
        "train_rows_flagged_outliers": int(train["outlier_mark"].sum()),
        "train_rows_iqr_removed": len(unflagged) - len(kept),
        "train_iqr_fence_rub_per_km": [round(float(np.exp(low)), 2), round(float(np.exp(high)), 2)],
    }


def train_and_score(settings: Settings) -> dict:
    split = TimeSplit.from_as_of(settings.as_of)
    run_id = uuid.uuid4()
    with connect(settings.dwh) as conn:
        data = load_dataset(conn, split.train_start)

    masks = split.masks(data["loading_date"])
    train_raw, test, future = data[masks["train"]], data[masks["test"]], data[masks["future"]]
    train, cleaning = clean_training_target(train_raw)
    test_clean = test[~test["outlier_mark"]]
    log.info("dataset split", extra={**split.as_dict(), **cleaning, "test_rows": len(test),
                                     "test_rows_clean": len(test_clean), "future_rows": len(future)})

    X_train, y_train = build_features(train), train[TARGET]
    val_start = pd.Timestamp(split.train_end - timedelta(days=VALIDATION_DAYS))
    val_mask = train["loading_date"] >= val_start
    X_val, y_val = X_train[val_mask.to_numpy()], y_train[val_mask.to_numpy()]

    models = [CatBoostModel(seed=settings.random_seed), ridge_baseline(settings.random_seed), tree_baseline(settings.random_seed)]
    X_test, X_test_clean, X_future = build_features(test), build_features(test_clean), build_features(future)

    metrics_rows, importance_rows, results = [], [], {}
    for model in models:
        model.fit(X_train, y_train, X_val, y_val)
        per_split = {
            "train": regression_report(y_train, model.predict(X_train)),
            "test": regression_report(test_clean[TARGET], model.predict(X_test_clean)),
            "test_unfiltered": regression_report(test[TARGET], model.predict(X_test)),
        }
        results[model.name] = per_split
        for split_name, report in per_split.items():
            metrics_rows.append({"run_id": str(run_id), "model_name": model.name, "split": split_name, **report})
        for feature, value in model.feature_importance(FEATURES).items():
            importance_rows.append({"run_id": str(run_id), "model_name": model.name, "feature": feature, "importance": value})
        log.info("model evaluated", extra={"model": model.name, **{f"test_{k}": round(v, 4) for k, v in per_split["test"].items()}})

    champion = min(results, key=lambda name: results[name]["test"]["mape_pct"])
    scored_at = datetime.now(UTC)
    predictions = pd.concat(
        [_prediction_frame(m, test, X_test, "test", run_id, scored_at, m.name == champion) for m in models]
        + [_prediction_frame(m, future, X_future, "future", run_id, scored_at, m.name == champion) for m in models],
        ignore_index=True,
    )

    params = {"split": split.as_dict(), "cleaning": cleaning, "features": FEATURES,
              "validation_days": VALIDATION_DAYS, "models": {m.name: m.describe() for m in models}}
    with connect(settings.dwh) as conn:
        conn.execute(
            "INSERT INTO ml.model_runs (run_id, trained_at, as_of, train_start, train_end_excl, test_start, test_end_incl, "
            "champion_model, params) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, scored_at, split.as_of, split.train_start, split.train_end, split.test_start, split.test_end,
             champion, json.dumps(params, default=str)),
        )
        copy_df(conn, "ml.model_metrics", pd.DataFrame(metrics_rows),
                ["run_id", "model_name", "split", "n_rows", "r2", "mape_pct", "rmse_rub", "mae_rub"])
        copy_df(conn, "ml.feature_importance", pd.DataFrame(importance_rows), ["run_id", "model_name", "feature", "importance"])
        # Predictions are a snapshot: replace them atomically, so re-runs never duplicate rows.
        conn.execute("DELETE FROM mart.ml_price_predictions")
        n_pred = copy_df(conn, "mart.ml_price_predictions", predictions, PREDICTION_COLUMNS)
        conn.commit()

    _save_artifacts(settings, run_id, models, results, champion, params)
    summary = {
        "run_id": str(run_id),
        "champion": champion,
        "predictions_written": n_pred,
        "test_metrics": {name: {k: round(v, 4) for k, v in r["test"].items()} for name, r in results.items()},
    }
    log.info("ml run finished", extra=summary)
    return summary


def _prediction_frame(model, rows: pd.DataFrame, X: pd.DataFrame, split_name: str, run_id, scored_at, is_champion: bool) -> pd.DataFrame:
    pred = model.predict(X)
    out = rows[["price_id", "loading_date", "loading_city_gcid", "unloading_city_gcid", "body_group",
                "weight_category", "distance", "outlier_mark"]].copy()
    out["loading_date"] = out["loading_date"].dt.date
    out["model_name"] = model.name
    out["split"] = split_name
    out["predicted_price_rub"] = pred
    if split_name == "test":
        actual = rows[TARGET].to_numpy()
        out["actual_price_rub"] = actual
        out["abs_error_rub"] = np.abs(actual - pred)
        out["ape_pct"] = np.abs(actual - pred) / actual * 100
    else:  # future deals: the source may carry a planned price, but it is not a realised fact
        out["actual_price_rub"] = np.nan
        out["abs_error_rub"] = np.nan
        out["ape_pct"] = np.nan
    out["is_champion"] = is_champion
    out["run_id"] = str(run_id)
    out["scored_at"] = scored_at.isoformat()
    return out


def _save_artifacts(settings: Settings, run_id, models, results, champion, params) -> None:
    out_dir = settings.artifacts_dir / "ml"
    out_dir.mkdir(parents=True, exist_ok=True)
    for model in models:
        if isinstance(model, CatBoostModel):
            model.model.save_model(str(out_dir / "catboost_model.cbm"))
    report = {"run_id": str(run_id), "champion": champion, "metrics": results, "params": params}
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    lines = ["| Модель | Выборка | Строк | R² | MAPE, % | RMSE, ₽ | MAE, ₽ |", "|---|---|---:|---:|---:|---:|---:|"]
    for name, per_split in results.items():
        for split_name in ("test", "test_unfiltered"):
            r = per_split[split_name]
            lines.append(f"| {name} | {split_name} | {r['n_rows']} | {r['r2']:.3f} | {r['mape_pct']:.2f} | "
                         f"{r['rmse_rub']:,.0f} | {r['mae_rub']:,.0f} |".replace(",", " "))
    (out_dir / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
