"""Title normalisation -- the front half of local-file matching."""

import pytest
from rapidfuzz import fuzz

from setbuilder.library import ACCEPT_THRESHOLD, CONFIRM_THRESHOLD
from setbuilder.normalize import match_key, normalize_title, split_artist_title


@pytest.mark.parametrize("raw,expected", [
    ("Sammy Virji - Shella Verse (Original Mix)", "sammy virji shella verse"),
    ("Chris Stussy - Ill Be There (Extended Mix)", "chris stussy ill be there"),
    ("Track Name [Label Records]", "track name"),
    ("Track Name FREE DOWNLOAD", "track name"),
    ("Track Name OUT NOW", "track name"),
    ("Artist ft. Someone - Song", "artist someone song"),
    ("Artist feat. Someone - Song", "artist someone song"),
    ("Artist featuring Someone - Song", "artist someone song"),
    ("Song Title | Label Channel", "song title"),
    ("PREMIERE: Artist - Song", "artist song"),
    ("[Free DL] Skepsis - Hold Me Close", "skepsis hold me close"),
])
def test_normalisation_strips_noise(raw, expected):
    assert normalize_title(raw) == expected


def test_emoji_are_removed():
    assert normalize_title("salute - Wild Youth 🌊🔥") == "salute wild youth"


def test_pipe_truncation_survives_emoji_stripping():
    """'|' is a symbol codepoint, so ordering here is easy to get wrong."""
    assert normalize_title("Song 🔥 | Some Label") == "song"


def test_case_and_punctuation_are_flattened():
    assert normalize_title("HOLD  ME   CLOSE") == "hold me close"
    assert normalize_title("B.O.T.A.") == "b o t a"


def test_a_parenthesised_subtitle_is_kept():
    """Only mix descriptors are noise; a real subtitle identifies the track."""
    assert normalize_title("B.O.T.A. (Baddest Of Them All)") == \
           "b o t a baddest of them all"


@pytest.mark.parametrize("raw,expected", [
    ("Song (Skepsis Remix)", "song skepsis remix"),
    ("Song (VIP)", "song vip"),
    ("Song (Bootleg)", "song bootleg"),
])
def test_version_credits_survive_normalisation(raw, expected):
    assert normalize_title(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_empty_input(raw):
    assert normalize_title(raw) == ""


def test_a_wholly_bracketed_title_is_not_erased():
    """Stripping brackets must not leave nothing to match on."""
    assert normalize_title("(Original Mix)") == ""
    assert normalize_title("[Artist] [Title]") != ""


@pytest.mark.parametrize("raw,artist,title", [
    ("Sammy Virji - Shella Verse", "Sammy Virji", "Shella Verse"),
    ("Artist – Title", "Artist", "Title"),
    ("Artist — Title", "Artist", "Title"),
    ("No separator here", "", "No separator here"),
])
def test_split_artist_title(raw, artist, title):
    assert split_artist_title(raw) == (artist, title)


def test_match_key_does_not_duplicate_the_artist():
    """SoundCloud titles usually already contain the artist name."""
    assert match_key("Sammy Virji", "Sammy Virji - Shella Verse") == \
           "sammy virji shella verse"
    assert match_key("Sammy Virji", "Shella Verse") == "sammy virji shella verse"


# --- the matching behaviour these thresholds produce ---------------------

def score(left, right):
    return fuzz.token_set_ratio(normalize_title(left), normalize_title(right))


def test_noisy_soundcloud_title_matches_a_clean_filename():
    soundcloud = "PREMIERE: Sammy Virji - Shella Verse (Original Mix) [XL] FREE DOWNLOAD 🔥"
    local = "Sammy Virji - Shella Verse"
    assert score(soundcloud, local) >= ACCEPT_THRESHOLD


def test_word_order_does_not_break_the_match():
    assert score("Shella Verse - Sammy Virji", "Sammy Virji - Shella Verse") >= \
           ACCEPT_THRESHOLD


def test_different_tracks_stay_below_the_confirm_floor():
    assert score("Sammy Virji - Shella Verse",
                 "Interplanetary Criminal - Tremors") < CONFIRM_THRESHOLD


def test_token_set_ratio_alone_cannot_see_a_remix_suffix():
    """Documents *why* matching needs a version guard on top of the scorer.

    token_set_ratio scores on the intersection of the two token sets, so the
    remix's extra tokens are invisible to it and it returns a perfect 100.
    """
    assert score("Artist - Song Title",
                 "Artist - Song Title (Someone Else Remix)") == 100.0


def test_thresholds_are_ordered_as_specified():
    assert CONFIRM_THRESHOLD == 70.0
    assert ACCEPT_THRESHOLD == 87.0
    assert CONFIRM_THRESHOLD < ACCEPT_THRESHOLD


def test_version_signature_separates_remixes_from_originals():
    from setbuilder.normalize import version_signature as sig
    assert sig("Artist - Song Title") == frozenset()
    assert sig("Artist - Song Title (Original Mix)") == frozenset()
    assert sig("Artist - Song Title (Extended Mix)") == frozenset()
    assert sig("Artist - Song (Skepsis Remix)") == frozenset({"remix"})
    assert sig("Artist - Song (VIP)") == frozenset({"vip"})
    assert sig("Artist - Song (Bootleg)") == frozenset({"bootleg"})
    # Same version on both sides is not a conflict.
    assert sig("Song (Skepsis Remix) FREE DOWNLOAD") == sig("Song (Skepsis Remix)")


# --- display titles ------------------------------------------------------

@pytest.mark.parametrize("title,artist,expected", [
    ("Alpha Nine - Cold Open", "Alpha Nine", "Cold Open"),
    ("PREMIERE: Alpha Nine - Cold Open", "Alpha Nine", "Cold Open"),
    ("Beta Crew - Rolling Groove 🔥 | Some Channel", "Beta Crew", "Rolling Groove"),
    ("OUT NOW: Track Name", "Artist", "Track Name"),
    ("Track Name FREE DOWNLOAD", "Artist", "Track Name"),
    ("Just A Title", "Someone Else", "Just A Title"),
])
def test_clean_display_title(title, artist, expected):
    from setbuilder.normalize import clean_display_title
    assert clean_display_title(title, artist) == expected


def test_display_title_keeps_case_and_version_credits():
    from setbuilder.normalize import clean_display_title
    assert clean_display_title("Sammy Virji - Shella Verse (Skepsis Remix)",
                               "Sammy Virji") == "Shella Verse (Skepsis Remix)"


def test_display_title_only_strips_a_matching_artist_prefix():
    """'Other Artist - Song' keeps its prefix when the artist is someone else."""
    from setbuilder.normalize import clean_display_title
    assert clean_display_title("Other Artist - Song", "Label Channel") == \
           "Other Artist - Song"


def test_display_title_never_returns_empty_for_real_input():
    from setbuilder.normalize import clean_display_title
    assert clean_display_title("PREMIERE", "Artist") == "PREMIERE"
    assert clean_display_title("", "Artist") == ""
