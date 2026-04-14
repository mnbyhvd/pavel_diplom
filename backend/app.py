"""
FastAPI — Music Streaming Service API + Spotify OAuth.
Data source: PostgreSQL (via SQLAlchemy async + asyncpg).
"""

from __future__ import annotations

import logging
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, BackgroundTasks, Cookie, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from data_manager import DataManager
from database import AsyncSessionLocal, create_tables, get_db

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Globals ──────────────────────────────────────────────────

data_manager = DataManager()
collection_status: dict = {"running": False, "message": "idle", "progress": 0}
_preview_cache: dict[str, str | None] = {}

SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI  = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8000/auth/callback")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

GENRE_COLORS = [
    "#1db954","#e91e63","#9c27b0","#3f51b5","#2196f3",
    "#00bcd4","#009688","#ff5722","#ff9800","#ffc107",
    "#607d8b","#795548","#f44336","#8bc34a","#cddc39",
    "#673ab7","#03a9f4","#4caf50","#ff4081","#7c4dff",
]


# ─── Lifespan ─────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure schema exists, then load data
    await create_tables()
    data_manager.load()
    yield
    # Dispose async engine on shutdown
    from database import async_engine
    await async_engine.dispose()


app = FastAPI(title="MusicRec API", version="4.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# ─── Helpers ──────────────────────────────────────────────────

def _rec():
    if data_manager.recommender is None:
        raise HTTPException(503, "Recommender not ready")
    return data_manager.recommender

def _has_spotify() -> bool:
    return bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

def _spotify_auth():
    if not _has_spotify():
        raise HTTPException(503, "Spotify credentials not configured")
    from spotify_auth import SpotifyAuth
    return SpotifyAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI)


def _row_to_api(row) -> dict:
    """Convert a SQLAlchemy Row to API-safe dict."""
    from recommender import _safe
    import math

    def sf(key, default=0.0):
        val = row._mapping.get(key)
        if val is None:
            return default
        try:
            v = float(val)
            return default if (math.isnan(v) or math.isinf(v)) else round(v, 4)
        except (TypeError, ValueError):
            return default

    return {
        "track_id":        str(row.track_id),
        "track_name":      str(row.track_name),
        "artist_name":     str(row.artist_name),
        "valence":         sf("valence"),
        "energy":          sf("energy"),
        "danceability":    sf("danceability"),
        "acousticness":    sf("acousticness"),
        "tempo":           sf("tempo"),
        "instrumentalness": sf("instrumentalness"),
        "speechiness":     sf("speechiness"),
        "liveness":        sf("liveness"),
        "distance":        None,
        "preview_url":     _safe(row._mapping.get("preview_url")),
        "album_image":     _safe(row._mapping.get("album_image")),
        "external_url":    _safe(row._mapping.get("external_url")),
        "popularity":      int(row._mapping.get("popularity") or 0),
        "track_genre":     str(row._mapping.get("track_genre") or ""),
    }


# ─── OAuth endpoints ───────────────────────────────────────────

@app.get("/auth/login")
def auth_login(response: Response):
    auth = _spotify_auth()
    url, session_id = auth.get_auth_url()
    resp = RedirectResponse(url)
    resp.set_cookie("spotify_session", session_id, max_age=600, httponly=True, samesite="lax")
    return resp


@app.get("/auth/callback")
async def auth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    if error or not code:
        return RedirectResponse("/#auth_error")
    auth = _spotify_auth()
    session_id = await auth.exchange_code(code, state)
    if not session_id:
        return RedirectResponse("/#auth_error")
    resp = RedirectResponse("/#auth_success")
    resp.set_cookie("spotify_session", session_id, max_age=86400*7, httponly=True, samesite="lax")
    return resp


@app.get("/auth/me")
async def auth_me(spotify_session: str = Cookie(None)):
    if not spotify_session:
        return JSONResponse({"authenticated": False})
    auth = _spotify_auth()
    token = await auth.get_valid_token(spotify_session)
    if not token:
        return JSONResponse({"authenticated": False})
    user = await auth.get_user_info(token)
    return {
        "authenticated": True,
        "access_token":  token,
        "display_name":  user.get("display_name"),
        "email":         user.get("email"),
        "avatar":        user["images"][0]["url"] if user.get("images") else None,
        "product":       user.get("product"),
        "country":       user.get("country"),
    }


