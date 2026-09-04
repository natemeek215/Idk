"""Test fixtures.

``SETBUILDER_HOME`` is redirected to a temporary directory *before* anything
from the package is imported, so a test run can never touch a real library,
a real database, or real credentials.
"""

import os
import tempfile

_TEMP_HOME = tempfile.mkdtemp(prefix="setbuilder-tests-")
os.environ["SETBUILDER_HOME"] = _TEMP_HOME

import pytest  # noqa: E402

from setbuilder import db  # noqa: E402


@pytest.fixture()
def clean_db():
    """A database with the schema in place and every table emptied."""
    connection = db.connect()
    for table in ("labels", "model", "analysis", "tracks", "playlists",
                  "local_files", "custom_arcs", "sets"):
        connection.execute(f"DELETE FROM {table}")
    connection.commit()
    yield db
    connection.commit()


def make_track(track_id, title="Track", artist="Artist", bpm=128.0, camelot="8A",
               energy=5.0, label="", confidence="full", **extra):
    """A track shaped the way the ranking code expects to receive one."""
    return {
        "id": track_id, "title": title, "artist": artist, "bpm": bpm,
        "camelot": camelot, "energy": energy, "label": label,
        "confidence": confidence, **extra,
    }


@pytest.fixture()
def track_factory():
    return make_track
