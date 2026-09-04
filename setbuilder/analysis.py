"""The analysis pipeline: get audio, compute, cache, never repeat.

Cache keys are the file's SHA-256 for local files and ``sc:<track id>`` for a
SoundCloud stream.  A cache hit short-circuits everything below.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import config, db
from .camelot import camelot_to_key_name, parse_key

# Confidence levels, most to least trustworthy.
FULL = "full"          # full-length audio analysed
PREVIEW = "preview"    # ~30s snippet: tempo/key fine, energy is an estimate
TAG = "tag"            # taken from file tags, no audio analysed


def analyze_audio(path: str, tag_bpm: Optional[float] = None,
                  tag_key: Optional[str] = None,
                  confidence: str = FULL) -> Dict[str, Any]:
    """Analyse one audio file.  Runs inside a worker process.

    When the file already carries a BPM and key in its tags (Serato and
    Rekordbox both write these), those are trusted and the corresponding
    detectors are skipped -- but the energy features are still computed,
    because no tagger writes those.
    """
    from . import features as feature_module
    from .energy import heuristic_energy

    payload: Dict[str, Any] = {
        "bpm": None, "bpm_raw": None, "bpm_source": None,
        "camelot": None, "key_name": None, "key_source": None,
        "features": {}, "energy": None, "energy_source": None,
        "confidence": confidence, "duration_s": None,
        "analyzed_at": time.time(), "error": None,
    }

    try:
        y, sr = feature_module.load_audio(path)
    except Exception as exc:
        # No decodable audio.  Tags are better than nothing.
        if tag_bpm or tag_key:
            payload.update({
                "bpm": float(tag_bpm) if tag_bpm else None,
                "bpm_raw": float(tag_bpm) if tag_bpm else None,
                "bpm_source": "tag" if tag_bpm else None,
                "camelot": tag_key, "key_source": "tag" if tag_key else None,
                "key_name": camelot_to_key_name(tag_key) if tag_key else None,
                "confidence": TAG,
                "error": f"could not decode audio ({exc.__class__.__name__}); "
                         "used tags only",
            })
            return payload
        payload["error"] = f"could not decode audio: {exc}"
        return payload

    if y is None or len(y) < sr // 2:
        payload["error"] = "audio too short to analyse"
        return payload

    payload["duration_s"] = round(len(y) / sr, 2)

    # --- tempo
    if tag_bpm:
        payload["bpm"] = float(tag_bpm)
        payload["bpm_raw"] = float(tag_bpm)
        payload["bpm_source"] = "tag"
    else:
        try:
            corrected, raw = feature_module.detect_bpm(y, sr)
            payload["bpm"] = round(corrected, 2)
            payload["bpm_raw"] = round(raw, 2)
            payload["bpm_source"] = "analyzed"
        except Exception as exc:
            payload["error"] = f"tempo detection failed: {exc}"

    # --- key
    if tag_key:
        payload["camelot"] = tag_key
        payload["key_source"] = "tag"
        payload["key_name"] = camelot_to_key_name(tag_key)
    else:
        try:
            camelot, source, _strength = feature_module.detect_key(y, sr)
            payload["camelot"] = camelot
            payload["key_source"] = source
            payload["key_name"] = camelot_to_key_name(camelot) if camelot else None
        except Exception as exc:
            payload["error"] = ((payload["error"] or "") +
                                f" key detection failed: {exc}").strip()

    # --- energy
    try:
        extracted = feature_module.extract_features(y, sr, bpm=payload["bpm"] or 0.0)
        payload["features"] = extracted
        # Workers score with the heuristic; the parent re-scores with the fitted
        # model afterwards, so a worker never needs to load model state.
        payload["energy"] = heuristic_energy(extracted)
        payload["energy_source"] = "heuristic"
    except Exception as exc:
        payload["error"] = ((payload["error"] or "") +
                            f" feature extraction failed: {exc}").strip()

    return payload


def analyze_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Worker entry point.  Returns ``{cache_key, sc_id, analysis}``."""
    result = analyze_audio(
        job["path"],
        tag_bpm=job.get("tag_bpm"),
        tag_key=job.get("tag_key"),
        confidence=job.get("confidence", FULL),
    )
    return {"cache_key": job["cache_key"], "sc_id": job.get("sc_id"),
            "analysis": result}


# --- audio acquisition ---------------------------------------------------

def cached_stream_path(sc_id: str) -> Path:
    return config.AUDIO_CACHE / f"sc-{sc_id}.mp3"


def _has_ffmpeg() -> bool:
    from shutil import which
    return which("ffmpeg") is not None


def download_stream(client, track: Dict[str, Any]) -> Optional[Path]:
    """Fetch a track's stream into the audio cache.  Returns None if unavailable.

    Progressive URLs are streamed straight to disk; HLS playlists need ffmpeg.
    """
    sc_id = str(track.get("sc_id") or track.get("id"))
    destination = cached_stream_path(sc_id)
    if destination.exists() and destination.stat().st_size > 10_000:
        return destination

    config.ensure_home()
    media_url = client.stream_url(track)
    if not media_url:
        return None

    temporary = destination.with_suffix(".part")
    try:
        if ".m3u8" in media_url:
            if not _has_ffmpeg():
                return None
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", media_url,
                 "-c", "copy", str(temporary)],
                check=True, timeout=300,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            with client.session.get(media_url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(1 << 16):
                        if chunk:
                            handle.write(chunk)
        if temporary.stat().st_size < 10_000:
            temporary.unlink(missing_ok=True)
            return None
        temporary.replace(destination)
        return destination
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def plan_track(track: Dict[str, Any]) -> Dict[str, Any]:
    """Decide how a track should be analysed.

    Returns a dict with ``strategy`` in {cached, local, stream, blocked,
    unmatched} plus whatever the strategy needs.
    """
    match_state = track.get("match_state")
    local_path = track.get("local_path")

    if match_state in ("matched", "confirmed") and local_path and \
            Path(local_path).exists():
        row = db.query_one(
            "SELECT sha256, tag_bpm, tag_key FROM local_files WHERE path = ?",
            (local_path,),
        ) or {}
        cache_key = row.get("sha256") or track.get("local_sha256")
        if cache_key:
            return {
                "strategy": "local",
                "cache_key": cache_key,
                "path": local_path,
                "tag_bpm": row.get("tag_bpm"),
                "tag_key": parse_key(row.get("tag_key") or ""),
                "confidence": FULL,
                "source": "local",
            }

    access = (track.get("access") or "").lower()
    if access == "blocked":
        return {"strategy": "blocked", "source": "unmatched",
                "reason": "add file to library to analyze"}

    sc_id = str(track.get("sc_id"))
    return {
        "strategy": "stream",
        "cache_key": f"sc:{sc_id}",
        "sc_id": sc_id,
        # A ~30s preview is enough for tempo and key but not for energy.
        "confidence": PREVIEW if access == "preview" else FULL,
        "source": "preview" if access == "preview" else "stream",
    }


def cpu_workers() -> int:
    """Leave one core free so the machine stays usable during a big import."""
    try:
        available = len(os.sched_getaffinity(0))
    except AttributeError:
        available = os.cpu_count() or 2
    return max(1, min(8, available - 1)) if available > 1 else 1
