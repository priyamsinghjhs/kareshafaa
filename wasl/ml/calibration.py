"""Post-hoc probability calibration and per-case rationale for the outreach-priority model.

* CalibratedModel - optional Platt recalibration, chosen on a temporal tuning slice inside the training data;
  validated on the most recent 25 % (temporal hold-out) that was not used for any choice.
* calibration_stats - ECE, Brier, calibration-in-the-large, calibration slope/intercept, reliability bins with
  Wilson 95 % intervals.
* explain - per-case rationale by one-feature-at-a-time substitution with the training reference profile
  (model-agnostic; exact for additive models, an approximation for boosted trees), turned into plain language.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from .features import CAT_FEATURES, FEATURES, NUM_FEATURES


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


class CalibratedModel:
    """Pipeline plus an optional Platt recalibration, chosen by TEMPORAL tuning inside the training data:
    the older 80 % of training cases fit the model (and a Platt map on its 5-fold out-of-fold predictions); the
    newest 20 % of training cases decide whether the Platt map improves calibration there (lower ECE and a slope
    closer to 1). The chosen variant is then refitted on all training cases. The validation hold-out (most recent
    25 % overall) is never used for the choice. Platt scaling is monotone: within-tier ordering is unchanged."""

    def __init__(self, pipe: Any, method: str = "auto"):
        self.pipe = pipe
        self.method = method
        self.cal: Any = None
        self.tuning: dict = {}

    @staticmethod
    def _platt_from_oof(pipe: Any, X: pd.DataFrame, y: np.ndarray) -> Any:
        from sklearn.model_selection import StratifiedKFold, cross_val_predict

        oof = cross_val_predict(pipe, X, y, cv=StratifiedKFold(5, shuffle=True, random_state=0),
                                method="predict_proba")[:, 1]
        return LogisticRegression(C=1e6, max_iter=1000).fit(_logit(oof).reshape(-1, 1), y)

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> CalibratedModel:
        from sklearn.base import clone

        if self.method == "none":
            self.tuning = {"chosen": "none", "reason": "configured (ml_recalibration='none')"}
            self.pipe.fit(X, y)
            return self
        if self.method == "platt":
            self.cal = self._platt_from_oof(clone(self.pipe), X, y)
            self.tuning = {"chosen": "platt", "reason": "configured"}
            self.pipe.fit(X, y)
            return self
        k = int(len(X) * 0.8)
        base = clone(self.pipe).fit(X.iloc[:k], y[:k])
        platt = self._platt_from_oof(clone(self.pipe), X.iloc[:k], y[:k])
        raw_t = base.predict_proba(X.iloc[k:])[:, 1]
        cal_t = platt.predict_proba(_logit(raw_t).reshape(-1, 1))[:, 1]
        yt = y[k:]
        s_raw, s_cal = calibration_stats(yt, raw_t), calibration_stats(yt, cal_t)

        def score(st):
            return st["ece"] + 0.1 * abs((st["slope"] if st["slope"] is not None else 1.0) - 1.0)

        self.method = "platt" if score(s_cal) < score(s_raw) else "none"
        self.tuning = {"n_tune": int(len(yt)), "none": {k_: s_raw[k_] for k_ in ("ece", "slope", "citl")},
                       "platt": {k_: s_cal[k_] for k_ in ("ece", "slope", "citl")}, "chosen": self.method}
        self.cal = self._platt_from_oof(clone(self.pipe), X, y) if self.method == "platt" else None
        self.pipe.fit(X, y)
        return self

    @property
    def input_columns(self) -> list[str]:
        """The columns THIS model was trained on (a production model may predate a feature-set change)."""
        names = getattr(self.pipe, "feature_names_in_", None)
        return list(names) if names is not None else list(FEATURES)

    def raw_proba(self, X: pd.DataFrame) -> np.ndarray:
        cols = self.input_columns
        return self.pipe.predict_proba(X[cols] if set(cols) <= set(X.columns) else X)[:, 1]

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.raw_proba(X)
        p = raw if self.cal is None else self.cal.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]
        return np.column_stack([1 - p, p])


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def calibration_stats(y: np.ndarray, p: np.ndarray, bins: int = 10) -> dict:
    from .registry import ece  # noqa: PLC0415

    lr = LogisticRegression(C=1e6, max_iter=1000).fit(_logit(p).reshape(-1, 1), y) if len(set(y)) == 2 else None
    # equal-frequency bins are more stable than equal-width for skewed scores
    order = np.argsort(p)
    rel = []
    for chunk in np.array_split(order, bins):
        if len(chunk) == 0:
            continue
        k, n = int(y[chunk].sum()), len(chunk)
        lo, hi = wilson(k, n)
        mp = float(p[chunk].mean())
        rel.append({"mean_pred": round(mp, 3), "observed": round(k / n, 3), "ci": [round(lo, 3), round(hi, 3)],
                    "n": n, "within_ci": lo <= mp <= hi})
    return {"ece": round(ece(y, p), 4), "brier": round(float(np.mean((p - y) ** 2)), 4),
            "citl": round(float(p.mean() - y.mean()), 4),
            "slope": round(float(lr.coef_[0][0]), 3) if lr is not None else None,
            "intercept": round(float(lr.intercept_[0]), 3) if lr is not None else None,
            "reliability": rel, "bins_within_ci": sum(r["within_ci"] for r in rel), "bins": len(rel)}


def reference_profile(df: pd.DataFrame) -> dict:
    prof: dict[str, Any] = {}
    for c in CAT_FEATURES:
        prof[c] = str(df[c].astype(str).mode().iloc[0])
    for c in NUM_FEATURES:
        prof[c] = float(df[c].median())
    return prof


LABELS = {
    "has_smartphone": ("No smartphone", "Has a smartphone"),
    "whatsapp": ("No WhatsApp", "Uses WhatsApp"),
    "phone_valid": ("Phone number not valid", "Valid phone number"),
    "alt_contact": ("No alternative contact", "Has an alternative contact"),
    "early_notified": ("Not yet notified in the first 3 days", "Notified in the first 3 days"),
    "early_teach_back_failed": ("No failed teach-back recorded", "Did not confirm understanding at teach-back"),
}


def _label(feat: str, value: Any) -> str:
    if feat in LABELS:
        return LABELS[feat][1 if float(value) >= 1 else 0]
    if feat == "prior_no_shows":
        return f"{int(value)} previous missed appointment(s)"
    if feat == "prior_screens":
        return f"{int(value)} previous screening(s)"
    if feat == "early_failed_contacts":
        return f"{int(value)} failed contact attempt(s) in the first 3 days"
    if feat == "language":
        return f"Preferred language: {value} (interpreter/translation support)"
    return {"programme": "Programme", "tier": "Urgency tier", "region": "Region", "age_band": "Age band"}.get(feat, feat) \
        + f": {value}"


def explain(model: Any, rows: pd.DataFrame, profile: dict, top: int = 3, min_delta: float = 0.02) -> list[list[dict]]:
    """For each row: up to `top` factors that most change the predicted delay risk versus the reference profile."""
    feats = [f for f in getattr(model, "input_columns", FEATURES) if f in profile and f in rows.columns]
    base = model.predict_proba(rows)[:, 1]
    deltas = {}
    for f in feats:
        alt = rows.copy()
        alt[f] = profile[f] if isinstance(profile[f], str) else float(profile[f])
        deltas[f] = base - model.predict_proba(alt)[:, 1]
    out = []
    for i in range(len(rows)):
        items = sorted(((f, float(deltas[f][i])) for f in feats), key=lambda t: -abs(t[1]))
        why = []
        for f, d in items[:top]:
            if abs(d) < min_delta:
                continue
            why.append({"feature": f, "text": _label(f, rows.iloc[i][f]), "effect": round(d, 3),
                        "direction": "raises" if d > 0 else "lowers"})
        out.append(why)
    return out
