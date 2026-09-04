"""Background jobs: import, analysis, library scan -- with live progress.

Analysis runs in a process pool because librosa and Essentia are CPU-bound and
hold the GIL.  Downloads run in a small thread pool in the parent, so worker
processes never touch the SoundCloud token.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from . import analysis, db, energy, library, soundcloud

_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.state: Dict[str, Any] = {
            "id": self.id,
            "kind": kind,
            "status": "running",
            "done": 0,
            "total": 0,
            "current": "",
            "phase": "starting",
            "messages": [],
            "result": None,
            "error": None,
            "started_at": time.time(),
        }
        with _jobs_lock:
            _jobs[self.id] = self.state

    def update(self, **fields: Any) -> None:
        with _jobs_lock:
            self.state.update(fields)

    def progress(self, done: int, total: int, current: str = "") -> None:
        self.update(done=done, total=total, current=current)

    def log(self, message: str) -> None:
        with _jobs_lock:
            self.state["messages"].append(message)
            self.state["messages"] = self.state["messages"][-50:]

    def finish(self, result: Any) -> None:
        self.update(status="done", result=result, phase="done",
                    finished_at=time.time())

    def fail(self, error: str) -> None:
        self.update(status="error", error=error, phase="failed",
                    finished_at=time.time())


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        state = _jobs.get(job_id)
        return dict(state) if state else None


def recent_jobs(limit: int = 10) -> List[Dict[str, Any]]:
    with _jobs_lock:
        states = sorted(_jobs.values(), key=lambda s: -s["started_at"])
        return [dict(state) for state in states[:limit]]


def run_in_thread(target: Callable[[Job], Any], kind: str) -> Job:
    job = Job(kind)

    def wrapper() -> None:
        try:
            target(job)
        except Exception as exc:  # surfaced in the UI, not swallowed
            job.fail(f"{exc.__class__.__name__}: {exc}")
            job.log(traceback.format_exc(limit=3))

    threading.Thread(target=wrapper, daemon=True).start()
    return job


# --- library scan --------------------------------------------------------

def start_scan(folders: List[str]) -> Job:
    def work(job: Job) -> None:
        job.update(phase="scanning library")
        stats = library.scan_library(
            folders,
            progress=lambda done, total, name: job.progress(done, total, name),
        )
        stats.update(library.library_stats())
        job.finish(stats)

    return run_in_thread(work, "scan")


# --- import + analyse ----------------------------------------------------

def _persist_playlist(playlist: Dict[str, Any], url: str) -> int:
    db.execute(
        "INSERT INTO playlists (sc_id, url, title, imported_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(sc_id) DO UPDATE SET url=excluded.url, title=excluded.title, "
        "imported_at=excluded.imported_at",
        (str(playlist.get("id")), url, playlist.get("title") or "Playlist", time.time()),
    )
    row = db.query_one("SELECT id FROM playlists WHERE sc_id = ?",
                       (str(playlist.get("id")),))
    return int(row["id"])


def _persist_track(track: Dict[str, Any], playlist_id: int) -> int:
    db.execute(
        """
        INSERT INTO tracks (sc_id, playlist_id, position, title, artist, permalink_url,
                            duration_ms, access, artwork_url, label, genre, stream_url,
                            source, local_path, match_score, match_state, cache_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sc_id) DO UPDATE SET
            playlist_id=excluded.playlist_id, position=excluded.position,
            title=excluded.title, artist=excluded.artist,
            permalink_url=excluded.permalink_url, duration_ms=excluded.duration_ms,
            access=excluded.access, artwork_url=excluded.artwork_url,
            label=excluded.label, genre=excluded.genre, stream_url=excluded.stream_url,
            source=excluded.source, local_path=excluded.local_path,
            match_score=excluded.match_score, match_state=excluded.match_state,
            cache_key=excluded.cache_key
        """,
        (track["sc_id"], playlist_id, track.get("position"), track.get("title"),
         track.get("artist"), track.get("permalink_url"), track.get("duration_ms"),
         track.get("access"), track.get("artwork_url"),
         track.get("label") or track.get("local_label"), track.get("genre"),
         track.get("stream_url"), track.get("source"), track.get("local_path"),
         track.get("match_score"), track.get("match_state"), track.get("cache_key")),
    )
    row = db.query_one("SELECT id FROM tracks WHERE sc_id = ?", (track["sc_id"],))
    return int(row["id"])


def start_import(url: str) -> Job:
    def work(job: Job) -> None:
        job.update(phase="talking to SoundCloud")
        client = soundcloud.SoundCloudClient()
        resolved = client.resolve_playlist(url)
        playlist = resolved["playlist"]
        raw_tracks = resolved["tracks"]

        job.log(f"{playlist.get('title')}: {len(raw_tracks)} tracks")
        flattened = [soundcloud.flatten_track(track, index)
                     for index, track in enumerate(raw_tracks)]

        job.update(phase="matching your library", total=len(flattened))
        matched = library.match_tracks(flattened)

        playlist_id = _persist_playlist(playlist, url)

        # Plan every track, and write the rows before analysis so the library
        # screen is usable while the audio work is still going.
        planned: List[Dict[str, Any]] = []
        for track in matched:
            plan = analysis.plan_track(track)
            track["source"] = plan.get("source")
            track["cache_key"] = plan.get("cache_key")
            track_id = _persist_track(track, playlist_id)
            planned.append({**track, "id": track_id, "plan": plan})

        _run_analysis(job, planned, client)

        summary = _summarize(playlist_id)
        summary["playlist"] = {"id": playlist_id, "title": playlist.get("title"),
                               "url": url}
        job.finish(summary)

    return run_in_thread(work, "import")


def _run_analysis(job: Job, planned: List[Dict[str, Any]], client) -> None:
    """Resolve audio for each planned track, then analyse what needs analysing."""
    pending: List[Dict[str, Any]] = []
    cached = blocked = 0

    job.update(phase="checking the cache")
    for track in planned:
        plan = track["plan"]
        if plan["strategy"] == "blocked":
            blocked += 1
            continue
        cache_key = plan.get("cache_key")
        if cache_key and db.get_analysis(cache_key):
            cached += 1          # never re-analyse
            continue
        pending.append(track)

    job.log(f"{cached} already analysed, {blocked} not streamable")

    # Download whatever needs a stream, in parallel but politely.
    downloads = [t for t in pending if t["plan"]["strategy"] == "stream"]
    if downloads:
        job.update(phase="fetching streams", total=len(downloads), done=0)
        completed = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(analysis.download_stream, client, track): track
                for track in downloads
            }
            for future in as_completed(futures):
                track = futures[future]
                completed += 1
                try:
                    path = future.result()
                except Exception:
                    path = None
                track["plan"]["path"] = str(path) if path else None
                job.progress(completed, len(downloads), track.get("title", ""))

    ready = [t for t in pending if t["plan"].get("path")]
    skipped = [t for t in pending if not t["plan"].get("path")]
    for track in skipped:
        db.execute("UPDATE tracks SET source = ? WHERE id = ?",
                   ("unmatched", track["id"]))

    if not ready:
        return

    job.update(phase="analysing audio", total=len(ready), done=0)
    jobs_payload = [
        {
            "cache_key": t["plan"]["cache_key"],
            "path": t["plan"]["path"],
            "tag_bpm": t["plan"].get("tag_bpm"),
            "tag_key": t["plan"].get("tag_key"),
            "confidence": t["plan"].get("confidence", analysis.FULL),
            "sc_id": t.get("sc_id"),
        }
        for t in ready
    ]
    titles = {t["plan"]["cache_key"]: t.get("title", "") for t in ready}

    completed = 0
    workers = analysis.cpu_workers()
    job.log(f"analysing on {workers} core(s)")
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(analysis.analyze_job, payload)
                       for payload in jobs_payload]
            for future in as_completed(futures):
                completed += 1
                try:
                    result = future.result()
                except Exception as exc:
                    job.log(f"worker failed: {exc}")
                    job.progress(completed, len(ready), "")
                    continue
                db.save_analysis(result["cache_key"], result["analysis"])
                job.progress(completed, len(ready),
                             titles.get(result["cache_key"], ""))
    except Exception as exc:
        # A pool that cannot start (sandboxed environments, low memory) should
        # degrade to in-process analysis rather than failing the whole import.
        job.log(f"process pool unavailable ({exc}); analysing in-process")
        for payload in jobs_payload:
            completed += 1
            result = analysis.analyze_job(payload)
            db.save_analysis(result["cache_key"], result["analysis"])
            job.progress(completed, len(ready), titles.get(result["cache_key"], ""))

    # Apply the fitted energy model, if there is one, now that we are back in
    # the parent process where the model lives.
    job.update(phase="scoring energy")
    energy.rescore_all()


def _summarize(playlist_id: int) -> Dict[str, Any]:
    rows = db.query(
        "SELECT t.*, a.energy, a.bpm, a.camelot, a.confidence, a.error "
        "FROM tracks t LEFT JOIN analysis a ON a.cache_key = t.cache_key "
        "WHERE t.playlist_id = ?",
        (playlist_id,),
    )
    total = len(rows)
    analyzed = sum(1 for row in rows if row.get("bpm"))
    from_preview = sum(1 for row in rows if row.get("confidence") == "preview"
                       and row.get("bpm"))
    from_local = sum(1 for row in rows if row.get("source") == "local"
                     and row.get("bpm"))
    not_streamable = sum(1 for row in rows
                         if (row.get("access") == "blocked" or not row.get("bpm")))
    return {
        "total": total,
        "analyzed": analyzed,
        "from_local": from_local,
        "from_preview": from_preview,
        "not_streamable": not_streamable,
        "headline": (
            f"{analyzed} of {total} tracks analyzed. "
            f"{from_preview} matched from previews (energy estimated). "
            f"{not_streamable} not streamable — add files to analyze."
        ),
    }


def start_reanalysis(track_ids: List[int]) -> Job:
    """Re-run analysis for specific tracks, e.g. after confirming a match."""
    def work(job: Job) -> None:
        client = None
        planned = []
        for track_id in track_ids:
            row = db.query_one("SELECT * FROM tracks WHERE id = ?", (track_id,))
            if not row:
                continue
            plan = analysis.plan_track(row)
            db.execute("UPDATE tracks SET cache_key = ?, source = ? WHERE id = ?",
                       (plan.get("cache_key"), plan.get("source"), track_id))
            planned.append({**row, "id": track_id, "plan": plan})
        if any(t["plan"]["strategy"] == "stream" for t in planned):
            client = soundcloud.SoundCloudClient()
        _run_analysis(job, planned, client)
        job.finish({"tracks": len(planned)})

    return run_in_thread(work, "reanalyze")
