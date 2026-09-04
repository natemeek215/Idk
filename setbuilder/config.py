"""Paths and persisted settings.  Everything lives under ~/.setbuilder."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

HOME = Path(os.environ.get("SETBUILDER_HOME", Path.home() / ".setbuilder"))

DB_PATH = HOME / "db.sqlite"
CREDENTIALS_PATH = HOME / "credentials.json"
TOKEN_PATH = HOME / "token.json"
SETTINGS_PATH = HOME / "settings.json"
AUDIO_CACHE = HOME / "audio-cache"

HOST = "127.0.0.1"
PORT = int(os.environ.get("SETBUILDER_PORT", "7000"))

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".aiff", ".aif",
    ".ogg", ".opus", ".wma", ".alac", ".m4b",
}

# A folder needs more than this many audio files to be offered as a library.
LIBRARY_FOLDER_THRESHOLD = 50

DEFAULT_SETTINGS: Dict[str, Any] = {
    "music_folders": [],
    "set_length": 20,
    "arc": "classic_build",
    "bpm_tolerance": 0.06,
    "harmonic_only": False,
    "setup_complete": False,
}


def ensure_home() -> Path:
    HOME.mkdir(parents=True, exist_ok=True)
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)
    return HOME


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    ensure_home()
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    temp.replace(path)
    # Credentials and tokens are secrets; keep them owner-readable only.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    stored = _read_json(SETTINGS_PATH)
    if stored:
        settings.update(stored)
    return settings


def save_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    merged = load_settings()
    merged.update(settings)
    _write_json(SETTINGS_PATH, merged)
    return merged


def load_credentials() -> Optional[Dict[str, str]]:
    stored = _read_json(CREDENTIALS_PATH)
    if stored and stored.get("client_id") and stored.get("client_secret"):
        return stored
    return None


def save_credentials(client_id: str, client_secret: str) -> None:
    _write_json(CREDENTIALS_PATH, {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
    })


def load_token() -> Optional[Dict[str, Any]]:
    return _read_json(TOKEN_PATH)


def save_token(token: Dict[str, Any]) -> None:
    _write_json(TOKEN_PATH, token)


def clear_token() -> None:
    try:
        TOKEN_PATH.unlink()
    except FileNotFoundError:
        pass
