# Set Builder

Paste a SoundCloud playlist URL. Set Builder analyses every track, then ranks
what should come next against an energy arc you choose. **You** pick the
tracks — it ranks the options and tells you why in one line.

Runs entirely on your laptop. No accounts, no server, no telemetry.

```bash
./setup.sh        # once
python run.py     # opens http://127.0.0.1:7000
```

---

## What it does

**One input, zero configuration.** Paste a link, watch a progress bar, get a
working set builder. Every setting has a default that works.

- **Finds your files first.** Most of a playlist is usually already on your
  machine. Set Builder fuzzy-matches SoundCloud titles to local files and reads
  the BPM and key Serato or Rekordbox already wrote into the tags.
- **Falls back to the stream** when there's no local file, and says so.
- **Never hides low-confidence data.** Energy from a 30-second preview is
  flagged with a warning icon everywhere it appears — table, suggestion,
  chart, export. A number that isn't solid never looks solid.
- **Learns your ear.** Ships with a heuristic energy score so it works
  immediately; rate 25+ tracks and a ridge model fitted to *your* ratings takes
  over. The UI says which one is running.

---

## Setup

### 1. Dependencies

```bash
./setup.sh
```

Creates `.venv`, installs everything, and verifies the audio stack actually
imports — it runs librosa's beat tracker on a synthetic click track rather than
just importing it, because a broken numba cache only shows up when you use it.

Essentia is installed separately. If its wheel won't build on your machine,
setup says so and carries on: key detection falls back to a
Krumhansl–Schmuckler chroma correlation and nothing else changes.

`ffmpeg` is optional. MP3, FLAC, WAV and OGG decode without it; you need it for
m4a/AAC files and HLS streams.

### 2. SoundCloud credentials

