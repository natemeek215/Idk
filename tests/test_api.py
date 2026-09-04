"""End-to-end tests through the HTTP API with a seeded database.

These never touch SoundCloud or decode any audio -- they seed the analysis
cache directly and exercise the paths the UI actually calls.
"""

import time

import pytest
from fastapi.testclient import TestClient

from setbuilder import db
from setbuilder.api import app


@pytest.fixture()
def client(clean_db):
    return TestClient(app)


def seed(track_id, title, artist, bpm, camelot, energy, *, label="",
         confidence="full", source="local", access="playable", position=0):
    """Insert a track plus its cached analysis."""
    cache_key = f"key-{track_id}"
    db.execute(
        "INSERT INTO tracks (id, sc_id, playlist_id, position, title, artist, "
        "access, label, source, local_path, match_state, cache_key) "
        "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 'matched', ?)",
        (track_id, f"sc{track_id}", position, title, artist, access, label,
         source, f"/music/{track_id}.mp3", cache_key),
    )
    db.save_analysis(cache_key, {
        "bpm": bpm, "bpm_raw": bpm, "bpm_source": "analyzed",
        "camelot": camelot, "key_name": "n/a", "key_source": "essentia",
        "features": {"rms_sub": -30.0, "onset_rate": 3.0, "fullness": 0.3},
        "energy": energy, "energy_source": "heuristic", "confidence": confidence,
        "duration_s": 360.0, "analyzed_at": time.time(), "error": None,
    })
    return track_id


@pytest.fixture()
def seeded(client):
    db.execute("INSERT INTO playlists (id, sc_id, title, url, imported_at) "
               "VALUES (1, 'p1', 'Test set', 'http://x', ?)", (time.time(),))
    seed(1, "Opener", "Alpha", 126.0, "8A", 3.0, position=0)
    seed(2, "Groover", "Beta", 128.0, "8A", 5.0, position=1)
    seed(3, "Lifter", "Gamma", 128.5, "9A", 6.5, position=2)
    seed(4, "Banger", "Delta", 129.0, "9A", 8.5, position=3)
    seed(5, "Garage cut", "Epsilon", 138.0, "4A", 7.0, position=4)
    seed(6, "Half-timer", "Zeta", 69.0, "4A", 6.8, position=5)
    seed(7, "Preview only", "Eta", 130.0, "10A", 6.0,
         confidence="preview", source="preview", position=6)
    seed(8, "Blocked", "Theta", None, None, None,
         source="unmatched", access="blocked", position=7)
    return client


# --- status and tracks ---------------------------------------------------

def test_status_reports_a_fresh_install(client):
    body = client.get("/api/status").json()
    assert body["has_credentials"] is False
    assert body["setup_complete"] is False
    assert body["calibration"]["model_active"] is False
    assert body["token"]["budget"] == 50


def test_tracks_listing_and_counts(seeded):
    body = seeded.get("/api/tracks").json()
    assert body["counts"]["total"] == 8
    assert body["counts"]["analyzed"] == 7
    assert body["counts"]["preview"] == 1
    assert body["counts"]["blocked"] == 1


def test_low_confidence_is_surfaced_not_hidden(seeded):
    tracks = {t["title"]: t for t in seeded.get("/api/tracks").json()["tracks"]}

    preview = tracks["Preview only"]
    assert preview["badge"] == "preview"
    assert any("30s preview" in w for w in preview["warnings"])

    blocked = tracks["Blocked"]
    assert blocked["analyzed"] is False
    assert any("add file to library" in w.lower() for w in blocked["warnings"])


def test_import_requires_credentials(client):
    response = client.post("/api/import", json={"url": "https://soundcloud.com/a/sets/b"})
    assert response.status_code == 400
    assert "credentials" in response.json()["detail"].lower()


def test_import_rejects_a_non_soundcloud_url(client):
    response = client.post("/api/import", json={"url": "https://example.com/x"})
    assert response.status_code == 400


# --- arcs ----------------------------------------------------------------

def test_arcs_endpoint_lists_all_five_built_ins(client):
    body = client.get("/api/arcs").json()
    keys = {arc["key"] for arc in body["arcs"]}
    assert keys == {"warm_up", "classic_build", "peak_time",
                    "peak_valley_peak", "closing"}
    assert all(len(arc["curve"]) > 10 for arc in body["arcs"])


def test_custom_arc_round_trip(client):
    points = [0.2, 0.4, 0.6, 0.5, 0.8, 1.0]
    created = client.post("/api/arcs/custom",
                          json={"name": "friday", "points": points}).json()
    assert created["arc"]["key"] == "custom:friday"
    assert any(a["key"] == "custom:friday" for a in created["arcs"])

    remaining = client.delete("/api/arcs/custom/friday").json()
    assert not any(a["key"] == "custom:friday" for a in remaining["arcs"])


