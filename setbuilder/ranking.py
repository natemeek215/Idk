"""Ranking: given the set so far and the next slot's target energy, score every
remaining track.

The app never picks a track -- it orders the options and explains each one in a
line you can read at a glance in a dark room.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from .bpm import bpm_delta, bpm_score
from .camelot import harmonic_score

WEIGHT_BPM = 0.35
WEIGHT_HARMONIC = 0.30
WEIGHT_ENERGY = 0.30
WEIGHT_VARIETY = 0.05

# Overshooting the arc is worse than lagging it, so the Gaussian is narrower
# on the high side.  Both are in 1-10 energy units.
OVERSHOOT_SCALE = 1.6
UNDERSHOOT_SCALE = 2.4

# Variety look-back windows, per the spec.
ARTIST_WINDOW = 4
LABEL_WINDOW = 3


def energy_fit(track_energy: Optional[float],
               target_energy: float) -> tuple[float, float]:
    """Score how well a track's energy matches the slot target.

    Returns (score, signed difference).  A track with no energy value scores
    at the neutral midpoint rather than being ranked as a bad fit.
    """
    if track_energy is None:
        return 0.5, 0.0
    difference = float(track_energy) - float(target_energy)
    scale = OVERSHOOT_SCALE if difference > 0 else UNDERSHOOT_SCALE
    return math.exp(-((difference / scale) ** 2)), difference


def variety_score(candidate: Dict[str, Any],
                  recent: Sequence[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """Penalise the same artist within 4 tracks and the same label within 3."""
    artist = (candidate.get("artist") or "").strip().lower()
    label = (candidate.get("label") or "").strip().lower()

    if artist:
        for offset, played in enumerate(reversed(recent[-ARTIST_WINDOW:])):
            if (played.get("artist") or "").strip().lower() == artist:
                return 0.0, f"same artist {offset + 1} back"
    if label:
        for offset, played in enumerate(reversed(recent[-LABEL_WINDOW:])):
            if (played.get("label") or "").strip().lower() == label:
                return 0.35, f"same label {offset + 1} back"
    return 1.0, None


def _format_bpm_segment(previous: Optional[Dict[str, Any]],
                        candidate: Dict[str, Any], label: str) -> str:
    candidate_bpm = candidate.get("bpm")
    if not previous:
        return f"{candidate_bpm:.0f} BPM" if candidate_bpm else "BPM unknown"
    if not candidate_bpm or not previous.get("bpm"):
        return "BPM unknown"
    if label != "straight":
        return f"{label} ({candidate_bpm:.0f})"
    delta = bpm_delta(previous.get("bpm"), candidate_bpm) or 0.0
    return f"{delta:+.1f} BPM"


def _format_key_segment(previous: Optional[Dict[str, Any]],
                        candidate: Dict[str, Any]) -> str:
    candidate_key = candidate.get("camelot") or "?"
    if not previous or not previous.get("camelot"):
        return str(candidate_key)
    return f"{previous['camelot']} → {candidate_key}"


def _verdict(components: Dict[str, Any], position: float,
             has_previous: bool) -> str:
    """The short human judgement at the end of the reason line."""
    energy_difference = components["energy_difference"]
    harmonic = components["harmonic"]

    notes: List[str] = []

    if energy_difference > 1.5:
        notes.append("big lift")
    elif energy_difference > 0.7:
        notes.append("lift")
    elif energy_difference < -1.5:
        notes.append("pulls back hard")
    elif energy_difference < -0.7:
        notes.append("eases off")

    if harmonic < 0.5:
        notes.append("key clash")
    if has_previous and components["bpm"] < 0.35:
        notes.append("tempo stretch")

    if not notes:
        return "smooth"

    if energy_difference > 1.5 and position < 0.5:
        notes.append("early")
    return ", ".join(notes)


def score_track(candidate: Dict[str, Any], previous: Optional[Dict[str, Any]],
                target_energy: float, recent: Sequence[Dict[str, Any]],
                position: float, bpm_tolerance: float = 0.06,
                harmonic_only: bool = False) -> Optional[Dict[str, Any]]:
    """Score one candidate for the next slot.  Returns None if filtered out."""
    if previous:
        raw_bpm, bpm_relation = bpm_score(previous.get("bpm"), candidate.get("bpm"),
                                          taper_end=bpm_tolerance)
        raw_harmonic, harmonic_label = harmonic_score(previous.get("camelot"),
                                                      candidate.get("camelot"))
    else:
        # Nothing to mix out of yet -- the opener is chosen on energy alone.
        raw_bpm, bpm_relation = 1.0, "straight"
        raw_harmonic, harmonic_label = 1.0, "opening track"

    if harmonic_only and previous and raw_harmonic < 0.55:
        return None

    fit, energy_difference = energy_fit(candidate.get("energy"), target_energy)
    variety, variety_note = variety_score(candidate, recent)

    total = (WEIGHT_BPM * raw_bpm
             + WEIGHT_HARMONIC * raw_harmonic
             + WEIGHT_ENERGY * fit
             + WEIGHT_VARIETY * variety)

    components = {
        "bpm": round(raw_bpm, 3),
        "bpm_relation": bpm_relation,
        "harmonic": round(raw_harmonic, 3),
        "harmonic_label": harmonic_label,
        "energy": round(fit, 3),
        "energy_difference": round(energy_difference, 2),
        "variety": round(variety, 3),
        "variety_note": variety_note,
    }

    segments = [
        _format_bpm_segment(previous, candidate, bpm_relation),
        _format_key_segment(previous, candidate),
    ]
    energy_value = candidate.get("energy")
    if energy_value is not None:
        segments.append(f"energy {energy_value:.1f}, arc wants {target_energy:.1f}")
    else:
        segments.append(f"energy unknown, arc wants {target_energy:.1f}")

    verdict = _verdict(components, position, previous is not None)
    if variety_note:
        verdict = f"{verdict} · {variety_note}"

    reason = " · ".join(segments)
    if candidate.get("confidence") == "preview":
        reason += "  ⚠ energy from 30s preview"

    return {
        **candidate,
        "score": round(total, 4),
        "components": components,
        "reason": reason,
        "verdict": verdict,
        "target_energy": round(target_energy, 2),
    }


def rank(candidates: Sequence[Dict[str, Any]], current_set: Sequence[Dict[str, Any]],
         target_energy: float, position: float, bpm_tolerance: float = 0.06,
         harmonic_only: bool = False, limit: int = 5) -> List[Dict[str, Any]]:
    """Rank every candidate for the next slot and return the top ``limit``."""
    previous = current_set[-1] if current_set else None
    used_ids = {track.get("id") for track in current_set}

    scored: List[Dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("id") in used_ids:
            continue
        # A blocked track has no audio and therefore no numbers to rank on.
        if candidate.get("bpm") is None and candidate.get("energy") is None:
            continue
        result = score_track(candidate, previous, target_energy, current_set,
                             position, bpm_tolerance, harmonic_only)
        if result:
            scored.append(result)

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit] if limit else scored
