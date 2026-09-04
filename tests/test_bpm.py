"""BPM octave correction and tempo compatibility.

Beat trackers report octave errors constantly.  These tests pin down both the
ordinary folds and the awkward cases (values whose octaves all sit outside the
window) so the correction can never oscillate or return something undefined.
"""

import math

import pytest

from setbuilder.bpm import (
    HIGH,
    LOW,
    bpm_delta,
    bpm_score,
    correct_bpm,
    octave_ratio,
)


@pytest.mark.parametrize("raw,expected", [
    (128.0, 128.0),    # already fine, untouched
    (138.0, 138.0),    # UK garage
    (174.0, 174.0),    # just inside the ceiling
    (90.0, 90.0),      # exactly on the floor
    (175.0, 175.0),    # exactly on the ceiling
    (64.0, 128.0),     # half-time detection
    (69.0, 138.0),     # half-time garage
    (256.0, 128.0),    # double-time detection
    (276.0, 138.0),
    (32.0, 128.0),     # two octaves down
    (512.0, 128.0),    # two octaves up
    (45.0, 90.0),      # doubles to exactly the floor
    (200.0, 100.0),    # halves into range
])
def test_octave_correction(raw, expected):
    assert correct_bpm(raw) == pytest.approx(expected)


def test_result_is_in_range_for_typical_tempos():
    """Every plausible detection folds into the window."""
    for raw in [60, 64, 70, 75, 80, 100, 110, 120, 125, 128, 130, 140, 150,
                160, 170, 240, 250, 260, 280, 300, 320, 350]:
        corrected = correct_bpm(float(raw))
        assert LOW <= corrected <= HIGH, f"{raw} -> {corrected}"


def test_correction_only_moves_by_octaves():
    """The corrected value is always the raw value times a power of two."""
    for raw in [37.0, 64.0, 88.0, 128.0, 199.0, 301.0]:
        corrected = correct_bpm(raw)
        ratio = math.log2(corrected / raw)
        assert ratio == pytest.approx(round(ratio)), f"{raw} -> {corrected}"


def test_ambiguous_value_does_not_oscillate():
    """88's octaves are 44/88/176/352 -- none land in [90, 175].

    A naive 'double while low, halve while high' loop flips forever here.  The
    result must be deterministic and stable under re-application.
    """
    corrected = correct_bpm(88.0)
    assert corrected in (88.0, 176.0)
    assert correct_bpm(corrected) == corrected


def test_correction_is_idempotent():
    for raw in [32.0, 45.0, 64.0, 88.0, 128.0, 200.0, 256.0, 512.0]:
        once = correct_bpm(raw)
        assert correct_bpm(once) == pytest.approx(once)


@pytest.mark.parametrize("raw", [0, -1, -128.0, None, float("nan"), float("inf")])
def test_invalid_input_returns_zero(raw):
    assert correct_bpm(raw) == 0.0


def test_octave_ratio():
    assert octave_ratio(64.0, 128.0) == pytest.approx(1.0)
    assert octave_ratio(256.0, 128.0) == pytest.approx(-1.0)
    assert octave_ratio(128.0, 128.0) == pytest.approx(0.0)


# --- compatibility -------------------------------------------------------

def test_exact_match_scores_one():
    assert bpm_score(128.0, 128.0)[0] == pytest.approx(1.0)


def test_within_two_percent_is_perfect():
    # 2% of 128 is 2.56 BPM.
    assert bpm_score(128.0, 130.5)[0] == pytest.approx(1.0)
    assert bpm_score(128.0, 125.6)[0] == pytest.approx(1.0)


def test_beyond_six_percent_scores_zero():
    # 6% of 128 is 7.68 BPM.
    assert bpm_score(128.0, 136.0)[0] == 0.0
    assert bpm_score(128.0, 120.0)[0] == 0.0


def test_taper_is_monotonic_between_two_and_six_percent():
    previous = 1.0
    for target in [130.6, 131.5, 132.5, 133.5, 134.5, 135.5]:
        score = bpm_score(128.0, target)[0]
        assert score <= previous
        previous = score
    assert 0.0 < bpm_score(128.0, 132.0)[0] < 1.0


def test_half_time_is_compatible():
    """138 garage into 69 is a real transition and must not score zero."""
    score, label = bpm_score(138.0, 69.0)
    assert label == "half-time"
    assert score > 0.9


def test_double_time_is_compatible():
    score, label = bpm_score(128.0, 256.0)
    assert label == "double-time"
    assert score > 0.9


def test_straight_beats_half_time_when_both_apply():
    """A straight match must outrank an equally-close half-time match."""
    straight = bpm_score(128.0, 128.0)[0]
    half = bpm_score(128.0, 64.0)[0]
    assert straight > half


def test_half_time_tolerance_follows_the_scaled_tempo():
    # 69.0 is exact half of 138; 71 is ~2.9% off and should have decayed.
    assert bpm_score(138.0, 69.0)[0] > bpm_score(138.0, 71.0)[0] > 0.0


def test_unknown_tempo_is_neutral():
    assert bpm_score(None, 128.0)[1] == "bpm unknown"
    assert bpm_score(128.0, None)[0] == 0.5
    assert bpm_score(0, 128.0)[0] == 0.5


def test_tolerance_slider_widens_the_window():
    """A wider tolerance must let further-away tempos through."""
    assert bpm_score(128.0, 136.0, taper_end=0.06)[0] == 0.0
    assert bpm_score(128.0, 136.0, taper_end=0.12)[0] > 0.0


def test_bpm_delta_reports_against_the_nearest_relation():
    """A half-time move reads as ~0 off, not -69."""
    assert bpm_delta(138.0, 69.0) == pytest.approx(0.0)
    assert bpm_delta(128.0, 129.5) == pytest.approx(1.5)
    assert bpm_delta(128.0, 126.0) == pytest.approx(-2.0)
    assert bpm_delta(None, 128.0) is None
