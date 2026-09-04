"""Energy scoring: the heuristic, and the ridge model that replaces it."""

import time

import pytest

from setbuilder import db, energy
from setbuilder.features import FEATURE_NAMES


def features(**overrides):
    base = {
        "rms_sub": -35.0, "rms_bass": -30.0, "rms_mid": -35.0, "rms_high": -46.0,
        "onset_rate": 3.0, "flux_mean": 0.15, "flux_var": 0.02,
        "percussive_ratio": 0.5, "loudness_range": 12.0, "fullness": 0.30,
        "rms_overall": -20.0, "bpm": 128.0,
    }
    base.update(overrides)
    return base


# --- heuristic -----------------------------------------------------------

def test_heuristic_stays_on_the_one_to_ten_scale():
    quiet = energy.heuristic_energy(features(
        rms_sub=-60.0, rms_bass=-55.0, rms_mid=-60.0, rms_high=-70.0,
        onset_rate=0.2, flux_mean=0.0, fullness=0.0, percussive_ratio=0.1,
        loudness_range=30.0))
    loud = energy.heuristic_energy(features(
        rms_sub=-10.0, rms_bass=-8.0, rms_mid=-14.0, rms_high=-20.0,
        onset_rate=9.0, flux_mean=0.5, flux_var=0.1, fullness=0.9,
        percussive_ratio=0.95, loudness_range=2.0))
    assert 1.0 <= quiet <= 10.0
    assert 1.0 <= loud <= 10.0
    assert loud > quiet


def test_heuristic_handles_missing_features():
    assert 1.0 <= energy.heuristic_energy({}) <= 10.0


@pytest.mark.parametrize("feature", ["rms_sub", "onset_rate", "fullness"])
def test_the_three_headline_features_move_the_score_most(feature):
    """Sub-bass, onset rate and fullness are meant to carry the most weight."""
    weights = energy.HEURISTIC_WEIGHTS
    others = [name for name in weights
              if name not in ("rms_sub", "onset_rate", "fullness")]
    assert all(weights[feature] >= weights[other] for other in others)


def test_more_sub_bass_raises_the_score():
    low = energy.heuristic_energy(features(rms_sub=-50.0))
    high = energy.heuristic_energy(features(rms_sub=-20.0))
    assert high > low


def test_a_wider_loudness_range_lowers_the_score():
    """A dynamic, breathing track reads as less driving."""
    compressed = energy.heuristic_energy(features(loudness_range=4.0))
    dynamic = energy.heuristic_energy(features(loudness_range=20.0))
    assert compressed > dynamic


def test_normalised_features_are_bounded():
    extreme = features(rms_sub=200.0, onset_rate=-50.0, fullness=99.0)
    for value in energy.normalized_features(extreme).values():
        assert 0.0 <= value <= 1.0


# --- model ---------------------------------------------------------------

def _seed_labels(count, rating_for):
    """Insert ``count`` analysed tracks with varied features, plus labels."""
    for index in range(count):
        cache_key = f"cal-{index}"
        db.save_analysis(cache_key, {
            "bpm": 128.0, "camelot": "8A",
            "features": features(
                rms_sub=-50.0 + index, onset_rate=index / 4.0,
                fullness=min(0.9, index / 40.0),
                percussive_ratio=min(0.95, 0.2 + index / 60.0)),
            "energy": 5.0, "energy_source": "heuristic", "confidence": "full",
            "analyzed_at": time.time(),
        })
        energy.save_label(cache_key, rating_for(index))


def test_model_does_not_fit_below_the_minimum(clean_db):
    _seed_labels(10, lambda index: 5.0)
    result = energy.fit_model()
    assert result["fitted"] is False
    assert result["n_labels"] == 10
    assert energy.calibration_status()["model_active"] is False


def test_model_fits_and_takes_over_at_the_minimum(clean_db):
    _seed_labels(30, lambda index: 1.0 + index * 0.3)
    result = energy.fit_model()
    assert result["fitted"] is True
    assert result["n_labels"] == 30

    status = energy.calibration_status()
    assert status["model_active"] is True
    assert status["labels"] == 30

    scored, source = energy.score_energy(features())
    assert source == "model"
    assert 1.0 <= scored <= 10.0


def test_model_learns_the_direction_of_your_ratings(clean_db):
    """Rate louder, busier tracks higher and the model must follow."""
    _seed_labels(30, lambda index: 1.0 + index * 0.3)
    energy.fit_model()

    quiet = energy.model_energy(features(rms_sub=-50.0, onset_rate=0.0, fullness=0.0))
    loud = energy.model_energy(features(rms_sub=-21.0, onset_rate=7.0, fullness=0.7))
    assert loud > quiet


def test_model_predictions_are_clamped(clean_db):
    _seed_labels(30, lambda index: 1.0 + index * 0.3)
    energy.fit_model()
    extreme = energy.model_energy(features(rms_sub=999.0, onset_rate=999.0,
                                           fullness=999.0))
    assert 1.0 <= extreme <= 10.0


def test_rescore_all_switches_cached_rows_to_the_model(clean_db):
    _seed_labels(30, lambda index: 1.0 + index * 0.3)
    energy.fit_model()
    updated = energy.rescore_all()
    assert updated == 30

    rows = db.query("SELECT energy_source FROM analysis")
    assert all(row["energy_source"] == "model" for row in rows)


def test_labels_survive_a_refit_and_can_be_updated(clean_db):
    _seed_labels(30, lambda index: 5.0)
    assert energy.label_count() == 30
    energy.save_label("cal-0", 9.0)
    assert energy.label_count() == 30          # updated, not duplicated
    row = db.query_one("SELECT rating FROM labels WHERE cache_key = 'cal-0'")
    assert row["rating"] == 9.0


def test_ratings_are_clamped_to_the_scale(clean_db):
    energy.save_label("x", 99.0)
    energy.save_label("y", -4.0)
    ratings = {row["cache_key"]: row["rating"]
               for row in db.query("SELECT cache_key, rating FROM labels")}
    assert ratings["x"] == 10.0
    assert ratings["y"] == 1.0


def test_score_energy_falls_back_to_the_heuristic_without_a_model(clean_db):
    value, source = energy.score_energy(features())
    assert source == "heuristic"
    assert value == energy.heuristic_energy(features())


def test_model_feature_order_is_stable(clean_db):
    """The stored payload's order must match what predictions assume."""
    _seed_labels(30, lambda index: 1.0 + index * 0.3)
    energy.fit_model()
    model = energy.load_model()
    assert model["feature_names"] == energy._model_feature_names()
    assert len(model["coefficients"]) == len(model["feature_names"])
    assert all(name in FEATURE_NAMES for name in model["feature_names"])
