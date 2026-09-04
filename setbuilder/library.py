"""Local music library: folder discovery, tag reading, and fuzzy matching.

Serato and Rekordbox write BPM and key back into file tags, so when a matched
file already carries both we use them and skip re-analysis entirely.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from rapidfuzz import fuzz, process

from . import config, db
from .camelot import parse_key
from .normalize import match_key, split_artist_title, version_signature

# Match thresholds from the spec.
ACCEPT_THRESHOLD = 87.0
CONFIRM_THRESHOLD = 70.0


# --- folder discovery ----------------------------------------------------

def _count_audio_files(folder: Path, limit: int = 400) -> int:
    """Count audio files under ``folder``, stopping early once past ``limit``."""
    count = 0
    for root, dirnames, filenames in os.walk(folder, followlinks=False):
        # Skip the usual noise so a scan of ~/Documents stays quick.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() in config.AUDIO_EXTENSIONS:
                count += 1
                if count > limit:
                    return count
    return count


def suggest_music_folders() -> List[Dict[str, Any]]:
    """Find candidate library folders: >50 audio files under ~/Music, ~/Documents.

    Returns the roots themselves when they qualify, plus their immediate
    subfolders, so a 'Music/Serato/…' layout surfaces something clickable.
    """
    home = Path.home()
    roots = [home / "Music", home / "Documents", home / "Downloads"]
    suggestions: List[Dict[str, Any]] = []
    seen: set = set()

    for root in roots:
        if not root.is_dir():
            continue
        candidates = [root] + sorted(
            (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name.lower(),
        )
        for candidate in candidates:
            resolved = str(candidate.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            count = _count_audio_files(candidate)
            if count > config.LIBRARY_FOLDER_THRESHOLD:
                suggestions.append({
                    "path": resolved,
                    "name": str(candidate).replace(str(home), "~"),
                    "count": count,
                    "approximate": count > 400,
                })

    # Prefer the fullest folders, and drop a child when its parent is listed.
    suggestions.sort(key=lambda s: -s["count"])
    pruned: List[Dict[str, Any]] = []
    for suggestion in suggestions:
        if any(suggestion["path"].startswith(kept["path"] + os.sep) for kept in pruned):
            continue
        pruned.append(suggestion)
    return pruned[:8]


# --- tag reading ---------------------------------------------------------

def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _first_tag(tags: Any, *names: str) -> Optional[str]:
    if not tags:
        return None
    for name in names:
        try:
            value = tags.get(name)
        except Exception:
            value = None
        if value is None:
            continue
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None:
            continue
        text = str(getattr(value, "text", value))
        if isinstance(getattr(value, "text", None), list) and value.text:
            text = str(value.text[0])
        text = text.strip()
        if text:
            return text
    return None


def read_tags(path: Path) -> Dict[str, Any]:
    """Read title/artist/BPM/key from a file's tags.  Never raises."""
    from mutagen import File as MutagenFile  # imported lazily; heavy-ish

    info: Dict[str, Any] = {
        "title": None, "artist": None, "album": None, "label": None,
        "tag_bpm": None, "tag_key": None, "duration_s": None,
    }
    try:
        audio = MutagenFile(str(path))
    except Exception:
        audio = None
    if audio is None:
        return info

    try:
        info["duration_s"] = float(getattr(audio.info, "length", 0.0)) or None
    except Exception:
        pass

    tags = audio.tags
    info["title"] = _first_tag(tags, "TIT2", "title", "\xa9nam", "Title")
    info["artist"] = _first_tag(tags, "TPE1", "artist", "\xa9ART", "Artist",
                                "TPE2", "albumartist")
    info["album"] = _first_tag(tags, "TALB", "album", "\xa9alb", "Album")
    info["label"] = _first_tag(tags, "TPUB", "organization", "label", "publisher",
                               "----:com.apple.iTunes:LABEL")

    bpm_text = _first_tag(tags, "TBPM", "bpm", "tmpo", "BPM",
                          "----:com.apple.iTunes:BPM")
    if bpm_text:
        try:
            value = float(str(bpm_text).replace(",", "."))
            if 20 < value < 400:
                info["tag_bpm"] = value
        except ValueError:
            pass

    key_text = _first_tag(tags, "TKEY", "initialkey", "key", "Key",
                          "----:com.apple.iTunes:initialkey",
                          "----:com.apple.iTunes:KEY")
    if key_text:
        info["tag_key"] = parse_key(key_text)

    return info


def iter_audio_files(folders: Iterable[str]) -> Iterable[Path]:
    for folder in folders:
        root = Path(folder).expanduser()
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                if Path(name).suffix.lower() in config.AUDIO_EXTENSIONS:
                    yield Path(dirpath) / name


