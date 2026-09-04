"""Console entry points, one per stage of the build.

Each stage is testable on its own before the UI is involved:

    python -m setbuilder.cli auth                     # 1. credentials + token
    python -m setbuilder.cli fetch <playlist-url>     # 1. print the track list
    python -m setbuilder.cli scan ~/Music             # 2. index local files
    python -m setbuilder.cli match <playlist-url>     # 2. show the match rate
    python -m setbuilder.cli analyze <playlist-url>   # 3. BPM / key / energy
    python -m setbuilder.cli library                  # 3. dump the cache
    python -m setbuilder.cli suggest --arc peak_time  # 4. ranking, no UI
    python -m setbuilder.cli arcs                     # 4. print arc shapes
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict, List, Optional

from . import arcs, config, db, energy, export, jobs, library, ranking, soundcloud


def _print_table(rows: List[Dict[str, Any]], columns: List[tuple]) -> None:
    if not rows:
        print("  (nothing)")
        return
    widths = [max(len(header), *(len(str(row.get(key, "") or "")) for row in rows))
              for header, key in columns]
    header_line = "  ".join(h.ljust(w) for (h, _), w in zip(columns, widths))
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print("  ".join(str(row.get(key, "") or "").ljust(w)
                        for (_, key), w in zip(columns, widths)))


def _watch(job: jobs.Job, label: str) -> Dict[str, Any]:
    """Follow a background job on one refreshing console line."""
    last = ""
    while True:
        state = jobs.get_job(job.id) or {}
        done, total = state.get("done", 0), state.get("total", 0)
        phase = state.get("phase", "")
        current = (state.get("current") or "")[:48]
        bar = ""
        if total:
            filled = int(24 * done / total)
            bar = "[" + "#" * filled + "." * (24 - filled) + f"] {done}/{total}"
        line = f"\r{label}: {phase} {bar} {current}".ljust(len(last))
        if line != last:
            sys.stdout.write(line)
            sys.stdout.flush()
            last = line
        if state.get("status") != "running":
            print()
            for message in state.get("messages", []):
                print(f"  · {message}")
            if state.get("status") == "error":
                print(f"  ! {state.get('error')}")
                sys.exit(1)
            return state.get("result") or {}
        time.sleep(0.25)


# --- commands ------------------------------------------------------------

def cmd_auth(args) -> None:
    if args.client_id and args.client_secret:
        config.save_credentials(args.client_id, args.client_secret)
        print("Credentials saved to", config.CREDENTIALS_PATH)

    if not config.load_credentials():
        print("No credentials yet.")
        print(f"Register an app at {soundcloud.REGISTER_URL} (needs Artist Pro),")
        print("then run: python -m setbuilder.cli auth --client-id ID --client-secret SECRET")
        sys.exit(1)

    status = soundcloud.token_budget_status()
    print(f"Token budget: {status['used']}/{status['budget']} used in the last 12h")
    client = soundcloud.SoundCloudClient()
    client.access_token()
    status = soundcloud.token_budget_status()
    print(f"Access token OK (expires in "
          f"{int((status['expires_at'] or 0) - time.time())}s)")
    print(f"Token budget now: {status['used']}/{status['budget']}")


def cmd_fetch(args) -> None:
    """Stage 1: resolve a playlist and print the track list."""
    client = soundcloud.SoundCloudClient()
    resolved = client.resolve_playlist(args.url)
    playlist, tracks = resolved["playlist"], resolved["tracks"]
    print(f"\n{playlist.get('title')} — {len(tracks)} tracks\n")
    rows = []
    for index, track in enumerate(tracks):
        flat = soundcloud.flatten_track(track, index)
        rows.append({
            "#": index + 1,
            "title": flat["title"][:52],
            "artist": flat["artist"][:24],
            "access": flat["access"],
            "len": f"{(flat['duration_ms'] or 0) // 60000}:"
                   f"{((flat['duration_ms'] or 0) // 1000) % 60:02d}",
        })
    _print_table(rows, [("#", "#"), ("TITLE", "title"), ("ARTIST", "artist"),
                        ("ACCESS", "access"), ("LEN", "len")])
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["access"]] = counts.get(row["access"], 0) + 1
    print("\n" + ", ".join(f"{count} {access}" for access, count in counts.items()))


def cmd_scan(args) -> None:
    """Stage 2: index local audio files."""
    folders = args.folders or config.load_settings().get("music_folders")
    if not folders:
        print("No folders given. Candidates found on this machine:\n")
        for suggestion in library.suggest_music_folders():
            print(f"  {suggestion['count']:>5}{'+' if suggestion['approximate'] else ''}"
                  f" files  {suggestion['name']}")
        print("\nRe-run: python -m setbuilder.cli scan <folder> [<folder> ...]")
        return
    config.save_settings({"music_folders": folders})
    result = _watch(jobs.start_scan(folders), "scan")
    print(f"\n{result.get('files', 0)} files indexed "
          f"({result.get('added', 0)} new, {result.get('unchanged', 0)} unchanged, "
          f"{result.get('removed', 0)} gone)")
    print(f"{result.get('with_tag_bpm', 0)} have a BPM tag, "
          f"{result.get('with_tag_key', 0)} have a key tag")


def cmd_match(args) -> None:
    """Stage 2: show the match rate for a playlist without analysing anything."""
    client = soundcloud.SoundCloudClient()
    resolved = client.resolve_playlist(args.url)
    flattened = [soundcloud.flatten_track(track, index)
                 for index, track in enumerate(resolved["tracks"])]
    matched = library.match_tracks(flattened)

    rows = [{
        "#": index + 1,
        "title": (track["title"] or "")[:46],
        "state": track["match_state"],
        "score": track.get("match_score"),
        "file": (track.get("local_path") or "").split("/")[-1][:40],
    } for index, track in enumerate(matched)]
    _print_table(rows, [("#", "#"), ("TITLE", "title"), ("STATE", "state"),
                        ("SCORE", "score"), ("FILE", "file")])

    total = len(matched)
    counts: Dict[str, int] = {}
    for track in matched:
        counts[track["match_state"]] = counts.get(track["match_state"], 0) + 1
    print(f"\n{total} tracks: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if total:
        confident = counts.get("matched", 0)
        print(f"match rate: {100.0 * confident / total:.0f}% confident, "
              f"{100.0 * counts.get('possible', 0) / total:.0f}% need confirming")


def cmd_analyze(args) -> None:
    """Stage 3: full import -- match, fetch, analyse, cache."""
    result = _watch(jobs.start_import(args.url), "import")
    print("\n" + str(result.get("headline", "")))
    cmd_library(args)


def cmd_library(args) -> None:
    """Stage 3: dump what is in the analysis cache."""
    rows = db.query(
        "SELECT t.title, t.artist, a.bpm, a.camelot, a.energy, a.energy_source, "
        "t.source, a.confidence FROM tracks t "
        "LEFT JOIN analysis a ON a.cache_key = t.cache_key ORDER BY a.energy DESC"
    )
    display = [{
        "title": (row["title"] or "")[:44],
        "artist": (row["artist"] or "")[:20],
        "bpm": f"{row['bpm']:.0f}" if row.get("bpm") else "—",
        "key": row.get("camelot") or "—",
        "energy": f"{row['energy']:.1f}" if row.get("energy") is not None else "—",
        "source": row.get("source") or "—",
        "conf": row.get("confidence") or "—",
    } for row in rows]
    _print_table(display, [("TITLE", "title"), ("ARTIST", "artist"), ("BPM", "bpm"),
                           ("KEY", "key"), ("NRG", "energy"),
                           ("SOURCE", "source"), ("CONF", "conf")])
    status = energy.calibration_status()
    print(f"\nenergy scorer: {'fitted model' if status['model_active'] else 'heuristic'}"
          f" ({status['labels']} labels)")


def cmd_arcs(args) -> None:
    """Stage 4: print each arc's shape."""
    for arc in arcs.BUILT_INS.values():
        print(f"\n{arc.name}  ({arc.key})")
        print(f"  {arc.description}")
        line = ""
        for step in range(41):
            value = arc.target(step / 40.0)
            line += f"{value:.2f} " if step % 5 == 0 else ""
        print("  " + line)
    for custom in arcs.load_custom_arcs():
        print(f"\n{custom['name']}  ({custom['key']})  custom")


