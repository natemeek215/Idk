"""Set export: M3U playlist and a plain-text tracklist."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _duration_seconds(track: Dict[str, Any]) -> int:
    duration = track.get("duration_s")
    if duration:
        return int(round(float(duration)))
    milliseconds = track.get("duration_ms")
    if milliseconds:
        return int(round(float(milliseconds) / 1000.0))
    return -1


def _display(track: Dict[str, Any]) -> str:
    artist = (track.get("artist") or "").strip()
    title = (track.get("title") or "Unknown").strip()
    return f"{artist} - {title}" if artist else title


def to_m3u(tracks: Sequence[Dict[str, Any]]) -> str:
    """Extended M3U.  Local files get their real path; stream-only tracks get
    their SoundCloud permalink so the entry is at least resolvable."""
    lines = ["#EXTM3U"]
    for track in tracks:
        lines.append(f"#EXTINF:{_duration_seconds(track)},{_display(track)}")
        target = track.get("local_path") or track.get("permalink_url") or ""
        if not track.get("local_path"):
            lines.append("# no local file — SoundCloud link only")
        lines.append(target)
    return "\n".join(lines) + "\n"


def _format_time(seconds: float) -> str:
    minutes, remainder = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remainder:02d}"
    return f"{minutes}:{remainder:02d}"


def to_text(tracks: Sequence[Dict[str, Any]], set_name: str = "Set",
            arc_name: Optional[str] = None) -> str:
    """Plain-text tracklist with BPM and key on every line."""
    lines: List[str] = [set_name]
    if arc_name:
        lines.append(f"Arc: {arc_name}")
    lines.append(f"{len(tracks)} tracks")
    lines.append("")

    elapsed = 0.0
    for index, track in enumerate(tracks, start=1):
        bpm = track.get("bpm")
        bpm_text = f"{bpm:.0f}" if bpm else "---"
        key_text = track.get("camelot") or "--"
        energy = track.get("energy")
        energy_text = f"{energy:.1f}" if energy is not None else "--"

        flags = []
        if track.get("confidence") == "preview":
            flags.append("energy estimated from 30s preview")
        elif track.get("confidence") == "tag":
            flags.append("from file tags")
        if not track.get("local_path"):
            flags.append("no local file")

        line = (f"{index:2d}. [{_format_time(elapsed)}] {_display(track)}"
                f"  —  {bpm_text} BPM · {key_text} · energy {energy_text}")
        if flags:
            line += f"   ({'; '.join(flags)})"
        lines.append(line)

        seconds = _duration_seconds(track)
        elapsed += seconds if seconds > 0 else 0

    return "\n".join(lines) + "\n"


def safe_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name)
    return (cleaned.strip() or "set").replace(" ", "-")[:80]
