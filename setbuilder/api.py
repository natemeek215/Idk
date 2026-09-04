"""FastAPI app.  Local only -- no accounts, no auth, no remote server."""

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis, arcs, config, db, energy, export, jobs, library, ranking, soundcloud

WEB_ROOT = Path(__file__).parent / "web"

app = FastAPI(title="Set Builder", docs_url=None, redoc_url=None)


# --- models --------------------------------------------------------------

class CredentialsIn(BaseModel):
    client_id: str
    client_secret: str


class FoldersIn(BaseModel):
    folders: List[str]


class ImportIn(BaseModel):
    url: str


class SettingsIn(BaseModel):
    set_length: Optional[int] = None
    arc: Optional[str] = None
    bpm_tolerance: Optional[float] = None
    harmonic_only: Optional[bool] = None
    music_folders: Optional[List[str]] = None


class SuggestIn(BaseModel):
    set_track_ids: List[int] = Field(default_factory=list)
    arc: str = arcs.DEFAULT_ARC
    set_length: int = 20
    bpm_tolerance: float = 0.06
    harmonic_only: bool = False
    limit: int = 5
    playlist_id: Optional[int] = None


class ConfirmMatchIn(BaseModel):
    track_id: int
    path: Optional[str] = None
    reject: bool = False


class LabelIn(BaseModel):
    cache_key: str
    rating: float


class CustomArcIn(BaseModel):
    name: str
    points: List[float]


class ExportIn(BaseModel):
    track_ids: List[int]
    name: str = "Set"
    arc: Optional[str] = None
    format: str = "m3u"


# --- helpers -------------------------------------------------------------

TRACK_SELECT = """
    SELECT t.id, t.sc_id, t.playlist_id, t.position, t.title, t.artist,
           t.permalink_url, t.duration_ms, t.access, t.artwork_url, t.genre,
           t.source, t.local_path, t.match_score, t.match_state, t.cache_key,
           COALESCE(t.label, lf.label) AS label,
           a.bpm, a.bpm_raw, a.bpm_source, a.camelot, a.key_name, a.key_source,
           a.energy, a.energy_source, a.confidence, a.duration_s, a.error
    FROM tracks t
    LEFT JOIN analysis a ON a.cache_key = t.cache_key
    LEFT JOIN local_files lf ON lf.path = t.local_path
"""


def _decorate(track: Dict[str, Any]) -> Dict[str, Any]:
    """Add the UI-facing flags that say how much to trust each number."""
    warnings: List[str] = []
    confidence = track.get("confidence")
    access = track.get("access")

    if access == "blocked":
        warnings.append("Not streamable — add file to library to analyze")
    if confidence == "preview":
        warnings.append("Energy estimated from a ~30s preview")
    if confidence == "tag":
        warnings.append("BPM and key from file tags; no audio analysed")
    if track.get("match_state") == "possible":
        warnings.append(
            f"Possible match ({track.get('match_score')}%) — confirm?"
        )
    if track.get("error"):
        warnings.append(str(track["error"]))
    if track.get("bpm_source") == "analyzed" and track.get("bpm_raw") and \
            track.get("bpm") and abs(track["bpm"] - track["bpm_raw"]) > 1:
        warnings.append(
            f"Tempo octave-corrected from {track['bpm_raw']:.0f}"
        )

    badge = {
        "local": "local file",
        "preview": "preview",
        "stream": "stream",
        "tag": "tag-derived",
    }.get(track.get("source") or "", "unmatched")
    if confidence == "tag":
        badge = "tag-derived"

    return {
        **track,
        "warnings": warnings,
        "badge": badge,
        "playable": bool(track.get("local_path")) or
                    bool(analysis.cached_stream_path(str(track.get("sc_id"))).exists()),
        "analyzed": track.get("bpm") is not None,
    }


def _all_tracks(playlist_id: Optional[int] = None) -> List[Dict[str, Any]]:
    if playlist_id:
        rows = db.query(TRACK_SELECT + " WHERE t.playlist_id = ? ORDER BY t.position",
                        (playlist_id,))
    else:
        rows = db.query(TRACK_SELECT + " ORDER BY t.playlist_id, t.position")
    return [_decorate(row) for row in rows]