@app.post("/auth/logout")
def auth_logout(response: Response, spotify_session: str = Cookie(None)):
    if spotify_session and _has_spotify():
        from spotify_auth import SpotifyAuth
        SpotifyAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI).logout(spotify_session)
    response.delete_cookie("spotify_session")
    return {"ok": True}


@app.get("/auth/token")
async def auth_token(spotify_session: str = Cookie(None)):
    if not spotify_session:
        raise HTTPException(401, "Not authenticated")
    auth = _spotify_auth()
    token = await auth.get_valid_token(spotify_session)
    if not token:
        raise HTTPException(401, "Session expired")
    return {"access_token": token}


# ─── Home ─────────────────────────────────────────────────────

@app.get("/api/home")
def get_home():
    rec = _rec()
    featured   = rec.top_tracks(n=20)
    raw_genres = rec.genre_counts()
    genres = [
        {**g, "color": GENRE_COLORS[i % len(GENRE_COLORS)]}
        for i, g in enumerate(raw_genres[:50])
    ]
    v = round(random.uniform(0.2, 0.9), 2)
    e = round(random.uniform(0.2, 0.9), 2)
    mood_picks = rec.recommend_by_mood(valence=v, energy=e, n=8)
    return {
        "featured":   featured,
        "genres":     genres,
        "mood_picks": mood_picks,
        "mood_point": {"valence": v, "energy": e},
        "source":     data_manager.source,
    }


# ─── Tracks ───────────────────────────────────────────────────

