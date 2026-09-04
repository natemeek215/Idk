"""Ranking: weights, energy asymmetry, variety, filters and reason strings."""

import pytest

from setbuilder import ranking
from setbuilder.ranking import energy_fit, rank, score_track, variety_score

from .conftest import make_track


def test_weights_sum_to_one():
    total = (ranking.WEIGHT_BPM + ranking.WEIGHT_HARMONIC
             + ranking.WEIGHT_ENERGY + ranking.WEIGHT_VARIETY)
    assert total == pytest.approx(1.0)


# --- energy fit ----------------------------------------------------------

def test_exact_energy_match_scores_one():
    score, difference = energy_fit(6.0, 6.0)
    assert score == pytest.approx(1.0)
    assert difference == 0.0


def test_overshoot_is_punished_harder_than_undershoot():
    """Jumping the arc is worse than lagging it."""
    over, _ = energy_fit(8.0, 6.0)
    under, _ = energy_fit(4.0, 6.0)
    assert over < under


def test_energy_fit_decays_with_distance():
    previous = 1.0
    for candidate in [6.0, 6.5, 7.0, 8.0, 9.0]:
        score, _ = energy_fit(candidate, 6.0)
        assert score <= previous
        previous = score


def test_missing_energy_is_neutral():
    score, difference = energy_fit(None, 6.0)
    assert score == 0.5
    assert difference == 0.0


# --- variety -------------------------------------------------------------

def test_same_artist_within_four_is_penalised():
    recent = [make_track(index, artist="Sammy Virji") for index in range(1, 5)]
    score, note = variety_score(make_track(9, artist="Sammy Virji"), recent)
    assert score == 0.0
    assert "same artist" in note


def test_same_artist_five_back_is_fine():
    recent = [make_track(1, artist="Sammy Virji")] + \
             [make_track(index, artist=f"Other {index}") for index in range(2, 6)]
    score, note = variety_score(make_track(9, artist="Sammy Virji"), recent)
    assert score == 1.0
    assert note is None


def test_same_label_within_three_is_penalised():
    recent = [make_track(index, artist=f"A{index}", label="Ultra")
              for index in range(1, 4)]
    score, note = variety_score(
        make_track(9, artist="Someone Else", label="Ultra"), recent)
    assert 0.0 < score < 1.0
    assert "same label" in note


def test_same_label_four_back_is_fine():
    recent = [make_track(1, artist="A1", label="Ultra")] + \
             [make_track(index, artist=f"A{index}", label="Other")
              for index in range(2, 5)]
    score, _ = variety_score(make_track(9, artist="B", label="Ultra"), recent)
    assert score == 1.0


def test_blank_artist_and_label_do_not_collide():
    """Two tracks with no artist tag must not count as the same artist."""
    recent = [make_track(1, artist="", label="")]
    score, note = variety_score(make_track(2, artist="", label=""), recent)
    assert score == 1.0
    assert note is None


# --- scoring -------------------------------------------------------------

def test_perfect_candidate_scores_near_one():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    candidate = make_track(2, artist="Someone", bpm=128.0, camelot="8A", energy=6.0)
    result = score_track(candidate, previous, 6.0, [previous], 0.5)
    assert result["score"] == pytest.approx(1.0, abs=0.01)
    assert result["verdict"] == "smooth"


def test_half_time_is_flagged_in_the_reason():
    previous = make_track(1, bpm=138.0, camelot="4A", energy=6.0)
    candidate = make_track(2, artist="Other", bpm=69.0, camelot="4A", energy=6.0)
    result = score_track(candidate, previous, 6.0, [previous], 0.5)
    assert "half-time" in result["reason"]
    assert result["components"]["bpm_relation"] == "half-time"


def test_reason_string_has_the_expected_shape():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    candidate = make_track(2, artist="Other", bpm=129.5, camelot="9A", energy=6.2)
    result = score_track(candidate, previous, 6.0, [previous], 0.5)
    assert result["reason"] == "+1.5 BPM · 8A → 9A · energy 6.2, arc wants 6.0"
    assert result["verdict"] == "smooth"