def _tracks_by_ids(track_ids: List[int]) -> List[Dict[str, Any]]:
    if not track_ids:
        return []
    placeholders = ",".join("?" for _ in track_ids)
    rows = db.query(TRACK_SELECT + f" WHERE t.id IN ({placeholders})",
                    track_ids)
    by_id = {row["id"]: _decorate(row) for row in rows}
    # Preserve the caller's ordering -- for a set, order is the whole point.
    return [by_id[track_id] for track_id in track_ids if track_id in by_id]


# --- setup / status ------------------------------------------------------

@app.get("/api/status")
def status() -> Dict[str, Any]:
    settings = config.load_settings()
    credentials = config.load_credentials()
    playlists = db.query(
        "SELECT p.id, p.title, p.url, p.imported_at, COUNT(t.id) AS tracks "
        "FROM playlists p LEFT JOIN tracks t ON t.playlist_id = p.id "
        "GROUP BY p.id ORDER BY p.imported_at DESC"
    )
    return {
        "has_credentials": bool(credentials),
        "settings": settings,
        "library": library.library_stats(),
        "playlists": playlists,
        "token": soundcloud.token_budget_status(),
        "calibration": energy.calibration_status(),
        "register_url": soundcloud.REGISTER_URL,
        "setup_complete": bool(credentials) and bool(settings.get("music_folders")),
    }


@app.post("/api/credentials")
def set_credentials(payload: CredentialsIn) -> Dict[str, Any]:
    if not payload.client_id.strip() or not payload.client_secret.strip():
        raise HTTPException(400, "Both the client ID and client secret are required.")
    config.save_credentials(payload.client_id, payload.client_secret)
    # Verify once, which spends a token but tells you now rather than at import.
    try:
        client = soundcloud.SoundCloudClient()
        client.access_token()
    except soundcloud.SoundCloudError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "token": soundcloud.token_budget_status()}


@app.get("/api/folders/suggest")
def suggest_folders() -> Dict[str, Any]:
    return {"folders": library.suggest_music_folders()}


@app.post("/api/folders")
def set_folders(payload: FoldersIn) -> Dict[str, Any]:
    folders = [f for f in payload.folders if Path(f).expanduser().is_dir()]
    if not folders:
        raise HTTPException(400, "None of those folders exist.")
    config.save_settings({"music_folders": folders})
    job = jobs.start_scan(folders)
    return {"ok": True, "job_id": job.id, "folders": folders}


@app.post("/api/settings")
def update_settings(payload: SettingsIn) -> Dict[str, Any]:
    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    return {"settings": config.save_settings(changes)}


@app.post("/api/rescan")
def rescan() -> Dict[str, Any]:
    folders = config.load_settings().get("music_folders") or []
    if not folders:
        raise HTTPException(400, "No music folders configured yet.")
    return {"job_id": jobs.start_scan(folders).id}


# --- import --------------------------------------------------------------

