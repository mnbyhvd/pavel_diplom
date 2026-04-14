"""
DataManager — loads tracks from PostgreSQL and fits the recommender.

Flow:
  1. Connect via sync SQLAlchemy (psycopg2) — compatible with pandas
  2. pd.read_sql() pulls all rows into a DataFrame
  3. MusicRecommender.fit() builds the in-memory KNN index
  4. All recommendation queries run against the numpy/sklearn structures
  5. FastAPI endpoints use async queries for search / browse / stats
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from database import sync_engine
from features_meta import ALL_FEATURES
from recommender import MusicRecommender

logger = logging.getLogger(__name__)


def _build_demo_df() -> pd.DataFrame:
    """Fallback demo dataset (25 well-known tracks) when DB has no data."""
    rows = [
        ("4uLU6hMCjMI75M1A2tKUQC", "Never Gonna Give You Up", "Rick Astley", 72,
         0.897, 0.758, 0.886, 0.0166, 113.4, 0.0,  0.045, 0.08),
        ("7qiZfU4dY1lWllzX7mPBI3", "Shape of You",            "Ed Sheeran",  92,
         0.931, 0.730, 0.825, 0.0806, 95.9,  0.0,  0.079, 0.11),
        ("3n3Ppam7vgaVa1iaRUIOKE", "Someone Like You",        "Adele",       83,
         0.0706,0.175, 0.344, 0.760,  67.7,  0.0,  0.03,  0.09),
        ("1mea3bSkSGXuIRvnydlB5b", "Bohemian Rhapsody",       "Queen",       87,
         0.387, 0.406, 0.352, 0.0378, 71.6,  0.0,  0.04,  0.33),
        ("7GhIk7Il098yCjg4BQjzvb", "Blinding Lights",         "The Weeknd",  89,
         0.334, 0.730, 0.514, 0.000132,171.0, 0.0, 0.06,  0.09),
        ("3DXncPQOG4VBw3QHh3S817", "Watermelon Sugar",        "Harry Styles",80,
         0.557, 0.816, 0.548, 0.122,  95.4,  0.0,  0.05,  0.33),
        ("0VjIjW4GlUZAMYd2vXMi3b", "Levitating",              "Dua Lipa",    85,
         0.915, 0.825, 0.702, 0.00275,103.0, 0.0,  0.04,  0.10),
        ("2LawezPeJhN4AWuSB0GtAU", "Lose Yourself",           "Eminem",      79,
         0.302, 0.870, 0.755, 0.0121, 171.0, 0.0,  0.33,  0.09),
        ("0pqnGHJpmpxLKifKRmU6WP", "Clair de Lune",           "Debussy",     69,
         0.0585,0.0384,0.203, 0.992,  60.1,  0.92, 0.04,  0.11),
        ("3qT4bUD1MaWpGrTwcvguhb", "Hotel California",        "Eagles",      81,
         0.545, 0.480, 0.619, 0.00106,74.9,  0.0,  0.04,  0.24),
        ("5ChkMS8OtdzJeqyybCc9R5", "Smells Like Teen Spirit", "Nirvana",     85,
         0.106, 0.926, 0.415, 0.000178,117.0,0.0,  0.05,  0.15),
        ("60a0Rd6pjrkxjPbaKzXjfq", "Stayin' Alive",           "Bee Gees",    77,
         0.974, 0.860, 0.848, 0.00339,104.0, 0.0,  0.04,  0.12),
        ("2tznHmp70mWOG7tiDaXjnj", "Uptown Funk",             "Mark Ronson", 84,
         0.953, 0.934, 0.855, 0.0282, 115.0, 0.0,  0.10,  0.11),
        ("2374M0fQpWi3dLnB54qaLX", "Billie Jean",             "Michael Jackson",86,
         0.951, 0.793, 0.940, 0.00797,117.0, 0.0,  0.04,  0.10),
        ("6habFhsOp2NvshLv26DqMb", "Rolling in the Deep",    "Adele",       82,
         0.178, 0.786, 0.694, 0.0192, 104.9, 0.0,  0.05,  0.30),
        ("4Dvkj6JhhA12EX05fT7y2e", "As It Was",              "Harry Styles",90,
         0.657, 0.731, 0.520, 0.0256, 124.0, 0.0,  0.05,  0.10),
        ("6nGeLlakfzlBcFdZXauQo9", "Bad Guy",                "Billie Eilish",83,
         0.562, 0.425, 0.700, 0.212,  135.0, 0.0,  0.34,  0.10),
        ("3ee8Jmje8o58CHK66QrVC2", "Despacito",              "Luis Fonsi",   88,
         0.822, 0.800, 0.694, 0.0487, 178.0, 0.0,  0.08,  0.08),
        ("3DarAbFujv6eYNliUTyqtz", "Imagine",                "John Lennon",  78,
         0.492, 0.154, 0.432, 0.904,  75.1,  0.0,  0.04,  0.09),
        ("2Foc5Q5nqNiosCNqttzHof", "Numb",                   "Linkin Park",  82,
         0.195, 0.602, 0.456, 0.00189,107.0, 0.0,  0.04,  0.09),
        ("0DiWol3AO6WpXZgdpxHaty", "Dancing Queen",          "ABBA",         79,
         0.960, 0.842, 0.757, 0.0232, 101.9, 0.0,  0.05,  0.08),
        ("6I9VzXrHxO9rA9A5euc8Ak", "Havana",                 "Camila Cabello",83,
         0.411, 0.488, 0.612, 0.126,  105.0, 0.0,  0.05,  0.11),
        ("3AJwUDP919kvQ9QcozQPxg", "Yesterday",              "The Beatles",  75,
         0.314, 0.171, 0.430, 0.901,  96.9,  0.0,  0.03,  0.12),
        ("5W3cjX2J3tjhG8zb6u0qHn", "Stairway to Heaven",    "Led Zeppelin", 80,
         0.368, 0.334, 0.235, 0.519,  82.8,  0.0,  0.04,  0.15),
        ("0O45fw2L5vsWpdsOdXwNAR", "Moonlight Sonata",       "Beethoven",    65,
         0.0303,0.0264,0.160, 0.994,  55.3,  0.95, 0.04,  0.09),
    ]
    cols = [
        "track_id", "track_name", "artist_name", "popularity",
        "valence", "energy", "danceability", "acousticness", "tempo",
        "instrumentalness", "speechiness", "liveness",
    ]
    return pd.DataFrame(rows, columns=cols)


class DataManager:
    """Loads track data from PostgreSQL and maintains the fitted recommender."""

    def __init__(self) -> None:
        self.recommender: MusicRecommender | None = None
        self.source: str = "none"

    def load(
        self,
        features: list[str] | None = None,
        normalize: bool = True,
    ) -> None:
        """Load all tracks from PostgreSQL and fit the recommender."""
        features = features or ALL_FEATURES

        try:
            df = pd.read_sql(
                "SELECT * FROM tracks ORDER BY id",
                sync_engine,
            )
            if df.empty:
                raise ValueError("tracks table is empty")
            self.source = "postgresql"
            logger.info("Loaded %d tracks from PostgreSQL", len(df))
        except Exception as exc:
            logger.warning("PostgreSQL unavailable (%s) — using demo dataset", exc)
            df = _build_demo_df()
            self.source = "demo"

        # Drop rows missing core features
        df = df.dropna(subset=["valence", "energy", "track_id"])

        self.recommender = MusicRecommender(
            features=features,
            normalize=normalize,
            n_neighbors=10,
        )
        self.recommender.fit(df)
        logger.info(
            "Recommender ready: %d tracks, features=%s, source=%s",
            len(df),
            self.recommender.features,
            self.source,
        )

    def reload(self, **kwargs) -> None:
        self.load(**kwargs)
