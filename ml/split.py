"""Deterministic chronological split anchored at the snapshot date (never the wall clock)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd


@dataclass(frozen=True)
class TimeSplit:
    as_of: date
    train_start: date        # inclusive
    train_end: date          # exclusive; equals test_start
    test_start: date         # inclusive
    test_end: date           # inclusive; equals as_of

    @classmethod
    def from_as_of(cls, as_of: date, train_months: int = 3, test_weeks: int = 3) -> TimeSplit:
        train_start = (pd.Timestamp(as_of) - pd.DateOffset(months=train_months)).date()
        test_start = as_of - timedelta(weeks=test_weeks)
        return cls(as_of=as_of, train_start=train_start, train_end=test_start, test_start=test_start, test_end=as_of)

    def masks(self, loading_date: pd.Series) -> dict[str, pd.Series]:
        d = pd.to_datetime(loading_date).dt.date
        return {
            "train": (d >= self.train_start) & (d < self.train_end),
            "test": (d >= self.test_start) & (d <= self.test_end),
            "future": d > self.as_of,
        }

    def as_dict(self) -> dict[str, str]:
        return {k: v.isoformat() for k, v in self.__dict__.items()}
