"""Camelot wheel math.

The whole harmonic ranking rests on this, and an off-by-one on the wheel is
invisible until a set sounds wrong, so the full 24-key table is asserted
explicitly rather than derived.
"""

import pytest

from setbuilder.camelot import (
    ADJACENT,
    DRAMATIC,
    ENERGY_LIFT,
    INCOMPATIBLE,
    RELATIVE,
    SAME_KEY,
    camelot_to_key_name,
    camelot_to_pitch_class,
    harmonic_score,
    parse_camelot,
    parse_key,
    pitch_class_to_camelot,
    wheel_distance,
)

# The canonical wheel: number -> (major key, minor key).
WHEEL = {
    1:  ("B", "Ab"),
    2:  ("Gb", "Eb"),
    3:  ("Db", "Bb"),
    4:  ("Ab", "F"),
    5:  ("Eb", "C"),
    6:  ("Bb", "G"),
    7:  ("F", "D"),
    8:  ("C", "A"),
    9:  ("G", "E"),
    10: ("D", "B"),
    11: ("A", "Gb"),
    12: ("E", "Db"),
}


@pytest.mark.parametrize("number,major,minor", [
    (n, keys[0], keys[1]) for n, keys in WHEEL.items()
])
def test_wheel_names(number, major, minor):
    assert camelot_to_key_name(f"{number}B") == f"{major} major"
    assert camelot_to_key_name(f"{number}A") == f"{minor} minor"


def test_anchor_keys():
    """8B is C major and 8A is A minor -- the anchor the rest hangs off."""
    assert pitch_class_to_camelot(0, "major") == "8B"
    assert pitch_class_to_camelot(9, "minor") == "8A"


def test_adjacent_numbers_are_a_fifth_apart():
    for number in range(1, 13):
        here, _ = camelot_to_pitch_class(f"{number}B")
        there, _ = camelot_to_pitch_class(f"{(number % 12) + 1}B")
        assert (there - here) % 12 == 7


def test_relative_pairs_share_a_number():
    """nA is the relative minor of nB: three semitones below."""
    for number in range(1, 13):
        major_pc, major_mode = camelot_to_pitch_class(f"{number}B")
        minor_pc, minor_mode = camelot_to_pitch_class(f"{number}A")
        assert major_mode == "major" and minor_mode == "minor"
        assert (major_pc - minor_pc) % 12 == 3


def test_round_trip_all_keys():
    for number in range(1, 13):
        for letter in ("A", "B"):
            code = f"{number}{letter}"
            pitch_class, mode = camelot_to_pitch_class(code)
            assert pitch_class_to_camelot(pitch_class, mode) == code


@pytest.mark.parametrize("text,expected", [
    ("8A", "8A"),
    ("12b", "12B"),
    ("Am", "8A"),
    ("A minor", "8A"),
    ("a min", "8A"),
    ("C", "8B"),
    ("C major", "8B"),
    ("F#m", "11A"),
    ("Gbm", "11A"),        # enharmonic with F#m
    ("Ab major", "4B"),
    ("G#", "4B"),          # enharmonic with Ab major
    ("Bbmin", "3A"),
    ("Db maj", "3B"),
    ("1m", "6A"),          # Open Key notation
    ("12d", "5B"),
])
def test_parse_key(text, expected):
    assert parse_key(text) == expected


@pytest.mark.parametrize("text", ["", None, "nonsense", "H minor", "13A", "0A", "99"])
def test_parse_key_rejects_junk(text):
    assert parse_key(text) is None


def test_parse_camelot_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_camelot("13A")
    with pytest.raises(ValueError):
        parse_camelot("0B")
    with pytest.raises(ValueError):
        parse_camelot("8C")


def test_wheel_distance_wraps():
    assert wheel_distance(12, 1) == 1
    assert wheel_distance(1, 12) == 11
    assert wheel_distance(8, 8) == 0
    assert wheel_distance(8, 10) == 2
    assert wheel_distance(8, 3) == 7


# --- compatibility -------------------------------------------------------

def test_same_key_scores_highest():
    assert harmonic_score("8A", "8A") == (SAME_KEY, "same key")


def test_adjacent_both_directions():
    assert harmonic_score("8A", "9A")[0] == ADJACENT
    assert harmonic_score("8A", "7A")[0] == ADJACENT
    # and across the 12/1 boundary
    assert harmonic_score("12A", "1A")[0] == ADJACENT
    assert harmonic_score("1A", "12A")[0] == ADJACENT


def test_relative_major_minor():
    assert harmonic_score("8A", "8B") == (RELATIVE, "relative major/minor")
    assert harmonic_score("8B", "8A") == (RELATIVE, "relative major/minor")


def test_energy_lift_is_plus_two():
    assert harmonic_score("8A", "10A")[0] == ENERGY_LIFT
    assert harmonic_score("11A", "1A")[0] == ENERGY_LIFT   # wraps past 12


def test_dramatic_is_plus_seven():
    assert harmonic_score("8A", "3A")[0] == DRAMATIC       # 8 + 7 = 15 -> 3
    assert harmonic_score("1B", "8B")[0] == DRAMATIC


def test_incompatible_pairs():
    assert harmonic_score("8A", "2A")[0] == INCOMPATIBLE   # +6, tritone-ish
    assert harmonic_score("8A", "9B")[0] == INCOMPATIBLE   # letter flip + move
    assert harmonic_score("8A", "5B")[0] == INCOMPATIBLE


def test_unknown_keys_are_neutral_not_penalised():
    """A missing key must not rank a track below a genuine clash."""
    neutral, label = harmonic_score(None, "8A")
    assert label == "key unknown"
    assert neutral > INCOMPATIBLE
    assert harmonic_score("8A", None)[0] == neutral
    assert harmonic_score("garbage", "8A")[0] == neutral


def test_compatibility_ordering():
    """The five tiers must stay strictly ordered."""
    assert SAME_KEY > ADJACENT > RELATIVE > ENERGY_LIFT > DRAMATIC > INCOMPATIBLE
