"""Fuzzy matching of SoundCloud tracks to local files.

Exercises the three bands from the spec (>=87 accept, 70-87 confirm, <70
unmatched) and the guards that stop a confident-looking match from being the
wrong piece of audio.
"""

import time

from setbuilder import db, library
from setbuilder.normalize import match_key


def add_local_file(path, title, artist, duration_s=360.0, tag_bpm=None,
                   tag_key=None, label=""):
    db.execute(
        "INSERT INTO local_files (path, sha256, size, mtime, title, artist, "
        "album, label, tag_bpm, tag_key, duration_s, match_key, scanned_at) "
        "VALUES (?, ?, 1000, 1.0, ?, ?, '', ?, ?, ?, ?, ?, ?)",
        (path, f"sha-{path}", title, artist, label, tag_bpm, tag_key,
         duration_s, match_key(artist, title), time.time()),
    )


def sc_track(title, artist="", duration_ms=360_000, track_id="1"):
    return {"sc_id": track_id, "title": title, "artist": artist,
            "duration_ms": duration_ms, "access": "playable"}


def test_noisy_title_matches_a_clean_local_file(clean_db):
    add_local_file("/m/1.mp3", "Shella Verse", "Sammy Virji")
    [result] = library.match_tracks([sc_track(
        "PREMIERE: Sammy Virji - Shella Verse (Original Mix) [XL] FREE DOWNLOAD 🔥")])
    assert result["match_state"] == "matched"
    assert result["local_path"] == "/m/1.mp3"
    assert result["match_score"] >= library.ACCEPT_THRESHOLD


def test_an_unrelated_track_is_unmatched(clean_db):
    add_local_file("/m/1.mp3", "Shella Verse", "Sammy Virji")
    [result] = library.match_tracks([sc_track("Tremors", "Interplanetary Criminal")])
    assert result["match_state"] == "unmatched"
    assert result["local_path"] is None


def test_a_remix_does_not_silently_match_the_original(clean_db):
    """The whole point: never quietly put the wrong version in the set."""
    add_local_file("/m/original.mp3", "Song Title", "Artist")
    [result] = library.match_tracks([sc_track("Artist - Song Title (Skepsis Remix)")])
    assert result["match_state"] == "possible"
    assert "remix" in (result["match_reason"] or "").lower()


def test_the_original_does_not_match_a_local_remix(clean_db):
    add_local_file("/m/remix.mp3", "Song Title (Skepsis Remix)", "Artist")
    [result] = library.match_tracks([sc_track("Artist - Song Title")])
    assert result["match_state"] == "possible"


def test_the_same_remix_on_both_sides_matches_cleanly(clean_db):
    add_local_file("/m/remix.mp3", "Song Title (Skepsis Remix)", "Artist")
    [result] = library.match_tracks([
        sc_track("Artist - Song Title (Skepsis Remix) FREE DOWNLOAD")])
    assert result["match_state"] == "matched"


def test_mix_descriptors_are_not_treated_as_versions(clean_db):
    """(Original Mix) / (Extended Mix) must not trip the version guard."""
    add_local_file("/m/1.mp3", "Song Title", "Artist")
    [result] = library.match_tracks([sc_track("Artist - Song Title (Extended Mix)")])
    assert result["match_state"] == "matched"


def test_a_big_duration_gap_demotes_to_confirm(clean_db):
    """Same words, very different length: probably a radio edit, so ask."""
    add_local_file("/m/1.mp3", "Song Title", "Artist", duration_s=360.0)
    [result] = library.match_tracks([
        sc_track("Artist - Song Title", duration_ms=120_000)])
    assert result["match_state"] == "possible"
    assert "duration" in (result["match_reason"] or "").lower()


def test_a_short_preview_does_not_trip_the_duration_guard(clean_db):
    """A 30s preview is not evidence of a different edit."""
    add_local_file("/m/1.mp3", "Song Title", "Artist", duration_s=360.0)
    [result] = library.match_tracks([
        sc_track("Artist - Song Title", duration_ms=30_000)])
    assert result["match_state"] == "matched"


def test_matching_with_an_empty_library_is_graceful(clean_db):
    results = library.match_tracks([sc_track("Anything At All")])
    assert results[0]["match_state"] == "unmatched"
    assert results[0]["local_path"] is None


def test_tags_are_carried_through_for_matched_files(clean_db):
    """Serato/Rekordbox BPM and key ride along so analysis can skip detection."""
    add_local_file("/m/1.mp3", "Song Title", "Artist", tag_bpm=128.0, tag_key="8A")
    [result] = library.match_tracks([sc_track("Artist - Song Title")])
    assert result["local_tag_bpm"] == 128.0
    assert result["local_tag_key"] == "8A"


def test_match_one_returns_ranked_candidates(clean_db):
    add_local_file("/m/1.mp3", "Shella Verse", "Sammy Virji")
    add_local_file("/m/2.mp3", "Tremors", "Interplanetary Criminal")
    candidates = library.match_one("Sammy Virji", "Shella Verse")
    assert candidates[0]["path"] == "/m/1.mp3"
    assert candidates[0]["score"] > candidates[-1]["score"]


def test_library_stats_counts_tags(clean_db):
    add_local_file("/m/1.mp3", "A", "X", tag_bpm=128.0, tag_key="8A")
    add_local_file("/m/2.mp3", "B", "Y")
    stats = library.library_stats()
    assert stats == {"files": 2, "with_tag_bpm": 1, "with_tag_key": 1}
