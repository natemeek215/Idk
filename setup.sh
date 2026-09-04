#!/usr/bin/env bash
#
# Set Builder setup: create the venv, install dependencies, verify the audio
# libraries actually import.  Safe to re-run.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; OFF=$'\033[0m'

ok()   { printf "  %s✓%s %s\n" "$GREEN" "$OFF" "$1"; }
warn() { printf "  %s!%s %s\n" "$YELLOW" "$OFF" "$1"; }
bad()  { printf "  %s✗%s %s\n" "$RED" "$OFF" "$1"; }
step() { printf "\n%s\n" "$1"; }

# --- 1. Python -----------------------------------------------------------

step "Checking Python"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi

if [ -z "$PYTHON" ]; then
  bad "No python3 found. Install Python 3.11 or newer and re-run."
  exit 1
fi

if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  bad "$("$PYTHON" --version) is too old — Set Builder needs Python 3.11+."
  exit 1
fi
ok "$("$PYTHON" --version) at $(command -v "$PYTHON")"

# --- 2. Virtualenv -------------------------------------------------------

step "Creating the virtualenv"
if [ -d "$VENV" ]; then
  ok "reusing $VENV"
else
  "$PYTHON" -m venv "$VENV"
  ok "created $VENV"
fi

VPY="$VENV/bin/python"
[ -x "$VPY" ] || VPY="$VENV/Scripts/python.exe"

"$VPY" -m pip install --quiet --upgrade pip setuptools wheel
ok "pip up to date"

# --- 3. Dependencies -----------------------------------------------------

step "Installing dependencies (this takes a couple of minutes the first time)"

# Essentia is installed separately so a build failure degrades to the fallback
# key detector instead of aborting the whole install.
CORE_REQS="$(mktemp -t setbuilder-reqs.XXXXXX)"
trap 'rm -f "$CORE_REQS"' EXIT
grep -v '^essentia' requirements.txt > "$CORE_REQS"
"$VPY" -m pip install --quiet -r "$CORE_REQS"
ok "core dependencies installed"

if "$VPY" -m pip install --quiet essentia 2>/dev/null; then
  ok "essentia installed (edma key profile available)"
else
  warn "essentia would not install on this machine."
  warn "Key detection falls back to Krumhansl-Schmuckler chroma correlation."
  warn "Nothing else changes — the app works fine."
fi

# --- 4. Verify the audio stack -------------------------------------------

step "Verifying the audio libraries import"

"$VPY" - <<'PYCHECK'
import sys

failures = []

def check(label, fn):
    try:
        detail = fn()
    except Exception as exc:
        failures.append(label)
        print(f"  \033[31m✗\033[0m {label}: {exc.__class__.__name__}: {exc}")
    else:
        print(f"  \033[32m✓\033[0m {label}{f' ({detail})' if detail else ''}")

def _numpy():
    import numpy
    return numpy.__version__

def _librosa():
    import numpy as np
    import librosa
    # Not just an import: run the beat tracker on a synthetic click track so a
    # broken numba/llvmlite cache surfaces here rather than mid-import.
    sr = 22050
    times = np.arange(0, 4.0, 60.0 / 128.0)
    clicks = librosa.clicks(times=times, sr=sr, length=sr * 4)
    tempo, _ = librosa.beat.beat_track(y=clicks, sr=sr, start_bpm=128)
    return f"{librosa.__version__}, beat tracker → {float(np.atleast_1d(tempo)[0]):.0f} BPM"

def _soundfile():
    import soundfile
    formats = soundfile.available_formats()
    missing = [f for f in ("MP3", "FLAC", "WAV", "OGG") if f not in formats]
    if missing:
        raise RuntimeError(f"libsndfile cannot read {', '.join(missing)}")
    return f"libsndfile {soundfile.__libsndfile_version__}, MP3/FLAC/WAV/OGG ok"

def _mutagen():
    import mutagen
    return ".".join(str(p) for p in mutagen.version)

def _rapidfuzz():
    from rapidfuzz import fuzz
    assert fuzz.token_set_ratio("sammy virji shella", "shella sammy virji") > 95
    return "token_set_ratio ok"

def _sklearn():
    import sklearn
    from sklearn.linear_model import Ridge  # noqa: F401
    return sklearn.__version__

def _fastapi():
    import fastapi, uvicorn  # noqa: F401
    return fastapi.__version__

check("numpy", _numpy)
check("soundfile / libsndfile", _soundfile)
check("librosa", _librosa)
check("mutagen", _mutagen)
check("rapidfuzz", _rapidfuzz)
check("scikit-learn", _sklearn)
check("fastapi + uvicorn", _fastapi)

try:
    import essentia.standard as es
    es.KeyExtractor(profileType="edma")
    print("  \033[32m✓\033[0m essentia KeyExtractor (edma profile)")
except Exception as exc:
    print(f"  \033[33m!\033[0m essentia unavailable ({exc.__class__.__name__}) "
          "— using the chroma fallback for key detection")

if failures:
    print(f"\n\033[31mSetup failed:\033[0m {', '.join(failures)}")
    sys.exit(1)
PYCHECK

# --- 5. ffmpeg (optional) ------------------------------------------------

step "Checking optional tools"
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg found — m4a/AAC files and HLS streams will decode"
else
  warn "ffmpeg not found. MP3/FLAC/WAV/OGG still work."
  warn "Install it for m4a/AAC files and HLS SoundCloud streams:"
  warn "  macOS: brew install ffmpeg    ·    Debian/Ubuntu: sudo apt install ffmpeg"
fi

# --- 6. Tests ------------------------------------------------------------

step "Running the test suite"
if "$VPY" -m pytest tests -q; then
  ok "tests pass"
else
  bad "tests failed — please report what you see above"
  exit 1
fi

# --- done ----------------------------------------------------------------

cat <<EOF

${GREEN}Set Builder is ready.${OFF}

  ${DIM}start it with:${OFF}  python run.py

The browser opens at http://127.0.0.1:7000 and walks you through
SoundCloud credentials and picking your music folder. Two steps, once.

EOF
