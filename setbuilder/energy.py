"""Energy scoring: a heuristic that works out of the box, and a ridge model
fitted to your own 1-10 ratings once you have labelled enough tracks.

The heuristic exists so the app is useful on first import.  The model is the
point -- it replaces someone else's idea of "energy" with yours.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import db
from .features import FEATURE_NAMES

# Labels needed before the fitted model takes over from the heuristic.
MIN_LABELS_FOR_MODEL = 25
CALIBRATION_TARGET = 30

# Each entry maps a raw feature onto 0-1: (low, high, invert).
# Ranges are chosen for club-oriented electronic music at commercial masters.
_NORMALIZERS: Dict[str, Tuple[float, float, bool]] = {
    "rms_sub":          (-52.0, -18.0, False),
    "rms_bass":         (-46.0, -14.0, False),
    "rms_mid":          (-52.0, -20.0, False),
    "rms_high":         (-64.0, -28.0, False),
    "onset_rate":       (0.6, 6.5, False),
    "flux_mean":        (0.02, 0.35, False),
    "flux_var":         (0.0, 0.06, False),
    "percussive_ratio": (0.30, 0.80, False),
    # A wide loudness range means a dynamic, breathing track -- less "driving".
    "loudness_range":   (3.0, 22.0, True),
    "fullness":         (0.05, 0.65, False),
    "rms_overall":      (-30.0, -8.0, False),
}

# Sub-bass, onset rate and fullness carry the most weight, per the brief.
HEURISTIC_WEIGHTS: Dict[str, float] = {
    "rms_sub":          0.20,
    "onset_rate":       0.18,
    "fullness":         0.18,
    "percussive_ratio": 0.12,
    "rms_bass":         0.10,
    "flux_mean":        0.08,
    "rms_high":         0.06,
    "loudness_range":   0.05,
    "rms_mid":          0.02,
    "flux_var":         0.01,
}


def _normalize(name: str, value: float) -> float:
    low, high, invert = _NORMALIZERS.get(name, (0.0, 1.0, False))
    if high == low:
        return 0.0
    scaled = (float(value) - low) / (high - low)
    scaled = min(1.0, max(0.0, scaled))
    return 1.0 - scaled if invert else scaled


def normalized_features(features: Dict[str, Any]) -> Dict[str, float]:
    return {name: _normalize(name, features.get(name, 0.0) or 0.0)
            for name in _NORMALIZERS}


def heuristic_energy(features: Dict[str, Any]) -> float:
    """Weighted blend of normalised features, mapped onto 1-10."""
    if not features:
        return 5.0
    normalized = normalized_features(features)
    total_weight = sum(HEURISTIC_WEIGHTS.values())
    score = sum(HEURISTIC_WEIGHTS[name] * normalized.get(name, 0.0)
                for name in HEURISTIC_WEIGHTS) / total_weight
    return round(1.0 + 9.0 * score, 2)


# --- calibration ---------------------------------------------------------

def save_label(cache_key: str, rating: float) -> None:
    rating = min(10.0, max(1.0, float(rating)))
    db.execute(
        "INSERT INTO labels (cache_key, rating, labeled_at) VALUES (?, ?, ?) "
        "ON CONFLICT(cache_key) DO UPDATE SET rating=excluded.rating, "
        "labeled_at=excluded.labeled_at",
        (cache_key, rating, time.time()),
    )


def label_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM labels")
    return int((row or {}).get("n") or 0)


def _training_data() -> Tuple[np.ndarray, np.ndarray]:
    rows = db.query(
        "SELECT l.rating AS rating, a.features AS features "
        "FROM labels l JOIN analysis a ON a.cache_key = l.cache_key "
        "WHERE a.features IS NOT NULL"
    )
    vectors, ratings = [], []
    for row in rows:
        try:
            features = json.loads(row["features"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not features:
            continue
        # Feed the model the same normalised scale the heuristic uses, so a
        # single wildly-scaled feature (dB vs. a 0-1 ratio) cannot dominate.
        normalized = normalized_features(features)
        vectors.append([normalized.get(name, 0.0) for name in _model_feature_names()])
        ratings.append(float(row["rating"]))
    if not vectors:
        return np.zeros((0, len(_model_feature_names()))), np.zeros(0)
    return np.array(vectors, dtype=float), np.array(ratings, dtype=float)


def _model_feature_names() -> List[str]:
    """Model inputs: the normalised features, in a stable order."""
    return [name for name in FEATURE_NAMES if name in _NORMALIZERS]


def fit_model(alpha: float = 1.0) -> Dict[str, Any]:
    """Fit ridge regression on the labelled tracks and store the coefficients."""
    from sklearn.linear_model import Ridge
    from sklearn.model_selection import cross_val_score

    X, y = _training_data()
    if len(y) < MIN_LABELS_FOR_MODEL:
        return {
            "fitted": False,
            "n_labels": int(len(y)),
            "needed": MIN_LABELS_FOR_MODEL,
            "reason": f"{MIN_LABELS_FOR_MODEL} labelled tracks needed, have {len(y)}",
        }

    model = Ridge(alpha=alpha)
    model.fit(X, y)

    # Honest generalisation estimate; with few labels this is often negative,
    # which is worth surfacing rather than hiding.
    try:
        folds = min(5, max(2, len(y) // 5))
        r2 = float(np.mean(cross_val_score(Ridge(alpha=alpha), X, y, cv=folds,
                                           scoring="r2")))
    except Exception:
        r2 = float("nan")

    payload = {
        "feature_names": _model_feature_names(),
        "coefficients": model.coef_.tolist(),
        "intercept": float(model.intercept_),
        "alpha": alpha,
    }
    db.execute(
        "INSERT INTO model (id, payload, trained_at, n_labels, r2) VALUES (1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
        "trained_at=excluded.trained_at, n_labels=excluded.n_labels, r2=excluded.r2",
        (json.dumps(payload), time.time(), int(len(y)), None if np.isnan(r2) else r2),
    )
    return {"fitted": True, "n_labels": int(len(y)),
            "r2": None if np.isnan(r2) else round(r2, 3)}


def load_model() -> Optional[Dict[str, Any]]:
    row = db.query_one("SELECT * FROM model WHERE id = 1")
    if not row or not row.get("payload"):
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    payload["n_labels"] = row.get("n_labels")
    payload["r2"] = row.get("r2")
    payload["trained_at"] = row.get("trained_at")
    return payload


def model_energy(features: Dict[str, Any],
                 model: Optional[Dict[str, Any]] = None) -> Optional[float]:
    model = model or load_model()
    if not model or not features:
        return None
    normalized = normalized_features(features)
    vector = np.array([normalized.get(name, 0.0)
                       for name in model["feature_names"]], dtype=float)
    coefficients = np.array(model["coefficients"], dtype=float)
    if vector.shape != coefficients.shape:
        return None
    prediction = float(vector @ coefficients + model["intercept"])
    return round(min(10.0, max(1.0, prediction)), 2)


def score_energy(features: Dict[str, Any],
                 model: Optional[Dict[str, Any]] = None) -> Tuple[float, str]:
    """Best available energy score plus which method produced it."""
    if model is None:
        model = load_model()
    if model and (model.get("n_labels") or 0) >= MIN_LABELS_FOR_MODEL:
        predicted = model_energy(features, model)
        if predicted is not None:
            return predicted, "model"
    return heuristic_energy(features), "heuristic"


def rescore_all() -> int:
    """Re-apply the current scorer to every cached analysis."""
    model = load_model()
    rows = db.query("SELECT cache_key, features FROM analysis "
                    "WHERE features IS NOT NULL AND error IS NULL")
    updated = 0
    for row in rows:
        try:
            features = json.loads(row["features"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not features:
            continue
        energy, source = score_energy(features, model)
        db.execute("UPDATE analysis SET energy = ?, energy_source = ? "
                   "WHERE cache_key = ?", (energy, source, row["cache_key"]))
        updated += 1
    return updated


def calibration_status() -> Dict[str, Any]:
    model = load_model()
    count = label_count()
    return {
        "labels": count,
        "target": CALIBRATION_TARGET,
        "minimum": MIN_LABELS_FOR_MODEL,
        "model_active": bool(model and (model.get("n_labels") or 0) >= MIN_LABELS_FOR_MODEL),
        "r2": (model or {}).get("r2"),
        "trained_labels": (model or {}).get("n_labels"),
    }