def cmd_suggest(args) -> None:
    """Stage 4: build a set from the console, one slot at a time."""
    arc = arcs.get_arc(args.arc)
    set_length = args.length
    rows = db.query(
        "SELECT t.id, t.title, t.artist, t.label, t.local_path, t.permalink_url, "
        "t.duration_ms, a.bpm, a.camelot, a.energy, a.confidence, a.duration_s "
        "FROM tracks t LEFT JOIN analysis a ON a.cache_key = t.cache_key"
    )
    candidates = [row for row in rows if row.get("bpm") or row.get("energy")]
    if not candidates:
        print("Nothing analysed yet. Run 'analyze <playlist-url>' first.")
        return

    current: List[Dict[str, Any]] = []
    print(f"\nArc: {arc.name} · target length {set_length}")
    print("Enter a number to add that track, 'u' to undo, 'q' to finish.\n")

    while True:
        slot = len(current)
        position = arcs.slot_position(slot, set_length)
        target = arcs.target_energy_10(arc, slot, set_length)
        suggestions = ranking.rank(candidates, current, target, position,
                                   bpm_tolerance=args.tolerance,
                                   harmonic_only=args.harmonic_only,
                                   limit=5)
        print(f"── slot {slot + 1}/{set_length} · arc wants energy {target:.1f} "
              f"{'· beyond target length' if slot >= set_length else ''}")
        if not suggestions:
            print("   no candidates left")
            break
        for index, item in enumerate(suggestions, start=1):
            print(f"  {index}. {(item['artist'] or '?')[:20]} — {(item['title'] or '')[:44]}")
            print(f"     {item['reason']}  — {item['verdict']}")

        try:
            choice = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if choice in ("q", "quit", ""):
            break
        if choice in ("u", "undo"):
            if current:
                removed = current.pop()
                print(f"removed {removed['title']}")
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(suggestions):
            current.append(suggestions[int(choice) - 1])
            continue
        print("?")

    if current:
        print("\n" + export.to_text(current, "Set", arc.name))


