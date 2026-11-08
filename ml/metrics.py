from __future__ import annotations

import numpy as np


def regression_report(y_true, y_pred) -> dict[str, float]:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    err = y - p
    ss_res = float(np.sum(err ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "n_rows": int(len(y)),
        "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        "mape_pct": float(np.mean(np.abs(err / y)) * 100),
        "rmse_rub": float(np.sqrt(np.mean(err ** 2))),
        "mae_rub": float(np.mean(np.abs(err))),
    }
