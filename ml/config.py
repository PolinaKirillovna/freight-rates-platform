"""Загрузка гиперпараметров ML из configs/ml.yaml (путь переопределяется переменной ML_CONFIG).

YAML — единственный источник истины для прогона обучения; снимок попадает в
ml.model_runs.params. Небольшой dataclass проверяет, что все обязательные ключи на месте.
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
    catboost: dict           # только гиперпараметры оценщика (без target_transform)
    ridge: dict
    decision_tree: dict
    target_transforms: dict   # {имя модели: "log" | "none"}

    @classmethod
    def from_mapping(cls, raw: dict) -> MLConfig:
        split = _require(raw, "split")
        cleaning = _require(raw, "cleaning")
        models = _require(raw, "models")
        cb_params, cb_t = _split_model(models, "catboost")
        rg_params, rg_t = _split_model(models, "ridge")
        dt_params, dt_t = _split_model(models, "decision_tree")
        return cls(
            train_months=_require(split, "train_months"),
            test_weeks=_require(split, "test_weeks"),
            validation_days=_require(split, "validation_days"),
            iqr_k=float(_require(cleaning, "iqr_k")),
            catboost=cb_params,
            ridge=rg_params,
            decision_tree=dt_params,
            target_transforms={"catboost": cb_t, "ridge": rg_t, "decision_tree": dt_t},
        )


def _split_model(models: dict, name: str) -> tuple[dict, str]:
    """Отделить target_transform от гиперпараметров оценщика для одной модели."""
    section = dict(_require(models, name))
    if "target_transform" not in section:
        raise ValueError(f"ml config: missing required key 'target_transform' in models.{name}")
    transform = section.pop("target_transform")
    return section, transform


def _require(mapping: dict, key: str):
    if not isinstance(mapping, dict) or key not in mapping:
        raise ValueError(f"ml config: missing required key '{key}'")
    return mapping[key]


def load_ml_config(path: str | os.PathLike | None = None) -> MLConfig:
    path = Path(path or os.getenv("ML_CONFIG") or DEFAULT_CONFIG_PATH)
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return MLConfig.from_mapping(raw)
