"""Camelot wheel conversion and harmonic compatibility scoring.

The Camelot wheel maps every key to a number 1-12 and a letter (A = minor,
B = major). Adjacent numbers are a perfect fifth apart, and a number's A/B
pair are relative minor/major of each other.

Anchor: 8B = C major, 8A = A minor.
Moving +1 around the wheel raises the tonic by a perfect fifth (7 semitones).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Pitch classes with C = 0.
_PITCH_CLASSES = {
    "c": 0, "b#": 0,
    "c#": 1, "db": 1,
    "d": 2,
    "d#": 3, "eb": 3,
    "e": 4, "fb": 4,
    "f": 5, "e#": 5,
    "f#": 6, "gb": 6,
    "g": 7,
    "g#": 8, "ab": 8,
    "a": 9,
    "a#": 10, "bb": 10,
    "b": 11, "cb": 11,
}

# Preferred spelling per pitch class, for display.
_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

MAJOR = "major"
MINOR = "minor"

# Semitone offset from the relative major down to its relative minor tonic.
_RELATIVE_MINOR_OFFSET = 9


def _major_pitch_class(number: int) -> int:
    """Pitch class of the major key at the given Camelot number."""
    return (7 * (number - 8)) % 12


def pitch_class_to_camelot(pitch_class: int, mode: str) -> str:
    """Convert a pitch class (C=0) plus mode into Camelot notation."""
    pitch_class %= 12
    mode = _canonical_mode(mode)
    if mode == MAJOR:
        target = pitch_class
    else:
        # Find the major whose relative minor is this pitch class.
        target = (pitch_class - _RELATIVE_MINOR_OFFSET) % 12
    for number in range(1, 13):
        if _major_pitch_class(number) == target:
            return f"{number}{'B' if mode == MAJOR else 'A'}"
    raise AssertionError("unreachable: every pitch class maps to a Camelot number")


def camelot_to_pitch_class(camelot: str) -> Tuple[int, str]:
    """Inverse of :func:`pitch_class_to_camelot`. Returns (pitch_class, mode)."""
    number, letter = parse_camelot(camelot)
    major_pc = _major_pitch_class(number)
    if letter == "B":
        return major_pc, MAJOR
    return (major_pc + _RELATIVE_MINOR_OFFSET) % 12, MINOR


def _canonical_mode(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in ("minor", "min", "m", "aeolian", "a"):
        return MINOR
    return MAJOR


_KEY_RE = re.compile(
    r"^\s*([a-gA-G])\s*([#b♯♭]?)\s*[-_ ]*(maj(?:or)?|min(?:or)?|m|M)?\s*$"
)

_CAMELOT_RE = re.compile(r"^\s*(\d{1,2})\s*([abAB])\s*$")


def parse_camelot(camelot: str) -> Tuple[int, str]:
    """Parse '8A' / '12b' into (number, 'A'|'B'). Raises ValueError if invalid."""
    match = _CAMELOT_RE.match(camelot or "")
    if not match:
        raise ValueError(f"not a Camelot code: {camelot!r}")
    number = int(match.group(1))
    if not 1 <= number <= 12:
        raise ValueError(f"Camelot number out of range: {camelot!r}")
    return number, match.group(2).upper()


def parse_key(text: str) -> Optional[str]:
    """Best-effort parse of a key string (from a tag or an analyzer) to Camelot.

    Accepts Camelot codes ('8A'), Open Key codes ('1m'/'1d'), and musical
    spellings ('F#m', 'Ab major', 'Bbmin', 'C').  Returns None when the input
    is not recognisable as a key -- callers should treat that as "unknown"
    rather than guessing.
    """
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    # Already Camelot?
    try:
        number, letter = parse_camelot(raw)
        return f"{number}{letter}"
    except ValueError:
        pass

    # Open Key notation: 1m .. 12m (minor), 1d .. 12d (major).
    open_key = re.match(r"^\s*(\d{1,2})\s*([dmDM])\s*$", raw)
    if open_key:
        number = int(open_key.group(1))
        if 1 <= number <= 12:
            # Open Key n == Camelot n+5 (mod 12), same mode letter.
            camelot_number = ((number + 4) % 12) + 1
            mode = MINOR if open_key.group(2).lower() == "m" else MAJOR
            letter = "A" if mode == MINOR else "B"
            return f"{camelot_number}{letter}"

    normalized = raw.replace("♯", "#").replace("♭", "b")
    match = _KEY_RE.match(normalized)
    if not match:
        return None
    letter, accidental, mode_text = match.groups()
    pitch_class = _PITCH_CLASSES.get((letter + (accidental or "")).lower())
    if pitch_class is None:
        return None

    if mode_text is None:
        mode = MAJOR
    elif mode_text == "M":
        mode = MAJOR
    elif mode_text == "m":
        mode = MINOR
    else:
        mode = MINOR if mode_text.lower().startswith("min") else MAJOR
    return pitch_class_to_camelot(pitch_class, mode)


def camelot_to_key_name(camelot: str, prefer_flats: bool = True) -> str:
    """Human-readable key name for a Camelot code, e.g. '8A' -> 'A minor'."""
    pitch_class, mode = camelot_to_pitch_class(camelot)
    names = _FLAT_NAMES if prefer_flats else _SHARP_NAMES
    return f"{names[pitch_class]} {mode}"


def wheel_distance(from_number: int, to_number: int) -> int:
    """Signed forward distance around the 12-step wheel, in 0..11."""
    return (to_number - from_number) % 12


# Compatibility weights, per the spec.
SAME_KEY = 1.0
ADJACENT = 0.90
RELATIVE = 0.85
ENERGY_LIFT = 0.70
DRAMATIC = 0.55
INCOMPATIBLE = 0.15


def harmonic_score(from_key: Optional[str], to_key: Optional[str]) -> Tuple[float, str]:
    """Score a transition between two Camelot keys.

    Returns (score, short human-readable label).  Unknown keys score at the
    neutral midpoint rather than punishing a track for missing data.
    """
    if not from_key or not to_key:
        return 0.5, "key unknown"
    try:
        from_number, from_letter = parse_camelot(from_key)
        to_number, to_letter = parse_camelot(to_key)
    except ValueError:
        return 0.5, "key unknown"

    distance = wheel_distance(from_number, to_number)

    if from_letter == to_letter:
        if distance == 0:
            return SAME_KEY, "same key"
        if distance == 1 or distance == 11:
            return ADJACENT, "adjacent"
        if distance == 2:
            return ENERGY_LIFT, "energy lift"
        if distance == 7:
            return DRAMATIC, "dramatic change"
        return INCOMPATIBLE, "clash"

    # Letter flip.
    if distance == 0:
        return RELATIVE, "relative major/minor"
    return INCOMPATIBLE, "clash"