def cmd_calibrate(args) -> None:
    """Stage 6: rate tracks by feel to teach the model your ear."""
    status = energy.calibration_status()
    print(f"{status['labels']} tracks labelled "
          f"(model activates at {status['minimum']}, target {status['target']})")

    while True:
        rows = db.query(
            "SELECT t.title, t.artist, t.cache_key, a.energy FROM tracks t "
            "JOIN analysis a ON a.cache_key = t.cache_key "
            "WHERE a.energy IS NOT NULL "
            "AND t.cache_key NOT IN (SELECT cache_key FROM labels) "
            "ORDER BY RANDOM() LIMIT 1"
        )
        if not rows:
            print("Nothing left to label.")
            break
        track = rows[0]
        print(f"\n{track['artist']} — {track['title']}")
        print(f"  (current estimate: {track['energy']:.1f})")
        try:
            answer = input("  your rating 1-10 (enter to skip, q to stop) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if answer.lower() in ("q", "quit"):
            break
        if not answer:
            continue
        try:
            energy.save_label(track["cache_key"], float(answer))
        except ValueError:
            print("  not a number")
            continue
        status = energy.calibration_status()
        print(f"  saved ({status['labels']} labelled)")

    result = energy.fit_model()
    if result.get("fitted"):
        energy.rescore_all()
        print(f"\nModel fitted on {result['n_labels']} labels (cross-validated "
              f"R² {result.get('r2')}). It is now scoring your library.")
    else:
        print(f"\n{result.get('reason')} — still using the heuristic.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="setbuilder", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth", help="check or store SoundCloud credentials")
    auth.add_argument("--client-id")
    auth.add_argument("--client-secret")
    auth.set_defaults(func=cmd_auth)

    fetch = subparsers.add_parser("fetch", help="print a playlist's tracks")
    fetch.add_argument("url")
    fetch.set_defaults(func=cmd_fetch)

    scan = subparsers.add_parser("scan", help="index local audio files")
    scan.add_argument("folders", nargs="*")
    scan.set_defaults(func=cmd_scan)

    match = subparsers.add_parser("match", help="show the local match rate")
    match.add_argument("url")
    match.set_defaults(func=cmd_match)

    analyze = subparsers.add_parser("analyze", help="import and analyse a playlist")
    analyze.add_argument("url")
    analyze.set_defaults(func=cmd_analyze)

    lib = subparsers.add_parser("library", help="show the analysis cache")
    lib.set_defaults(func=cmd_library)

    arcs_parser = subparsers.add_parser("arcs", help="print the arc shapes")
    arcs_parser.set_defaults(func=cmd_arcs)

    suggest = subparsers.add_parser("suggest", help="build a set in the console")
    suggest.add_argument("--arc", default=arcs.DEFAULT_ARC)
    suggest.add_argument("--length", type=int, default=20)
    suggest.add_argument("--tolerance", type=float, default=0.06)
    suggest.add_argument("--harmonic-only", action="store_true")
    suggest.set_defaults(func=cmd_suggest)

    calibrate = subparsers.add_parser("calibrate", help="rate tracks to fit the model")
    calibrate.set_defaults(func=cmd_calibrate)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except soundcloud.SoundCloudError as exc:
        print(f"\nSoundCloud: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
