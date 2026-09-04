"""SoundCloud API client (OAuth 2.1, Client Credentials).

Token budget is the thing to be careful about: SoundCloud allows **50 tokens
per 12 hours per app**.  So:

* the access token is written to ``~/.setbuilder/token.json`` with its expiry
  and reused across runs,
* we refresh with the refresh token instead of minting a new token,
* we only mint a brand-new token when there is no refresh token or the refresh
  itself is rejected,
* every issuance is stamped into the token file so the UI can show how many
  we have spent in the current 12-hour window.

Reading public playlists needs no user session, so Client Credentials is all
we use.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, Iterator, Optional

import requests

from . import config

API_BASE = "https://api.soundcloud.com"
AUTH_BASE = "https://secure.soundcloud.com"
TOKEN_URL = f"{AUTH_BASE}/oauth/token"
REGISTER_URL = "https://soundcloud.com/you/apps/new"

# Refresh this many seconds before the token actually expires.
EXPIRY_MARGIN = 300
TOKEN_WINDOW = 12 * 3600
TOKEN_BUDGET = 50

USER_AGENT = "SetBuilder/1.0 (local DJ tool)"


class SoundCloudError(RuntimeError):
    """Any failure talking to SoundCloud, with a message fit for the UI."""


class MissingCredentials(SoundCloudError):
    pass


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _record_issuance(token: Dict[str, Any]) -> Dict[str, Any]:
    """Append a timestamp and prune issuances older than the 12-hour window."""
    now = time.time()
    history = [t for t in token.get("issued_history", []) if now - t < TOKEN_WINDOW]
    history.append(now)
    token["issued_history"] = history
    return token


def token_budget_status() -> Dict[str, Any]:
    """How many tokens we have minted in the current 12-hour window."""
    token = config.load_token() or {}
    now = time.time()
    history = [t for t in token.get("issued_history", []) if now - t < TOKEN_WINDOW]
    return {
        "used": len(history),
        "budget": TOKEN_BUDGET,
        "window_hours": 12,
        "expires_at": token.get("expires_at"),
        "valid": bool(token.get("access_token")) and
                 token.get("expires_at", 0) > now + EXPIRY_MARGIN,
    }


class SoundCloudClient:
    def __init__(self, client_id: Optional[str] = None,
                 client_secret: Optional[str] = None):
        if client_id and client_secret:
            self.client_id, self.client_secret = client_id, client_secret
        else:
            credentials = config.load_credentials()
            if not credentials:
                raise MissingCredentials(
                    "No SoundCloud credentials yet. Register an app at "
                    f"{REGISTER_URL} and paste the client ID and secret into setup."
                )
            self.client_id = credentials["client_id"]
            self.client_secret = credentials["client_secret"]
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    # --- auth ------------------------------------------------------------

    def _post_token(self, payload: Dict[str, str]) -> Dict[str, Any]:
        response = self.session.post(
            TOKEN_URL,
            data=payload,
            headers={
                # Credentials go in the Basic header -- SoundCloud does not
                # accept them in the request body.
                "Authorization": _basic_auth_header(self.client_id, self.client_secret),
                "Content-Type": "application/x-www-form-urlencoded",
                "accept": "application/json; charset=utf-8",
            },
            timeout=30,
        )
        if response.status_code == 429:
            raise SoundCloudError(
                "SoundCloud is rate-limiting token requests (50 per 12 hours). "
                "Wait for the window to roll over -- the cached token will be "
                "reused automatically once it is valid again."
            )
        if response.status_code >= 400:
            raise SoundCloudError(
                f"Token request failed ({response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        if "access_token" not in data:
            raise SoundCloudError(f"Token response had no access_token: {data}")

        token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at": time.time() + float(data.get("expires_in", 3600)),
            "scope": data.get("scope", ""),
        }
        existing = config.load_token() or {}
        token["issued_history"] = existing.get("issued_history", [])
        # A refresh keeps the same grant but still counts against the budget.
        _record_issuance(token)
        config.save_token(token)
        return token

    def access_token(self, force_new: bool = False) -> str:
        """Return a valid access token, minting or refreshing only if needed."""
        token = config.load_token() or {}
        now = time.time()

        if not force_new and token.get("access_token"):
            if token.get("expires_at", 0) > now + EXPIRY_MARGIN:
                return token["access_token"]
            if token.get("refresh_token"):
                try:
                    refreshed = self._post_token({
                        "grant_type": "refresh_token",
                        "refresh_token": token["refresh_token"],
                    })
                    return refreshed["access_token"]
                except SoundCloudError:
                    # Refresh token rejected -- fall through to a new grant.
                    pass

        return self._post_token({"grant_type": "client_credentials"})["access_token"]

    # --- requests --------------------------------------------------------

    def _get(self, path_or_url: str, params: Optional[Dict[str, Any]] = None,
             retry_on_401: bool = True) -> Any:
        url = (path_or_url if path_or_url.startswith("http")
               else f"{API_BASE}{path_or_url}")
        token = self.access_token()
        response = self.session.get(
            url,
            params=params,
            headers={
                "Authorization": f"OAuth {token}",
                "accept": "application/json; charset=utf-8",
            },
            timeout=60,
            allow_redirects=True,
        )
        if response.status_code == 401 and retry_on_401:
            # Token was revoked early; one forced refresh, then give up.
            self.access_token(force_new=True)
            return self._get(path_or_url, params, retry_on_401=False)
        if response.status_code == 404:
            raise SoundCloudError(f"SoundCloud returned 404 for {url}")
        if response.status_code == 429:
            raise SoundCloudError("SoundCloud rate limit hit. Try again shortly.")
        if response.status_code >= 400:
            raise SoundCloudError(
                f"SoundCloud error {response.status_code}: {response.text[:300]}"
            )
        if not response.content:
            return None
        return response.json()

    # --- resources -------------------------------------------------------

    def resolve(self, url: str) -> Dict[str, Any]:
        """Turn a soundcloud.com URL into the API resource it points at."""
        resource = self._get("/resolve", {"url": url.strip()})
        if not isinstance(resource, dict):
            raise SoundCloudError(f"Could not resolve {url!r} to a SoundCloud resource.")
        return resource

    def playlist(self, playlist_id: Any) -> Dict[str, Any]:
        return self._get(f"/playlists/{playlist_id}")

    def playlist_tracks(self, playlist_id: Any,
                        page_size: int = 50) -> Iterator[Dict[str, Any]]:
        """Yield every track in a playlist, following ``next_href``."""
        page = self._get(
            f"/playlists/{playlist_id}/tracks",
            {"linked_partitioning": "true", "limit": page_size, "access": "playable,preview,blocked"},
        )
        while page:
            if isinstance(page, list):
                # Some responses are a bare list with no pagination envelope.
                for item in page:
                    yield item
                return
            for item in page.get("collection", []):
                yield item
            next_href = page.get("next_href")
            if not next_href:
                return
            page = self._get(next_href)

    def resolve_playlist(self, url: str) -> Dict[str, Any]:
        """Resolve a pasted URL into ``{playlist, tracks}``.

        Accepts a playlist URL, and also a single-track URL (treated as a
        one-track playlist) so a paste never dead-ends.
        """
        resource = self.resolve(url)
        kind = resource.get("kind")

        if kind == "playlist":
            tracks = list(self.playlist_tracks(resource["id"]))
            # /playlists/:id/tracks is the paginated source of truth, but fall
            # back to the embedded list if it came back empty.
            if not tracks:
                tracks = resource.get("tracks", []) or []
            return {"playlist": resource, "tracks": tracks}

        if kind == "track":
            return {
                "playlist": {
                    "id": f"track-{resource['id']}",
                    "title": resource.get("title", "Single track"),
                    "permalink_url": resource.get("permalink_url", url),
                },
                "tracks": [resource],
            }

        raise SoundCloudError(
            f"That URL resolved to a {kind or 'unknown resource'}, not a playlist. "
            "Paste a link to a SoundCloud playlist or set."
        )

    def stream_url(self, track: Dict[str, Any]) -> Optional[str]:
        """Resolve a redirecting stream URL to a fetchable media URL."""
        access = track.get("access")
        if access == "blocked":
            return None
        stream_href = track.get("stream_url")
        if not stream_href:
            return None
        token = self.access_token()
        response = self.session.get(
            stream_href,
            headers={"Authorization": f"OAuth {token}"},
            allow_redirects=False,
            timeout=30,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            return response.headers.get("Location")
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                return stream_href
            return payload.get("location") or payload.get("url")
        return None


def flatten_track(track: Dict[str, Any], position: int) -> Dict[str, Any]:
    """Reduce a SoundCloud track object to the fields we store.

    Note: SoundCloud's ``bpm`` field is uploader-entered and frequently wrong,
    so it is deliberately not carried through -- we always compute tempo.
    """
    from .normalize import clean_display_title

    user = track.get("user") or {}
    publisher = track.get("publisher_metadata") or {}
    artist = (publisher.get("artist") or user.get("username") or "").strip()
    return {
        "sc_id": str(track.get("id")),
        "position": position,
        # The stored title is the display-cleaned one: SoundCloud titles repeat
        # the artist and shout FREE DOWNLOAD, and both would end up in exports.
        "title": clean_display_title(track.get("title"), artist) or "(untitled)",
        "raw_title": track.get("title") or "",
        "artist": artist,
        "permalink_url": track.get("permalink_url"),
        "duration_ms": track.get("duration"),
        "access": track.get("access") or ("blocked" if not track.get("streamable") else "playable"),
        "artwork_url": track.get("artwork_url") or user.get("avatar_url"),
        "label": (publisher.get("publisher") or track.get("label_name") or "").strip(),
        "genre": track.get("genre"),
        "stream_url": track.get("stream_url"),
    }
