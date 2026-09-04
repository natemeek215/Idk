"""BPM octave correction and BPM compatibility scoring.

Beat trackers routinely report a tempo an octave off -- 128 comes back as 64
or 256.  We fold the raw estimate into a plausible DJ range while keeping the
raw value so the UI can show what the analyzer actually said.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

# The window a corrected BPM should land in, per the spec.
LOW = 90.0
HIGH = 175.0
# Where we assume the music lives when no octave lands in range.
CENTER = 128.0


def correct_bpm(raw: float, low: float = LOW, high: float = HIGH,
                center: float = CENTER) -> float:
    """Fold ``raw`` by powers of two until it sits in [low, high].

    Doubles anything under ``low`` and halves anything over ``high``.  When no
    octave of the value lands inside the window (e.g. 88, whose octaves are 88
    and 176), the octave closest to ``center`` on a log scale wins, so the
    result is always defined rather than oscillating.
    """
    if raw is None:
        return 0.0
    raw = float(raw)
    if not math.isfinite(raw) or raw <= 0:
        return 0.0

    best_candidate = raw
    best_rank: Optional[Tuple[int, float]] = None
    for exponent in range(-5, 6):
        candidate = raw * (2.0 ** exponent)
        in_range = low <= candidate <= high
        rank = (0 if in_range else 1, abs(math.log2(candidate / center)))
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_candidate = candidate
    return best_candidate


def octave_ratio(raw: float, corrected: float) -> float:
    """How many octaves the correction moved the value (0 when untouched)."""
    if not raw or not corrected:
        return 0.0
    return math.log2(corrected / raw)


# --- compatibility -------------------------------------------------------

# 1.0 inside this fractional distance, tapering to 0 at TAPER_END.
PERFECT = 0.02
TAPER_END = 0.06


def _tempo_closeness(from_bpm: float, to_bpm: float,
                     taper_end: float = TAPER_END) -> float:
    """1.0 within +/-2%, linear taper to 0.0 at +/-``taper_end``.

    ``taper_end`` is the UI's BPM-tolerance slider, so widening it makes more
    of the library rank as mixable rather than merely reordering the same few.
    """
    if from_bpm <= 0 or to_bpm <= 0:
        return 0.0
    perfect = min(PERFECT, taper_end)
    difference = abs(to_bpm - from_bpm) / from_bpm
    if difference <= perfect:
        return 1.0
    if difference >= taper_end:
        return 0.0
    return 1.0 - (difference - perfect) / (taper_end - perfect)


# Straight mixes score at face value; the half/double-time relationships are
# real transitions but ask more of the DJ, so they are worth slightly less.
RELATIONS = (
    (1.0, 1.0, "straight"),
    (0.5, 0.97, "half-time"),
    (2.0, 0.97, "double-time"),
    (0.75, 0.90, "3/4-time"),
    (1.5, 0.90, "1.5x"),
)


def bpm_score(from_bpm: Optional[float], to_bpm: Optional[float],
              taper_end: float = TAPER_END) -> Tuple[float, str]:
    """Score a tempo transition, testing half- and double-time as well.

    138 garage into 69, or 128 into 256, scores as compatible -- those are real
    transitions.  Returns (score, label) where label names the relationship
    that scored best ('straight', 'half-time', 'double-time', ...).
    """
    if not from_bpm or not to_bpm:
        return 0.5, "bpm unknown"

    best_score = 0.0
    best_label = "straight"
    for multiplier, discount, label in RELATIONS:
        closeness = _tempo_closeness(from_bpm * multiplier, to_bpm, taper_end)
        score = closeness * discount
        if score > best_score:
            best_score = score
            best_label = label
    return best_score, best_label


def bpm_delta(from_bpm: Optional[float], to_bpm: Optional[float]) -> Optional[float]:
    """Signed BPM difference against the nearest tempo relationship.

    For a half-time move from 138 to 69 this reports ~0.0, not -69, so the
    reason string reads the way a DJ would describe the transition.
    """
    if not from_bpm or not to_bpm:
        return None
    best = None
    for multiplier, _discount, _label in RELATIONS:
        delta = to_bpm - (from_bpm * multiplier)
        if best is None or abs(delta) < abs(best):
            best = delta
    return best
