"""Title normalisation for matching SoundCloud tracks to local files.

SoundCloud titles carry a lot of noise that never appears in a file's tags:
label brackets, FREE DOWNLOAD shouts, emoji, premiere prefixes.  Both sides of
a comparison go through :func:`normalize_title` before we hand them to
rapidfuzz.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

# Mix descriptors that carry no identity -- two files differing only by these
# are the same track for matching purposes.
_NOISE_PHRASES = [
    "original mix",
    "extended mix",
    "extended version",
    "original version",
    "radio edit",
    "radio mix",
    "club mix",
    "full mix",
    "master",
    "mastered",
    "free download",
    "free dl",
    "freedownload",
    "out now",
    "out today",
    "buy now",
    "download link in description",
    "click buy for free download",
    "premiere",
    "exclusive",
    "forthcoming",
    "unreleased",
    "snippet",
    "clip",
    "preview",
    "hq",
    "hd",
    "320",
    "wav",
]

# Square/curly brackets on SoundCloud are nearly always a label or a tag.
_SQUARE = re.compile(r"[\[\{][^\]\}]*[\]\}]")
# Parentheses are different: they carry mix identity ("(Interplanetary
# Criminal Remix)") as often as they carry noise ("(Original Mix)"), so they
# are judged by their contents rather than deleted outright.
_PAREN = re.compile(r"\(([^()]*)\)")
_FEAT = re.compile(r"\b(?:feat|ft|featuring|w/)\.?\s+", re.IGNORECASE)
_PREMIERE_PREFIX = re.compile(
    r"^\s*(?:\[?\s*(?:premiere|exclusive|free\s*(?:dl|download)|out\s*now)\s*\]?)"
    r"\s*[:\-|]*\s*",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def _is_pure_noise(inner: str) -> bool:
    """True when a parenthesised chunk carries no identity of its own.

    '(original mix)' and '(free download)' are noise; '(Skepsis Remix)' and
    '(VIP)' name a different piece of audio and must survive.
    """
    text = inner.lower()
    for phrase in _NOISE_PHRASES:
        text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text)
    text = re.sub(r"\b(mix|version|edit|remaster(?:ed)?)\b", " ", text)
    return not _NON_WORD.sub(" ", text).strip()


def _strip_emoji(text: str) -> str:
    """Drop symbol/pictograph codepoints, keeping letters, digits and marks."""
    return "".join(
        ch for ch in text
        if unicodedata.category(ch)[0] not in ("S", "C") or ch in ("&", "+")
    )


def normalize_title(text: Optional[str]) -> str:
    """Reduce a title to a comparable core string.

    Lowercases, drops bracketed segments, everything after a ``|``, mix
    descriptors, ``feat.``/``ft.`` markers, emoji and punctuation.
    """
    if not text:
        return ""

    value = unicodedata.normalize("NFKC", str(text))

    # Everything after a pipe is channel/label chatter.  This runs before emoji
    # stripping because '|' is itself a symbol codepoint.
    value = value.split("|", 1)[0]

    value = _strip_emoji(value)
    value = value.lower()

    value = _PREMIERE_PREFIX.sub("", value)

    # Square/curly brackets go, unless they were the whole title (some titles
    # are entirely "[artist] - [title]"), which would leave nothing to match.
    stripped = _SQUARE.sub(" ", value)
    if stripped.strip():
        value = stripped

    # Parentheses: keep the contents when they identify a version, drop them
    # when they are only a mix descriptor.  Without this, "Song (Someone
    # Remix)" normalises identically to "Song" and the remix matches the
    # original at 100% -- the wrong file in the set.
    value = _PAREN.sub(
        lambda match: " " if _is_pure_noise(match.group(1)) else f" {match.group(1)} ",
        value,
    )

    value = _FEAT.sub(" ", value)

    for phrase in _NOISE_PHRASES:
        value = re.sub(rf"\b{re.escape(phrase)}\b", " ", value)

    value = _NON_WORD.sub(" ", value)
    value = _WHITESPACE.sub(" ", value).strip()
    return value


def split_artist_title(raw: Optional[str]) -> Tuple[str, str]:
    """Split a 'Artist - Title' string.  Returns ('', raw) when there is no dash."""
    if not raw:
        return "", ""
    text = str(raw)
    for separator in (" - ", " – ", " — ", " -- "):
        if separator in text:
            artist, title = text.split(separator, 1)
            return artist.strip(), title.strip()
    return "", text.strip()


_DISPLAY_NOISE = re.compile(
    r"\b(free\s*(?:dl|download)|out\s*now|out\s*today|buy\s*now|premiere|"
    r"exclusive|hq|320)\b\s*[!:.\-]*",
    re.IGNORECASE,
)


def clean_display_title(title: Optional[str], artist: Optional[str] = None) -> str:
    """A title fit to show in the UI and write into a tracklist.

    Unlike :func:`normalize_title` this preserves case and version credits --
    it only removes the shouting (``PREMIERE:``, ``FREE DOWNLOAD``), the
    channel chatter after a ``|``, and a leading ``Artist - `` that would
    otherwise be printed twice next to the artist column.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", str(title)).split("|", 1)[0]
    text = _PREMIERE_PREFIX.sub("", text)
    text = _DISPLAY_NOISE.sub(" ", text)
    # Drop "[Label Records]" but keep "[VIP]" -- square brackets are usually a
    # label, occasionally the version.
    text = _SQUARE.sub(
        lambda match: f" {match.group(0)[1:-1]} "
        if version_signature(match.group(0)) else " ",
        text,
    )
    text = _strip_emoji(text)
    text = _WHITESPACE.sub(" ", text).strip(" -–—:·,")

    if artist:
        leading_artist, remainder = split_artist_title(text)
        if leading_artist and remainder and \
                normalize_title(leading_artist) == normalize_title(artist):
            text = remainder

    return _WHITESPACE.sub(" ", text).strip(" -–—:·,") or str(title).strip()


# Words that mean "this is a different piece of audio", not a different tag.
_VERSION_MARKERS = frozenset({
    "remix", "rmx", "bootleg", "vip", "dub", "rework", "flip", "refix",
    "mashup", "instrumental", "acapella", "acappella", "live", "remake",
})


def version_signature(text: Optional[str]) -> frozenset:
    """Version markers present in a title.

    ``token_set_ratio`` scores on the intersection of the two token sets, so
    "Song" against "Song (Skepsis Remix)" comes back as a perfect 100 -- the
    remix's extra tokens are simply invisible to it.  Comparing signatures is
    what stops a remix from being accepted as the original.
    """
    tokens = set(normalize_title(text).split())
    return frozenset(tokens & _VERSION_MARKERS)


def match_key(artist: Optional[str], title: Optional[str]) -> str:
    """Build the string we actually fuzzy-match on.

    SoundCloud titles often already contain the artist, so we normalise the
    combination and de-duplicate repeated words rather than blindly
    concatenating.
    """
    normalized_title = normalize_title(title)
    normalized_artist = normalize_title(artist)
    if not normalized_artist:
        return normalized_title
    if normalized_artist in normalized_title:
        return normalized_title
    return f"{normalized_artist} {normalized_title}".strip()
