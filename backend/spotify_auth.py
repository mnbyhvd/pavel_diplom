"""
Spotify OAuth 2.0 — Authorization Code Flow.

Scopes required:
  streaming                 — Web Playback SDK (Premium only)
  user-read-email           — identify user
  user-read-private         — check Premium status
  user-modify-playback-state — transfer playback to SDK player
  user-read-playback-state  — read current playback
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

SPOTIFY_AUTH_URL    = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL   = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE    = "https://api.spotify.com/v1"

SCOPES = " ".join([
    "streaming",
    "user-read-email",
    "user-read-private",
    "user-modify-playback-state",
    "user-read-playback-state",
])

# In-memory session store: session_id -> token data
_sessions: dict[str, dict] = {}
# state -> session_id (for OAuth callback verification)
_pending_states: dict[str, str] = {}


class SpotifyAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_auth_url(self) -> tuple[str, str]:
        """Return (auth_url, session_id). Store state for callback verification."""
        session_id = secrets.token_urlsafe(24)
        state = secrets.token_urlsafe(16)
        _pending_states[state] = session_id

        params = {
            "client_id":     self.client_id,
            "response_type": "code",
            "redirect_uri":  self.redirect_uri,
            "scope":         SCOPES,
            "state":         state,
            "show_dialog":   "false",
        }
        return f"{SPOTIFY_AUTH_URL}?{urlencode(params)}", session_id

    async def exchange_code(self, code: str, state: str) -> Optional[str]:
        """Exchange auth code for tokens. Returns session_id or None."""
        session_id = _pending_states.pop(state, None)
        if session_id is None:
            return None

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SPOTIFY_TOKEN_URL,
                data={
                    "grant_type":   "authorization_code",
                    "code":         code,
                    "redirect_uri": self.redirect_uri,
                },
                auth=(self.client_id, self.client_secret),
            )
            resp.raise_for_status()
            data = resp.json()

        _sessions[session_id] = {
            "access_token":  data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_at":    time.time() + data.get("expires_in", 3600) - 60,
            "token_type":    data.get("token_type", "Bearer"),
        }
        return session_id

    async def get_valid_token(self, session_id: str) -> Optional[str]:
        """Return a valid access token, refreshing if needed."""
        session = _sessions.get(session_id)
        if not session:
            return None

        if time.time() > session["expires_at"]:
            refreshed = await self._refresh_token(session_id, session["refresh_token"])
            if not refreshed:
                return None

        return _sessions[session_id]["access_token"]

    async def get_user_info(self, access_token: str) -> dict:
        """Fetch current user profile from Spotify."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SPOTIFY_API_BASE}/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code != 200:
                return {}
            return resp.json()

    async def _refresh_token(self, session_id: str, refresh_token: str) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    SPOTIFY_TOKEN_URL,
                    data={
                        "grant_type":    "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    auth=(self.client_id, self.client_secret),
                )
                resp.raise_for_status()
                data = resp.json()

            _sessions[session_id].update({
                "access_token": data["access_token"],
                "expires_at":   time.time() + data.get("expires_in", 3600) - 60,
            })
            if "refresh_token" in data:
                _sessions[session_id]["refresh_token"] = data["refresh_token"]
            return True
        except Exception:
            return False

    def logout(self, session_id: str) -> None:
        _sessions.pop(session_id, None)


def get_session(session_id: str) -> Optional[dict]:
    return _sessions.get(session_id)
