"""
load_to_db.py — Bulk-import tracks from CSV into PostgreSQL.

Usage (run from the project root or inside the Docker container):
    python backend/load_to_db.py [--csv data/tracks.csv] [--chunk 2000] [--drop]

Options:
    --csv PATH     Path to CSV file (default: data/tracks.csv)
    --chunk N      Rows per INSERT batch (default: 2000)
    --drop         Drop and recreate the tracks table before import
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

# Allow running from project root OR backend/
sys.path.insert(0, str(Path(__file__).parent))

from database import sync_engine, Base  # noqa: E402
from models import Track  # noqa: F401 E402  — registers model with Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Columns from CSV that map to Track model columns
CSV_COLUMNS = [
    "track_id", "track_name", "artist_name", "popularity",
    "valence", "energy", "danceability", "acousticness",
    "tempo", "instrumentalness", "speechiness", "liveness",
    "track_genre", "preview_url", "album_image", "external_url",
]


def load(csv_path: Path, chunk_size: int, drop: bool) -> None:
    if not csv_path.exists():
        logger.error("CSV not found: %s", csv_path)
        sys.exit(1)

    logger.info("Reading %s ...", csv_path)
    df = pd.read_csv(csv_path, low_memory=False)
    logger.info("  %d rows, %d columns", len(df), len(df.columns))

    # Keep only columns that exist in both CSV and our model
    keep = [c for c in CSV_COLUMNS if c in df.columns]
    df = df[keep].copy()

    # Clean up
    df = df.drop_duplicates(subset=["track_id"])
    df = df.dropna(subset=["track_id", "track_name", "artist_name"])
    df["popularity"] = pd.to_numeric(df["popularity"], errors="coerce").fillna(0).astype(int)
    df["tempo"] = pd.to_numeric(df["tempo"], errors="coerce")

    # Replace NaN strings with None so SQLAlchemy inserts NULL
    str_cols = ["preview_url", "album_image", "external_url", "track_genre"]
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].where(df[col].notna() & (df[col] != ""), other=None)

    logger.info("After dedup/clean: %d rows", len(df))

    with sync_engine.begin() as conn:
        if drop:
            logger.warning("Dropping tracks table...")
            conn.execute(text("DROP TABLE IF EXISTS tracks CASCADE"))

        # Create tables if they don't exist
        Base.metadata.create_all(conn)

        # Create indexes
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tracks_genre
                ON tracks (track_genre)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tracks_popularity
                ON tracks (popularity DESC NULLS LAST)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tracks_search
                ON tracks
                USING gin(to_tsvector('simple',
                    coalesce(track_name, '') || ' ' || coalesce(artist_name, '')
                ))
        """))

    # Insert in chunks using pandas to_sql with ON CONFLICT DO NOTHING
    total = len(df)
    inserted = 0
    for start in range(0, total, chunk_size):
        chunk = df.iloc[start:start + chunk_size]
        chunk.to_sql(
            "tracks",
            sync_engine,
            if_exists="append",
            index=False,
            method="multi",
        )
        inserted += len(chunk)
        pct = inserted / total * 100
        logger.info("  Imported %d / %d rows (%.1f%%)", inserted, total, pct)

    logger.info("Done! %d tracks in database.", inserted)

    # Quick verify
    with sync_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM tracks")).scalar()
    logger.info("DB now contains %d tracks.", count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import tracks CSV → PostgreSQL")
    parser.add_argument("--csv",   default="data/tracks.csv", help="CSV file path")
    parser.add_argument("--chunk", default=2000, type=int,    help="Rows per batch")
    parser.add_argument("--drop",  action="store_true",        help="Drop table first")
    args = parser.parse_args()

    load(Path(args.csv), args.chunk, args.drop)


if __name__ == "__main__":
    main()
