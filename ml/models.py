"""Model zoo: CatBoost (main model) and two scikit-learn baselines.

Every preprocessing step that learns statistics (imputer, scaler, encoders) lives inside a Pipeline
and is fitted on the training split only.
"""
from __future__ import annotations

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


@dataclass
class CatBoostModel:
    seed: int
    params: dict          # hyperparameters come from configs/ml.yaml (see ml.config)
    name: str = "catboost"
    model: CatBoostRegressor | None = None
    best_iterations: int | None = None

    def fit(self, X: pd.DataFrame, y: pd.Series, X_val: pd.DataFrame, y_val: pd.Series) -> CatBoostModel:
        cat_idx = [X.columns.get_loc(c) for c in CATEGORICAL_FEATURES]
        # 1) early stopping on the last weeks of train (validation strictly after the fitting part)
        probe = CatBoostRegressor(**self.params, random_seed=self.seed, verbose=False, allow_writing_files=False)
        fit_mask = ~X.index.isin(X_val.index)
        probe.fit(Pool(X[fit_mask], y[fit_mask], cat_features=cat_idx),
                  eval_set=Pool(X_val, y_val, cat_features=cat_idx), use_best_model=True)
        self.best_iterations = int(probe.get_best_iteration()) + 1
        # 2) refit on the full train window with the found number of trees
        final_params = {k: v for k, v in self.params.items() if k not in {"od_type", "od_wait"}}
        final_params["iterations"] = self.best_iterations
        self.model = CatBoostRegressor(**final_params, random_seed=self.seed, verbose=False, allow_writing_files=False)
        self.model.fit(Pool(X, y, cat_features=cat_idx))
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict(X)

    def feature_importance(self, columns) -> dict[str, float]:
        return dict(zip(columns, self.model.get_feature_importance(), strict=False))

    def describe(self) -> dict:
        return {**self.params, "best_iterations": self.best_iterations, "random_seed": self.seed}


@dataclass
class SklearnModel:
    name: str
    pipeline: Pipeline

    def fit(self, X: pd.DataFrame, y: pd.Series, *_ignored) -> SklearnModel:
        self.pipeline.fit(X, y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
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
        return {"estimator": repr(self.pipeline.named_steps["model"])}


def ridge_baseline(seed: int, params: dict) -> SklearnModel:
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), CATEGORICAL_FEATURES),
    ])
    return SklearnModel("ridge", Pipeline([("prep", prep), ("model", Ridge(**params, random_state=seed))]))


def tree_baseline(seed: int, params: dict) -> SklearnModel:
    prep = ColumnTransformer([
        ("num", SimpleImputer(strategy="median"), NUMERIC_FEATURES),
        ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), CATEGORICAL_FEATURES),
    ])
    tree = DecisionTreeRegressor(**params, random_state=seed)
    return SklearnModel("decision_tree", Pipeline([("prep", prep), ("model", tree)]))
