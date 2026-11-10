"""Load ML hyperparameters from configs/ml.yaml (override the path with ML_CONFIG).

The YAML is the single source of truth for the training run; a snapshot ends up in
ml.model_runs.params. A small dataclass validates that all required keys are present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "ml.yaml"


@dataclass(frozen=True)
class MLConfig:
    train_months: int
    test_weeks: int
    validation_days: int
    iqr_k: float
    catboost: dict
    ridge: dict
    decision_tree: dict

    @classmethod
    def from_mapping(cls, raw: dict) -> MLConfig:
        split = _require(raw, "split")
        cleaning = _require(raw, "cleaning")
        models = _require(raw, "models")
        return cls(
            train_months=_require(split, "train_months"),
            test_weeks=_require(split, "test_weeks"),
            validation_days=_require(split, "validation_days"),
            iqr_k=float(_require(cleaning, "iqr_k")),
            catboost=_require(models, "catboost"),
            ridge=_require(models, "ridge"),
            decision_tree=_require(models, "decision_tree"),
        )


def _require(mapping: dict, key: str):
    if not isinstance(mapping, dict) or key not in mapping:
        raise ValueError(f"ml config: missing required key '{key}'")
    return mapping[key]


def load_ml_config(path: str | os.PathLike | None = None) -> MLConfig:
    path = Path(path or os.getenv("ML_CONFIG") or DEFAULT_CONFIG_PATH)
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return MLConfig.from_mapping(raw)