def test_custom_arc_rejects_the_wrong_point_count(client):
    response = client.post("/api/arcs/custom", json={"name": "bad", "points": [0.1, 0.2]})
    assert response.status_code == 400


# --- suggestions ---------------------------------------------------------

def test_first_slot_follows_the_arc_target(seeded):
    body = seeded.post("/api/suggest", json={
        "set_track_ids": [], "arc": "warm_up", "set_length": 20,
    }).json()
    assert body["slot"] == 1
    assert body["position"] == 0.0
    # Warm-up starts at 0.20 -> 1 + 9*0.2 = 2.8
    assert body["target_energy"] == pytest.approx(2.8, abs=0.1)
    # The lowest-energy track should lead for a cold opener.
    assert body["suggestions"][0]["title"] == "Opener"


def test_suggestions_advance_with_the_set(seeded):
    body = seeded.post("/api/suggest", json={
        "set_track_ids": [1, 2], "arc": "classic_build", "set_length": 10,
    }).json()
    assert body["slot"] == 3
    assert body["position"] == pytest.approx(2 / 9, abs=1e-4)
    assert all(item["id"] not in (1, 2) for item in body["suggestions"])
    assert len(body["suggestions"]) <= 5


def test_every_suggestion_carries_a_reason_and_verdict(seeded):
    body = seeded.post("/api/suggest", json={
        "set_track_ids": [2], "arc": "classic_build", "set_length": 20,
    }).json()
    for item in body["suggestions"]:
        assert item["reason"]
        assert item["verdict"]
        assert "arc wants" in item["reason"]
        assert set(item["components"]) >= {"bpm", "harmonic", "energy", "variety"}


def test_half_time_transition_is_offered_and_labelled(seeded):
    """138 into 69 must be reachable and named as half-time."""
    body = seeded.post("/api/suggest", json={
        "set_track_ids": [5], "arc": "peak_time", "set_length": 20, "limit": 10,
    }).json()
    half = next((s for s in body["suggestions"] if s["title"] == "Half-timer"), None)
    assert half is not None
    assert half["components"]["bpm_relation"] == "half-time"
    assert "half-time" in half["reason"]


def test_harmonic_only_narrows_the_list(seeded):
    payload = {"set_track_ids": [2], "arc": "classic_build",
               "set_length": 20, "limit": 20}
    wide = seeded.post("/api/suggest", json=payload).json()
    narrow = seeded.post("/api/suggest",
                         json={**payload, "harmonic_only": True}).json()
    assert len(narrow["suggestions"]) < len(wide["suggestions"])
    # Everything left must be genuinely compatible with 8A.
    assert all(s["components"]["harmonic"] >= 0.55 for s in narrow["suggestions"])


def test_bpm_tolerance_slider_changes_the_ranking(seeded):
    payload = {"set_track_ids": [2], "arc": "classic_build", "set_length": 20,
               "limit": 20}
    narrow = seeded.post("/api/suggest", json={**payload, "bpm_tolerance": 0.02}).json()
    wide = seeded.post("/api/suggest", json={**payload, "bpm_tolerance": 0.20}).json()
    narrow_scores = {s["id"]: s["score"] for s in narrow["suggestions"]}
    wide_scores = {s["id"]: s["score"] for s in wide["suggestions"]}
    # The 138 BPM track is far from 128; a wider window must help it.
    assert wide_scores[5] > narrow_scores[5]


def test_blocked_tracks_are_never_suggested(seeded):
    body = seeded.post("/api/suggest", json={
        "set_track_ids": [], "arc": "peak_time", "set_length": 20, "limit": 20,
    }).json()
    assert all(item["title"] != "Blocked" for item in body["suggestions"])


def test_beyond_set_length_is_flagged_not_blocked(seeded):
    body = seeded.post("/api/suggest", json={
        "set_track_ids": [1, 2, 3], "arc": "warm_up", "set_length": 3,
    }).json()
    assert body["beyond_set_length"] is True
    assert body["suggestions"]


# --- plot ----------------------------------------------------------------

def test_plot_returns_target_and_actual_series(seeded):
    body = seeded.post("/api/set/plot", json={
        "set_track_ids": [1, 2, 4], "arc": "classic_build", "set_length": 12,
    }).json()
    assert len(body["target"]) == 12
    assert len(body["actual"]) == 3
    assert body["actual"][0]["energy"] == 3.0
    assert body["target"][0]["slot"] == 1
    assert all(1.0 <= point["energy"] <= 10.0 for point in body["target"])


