"""Набор моделей: CatBoost (основная) и два бейзлайна на scikit-learn.

Каждый шаг предобработки, обучающий статистики (импьютер, масштабатор, кодировщики), живёт внутри
Pipeline и обучается только на обучающей выборке.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
from sklearn.tree import DecisionTreeRegressor

from ml.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES


class RegressionModel(ABC):
    """Общий интерфейс моделей цены: обучение на train, предсказание, важность признаков, описание.

    Шаблонные методы fit/predict применяют target_transform: при "log" модель учится на np.log(y),
    а прогноз возвращается в рублях как np.exp(predict). Метрики всегда считаются в рублях.
    """

    name: str
    target_transform: str = "none"

    def fit(self, X: pd.DataFrame, y: pd.Series, X_val: pd.DataFrame, y_val: pd.Series) -> RegressionModel:
        self._fit(X, self._encode(y), X_val, self._encode(y_val))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._decode(self._predict(X))

    def _encode(self, y: pd.Series) -> pd.Series:
        return np.log(y) if self.target_transform == "log" else y

    def _decode(self, pred: np.ndarray) -> np.ndarray:
        return np.exp(pred) if self.target_transform == "log" else pred

    @abstractmethod
    def _fit(self, X: pd.DataFrame, y: pd.Series, X_val: pd.DataFrame, y_val: pd.Series) -> None:
        ...

    @abstractmethod
    def _predict(self, X: pd.DataFrame) -> np.ndarray:
        ...

    @abstractmethod
    def feature_importance(self, columns) -> dict[str, float]:
        ...

    @abstractmethod
    def describe(self) -> dict:
        ...


@dataclass
class CatBoostModel(RegressionModel):
    seed: int
    params: dict          # гиперпараметры берутся из configs/ml.yaml (см. ml.config)
    target_transform: str = "none"
    name: str = "catboost"
    model: CatBoostRegressor | None = None
    best_iterations: int | None = None

    def _fit(self, X: pd.DataFrame, y: pd.Series, X_val: pd.DataFrame, y_val: pd.Series) -> None:
        cat_idx = [X.columns.get_loc(c) for c in CATEGORICAL_FEATURES]
        # 1) ранняя остановка на последних неделях train (валидация строго после обучающей части)
        probe = CatBoostRegressor(**self.params, random_seed=self.seed, verbose=False, allow_writing_files=False)
        fit_mask = ~X.index.isin(X_val.index)
        probe.fit(Pool(X[fit_mask], y[fit_mask], cat_features=cat_idx),
                  eval_set=Pool(X_val, y_val, cat_features=cat_idx), use_best_model=True)
        self.best_iterations = int(probe.get_best_iteration()) + 1
        # 2) переобучение на всём окне train с найденным числом деревьев
        final_params = {k: v for k, v in self.params.items() if k not in {"od_type", "od_wait"}}
        final_params["iterations"] = self.best_iterations
        self.model = CatBoostRegressor(**final_params, random_seed=self.seed, verbose=False, allow_writing_files=False)
        self.model.fit(Pool(X, y, cat_features=cat_idx))

    def _predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict(X)

    def feature_importance(self, columns) -> dict[str, float]:
        return dict(zip(columns, self.model.get_feature_importance(), strict=False))

    def describe(self) -> dict:
        return {**self.params, "target_transform": self.target_transform,
                "best_iterations": self.best_iterations, "random_seed": self.seed}


@dataclass
class SklearnModel(RegressionModel):
    name: str
    pipeline: Pipeline
    target_transform: str = "none"

    def _fit(self, X: pd.DataFrame, y: pd.Series, *_ignored) -> None:
        self.pipeline.fit(X, y)

    def _predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.pipeline.predict(X)

    def feature_importance(self, columns) -> dict[str, float]:
        est = self.pipeline.named_steps["model"]
        if not hasattr(est, "feature_importances_"):
            return {}
        names = self.pipeline.named_steps["prep"].get_feature_names_out()
        totals: dict[str, float] = {}
        for raw_name, value in zip(names, est.feature_importances_, strict=False):
            original = raw_name.split("__", 1)[1]
            totals[original] = totals.get(original, 0.0) + float(value) * 100
        return totals

    def describe(self) -> dict:
        return {"estimator": repr(self.pipeline.named_steps["model"]), "target_transform": self.target_transform}


def ridge_baseline(seed: int, params: dict, target_transform: str = "none") -> SklearnModel:
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), CATEGORICAL_FEATURES),
    ])
    return SklearnModel("ridge", Pipeline([("prep", prep), ("model", Ridge(**params, random_state=seed))]), target_transform)


def tree_baseline(seed: int, params: dict, target_transform: str = "none") -> SklearnModel:
    prep = ColumnTransformer([
        ("num", SimpleImputer(strategy="median"), NUMERIC_FEATURES),
        ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), CATEGORICAL_FEATURES),
    ])
    tree = DecisionTreeRegressor(**params, random_state=seed)
    return SklearnModel("decision_tree", Pipeline([("prep", prep), ("model", tree)]), target_transform)