@app.post("/api/import")
def start_import(payload: ImportIn) -> Dict[str, Any]:
    url = payload.url.strip()
    if "soundcloud.com" not in url:
        raise HTTPException(400, "That does not look like a SoundCloud URL.")
    if not config.load_credentials():
        raise HTTPException(400, "Add your SoundCloud credentials first.")
    return {"job_id": jobs.start_import(url).id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    return job


@app.get("/api/jobs")
def job_list() -> Dict[str, Any]:
    return {"jobs": jobs.recent_jobs()}


# --- library -------------------------------------------------------------

@app.get("/api/tracks")
def get_tracks(playlist_id: Optional[int] = None) -> Dict[str, Any]:
    tracks = _all_tracks(playlist_id)
    return {
        "tracks": tracks,
        "counts": {
            "total": len(tracks),
            "analyzed": sum(1 for t in tracks if t["analyzed"]),
            "preview": sum(1 for t in tracks if t.get("confidence") == "preview"),
            "blocked": sum(1 for t in tracks if t.get("access") == "blocked"),
            "possible": sum(1 for t in tracks if t.get("match_state") == "possible"),
        },
    }


@app.get("/api/tracks/{track_id}/candidates")
def match_candidates(track_id: int) -> Dict[str, Any]:
    row = db.query_one("SELECT title, artist FROM tracks WHERE id = ?", (track_id,))
    if not row:
        raise HTTPException(404, "No such track.")
    return {"candidates": library.match_one(row["artist"], row["title"])}


@app.post("/api/tracks/confirm")
def confirm_match(payload: ConfirmMatchIn) -> Dict[str, Any]:
    if payload.reject:
        db.execute(
            "UPDATE tracks SET match_state = 'rejected', local_path = NULL, "
            "source = NULL, cache_key = NULL WHERE id = ?",
            (payload.track_id,),
        )
        job = jobs.start_reanalysis([payload.track_id])
        return {"ok": True, "job_id": job.id}

    if not payload.path:
        raise HTTPException(400, "A file path is required to confirm a match.")
    row = db.query_one("SELECT sha256 FROM local_files WHERE path = ?", (payload.path,))
    if not row:
        raise HTTPException(404, "That file is not in the scanned library.")
    db.execute(
        "UPDATE tracks SET match_state = 'confirmed', local_path = ?, "
        "match_score = 100.0, source = 'local', cache_key = ? WHERE id = ?",
        (payload.path, row["sha256"], payload.track_id),
    )
    job = jobs.start_reanalysis([payload.track_id])
    return {"ok": True, "job_id": job.id}


@app.get("/api/tracks/{track_id}/audio")
def track_audio(track_id: int):
    """Serve local audio for the in-page player.  Localhost only."""
    row = db.query_one("SELECT local_path, sc_id FROM tracks WHERE id = ?", (track_id,))
    if not row:
        raise HTTPException(404, "No such track.")

    path = row.get("local_path")
    if not path or not Path(path).exists():
        cached = analysis.cached_stream_path(str(row.get("sc_id")))
        path = str(cached) if cached.exists() else None
    if not path:
        raise HTTPException(404, "No audio available for this track.")

    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


# --- arcs ----------------------------------------------------------------

@app.get("/api/arcs")
def get_arcs() -> Dict[str, Any]:
    return {"arcs": arcs.list_arcs(), "default": arcs.DEFAULT_ARC,
            "custom_points": arcs.CUSTOM_POINTS}


@app.post("/api/arcs/custom")
def save_custom_arc(payload: CustomArcIn) -> Dict[str, Any]:
    if not payload.name.strip():
        raise HTTPException(400, "A custom arc needs a name.")
    try:
        saved = arcs.save_custom_arc(payload.name, payload.points)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"arc": saved, "arcs": arcs.list_arcs()}


@app.delete("/api/arcs/custom/{name}")
def remove_custom_arc(name: str) -> Dict[str, Any]:
    arcs.delete_custom_arc(name)
    return {"arcs": arcs.list_arcs()}


# --- ranking -------------------------------------------------------------

@app.post("/api/suggest")
def suggest(payload: SuggestIn) -> Dict[str, Any]:
    arc = arcs.get_arc(payload.arc)
    set_length = max(1, payload.set_length)
    current_set = _tracks_by_ids(payload.set_track_ids)

    slot_index = len(current_set)
    position = arcs.slot_position(slot_index, set_length)
    target = arcs.target_energy_10(arc, slot_index, set_length)

    candidates = _all_tracks(payload.playlist_id)
    suggestions = ranking.rank(
        candidates, current_set, target, position,
        bpm_tolerance=payload.bpm_tolerance,
        harmonic_only=payload.harmonic_only,
        limit=payload.limit,
    )

    return {
        "slot": slot_index + 1,
        "position": round(position, 4),
        "target_energy": target,
        "beyond_set_length": slot_index >= set_length,
        "suggestions": suggestions,
        "considered": len(candidates),
        "arc": {"key": arc.key, "name": arc.name, "curve": arc.curve()},
        "set": current_set,
    }


