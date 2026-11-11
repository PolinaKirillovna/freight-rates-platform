"""Анализ достижимой точности модели на синтетических данных.

Запуск после полного прогона конвейера (нужны core и mart.ml_price_predictions):

    python -m ml.analysis            # или make analyze

Отчёт печатается в лог и сохраняется в artifacts/ml/accuracy_analysis.md. Разделы:

1. Теоретический минимум MAPE. Генератор источника (sql/source/synthetic.sql) накладывает на цену
   логнормальный шум exp(sigma * Z) с sigma = 0.16 и множители выбросов x4 / x0.25. Полосы
   r_out в (0.010; 0.012] и [0.988; 0.990) получают множитель, но никогда не размечаются ночным
   прогоном, поэтому даже идеальная модель не опустится ниже ~13 % MAPE на тесте, очищенном
   только по флагу outlier_mark.
2. Базовая модель «медиана ячейки маршрута из обучения» — нижняя планка пользы ML.
3. Ошибка лучшей модели в разрезах: полный тест, тест без размеченных выбросов, вклад
   неразмеченных выбросов, последний день снимка, группы кузова, весовые категории, маршруты.
4. Разбор Ridge-регрессии: почему у линейной модели большая относительная ошибка.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common.config import Settings, load_settings
from common.db import connect, fetch_df
from common.logging import get_logger, setup_logging
from ml.metrics import regression_report
from ml.split import TimeSplit

log = get_logger(__name__)

SIGMA = 0.16                  # сигма логнормального шума в генераторе источника
OUTLIER_RATIO_HIGH = 2.2      # факт/прогноз выше — похоже на неразмеченный выброс x4
OUTLIER_RATIO_LOW = 0.45     # факт/прогноз ниже — похоже на неразмеченный выброс x0.25
UNMARKED_BAND_SHARE = 0.002   # доля каждой из двух неразмечаемых полос выбросов

CELL = ["loading_city_gcid", "unloading_city_gcid", "body_group", "weight_category"]
ROUTE = ["loading_city_gcid", "unloading_city_gcid"]

DATA_SQL = """
SELECT f.price_id, f.loading_date, f.loading_city_gcid, f.unloading_city_gcid,
       g.body_group, f.weight_category, f.distance, f.outlier_mark, f.price_rub
FROM core.fact_price f
JOIN core.dim_body_group g ON g.body_group_code = f.primary_body_group_code
WHERE f.is_fact AND f.price_type IN (1, 2, 3, 4, 5, 9) AND NOT f.complex
  AND f.distance > 0 AND f.price_rub > 0
  AND f.loading_date BETWEEN %(s)s AND %(e)s
"""

PREDICTIONS_SQL = """
SELECT p.model_name, p.price_id, p.loading_date, p.loading_city_gcid, p.unloading_city_gcid,
       p.body_group, p.weight_category, p.outlier_mark,
       p.actual_price_rub, p.predicted_price_rub, p.ape_pct,
       (SELECT champion_model FROM ml.model_runs ORDER BY trained_at DESC LIMIT 1) AS champion
