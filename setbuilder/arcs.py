"""Energy arcs: functions from set position (0..1) to target energy (0..1)."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import db

CUSTOM_POINTS = 6


def _clip(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def hermite(keypoints: Sequence[Sequence[float]], position: float) -> float:
    """Smooth cubic Hermite interpolation through (x, y) keypoints.

    Tangents are Catmull-Rom finite differences adapted to non-uniform x
    spacing, so a keypoint can sit anywhere -- which is what lets the classic
    build dip at exactly 0.65 rather than on a sixth-of-the-set grid.
    """
    points = sorted(((float(x), float(y)) for x, y in keypoints), key=lambda p: p[0])
    count = len(points)
    if count == 0:
        return 0.0
    if count == 1:
        return _clip(points[0][1])

    position = min(1.0, max(0.0, float(position)))
    if position <= points[0][0]:
        return _clip(points[0][1])
    if position >= points[-1][0]:
        return _clip(points[-1][1])

    index = 0
    for candidate in range(count - 1):
        if points[candidate][0] <= position <= points[candidate + 1][0]:
            index = candidate
            break

    x0, y0 = points[index]
    x1, y1 = points[index + 1]
    width = x1 - x0
    if width <= 0:
        return _clip(y1)
    t = (position - x0) / width

    def tangent(i: int) -> float:
        previous = points[max(i - 1, 0)]
        following = points[min(i + 1, count - 1)]
        run = following[0] - previous[0]
        if run <= 0:
            return 0.0
        return (following[1] - previous[1]) / run

    m0 = tangent(index) * width
    m1 = tangent(index + 1) * width

    t2, t3 = t * t, t * t * t
    value = ((2 * t3 - 3 * t2 + 1) * y0
             + (t3 - 2 * t2 + t) * m0
             + (-2 * t3 + 3 * t2) * y1
             + (t3 - t2) * m1)
    return _clip(value)


def catmull_rom(values: Sequence[float], position: float) -> float:
    """Interpolate evenly spaced control points (the custom-arc editor's shape)."""
    points = [float(v) for v in values]
    count = len(points)
    if count < 2:
        return _clip(points[0]) if points else 0.0
    keypoints = [(index / (count - 1), value) for index, value in enumerate(points)]
    return hermite(keypoints, position)


def _from_points(values: Sequence[float]) -> Callable[[float], float]:
    return lambda position: catmull_rom(values, position)


def _from_keypoints(keypoints: Sequence[Sequence[float]]) -> Callable[[float], float]:
    return lambda position: hermite(keypoints, position)


def _warm_up(position: float) -> float:
    # Gentle, essentially linear rise from a cold room to something moving.
    return _clip(0.20 + 0.35 * position)


_CLASSIC_BUILD_POINTS = [
    (0.00, 0.30), (0.30, 0.62), (0.50, 0.86),
    (0.65, 0.75),  # the breather
    (0.82, 0.93), (1.00, 1.00),
]


def _classic_build(position: float) -> float:
    """0.30 -> 1.0 with a breather down to 0.75 around position 0.65."""
    return hermite(_CLASSIC_BUILD_POINTS, position)


def _peak_time(position: float) -> float:
    """0.65 -> 0.95, held high, with a small oscillation so it breathes."""
    base = 0.65 + 0.30 * position
    oscillation = 0.04 * math.sin(position * math.pi * 4.0)
    return _clip(base + oscillation)


_PEAK_VALLEY_PEAK_POINTS = [
    (0.00, 0.55), (0.28, 0.90),
    (0.50, 0.50),  # the valley, exactly at the midpoint
    (0.75, 0.85), (1.00, 1.00),
]


def _peak_valley_peak(position: float) -> float:
    """Up to 0.9, down to 0.5 at the midpoint, back up to 1.0."""
    return hermite(_PEAK_VALLEY_PEAK_POINTS, position)


def _closing(position: float) -> float:
    """0.85 -> 0.35, a long comedown that stays up a little longer than linear."""
    return _clip(0.85 - 0.50 * (position ** 1.35))


@dataclass(frozen=True)
class Arc:
    key: str
    name: str
    description: str
    function: Callable[[float], float]

    def target(self, position: float) -> float:
        return _clip(self.function(min(1.0, max(0.0, position))))

    def curve(self, samples: int = 41) -> List[Dict[str, float]]:
        if samples < 2:
            samples = 2
        return [
            {"position": index / (samples - 1),
             "target": round(self.target(index / (samples - 1)), 4)}
            for index in range(samples)
        ]


BUILT_INS: Dict[str, Arc] = {
    arc.key: arc for arc in [
        Arc("warm_up", "Warm-up", "0.20 → 0.55, gentle linear rise", _warm_up),
        Arc("classic_build", "Classic build",
            "0.30 → 1.0, small dip to 0.75 around 0.65", _classic_build),
        Arc("peak_time", "Peak time",
            "0.65 → 0.95, sustained with small oscillation", _peak_time),
        Arc("peak_valley_peak", "Peak–valley–peak",
            "rise to 0.9, drop to 0.5 at the midpoint, back to 1.0",
            _peak_valley_peak),
        Arc("closing", "Closing", "0.85 → 0.35, long comedown", _closing),
    ]
}

DEFAULT_ARC = "classic_build"


# --- custom arcs ---------------------------------------------------------

def save_custom_arc(name: str, points: Sequence[float]) -> Dict[str, Any]:
    values = [_clip(v) for v in points]
    if len(values) != CUSTOM_POINTS:
        raise ValueError(f"a custom arc needs exactly {CUSTOM_POINTS} control points")
    db.execute(
        "INSERT INTO custom_arcs (name, points, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET points=excluded.points, "
        "created_at=excluded.created_at",
        (name.strip(), json.dumps(values), time.time()),
    )
    return {"key": f"custom:{name.strip()}", "name": name.strip(), "points": values}


def delete_custom_arc(name: str) -> None:
    db.execute("DELETE FROM custom_arcs WHERE name = ?", (name.strip(),))


def load_custom_arcs() -> List[Dict[str, Any]]:
    rows = db.query("SELECT name, points FROM custom_arcs ORDER BY name")
    arcs = []
    for row in rows:
        try:
            points = json.loads(row["points"])
        except json.JSONDecodeError:
            continue
        arcs.append({"key": f"custom:{row['name']}", "name": row["name"],
                     "points": points, "custom": True})
    return arcs


def get_arc(key: Optional[str]) -> Arc:
    """Resolve an arc key, including ``custom:<name>``.  Falls back to the default."""
    if key and key.startswith("custom:"):
        name = key.split(":", 1)[1]
        row = db.query_one("SELECT name, points FROM custom_arcs WHERE name = ?",
                           (name,))
        if row:
            try:
                points = json.loads(row["points"])
                return Arc(key, row["name"], "custom arc", _from_points(points))
            except json.JSONDecodeError:
                pass
    if key and key.startswith("points:"):
        # An unsaved curve straight from the editor.
        try:
            points = [float(v) for v in key.split(":", 1)[1].split(",")]
            return Arc(key, "Custom", "unsaved custom arc", _from_points(points))
        except ValueError:
            pass
    return BUILT_INS.get(key or DEFAULT_ARC, BUILT_INS[DEFAULT_ARC])


def list_arcs() -> List[Dict[str, Any]]:
    listed: List[Dict[str, Any]] = [
        {"key": arc.key, "name": arc.name, "description": arc.description,
         "custom": False, "curve": arc.curve()}
        for arc in BUILT_INS.values()
    ]
    for custom in load_custom_arcs():
        arc = get_arc(custom["key"])
        listed.append({**custom, "description": "custom arc", "curve": arc.curve()})
    return listed


def slot_position(index: int, set_length: int) -> float:
    """Normalised position of slot ``index`` (0-based) in a set of ``set_length``."""
    if set_length <= 1:
        return 0.0
    return min(1.0, max(0.0, index / float(set_length - 1)))


def target_energy_10(arc: Arc, index: int, set_length: int) -> float:
    """Target energy for a slot, on the same 1-10 scale as track energy."""
    return round(1.0 + 9.0 * arc.target(slot_position(index, set_length)), 2)