def test_plot_preserves_the_set_order(seeded):
    body = seeded.post("/api/set/plot", json={
        "set_track_ids": [4, 1, 2], "arc": "closing", "set_length": 5,
    }).json()
    assert [point["title"] for point in body["actual"]] == \
           ["Banger", "Opener", "Groover"]


def test_plot_marks_preview_confidence(seeded):
    body = seeded.post("/api/set/plot", json={
        "set_track_ids": [7], "arc": "peak_time", "set_length": 5,
    }).json()
    assert body["actual"][0]["confidence"] == "preview"


# --- export --------------------------------------------------------------

def test_m3u_export(seeded):
    response = seeded.post("/api/export", json={
        "track_ids": [1, 2], "name": "Friday night", "format": "m3u",
    })
    assert response.status_code == 200
    body = response.text
    assert body.startswith("#EXTM3U")
    assert "Alpha - Opener" in body
    assert "/music/1.mp3" in body
    assert "Friday-night.m3u8" in response.headers["content-disposition"]


def test_text_export_carries_bpm_and_key_per_line(seeded):
    body = seeded.post("/api/export", json={
        "track_ids": [1, 2, 7], "name": "Set", "arc": "peak_time", "format": "txt",
    }).text
    assert "Arc: Peak time" in body
    assert "126 BPM · 8A" in body
    assert "128 BPM · 8A" in body
    # Low-confidence data must be labelled, not silently presented as solid.
    assert "energy estimated from 30s preview" in body


def test_export_rejects_an_empty_set(seeded):
    assert seeded.post("/api/export",
                       json={"track_ids": [], "format": "m3u"}).status_code == 400


# --- calibration ---------------------------------------------------------

def test_calibration_starts_on_the_heuristic(seeded):
    body = seeded.get("/api/calibration/next?limit=1").json()
    assert body["model_active"] is False
    assert body["labels"] == 0
    assert len(body["tracks"]) == 1


def test_labelling_below_the_threshold_keeps_the_heuristic(seeded):
    body = seeded.post("/api/calibration/label",
                       json={"cache_key": "key-1", "rating": 4.0}).json()
    assert body["calibration"]["labels"] == 1
    assert body["calibration"]["model_active"] is False
    assert body["refit"] is None


def test_model_takes_over_at_twenty_five_labels(seeded):
    # Seed enough analysed tracks to label, with features that vary.
    for index in range(10, 40):
        cache_key = f"key-{index}"
        db.execute(
            "INSERT INTO tracks (id, sc_id, playlist_id, position, title, artist, "
            "access, source, match_state, cache_key) VALUES "
            "(?, ?, 1, ?, ?, 'Artist', 'playable', 'local', 'matched', ?)",
            (index, f"sc{index}", index, f"Track {index}", cache_key),
        )
        db.save_analysis(cache_key, {
            "bpm": 128.0, "camelot": "8A",
            "features": {"rms_sub": -40.0 + index, "onset_rate": index / 8.0,
                         "fullness": index / 50.0, "percussive_ratio": 0.3 + index / 100.0},
            "energy": 5.0, "energy_source": "heuristic", "confidence": "full",
            "analyzed_at": time.time(),
        })

    last = None
    for index in range(10, 38):
        last = seeded.post("/api/calibration/label", json={
            "cache_key": f"key-{index}", "rating": 1.0 + (index - 10) * 0.3,
        }).json()

    assert last["calibration"]["labels"] >= 25
    assert last["calibration"]["model_active"] is True
    assert last["refit"]["fitted"] is True

    # The fitted model must now be what the library shows.
    tracks = seeded.get("/api/tracks").json()["tracks"]
    scored = [t for t in tracks if t["energy_source"] == "model"]
    assert scored, "model should have rescored the cached analyses"

    # And the status line must say so rather than quietly switching.
    assert seeded.get("/api/status").json()["calibration"]["model_active"] is True


def test_resetting_labels_returns_to_the_heuristic(seeded):
    seeded.post("/api/calibration/label", json={"cache_key": "key-1", "rating": 4.0})
    body = seeded.delete("/api/calibration/labels").json()
    assert body["calibration"]["labels"] == 0
    assert body["calibration"]["model_active"] is False


# --- settings ------------------------------------------------------------

def test_settings_round_trip(client):
    body = client.post("/api/settings", json={
        "set_length": 32, "arc": "peak_time", "harmonic_only": True,
    }).json()
    assert body["settings"]["set_length"] == 32
    assert body["settings"]["arc"] == "peak_time"
    assert client.get("/api/status").json()["settings"]["set_length"] == 32


# --- static ---------------------------------------------------------------

def test_index_and_assets_are_served(client):
    assert client.get("/healthz").text == "ok"
    index = client.get("/")
    assert index.status_code == 200
    assert "SetBuilder" in index.text or "Set Builder" in index.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