FROM mart.ml_price_predictions p
WHERE p.split = 'test' AND p.run_id = (SELECT run_id FROM ml.model_runs ORDER BY trained_at DESC LIMIT 1)
"""


def theoretical_floor(seed: int) -> dict:
    """Минимально достижимый MAPE при точном знании детерминированной части цены."""
    z = np.random.default_rng(seed).standard_normal(2_000_000)
    mape_center = float(np.abs(1 - np.exp(-SIGMA * z)).mean() * 100)
    shifts = np.linspace(0.9, 1.05, 151)
    mape_best = float(min(np.abs(1 - c * np.exp(-SIGMA * z)).mean() for c in shifts) * 100)
    # Неразмечаемые полосы: доля UNMARKED_BAND_SHARE с множителем 4 (ошибка 75 %)
    # и столько же с множителем 0.25 (ошибка 300 %).
    with_bands = float((mape_best / 100 * (1 - 2 * UNMARKED_BAND_SHARE)
                        + UNMARKED_BAND_SHARE * 0.75 + UNMARKED_BAND_SHARE * 3.0) * 100)
    return {"mape_center_pct": round(mape_center, 2), "mape_best_shift_pct": round(mape_best, 2),
            "mape_with_unmarked_bands_pct": round(with_bands, 2)}


def route_median_baseline(train_clean: pd.DataFrame, test_clean: pd.DataFrame) -> dict:
    """Медиана ячейки маршрута из обучения; запас — медиана руб/км маршрута, затем общая."""
    cell_median = train_clean.groupby(CELL)["price_rub"].median()
    km = train_clean["price_rub"] / train_clean["distance"]
    route_km_median = km.groupby([train_clean[c] for c in ROUTE]).median()
    global_km_median = float(km.median())

    joined = test_clean.merge(cell_median.rename("cell_med"), how="left", left_on=CELL, right_index=True)
    by_route = test_clean.merge(route_km_median.rename("km_med"), how="left", left_on=ROUTE, right_index=True)
    prediction = joined["cell_med"].to_numpy()
    prediction = np.where(np.isnan(prediction), by_route["km_med"].to_numpy() * test_clean["distance"].to_numpy(), prediction)
    prediction = np.where(np.isnan(prediction), global_km_median * test_clean["distance"].to_numpy(), prediction)
    report = regression_report(test_clean["price_rub"], prediction)
    report["cell_coverage_pct"] = round(float(joined["cell_med"].notna().mean() * 100), 1)
    return report


def champion_breakdown(preds: pd.DataFrame, split: TimeSplit) -> dict:
    """Ошибка лучшей модели: полный тест, без флага, вклад неразмеченных выбросов, последний день."""
    champion = preds[preds["model_name"] == preds["champion"]].copy()
    clean = champion[~champion["outlier_mark"]].copy()
    ratio = clean["actual_price_rub"] / clean["predicted_price_rub"]
    suspected = (ratio > OUTLIER_RATIO_HIGH) | (ratio < OUTLIER_RATIO_LOW)
    last_day = clean["loading_date"] == split.test_end

    return {
        "champion": str(champion["champion"].iloc[0]),
        "test_full_rows": len(champion),
        "test_full_mape_pct": round(float(champion["ape_pct"].mean()), 2),
        "test_clean_rows": len(clean),
        "test_clean_mape_pct": round(float(clean["ape_pct"].mean()), 2),
        "suspected_unmarked_rows": int(suspected.sum()),
        "suspected_unmarked_share_pct": round(float(suspected.mean() * 100), 2),
        "suspected_unmarked_mape_contribution_pp": round(float(clean.loc[suspected, "ape_pct"].sum() / len(clean)), 2),
        "test_clean_wo_suspected_mape_pct": round(float(clean.loc[~suspected, "ape_pct"].mean()), 2),
        "last_day_rows": int(last_day.sum()),
        "last_day_mape_pct": round(float(clean.loc[last_day, "ape_pct"].mean()), 2),
        "other_days_mape_pct": round(float(clean.loc[~last_day, "ape_pct"].mean()), 2),
        "mape_by_body_group": clean.groupby("body_group")["ape_pct"].mean().round(2).to_dict(),
        "mape_by_weight_category": clean.groupby("weight_category")["ape_pct"].mean().round(2).to_dict(),
    }


def worst_routes(preds: pd.DataFrame, min_rows: int = 30) -> pd.DataFrame:
    champion = preds[(preds["model_name"] == preds["champion"]) & ~preds["outlier_mark"]]
    routes = champion.groupby(ROUTE)["ape_pct"].agg(rows="size", mape_pct="mean")
    return routes[routes["rows"] >= min_rows].sort_values("mape_pct").round(2)


def ridge_price_deciles(preds: pd.DataFrame) -> pd.DataFrame:
    """Ошибка Ridge-регрессии по децилям фактической цены: где именно резко возрастает MAPE."""
    ridge = preds[(preds["model_name"] == "ridge") & ~preds["outlier_mark"]].copy()
    if ridge.empty:
        return pd.DataFrame()
    ridge["decile"] = pd.qcut(ridge["actual_price_rub"], 10, labels=False) + 1
    table = ridge.groupby("decile").agg(price_from=("actual_price_rub", "min"),
                                        price_to=("actual_price_rub", "max"),
                                        mape_pct=("ape_pct", "mean")).round(0)
    table["ape_share_pct"] = (ridge.groupby("decile")["ape_pct"].sum() / ridge["ape_pct"].sum() * 100).round(1)
    return table


def run_analysis(settings: Settings) -> dict:
    split = TimeSplit.from_as_of(settings.as_of)
    with connect(settings.dwh) as conn:
        data = fetch_df(conn, DATA_SQL, {"s": split.train_start, "e": split.test_end})
        preds = fetch_df(conn, PREDICTIONS_SQL)
    if preds.empty:
        raise SystemExit("Нет прогнозов: сначала выполните полный прогон (python -m etl.cli all)")

    data["loading_date"] = pd.to_datetime(data["loading_date"]).dt.date
    preds["loading_date"] = pd.to_datetime(preds["loading_date"]).dt.date
    for frame, cols in ((data, ("price_rub", "distance")),
                        (preds, ("actual_price_rub", "predicted_price_rub", "ape_pct"))):
        for col in cols:
            frame[col] = frame[col].astype(float)

    masks = split.masks(pd.Series(data["loading_date"]))
    train_clean = data[masks["train"].to_numpy() & ~data["outlier_mark"]]
    test_clean = data[masks["test"].to_numpy() & ~data["outlier_mark"]]

    floor = theoretical_floor(settings.random_seed)
    baseline = route_median_baseline(train_clean, test_clean)
    breakdown = champion_breakdown(preds, split)
    routes = worst_routes(preds)
    deciles = ridge_price_deciles(preds)

    report = _render_markdown(floor, baseline, breakdown, routes, deciles)
    out = settings.artifacts_dir / "ml" / "accuracy_analysis.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    summary = {"floor": floor, "baseline_mape_pct": round(baseline["mape_pct"], 2), **{
        k: v for k, v in breakdown.items() if not isinstance(v, dict)}, "report": str(out)}
    log.info("accuracy analysis finished", extra=summary)
    print(report)
    return summary


def _markdown_table(frame: pd.DataFrame) -> str:
    """Таблица в разметке Markdown без дополнительных зависимостей."""
    reset = frame.reset_index()
    header = "| " + " | ".join(map(str, reset.columns)) + " |"
    sep = "|" + "---|" * len(reset.columns)
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in reset.itertuples(index=False)]
    return "\n".join([header, sep, *rows])


def _render_markdown(floor: dict, baseline: dict, breakdown: dict,
                     routes: pd.DataFrame, deciles: pd.DataFrame) -> str:
    baseline_mae = f"{baseline['mae_rub']:,.0f}".replace(",", " ")
    lines = [
        "# Достижимая точность на синтетических данных",
        "",
        "## 1. Теоретический минимум MAPE",
        f"- шум генератора exp({SIGMA}·Z): идеальная модель получает **{floor['mape_center_pct']} %**"
        f" (с оптимальным сдвигом прогноза — {floor['mape_best_shift_pct']} %);",
        f"- полосы выбросов x4/x0.25, которые никогда не размечаются ({UNMARKED_BAND_SHARE * 200:.1f} % строк),"
        f" поднимают этот минимум до **{floor['mape_with_unmarked_bands_pct']} %**.",
        "",
        "## 2. Базовая модель «медиана ячейки маршрута из обучения»",
        f"- MAPE **{baseline['mape_pct']:.2f} %**, R² {baseline['r2']:.3f}, MAE {baseline_mae} ₽"
        f" (точная ячейка нашлась для {baseline['cell_coverage_pct']} % строк теста).",
        "",
        f"## 3. Лучшая модель ({breakdown['champion']}) на тесте",
        f"- полный тест ({breakdown['test_full_rows']} строк): MAPE **{breakdown['test_full_mape_pct']} %**;",
        f"- без размеченных выбросов ({breakdown['test_clean_rows']} строк): MAPE **{breakdown['test_clean_mape_pct']} %**;",
        f"- подозрение на неразмеченный выброс (факт/прогноз > {OUTLIER_RATIO_HIGH} или < {OUTLIER_RATIO_LOW}):"
        f" {breakdown['suspected_unmarked_rows']} строк ({breakdown['suspected_unmarked_share_pct']} %),"
        f" их вклад {breakdown['suspected_unmarked_mape_contribution_pp']} п. п.;"
        f" без них MAPE **{breakdown['test_clean_wo_suspected_mape_pct']} %**;",
        f"- последний день снимка (разметка выбросов не прошла): {breakdown['last_day_rows']} строк,"
        f" MAPE {breakdown['last_day_mape_pct']} % против {breakdown['other_days_mape_pct']} % в остальные дни.",
        "",
        "MAPE по группам кузова: " + ", ".join(f"{k} {v}" for k, v in breakdown["mape_by_body_group"].items()) + ".",
        "",
        "MAPE по весовым категориям: " + ", ".join(f"{k} {v}" for k, v in breakdown["mape_by_weight_category"].items()) + ".",
        "",
        "Маршруты (не меньше 30 строк теста), лучшие и худшие по MAPE:",
        "",
        _markdown_table(pd.concat([routes.head(3), routes.tail(3)])),
        "",
        "## 4. Почему у Ridge-регрессии большая относительная ошибка",
        "Цена в данных мультипликативна (ставка за км × расстояние × сезон × весовой множитель × шум),",
        "а модель линейна и обучается на абсолютных рублях: на дешёвых рейсах её относительная ошибка особенно велика,",
        "и MAPE, нормированный на маленький знаменатель, резко возрастает. Ошибка по децилям фактической цены:",
        "",
        _markdown_table(deciles) if not deciles.empty else "(прогнозы Ridge-регрессии не найдены)",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    settings = load_settings()
    setup_logging(settings.log_level)
    run_analysis(settings)