@app.get("/api/tracks")
def get_all_tracks(limit: int = Query(default=5000, ge=1, le=20000)):
    rec = _rec()
    tracks = rec.all_tracks()
    if len(tracks) > limit:
        step = max(1, len(tracks) // limit)
        tracks = tracks[::step]
    return {"tracks": tracks, "source": data_manager.source}


@app.get("/api/tracks/{track_id}")
def get_track(track_id: str):
    rec   = _rec()
    track = rec.get_track(track_id)
    if not track:
        raise HTTPException(404, "Track not found")
    return track


# ─── Genres ───────────────────────────────────────────────────

@app.get("/api/genres")
def get_genres():
    rec    = _rec()
    counts = rec.genre_counts()
    if counts:
        return {"genres": [
            {**g, "color": GENRE_COLORS[i % len(GENRE_COLORS)]}
            for i, g in enumerate(counts)
        ]}
    from spotify_client import GENRES
    return {"genres": [
        {"name": g, "count": 0, "color": GENRE_COLORS[i % len(GENRE_COLORS)]}
        for i, g in enumerate(GENRES)
    ]}


@app.get("/api/genre/{genre}")
async def get_genre_tracks(
    genre: str,
    page: int     = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated genre tracks — served directly from PostgreSQL."""
    offset = (page - 1) * per_page

    count_res = await db.execute(
        text("SELECT COUNT(*) FROM tracks WHERE track_genre = :g"),
        {"g": genre},
    )
    total = count_res.scalar_one() or 0

    rows_res = await db.execute(
        text("""
            SELECT track_id, track_name, artist_name, popularity,
                   valence, energy, danceability, acousticness, tempo,
                   instrumentalness, speechiness, liveness,
                   track_genre, preview_url, album_image, external_url
              FROM tracks
             WHERE track_genre = :g
             ORDER BY popularity DESC NULLS LAST
             LIMIT :lim OFFSET :off
        """),
        {"g": genre, "lim": per_page, "off": offset},
    )
    tracks = [_row_to_api(r) for r in rows_res.fetchall()]
    return {"genre": genre, "total": total, "page": page, "per_page": per_page, "tracks": tracks}


# ─── Features metadata ────────────────────────────────────────

@app.get("/api/features")
def get_features():
    from features_meta import FEATURE_META, FEATURE_WEIGHTS, ALL_FEATURES
    rec    = _rec()
    result = []
    for key in ALL_FEATURES:
        if key not in rec._df.columns:
            continue
        meta = FEATURE_META.get(key, {})
        col  = rec._df[key].dropna()
        result.append({
            "key":         key,
            "label":       meta.get("label", key),
            "ru_label":    meta.get("ru_label", key),
            "description": meta.get("description", ""),
            "color":       meta.get("color", "#888888"),
            "unit":        meta.get("unit", "0–1"),
            "icon":        meta.get("icon", ""),
            "range_hint":  meta.get("range_hint", ""),
            "weight":      FEATURE_WEIGHTS.get(key, 1.0),
            "mean":        round(float(col.mean()), 4),
            "std":         round(float(col.std()), 4),
            "min":         round(float(col.min()), 4),
            "max":         round(float(col.max()), 4),
            "p25":         round(float(col.quantile(0.25)), 4),
            "p75":         round(float(col.quantile(0.75)), 4),
        })
    return {"features": result}


# ─── Recommendations ──────────────────────────────────────────

@app.get("/api/recommend/mood")
def recommend_by_mood(
    valence: float = Query(..., ge=0.0, le=1.0),
    energy:  float = Query(..., ge=0.0, le=1.0),
    n: int         = Query(default=8, ge=1, le=50),
):
    rec     = _rec()
    results = rec.recommend_by_mood(valence=valence, energy=energy, n=n)
    return {"query": {"valence": valence, "energy": energy}, "recommendations": results}


@app.get("/api/recommend/explain/{track_id}")
def explain_recommendation(track_id: str, n: int = Query(default=6, ge=1, le=20)):
    rec   = _rec()
    track = rec.get_track(track_id)
    if not track:
        raise HTTPException(404, "Track not found")
    result = rec.explain_recommendations(track_id, n=n)
    if not result:
        raise HTTPException(404, "Track not found")
    return result


@app.get("/api/recommend/{track_id}")
def recommend_by_track(
    track_id: str,
    n: int = Query(default=10, ge=1, le=50),
):
    rec   = _rec()
    track = rec.get_track(track_id)
    if not track:
        raise HTTPException(404, "Track not found")
    results = rec.recommend(track_id, n=n)
    return {"query": track, "recommendations": results}


# ─── Player preview ───────────────────────────────────────────

@app.get("/api/player/preview/{track_id}")
def get_preview_url(track_id: str):
    if track_id in _preview_cache:
        return {"track_id": track_id, "preview_url": _preview_cache[track_id]}

    rec   = _rec()
    track = rec.get_track(track_id)
    if track and track.get("preview_url"):
        _preview_cache[track_id] = track["preview_url"]
        return {"track_id": track_id, "preview_url": track["preview_url"]}

    if _has_spotify():
        try:
            from spotify_client import SpotifyCollector
            sp     = SpotifyCollector(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
            result = sp._sp.tracks([track_id])["tracks"]
            url    = result[0].get("preview_url") if result and result[0] else None
            _preview_cache[track_id] = url
            return {"track_id": track_id, "preview_url": url}
        except Exception as exc:
            logger.warning("preview fetch failed for %s: %s", track_id, exc)

    _preview_cache[track_id] = None
    return {"track_id": track_id, "preview_url": None}


# ─── Search (PostgreSQL full-text) ────────────────────────────

@app.get("/api/search/local")
async def search_local(
    q: str         = Query(..., min_length=1),
    limit: int     = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Search tracks using PostgreSQL.
    Tries full-text search first; falls back to ILIKE if no results.
    """
    q_clean = q.strip()

    # Full-text search via tsvector (uses GIN index — very fast)
    fts_res = await db.execute(
        text("""
            SELECT track_id, track_name, artist_name, popularity,
                   valence, energy, danceability, acousticness, tempo,
                   instrumentalness, speechiness, liveness,
                   track_genre, preview_url, album_image, external_url,
                   ts_rank(
                       to_tsvector('simple', coalesce(track_name,'') || ' ' || coalesce(artist_name,'')),
                       plainto_tsquery('simple', :q)
                   ) AS rank
              FROM tracks
             WHERE to_tsvector('simple', coalesce(track_name,'') || ' ' || coalesce(artist_name,''))
                   @@ plainto_tsquery('simple', :q)
             ORDER BY rank DESC, popularity DESC NULLS LAST
             LIMIT :lim
        """),
        {"q": q_clean, "lim": limit},
    )
    rows = fts_res.fetchall()

    # Fallback: ILIKE prefix search if full-text returns nothing
    if not rows:
        like_res = await db.execute(
            text("""
                SELECT track_id, track_name, artist_name, popularity,
                       valence, energy, danceability, acousticness, tempo,
                       instrumentalness, speechiness, liveness,
                       track_genre, preview_url, album_image, external_url
                  FROM tracks
                 WHERE track_name  ILIKE :pat
                    OR artist_name ILIKE :pat
                 ORDER BY popularity DESC NULLS LAST
                 LIMIT :lim
            """),
            {"pat": f"%{q_clean}%", "lim": limit},
        )
        rows = like_res.fetchall()

    results = [_row_to_api(r) for r in rows]
    return {"results": results, "total": len(results), "query": q}


@app.get("/api/search")
def search_tracks(
    q: str     = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Spotify search (requires credentials)."""
    _spotify_auth()
    from spotify_client import SpotifyCollector
    sp      = SpotifyCollector(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
    results = sp.search_track(q, limit=limit)
    rec     = _rec()
    for t in results:
        ex = rec.get_track(t["track_id"])
        if ex:
            t.update({"valence": ex["valence"], "energy": ex["energy"],
                      "danceability": ex["danceability"], "in_dataset": True})
        else:
            t["in_dataset"] = False
    return {"results": results}


# ─── Stats ────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    base = {**_rec().stats(), "source": data_manager.source}
    # Enrich with live DB count (authoritative)
    try:
        res = await db.execute(text("SELECT COUNT(*) FROM tracks"))
        base["db_total"] = res.scalar_one()
    except Exception:
        pass
    return base


# ─── Data import (CSV → PostgreSQL) ───────────────────────────

@app.post("/api/import")
async def import_csv(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Trigger async import of data/tracks.csv into PostgreSQL."""
    from pathlib import Path as P
    csv_path = P(__file__).parent.parent / "data" / "tracks.csv"
    if not csv_path.exists():
        raise HTTPException(404, "data/tracks.csv not found — run load_kaggle_dataset.py first")

    if collection_status["running"]:
        raise HTTPException(409, "Import already running")

    def _run():
        collection_status["running"] = True
        collection_status["message"] = "Importing CSV → PostgreSQL…"
        collection_status["progress"] = 0
        try:
            import pandas as pd
            from database import sync_engine
            from sqlalchemy import text as stext

            df = pd.read_csv(csv_path)
            total = len(df)
            logger.info("Importing %d tracks from %s", total, csv_path)

            # Truncate and re-import (idempotent)
            with sync_engine.begin() as conn:
                conn.execute(stext("TRUNCATE TABLE tracks RESTART IDENTITY"))

            chunk = 2000
            for i in range(0, total, chunk):
                batch = df.iloc[i : i + chunk]
                batch.to_sql(
                    "tracks",
                    sync_engine,
                    if_exists="append",
                    index=False,
                    method="multi",
                )
                pct = min(99, int((i + chunk) / total * 100))
                collection_status["progress"] = pct
                collection_status["message"]  = f"Importing… {min(i+chunk, total)}/{total}"

            collection_status["progress"] = 100
            collection_status["message"]  = f"Done — {total} tracks imported"
            logger.info("Import complete: %d tracks", total)
            data_manager.reload()
        except Exception as exc:
            logger.exception("Import failed")
            collection_status["message"] = f"Error: {exc}"
        finally:
            collection_status["running"] = False

    background_tasks.add_task(_run)
    return {"detail": "Import started — check /api/collection-status"}


@app.get("/api/collection-status")
def collection_status_ep():
    return collection_status


# ─── Frontend ─────────────────────────────────────────────────

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", response_class=FileResponse)
    def serve_index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/{full_path:path}", response_class=FileResponse)
    def serve_spa(full_path: str):
        f = FRONTEND_DIR / full_path
        if f.exists() and f.is_file():
            return FileResponse(str(f))
        return FileResponse(str(FRONTEND_DIR / "index.html"))
