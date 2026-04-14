"""
Spotify API client.

NOTE: Spotify deprecated /recommendations and /audio-features for apps
created after November 27, 2024. This client uses the Search API instead,
which is still available for all apps.

For a large dataset, use load_kaggle_dataset.py instead of collect_tracks().
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterator

import pandas as pd
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
RATE_DELAY = 0.25
DEFAULT_DATA_PATH = Path(__file__).parent.parent / "data" / "tracks.csv"

# Hardcoded genres (replaces deprecated /recommendations/available-genre-seeds)
GENRES = [
    "acoustic", "afrobeat", "alt-rock", "alternative", "ambient", "anime",
    "black-metal", "bluegrass", "blues", "bossanova", "brazil", "breakbeat",
    "british", "cantopop", "chicago-house", "children", "chill", "classical",
    "club", "comedy", "country", "dance", "dancehall", "death-metal",
    "deep-house", "detroit-techno", "disco", "disney", "drum-and-bass", "dub",
    "dubstep", "edm", "electro", "electronic", "emo", "folk", "forro", "french",
    "funk", "garage", "german", "gospel", "goth", "grindcore", "groove",
    "grunge", "guitar", "happy", "hard-rock", "hardcore", "hardstyle",
    "heavy-metal", "hip-hop", "holidays", "honky-tonk", "house", "idm",
    "indian", "indie", "indie-pop", "industrial", "iranian", "j-dance",
    "j-idol", "j-pop", "j-rock", "jazz", "k-pop", "kids", "latin",
    "latino", "malay", "mandopop", "metal", "metal-misc", "metalcore",
    "minimal-techno", "movies", "mpb", "new-age", "new-release", "opera",
    "pagode", "party", "philippines-opm", "piano", "pop", "pop-film",
    "post-dubstep", "power-pop", "progressive-house", "psych-rock", "punk",
    "punk-rock", "r-n-b", "rainy-day", "reggae", "reggaeton", "road-trip",
    "rock", "rock-n-roll", "rockabilly", "romance", "sad", "salsa", "samba",
    "sertanejo", "show-tunes", "singer-songwriter", "ska", "sleep", "songwriter",
    "soul", "soundtracks", "spanish", "study", "summer", "swedish", "synth-pop",
    "tango", "techno", "trance", "trip-hop", "turkish", "work-out", "world-music",
]


class SpotifyCollector:
    def __init__(self, client_id: str, client_secret: str) -> None:
        auth_manager = SpotifyClientCredentials(
            client_id=client_id, client_secret=client_secret
        )
        self._sp = spotipy.Spotify(auth_manager=auth_manager)

    def get_available_genres(self) -> list[str]:
        """Return genre list (hardcoded — Spotify deprecated the API endpoint)."""
        return GENRES

    def search_track(self, query: str, limit: int = 10) -> list[dict]:
        """Search Spotify for tracks matching a query string."""
        results = self._sp.search(q=query, type="track", limit=limit)
        tracks = []
        for item in results["tracks"]["items"]:
            tracks.append(
                {
                    "track_id": item["id"],
                    "track_name": item["name"],
                    "artist_name": ", ".join(a["name"] for a in item["artists"]),
                    "popularity": item["popularity"],
                    "preview_url": item.get("preview_url"),
                    "album_image": (
                        item["album"]["images"][0]["url"]
                        if item["album"]["images"]
                        else None
                    ),
                    "external_url": item["external_urls"].get("spotify"),
                }
            )
        return tracks

    def collect_tracks(
        self,
        genres: list[str] | None = None,
        tracks_per_genre: int = 50,
        output_path: Path = DEFAULT_DATA_PATH,
    ) -> pd.DataFrame:
        """Collect tracks via Search API and save to CSV.

        Uses search instead of deprecated /recommendations endpoint.
        For large datasets prefer load_kaggle_dataset.py.
        """
        if genres is None:
            genres = GENRES

        output_path.parent.mkdir(parents=True, exist_ok=True)

        all_rows: list[dict] = []
        seen_ids: set[str] = set()

        for genre in genres:
            logger.info("Collecting genre: %s", genre)
            try:
                track_ids = list(
                    self._search_by_genre(genre, limit=tracks_per_genre)
                )
                track_ids = [t for t in track_ids if t not in seen_ids]
                if not track_ids:
                    continue

                metadata = self._fetch_track_metadata(track_ids)

                # Try audio features (may return 403/404 for new apps)
                features = self._fetch_audio_features_safe(track_ids)

                for tid in track_ids:
                    meta = metadata.get(tid)
                    if meta is None:
                        continue
                    feat = features.get(tid, {})
                    seen_ids.add(tid)
                    all_rows.append(
                        {
                            "track_id": tid,
                            "track_name": meta["track_name"],
                            "artist_name": meta["artist_name"],
                            "popularity": meta["popularity"],
                            "preview_url": meta.get("preview_url"),
                            "album_image": meta.get("album_image"),
                            "external_url": meta.get("external_url"),
                            "genre": genre,
                            "valence": feat.get("valence"),
                            "energy": feat.get("energy"),
                            "danceability": feat.get("danceability"),
                            "acousticness": feat.get("acousticness"),
                            "tempo": feat.get("tempo"),
                            "loudness": feat.get("loudness"),
                            "speechiness": feat.get("speechiness"),
                            "instrumentalness": feat.get("instrumentalness"),
                            "liveness": feat.get("liveness"),
                        }
                    )
            except Exception as exc:
                logger.warning("Failed for genre %s: %s", genre, exc)
            time.sleep(RATE_DELAY)

        df = pd.DataFrame(all_rows)
        # Drop rows where audio features are missing (if API was unavailable)
        df = df.dropna(subset=["valence", "energy"])
        df.to_csv(output_path, index=False)
        logger.info("Saved %d tracks to %s", len(df), output_path)
        return df

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _search_by_genre(self, genre: str, limit: int = 50) -> Iterator[str]:
        """Use search API to find tracks by genre (works for all apps)."""
        per_page = min(50, limit)
        collected = 0
        offset = 0
        while collected < limit:
            try:
                resp = self._sp.search(
                    q=f"genre:{genre}",
                    type="track",
                    limit=per_page,
                    offset=offset,
                )
                items = resp["tracks"]["items"]
                if not items:
                    break
                for item in items:
                    yield item["id"]
                    collected += 1
                    if collected >= limit:
                        break
                offset += per_page
            except Exception as exc:
                logger.warning("search failed (%s, offset=%d): %s", genre, offset, exc)
                break
            time.sleep(RATE_DELAY)

    def _fetch_audio_features_safe(self, track_ids: list[str]) -> dict[str, dict]:
        """Fetch audio features; returns empty dict if endpoint is deprecated."""
        result: dict[str, dict] = {}
        for i in range(0, len(track_ids), BATCH_SIZE):
            batch = track_ids[i : i + BATCH_SIZE]
            try:
                features = self._sp.audio_features(batch)
                for feat in features or []:
                    if feat:
                        result[feat["id"]] = feat
            except spotipy.SpotifyException as exc:
                if exc.http_status in (403, 404):
                    logger.warning(
                        "audio-features endpoint not available for this app "
                        "(deprecated for apps created after Nov 2024). "
                        "Use load_kaggle_dataset.py to get a full dataset."
                    )
                    return result
                logger.warning("audio_features batch failed: %s", exc)
            time.sleep(RATE_DELAY)
        return result

    def _fetch_track_metadata(self, track_ids: list[str]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for i in range(0, len(track_ids), BATCH_SIZE):
            batch = track_ids[i : i + BATCH_SIZE]
            try:
                tracks = self._sp.tracks(batch)["tracks"]
                for item in tracks or []:
                    if item:
                        result[item["id"]] = {
                            "track_name": item["name"],
                            "artist_name": ", ".join(
                                a["name"] for a in item["artists"]
                            ),
                            "popularity": item["popularity"],
                            "preview_url": item.get("preview_url"),
                            "album_image": (
                                item["album"]["images"][0]["url"]
                                if item["album"]["images"]
                                else None
                            ),
                            "external_url": item["external_urls"].get("spotify"),
                        }
            except Exception as exc:
                logger.warning("tracks batch failed: %s", exc)
            time.sleep(RATE_DELAY)
        return result