def test_preview_confidence_is_called_out_not_hidden():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    candidate = make_track(2, artist="Other", bpm=128.0, camelot="8A", energy=6.0,
                           confidence="preview")
    result = score_track(candidate, previous, 6.0, [previous], 0.5)
    assert "30s preview" in result["reason"]


def test_big_early_lift_is_described_as_such():
    previous = make_track(1, bpm=128.0, camelot="4A", energy=6.0)
    candidate = make_track(2, artist="Other", bpm=128.0, camelot="4B", energy=7.8)
    result = score_track(candidate, previous, 6.0, [previous], 0.3)
    assert "big lift" in result["verdict"]
    assert "early" in result["verdict"]


def test_opening_slot_has_no_previous_track():
    candidate = make_track(2, bpm=124.0, camelot="8A", energy=3.0)
    result = score_track(candidate, None, 3.0, [], 0.0)
    assert result["components"]["harmonic_label"] == "opening track"
    assert result["reason"].startswith("124 BPM")


def test_harmonic_only_filters_out_clashes():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    clash = make_track(2, artist="X", bpm=128.0, camelot="2A", energy=6.0)
    assert score_track(clash, previous, 6.0, [previous], 0.5,
                       harmonic_only=True) is None
    assert score_track(clash, previous, 6.0, [previous], 0.5,
                       harmonic_only=False) is not None


def test_harmonic_only_keeps_the_compatible_moves():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    for key in ("8A", "9A", "7A", "8B"):
        candidate = make_track(2, artist="X", bpm=128.0, camelot=key, energy=6.0)
        assert score_track(candidate, previous, 6.0, [previous], 0.5,
                           harmonic_only=True) is not None


# --- rank ----------------------------------------------------------------

def test_rank_orders_by_score_and_respects_the_limit():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    candidates = [
        make_track(2, artist="A", bpm=128.0, camelot="8A", energy=6.0),   # perfect
        make_track(3, artist="B", bpm=128.0, camelot="2A", energy=6.0),   # key clash
        make_track(4, artist="C", bpm=150.0, camelot="8A", energy=6.0),   # tempo miss
        make_track(5, artist="D", bpm=128.0, camelot="9A", energy=9.5),   # overshoots
    ]
    ranked = rank(candidates, [previous], 6.0, 0.5, limit=3)
    assert len(ranked) == 3
    assert ranked[0]["id"] == 2
    assert [item["score"] for item in ranked] == \
           sorted((item["score"] for item in ranked), reverse=True)


def test_rank_excludes_tracks_already_in_the_set():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    candidates = [previous, make_track(2, artist="B", bpm=128.0, camelot="8A",
                                       energy=6.0)]
    ranked = rank(candidates, [previous], 6.0, 0.5)
    assert [item["id"] for item in ranked] == [2]


def test_rank_skips_tracks_with_no_data_at_all():
    """A blocked track has nothing to rank on and must not be suggested."""
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    blocked = make_track(2, artist="B", bpm=None, camelot=None, energy=None)
    assert rank([blocked], [previous], 6.0, 0.5) == []


def test_rank_on_an_empty_set_ranks_purely_on_energy():
    candidates = [
        make_track(2, artist="A", bpm=128.0, camelot="8A", energy=3.0),
        make_track(3, artist="B", bpm=175.0, camelot="2B", energy=8.0),
    ]
    ranked = rank(candidates, [], 3.0, 0.0)
    assert ranked[0]["id"] == 2


def test_wider_tolerance_admits_more_tracks():
    previous = make_track(1, bpm=128.0, camelot="8A", energy=6.0)
    candidate = make_track(2, artist="B", bpm=136.0, camelot="8A", energy=6.0)
    narrow = rank([candidate], [previous], 6.0, 0.5, bpm_tolerance=0.06)[0]
    wide = rank([candidate], [previous], 6.0, 0.5, bpm_tolerance=0.12)[0]
    assert wide["score"] > narrow["score"]
