"""Arc shapes and the interpolation behind them."""

import pytest

from setbuilder import arcs


@pytest.mark.parametrize("key", list(arcs.BUILT_INS))
def test_every_arc_stays_in_range(key):
    arc = arcs.BUILT_INS[key]
    for step in range(101):
        value = arc.target(step / 100.0)
        assert 0.0 <= value <= 1.0


@pytest.mark.parametrize("key,start,end", [
    ("warm_up", 0.20, 0.55),
    ("classic_build", 0.30, 1.00),
    ("peak_time", 0.65, 0.95),
    ("closing", 0.85, 0.35),
])
def test_endpoints_match_the_spec(key, start, end):
    arc = arcs.BUILT_INS[key]
    assert arc.target(0.0) == pytest.approx(start, abs=0.02)
    assert arc.target(1.0) == pytest.approx(end, abs=0.02)


def test_warm_up_rises_monotonically():
    arc = arcs.BUILT_INS["warm_up"]
    values = [arc.target(step / 20.0) for step in range(21)]
    assert values == sorted(values)


def test_classic_build_dips_around_two_thirds():
    """The breather is the whole character of this arc."""
    arc = arcs.BUILT_INS["classic_build"]
    assert arc.target(0.65) == pytest.approx(0.75, abs=0.03)
    # It must be a genuine dip: lower than the peak before it, then recover.
    assert arc.target(0.65) < arc.target(0.50)
    assert arc.target(1.0) > arc.target(0.65)


def test_peak_time_stays_high_and_oscillates():
    arc = arcs.BUILT_INS["peak_time"]
    values = [arc.target(step / 40.0) for step in range(41)]
    assert min(values) >= 0.60
    assert max(values) <= 1.0
    # "sustained with small oscillation" -- it must not be monotonic.
    assert values != sorted(values)


def test_peak_valley_peak_hits_its_valley_at_the_midpoint():
    arc = arcs.BUILT_INS["peak_valley_peak"]
    assert arc.target(0.5) == pytest.approx(0.50, abs=0.03)
    assert max(arc.target(p / 100) for p in range(0, 50)) >= 0.88
    assert arc.target(1.0) == pytest.approx(1.0, abs=0.02)


def test_closing_falls_monotonically():
    arc = arcs.BUILT_INS["closing"]
    values = [arc.target(step / 20.0) for step in range(21)]
    assert values == sorted(values, reverse=True)


# --- interpolation -------------------------------------------------------

def test_hermite_passes_through_its_keypoints():
    points = [(0.0, 0.2), (0.35, 0.9), (0.65, 0.4), (1.0, 0.8)]
    for x, y in points:
        assert arcs.hermite(points, x) == pytest.approx(y, abs=1e-9)


def test_hermite_clamps_outside_the_domain():
    points = [(0.0, 0.2), (1.0, 0.8)]
    assert arcs.hermite(points, -5.0) == pytest.approx(0.2)
    assert arcs.hermite(points, 99.0) == pytest.approx(0.8)


def test_hermite_output_is_always_clipped():
    # Steep neighbouring points make a cubic overshoot; it must still be 0-1.
    points = [(0.0, 0.0), (0.1, 1.0), (0.2, 0.0), (1.0, 1.0)]
    for step in range(101):
        assert 0.0 <= arcs.hermite(points, step / 100.0) <= 1.0


def test_catmull_rom_hits_its_control_points():
    values = [0.1, 0.4, 0.9, 0.5, 0.2, 1.0]
    for index, value in enumerate(values):
        position = index / (len(values) - 1)
        assert arcs.catmull_rom(values, position) == pytest.approx(value, abs=1e-9)


def test_catmull_rom_handles_degenerate_input():
    assert arcs.catmull_rom([], 0.5) == 0.0
    assert arcs.catmull_rom([0.7], 0.5) == pytest.approx(0.7)


# --- positions and targets ----------------------------------------------

def test_slot_position_spans_zero_to_one():
    assert arcs.slot_position(0, 20) == 0.0
    assert arcs.slot_position(19, 20) == 1.0
    assert arcs.slot_position(9, 20) == pytest.approx(9 / 19)


def test_slot_position_handles_a_one_track_set():
    assert arcs.slot_position(0, 1) == 0.0
    assert arcs.slot_position(5, 1) == 0.0


def test_slot_position_is_clamped_past_the_end():
    assert arcs.slot_position(40, 20) == 1.0


def test_target_energy_is_on_the_one_to_ten_scale():
    arc = arcs.BUILT_INS["classic_build"]
    first = arcs.target_energy_10(arc, 0, 20)
    last = arcs.target_energy_10(arc, 19, 20)
    assert 1.0 <= first <= 10.0
    assert last == pytest.approx(10.0, abs=0.2)
    assert first == pytest.approx(1.0 + 9.0 * 0.30, abs=0.2)


# --- custom arcs ---------------------------------------------------------

def test_save_and_load_a_custom_arc(clean_db):
    points = [0.1, 0.3, 0.5, 0.7, 0.9, 1.0]
    arcs.save_custom_arc("friday", points)

    loaded = arcs.load_custom_arcs()
    assert [a["name"] for a in loaded] == ["friday"]

    arc = arcs.get_arc("custom:friday")
    assert arc.target(0.0) == pytest.approx(0.1)
    assert arc.target(1.0) == pytest.approx(1.0)

    arcs.delete_custom_arc("friday")
    assert arcs.load_custom_arcs() == []


def test_custom_arc_requires_six_points(clean_db):
    with pytest.raises(ValueError):
        arcs.save_custom_arc("bad", [0.1, 0.2])


def test_unsaved_points_arc(clean_db):
    arc = arcs.get_arc("points:0.2,0.4,0.6,0.8,0.9,1.0")
    assert arc.target(0.0) == pytest.approx(0.2)
    assert arc.target(1.0) == pytest.approx(1.0)


def test_unknown_arc_key_falls_back_to_the_default(clean_db):
    assert arcs.get_arc("nope").key == arcs.DEFAULT_ARC
    assert arcs.get_arc(None).key == arcs.DEFAULT_ARC
    assert arcs.get_arc("custom:missing").key == arcs.DEFAULT_ARC