def scan_library(folders: List[str], progress=None) -> Dict[str, Any]:
    """Index every audio file under ``folders`` into ``local_files``.

    Files whose size and mtime are unchanged since the last scan are left
    alone -- notably we do not re-hash them.
    """
    known = {
        row["path"]: row
        for row in db.query("SELECT path, size, mtime FROM local_files")
    }
    added = updated = unchanged = 0
    seen: set = set()
    paths = list(iter_audio_files(folders))
    total = len(paths)

    for index, path in enumerate(paths):
        seen.add(str(path))
        try:
            stat = path.stat()
        except OSError:
            continue

        previous = known.get(str(path))
        if previous and previous["size"] == stat.st_size and \
                abs((previous["mtime"] or 0) - stat.st_mtime) < 1.0:
            unchanged += 1
            if progress and index % 25 == 0:
                progress(index + 1, total, path.name)
            continue

        tags = read_tags(path)
        try:
            digest = sha256_file(path)
        except OSError:
            continue

        artist = tags["artist"] or ""
        title = tags["title"]
        if not title:
            # Fall back to the filename, which is usually "Artist - Title".
            guessed_artist, guessed_title = split_artist_title(path.stem)
            title = guessed_title or path.stem
            artist = artist or guessed_artist

        db.execute(
            """
            INSERT INTO local_files (path, sha256, size, mtime, title, artist, album,
                                     label, tag_bpm, tag_key, duration_s, match_key,
                                     scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                sha256=excluded.sha256, size=excluded.size, mtime=excluded.mtime,
                title=excluded.title, artist=excluded.artist, album=excluded.album,
                label=excluded.label, tag_bpm=excluded.tag_bpm, tag_key=excluded.tag_key,
                duration_s=excluded.duration_s, match_key=excluded.match_key,
                scanned_at=excluded.scanned_at
            """,
            (str(path), digest, stat.st_size, stat.st_mtime, title, artist,
             tags["album"], tags["label"], tags["tag_bpm"], tags["tag_key"],
             tags["duration_s"], match_key(artist, title), time.time()),
        )
        if previous:
            updated += 1
        else:
            added += 1
        if progress:
            progress(index + 1, total, path.name)

    # Drop rows for files that have disappeared.
    removed = 0
    for path in list(known):
        if path not in seen and not Path(path).exists():
            db.execute("DELETE FROM local_files WHERE path = ?", (path,))
            removed += 1

    return {
        "total": total, "added": added, "updated": updated,
        "unchanged": unchanged, "removed": removed,
    }


# --- matching ------------------------------------------------------------

def _candidates() -> List[Dict[str, Any]]:
    return db.query(
        "SELECT path, sha256, title, artist, label, tag_bpm, tag_key, duration_s, "
        "match_key FROM local_files WHERE match_key IS NOT NULL AND match_key != ''"
    )


def match_tracks(tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fuzzy-match SoundCloud tracks against the indexed local library.

    Uses ``token_set_ratio``: >=87 accepts, 70-87 is offered for confirmation,
    below 70 is unmatched.
    """
    candidates = _candidates()
    if not candidates:
        return [dict(track, match_state="unmatched", match_score=0.0,
                     local_path=None) for track in tracks]

    choices = [row["match_key"] for row in candidates]
    results: List[Dict[str, Any]] = []

    for track in tracks:
        query = match_key(track.get("artist"), track.get("title"))
        if not query:
            results.append(dict(track, match_state="unmatched", match_score=0.0,
                                local_path=None))
            continue

        best = process.extractOne(query, choices, scorer=fuzz.token_set_ratio)
        if not best:
            results.append(dict(track, match_state="unmatched", match_score=0.0,
                                local_path=None))
            continue

        _matched_text, score, index = best
        candidate = candidates[index]

        # A duration that disagrees by more than 20s is a different edit, so
        # demote an otherwise-confident match to "please confirm".
        duration_conflict = False
        sc_duration = (track.get("duration_ms") or 0) / 1000.0
        local_duration = candidate.get("duration_s") or 0
        if sc_duration > 30 and local_duration > 30:
            duration_conflict = abs(sc_duration - local_duration) > 20

        # One side saying "remix"/"VIP"/"bootleg" when the other does not means
        # these are different pieces of audio, however well the words overlap.
        # Both signatures are taken over artist *and* title: a SoundCloud title
        # usually embeds the artist while a tagged file keeps them apart, and
        # comparing the two scopes would flag an artist called "Delta Dub" as a
        # dub version of itself.
        version_conflict = (
            version_signature(f"{track.get('artist') or ''} {track.get('title') or ''}")
            != version_signature(
                f"{candidate.get('artist') or ''} {candidate.get('title') or ''}")
        )

        if score >= ACCEPT_THRESHOLD and not duration_conflict \
                and not version_conflict:
            state = "matched"
        elif score >= CONFIRM_THRESHOLD:
            state = "possible"
        else:
            state = "unmatched"

        if version_conflict:
            conflict_note = "one side is a remix/edit of the other"
        elif duration_conflict:
            conflict_note = "duration differs from the SoundCloud version"
        else:
            conflict_note = None

        results.append(dict(
            track,
            match_state=state,
            match_score=round(float(score), 1),
            local_path=candidate["path"] if state != "unmatched" else None,
            local_sha256=candidate["sha256"] if state != "unmatched" else None,
            local_tag_bpm=candidate.get("tag_bpm"),
            local_tag_key=candidate.get("tag_key"),
            local_label=candidate.get("label"),
            match_reason=conflict_note,
        ))
    return results


def match_one(artist: Optional[str], title: Optional[str],
              limit: int = 5) -> List[Dict[str, Any]]:
    """Top candidate local files for a single track, for the confirm UI."""
    candidates = _candidates()
    if not candidates:
        return []
    query = match_key(artist, title)
    if not query:
        return []
    choices = [row["match_key"] for row in candidates]
    matches = process.extract(query, choices, scorer=fuzz.token_set_ratio, limit=limit)
    return [
        {
            "path": candidates[index]["path"],
            "sha256": candidates[index]["sha256"],
            "title": candidates[index]["title"],
            "artist": candidates[index]["artist"],
            "score": round(float(score), 1),
        }
        for _text, score, index in matches
    ]


def library_stats() -> Dict[str, Any]:
    row = db.query_one(
        "SELECT COUNT(*) AS files, "
        "SUM(CASE WHEN tag_bpm IS NOT NULL THEN 1 ELSE 0 END) AS with_bpm, "
        "SUM(CASE WHEN tag_key IS NOT NULL THEN 1 ELSE 0 END) AS with_key "
        "FROM local_files"
    ) or {}
    return {
        "files": row.get("files") or 0,
        "with_tag_bpm": row.get("with_bpm") or 0,
        "with_tag_key": row.get("with_key") or 0,
    }