First run walks you through it. You need an app registered at
[soundcloud.com/you/apps/new](https://soundcloud.com/you/apps/new) — self-serve,
but it requires an Artist Pro account.

Credentials go in `~/.setbuilder/credentials.json`, never in this repo.

> **Token budget.** SoundCloud allows **50 tokens per 12 hours per app**.
> Set Builder caches the access token to disk with its expiry, reuses it across
> runs, and refreshes with the refresh token rather than minting new ones. A
> normal day spends one or two. The count is shown in the top-right corner.

### 3. Your music folder

Set Builder scans `~/Music`, `~/Documents` and `~/Downloads` for folders with
more than 50 audio files and offers them as one-click options. Or type a path.

---

## Using it

### Import

Paste a playlist URL. Analysis runs across your cores with a live progress bar.
Nothing is ever analysed twice — results are cached by file SHA-256 (local) or
track ID (stream), so re-importing a playlist is instant.

You get a summary like:

> 48 of 61 tracks analyzed. 9 matched from previews (energy estimated).
> 4 not streamable — add files to analyze.

### Library

Sortable table: title, artist, BPM, Camelot key, energy, source badge.
Warning icons wherever confidence is low. Click a row to play it.

Matches scoring 70–87 show a **confirm?** button — that band is where fuzzy
matching is genuinely uncertain, so it asks instead of guessing.

### Set builder

The main screen. Left: your set as a line chart, actual energy against the
target arc, so you can see when you're drifting. Right: the top 5 suggestions
for the next slot, each with a plain-English reason:

```
+1.5 BPM · 8A → 9A · energy 6.2, arc wants 6.0    — smooth
half-time · 4A → 4B · energy 7.8, arc wants 6.0   — big lift, early
```

Widen or narrow the BPM tolerance and toggle "harmonic only" live; the list
re-ranks as you drag.

**Keyboard:** `1`–`5` add that suggestion · `⌘Z`/`Ctrl+Z` undo · `space`
preview · drag to reorder · `⌥1`–`⌥5` switch screens.

### Calibrate

Rate tracks 1–10 by feel. At 25 labels a ridge regression fitted to your
ratings replaces the heuristic and the UI tells you it has. Add more any time
and refit. Keys `1`–`9`, `0` for 10, `Enter` to save.

### Export

M3U (local file paths where they exist, SoundCloud permalinks where they
don't) and a plain-text tracklist with BPM, key, energy and running time per
line — with low-confidence values labelled.

---

## Arcs

| Arc | Shape |
|---|---|
| Warm-up | 0.20 → 0.55, gentle linear rise |
| Classic build | 0.30 → 1.0, small dip to 0.75 around position 0.65 |
| Peak time | 0.65 → 0.95, sustained with small oscillation |
| Peak–valley–peak | rise to 0.9, drop to 0.5 at midpoint, back to 1.0 |
| Closing | 0.85 → 0.35, long comedown |

Plus **Custom**: drag six control points, interpolated smoothly, save by name.

Set length (default 20) is what position normalises against.

---

## How ranking works

| Factor | Weight | |
|---|---|---|
| BPM compatibility | 0.35 | 1.0 within ±2%, tapering to 0 at ±6%. Also tests half- and double-time — 138 into 69 is a real transition and scores as one. |
| Harmonic compatibility | 0.30 | Camelot: same key 1.0 · ±1 0.9 · relative major/minor 0.85 · +2 0.7 · +7 0.55 · else 0.15 |
| Energy fit | 0.30 | Distance from the slot's target. Overshoot is punished harder than undershoot — jumping the arc is worse than lagging it. |
| Variety | 0.05 | Penalises the same artist within 4 tracks, same label within 3. |

Unknown values score at the neutral midpoint rather than at zero, so a track
with a missing key isn't ranked below one with a genuine clash.

---

## Analysis details

**BPM** — `librosa.beat.beat_track` with `start_bpm=128`, then octave
correction into [90, 175]. The raw value is stored too and shown when the
correction moved it.

**Key** — Essentia `KeyExtractor` with the `edma` profile (trained on
electronic music), falling back to Krumhansl–Schmuckler chroma correlation.
Stored as Camelot and as a key name.

**Energy features**, computed on the **middle 60%** so intros and outros don't
skew them: RMS in four bands (sub 20–60 Hz, bass 60–250, mid 250–4k, high 4k+),
onset rate, spectral flux mean and variance, percussive-to-harmonic ratio via
HPSS, loudness range (p95 − p10), and "fullness" (fraction of frames above 80%
of peak RMS).

SoundCloud's own `bpm` field is ignored — it's uploader-entered and frequently
wrong. There is no key field at all.

---

## Command line

Every stage is testable on its own, in the order it was built:

```bash
python run.py auth                          # credentials + token check
python run.py fetch <playlist-url>          # print the track list
python run.py scan ~/Music                  # index local files
python run.py match <playlist-url>          # match rate, no analysis
python run.py analyze <playlist-url>        # full import
python run.py library                       # dump the analysis cache
python run.py arcs                          # print arc shapes
python run.py suggest --arc peak_time       # build a set in the console
python run.py calibrate                     # rate tracks, fit the model
```

`python run.py scan` with no arguments lists the candidate folders it found.

---

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

The Camelot math and BPM octave correction have the heaviest coverage — both
are easy to get subtly wrong and hard to notice. The suite also covers arc
shapes, ranking, matching, the energy model, and the HTTP API end to end.

Two behaviours worth knowing about, both pinned by tests:

- **88 BPM is ambiguous.** Its octaves are 44/88/176/352 and none land inside
  [90, 175]. A naive "double while low, halve while high" loop oscillates
  forever here, so the correction picks the octave closest to 128 on a log
  scale instead — deterministic and stable under re-application.
- **`token_set_ratio` cannot see a remix suffix.** It scores on the
  intersection of the two token sets, so `Song` against `Song (Skepsis Remix)`
  returns a perfect 100. Matching therefore compares *version signatures* on
  top of the fuzzy score; a remix drops to "confirm?" rather than silently
  becoming the original.

---

## Where things live

```
~/.setbuilder/
  db.sqlite            tracks, analysis cache, labels, model, custom arcs
  credentials.json     SoundCloud client id + secret   (chmod 600)
  token.json           cached access token + expiry    (chmod 600)
  settings.json        folders, arc, set length, filters
  audio-cache/         downloaded streams
```

Delete `db.sqlite` to start over; the credentials survive.

```
setbuilder/
  camelot.py     wheel conversion + harmonic scoring
  bpm.py         octave correction + tempo compatibility
  normalize.py   title cleaning, version signatures
  library.py     folder scan, tags, fuzzy matching
  features.py    BPM / key / energy feature extraction
  energy.py      heuristic + ridge model
  arcs.py        the five arcs, custom arcs, interpolation
  ranking.py     the scoring above
  analysis.py    per-track pipeline + caching
  jobs.py        worker pool + progress
  soundcloud.py  OAuth, token budget, playlist paging
  api.py         FastAPI routes
  cli.py         console entry points
  web/           the UI (plain HTML/CSS/JS, no build step)
```