@app.post("/api/set/plot")
def set_plot(payload: SuggestIn) -> Dict[str, Any]:
    """Actual energy of the set so far against the chosen arc."""
    arc = arcs.get_arc(payload.arc)
    set_length = max(1, payload.set_length)
    current_set = _tracks_by_ids(payload.set_track_ids)
    return {
        "arc": {"key": arc.key, "name": arc.name, "curve": arc.curve()},
        "target": [
            {"slot": index + 1,
             "energy": arcs.target_energy_10(arc, index, set_length)}
            for index in range(set_length)
        ],
        "actual": [
            {"slot": index + 1, "energy": track.get("energy"),
             "title": track.get("title"),
             "confidence": track.get("confidence")}
            for index, track in enumerate(current_set)
        ],
    }


# --- calibration ---------------------------------------------------------

@app.get("/api/calibration/next")
def calibration_next(limit: int = 1) -> Dict[str, Any]:
    """Tracks to rate next, spread across the energy range so the model sees
    the whole spectrum rather than 30 peak-time bangers."""
    rows = db.query(
        TRACK_SELECT +
        " WHERE t.cache_key IS NOT NULL AND a.energy IS NOT NULL"
        "   AND t.cache_key NOT IN (SELECT cache_key FROM labels)"
        " ORDER BY a.energy",
    )
    if not rows:
        return {"tracks": [], **energy.calibration_status()}

    # Even stride through the energy-sorted list.
    count = min(limit, len(rows))
    step = max(1, len(rows) // max(1, count))
    picked = [rows[min(index * step, len(rows) - 1)] for index in range(count)]
    return {"tracks": [_decorate(row) for row in picked],
            **energy.calibration_status()}


@app.post("/api/calibration/label")
def calibration_label(payload: LabelIn) -> Dict[str, Any]:
    energy.save_label(payload.cache_key, payload.rating)
    status = energy.calibration_status()
    refit = None
    if status["labels"] >= energy.MIN_LABELS_FOR_MODEL:
        refit = energy.fit_model()
        energy.rescore_all()
    return {"ok": True, "calibration": energy.calibration_status(), "refit": refit}


@app.post("/api/calibration/fit")
def calibration_fit() -> Dict[str, Any]:
    result = energy.fit_model()
    if result.get("fitted"):
        energy.rescore_all()
    return {"result": result, "calibration": energy.calibration_status()}


@app.delete("/api/calibration/labels")
def calibration_reset() -> Dict[str, Any]:
    db.execute("DELETE FROM labels")
    db.execute("DELETE FROM model")
    energy.rescore_all()
    return {"calibration": energy.calibration_status()}


# --- sets and export -----------------------------------------------------

@app.post("/api/export")
def export_set(payload: ExportIn):
    tracks = _tracks_by_ids(payload.track_ids)
    if not tracks:
        raise HTTPException(400, "The set is empty.")
    filename = export.safe_filename(payload.name)
    arc_name = arcs.get_arc(payload.arc).name if payload.arc else None

    if payload.format == "m3u":
        body = export.to_m3u(tracks)
        return Response(
            body, media_type="audio/x-mpegurl",
            headers={"Content-Disposition":
                     f'attachment; filename="{filename}.m3u8"'},
        )
    body = export.to_text(tracks, payload.name, arc_name)
    return Response(
        body, media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.txt"'},
    )


@app.post("/api/set/save")
def save_set(payload: ExportIn) -> Dict[str, Any]:
    db.execute(
        "INSERT INTO sets (name, arc, set_length, track_ids, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (payload.name, payload.arc, len(payload.track_ids),
         json.dumps(payload.track_ids), time.time()),
    )
    return {"ok": True}


@app.get("/api/sets")
def list_sets() -> Dict[str, Any]:
    rows = db.query("SELECT * FROM sets ORDER BY updated_at DESC LIMIT 25")
    for row in rows:
        try:
            row["track_ids"] = json.loads(row["track_ids"])
        except (TypeError, json.JSONDecodeError):
            row["track_ids"] = []
    return {"sets": rows}


# --- static UI -----------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_file = WEB_ROOT / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Set Builder</h1><p>UI files are missing.</p>",
                            status_code=500)
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


if WEB_ROOT.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT)), name="static")
