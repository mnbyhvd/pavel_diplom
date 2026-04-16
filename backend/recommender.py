"""
Music recommendation engine — hybrid content-based filtering.

Algorithm (v2):
  1. Represent each track as a point in 8D audio feature space
  2. Z-score normalize all features with StandardScaler
  3. Apply per-feature weights by multiplying normalized values by sqrt(weight)
  4. PRIMARY: Cosine similarity KNN — measures angle between feature vectors,
     which captures the audio *profile* better than Euclidean distance in high-D space.
     (Kaggle studies on Spotify dataset consistently show cosine > Euclidean for audio)
  5. POST-PROCESSING pipeline:
     a. Genre boost  — same-genre tracks score better (soft, not hard filter)
     b. Popularity blend — blends similarity with track popularity (α=0.08)
     c. Artist diversity — at most MAX_PER_ARTIST tracks per artist in results

Reference implementations:
  - https://www.kaggle.com/code/vatsalmavani/music-recommendation-system
  - https://www.kaggle.com/code/thomasbs86/spotify-song-recommender
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from features_meta import ALL_FEATURES, FEATURE_WEIGHTS

# Genre boost: same-genre candidates get their distance multiplied by this factor
# (< 1.0 → shorter distance → ranked higher)
GENRE_SAME_FACTOR   = 0.75   # 25% bonus for same genre
GENRE_CROSS_FACTOR  = 1.15   # 15% penalty for different genre

# Popularity blend weight (0 = pure content, 1 = pure popularity)
POPULARITY_ALPHA = 0.08

# Maximum tracks from the same artist in one recommendation list
MAX_PER_ARTIST = 2

# How many extra candidates to fetch before post-processing
CANDIDATE_MULTIPLIER = 4


def _safe(value):
    """Convert NaN/inf/numpy scalars to JSON-safe Python types."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        if isinstance(value, (np.floating, np.integer)):
            v = value.item()
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                return None
            return v
    except (TypeError, ValueError):
        pass
    return value


class MusicRecommender:
    """
    Content-based music recommender with cosine similarity + post-processing.

    Improvements over v1 (plain weighted Euclidean KNN):
    - Cosine similarity (better for high-D audio feature vectors)
    - Genre-aware re-ranking (same genre gets a score boost)
    - Popularity blending (8% weight on popularity)
    - Artist diversity cap (max MAX_PER_ARTIST per artist)
    """

    def __init__(
        self,
        features: list[str] | None = None,
        weights: dict[str, float] | None = None,
        normalize: bool = True,
        n_neighbors: int = 10,
    ) -> None:
        self.features = features or ALL_FEATURES
        self.weights = weights or FEATURE_WEIGHTS
        self.normalize = normalize
        self.n_neighbors = n_neighbors

        self._scaler = StandardScaler()
        # Use cosine metric — measures angular distance between feature vectors.
        # Ball-tree doesn't support cosine; brute-force is fast enough for 90k tracks.
        self._knn = NearestNeighbors(metric="cosine", algorithm="brute")
        self._df: pd.DataFrame | None = None
        self._weight_sqrt: np.ndarray | None = None
        self._pop_norm: np.ndarray | None = None   # popularity normalized to [0,1]
        self._fitted = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> "MusicRecommender":
        """Fit the model on a track DataFrame."""
        df = df.copy()
        self._df = df.reset_index(drop=True)

        # Only keep features that actually exist in the dataframe
        self.features = [f for f in self.features if f in self._df.columns]

        # Build sqrt(weight) vector for weighted cosine
        w = np.array([self.weights.get(f, 1.0) for f in self.features])
        self._weight_sqrt = np.sqrt(w)

        X = self._df[self.features].fillna(0.0).values.astype(float)

        if self.normalize:
            X = self._scaler.fit_transform(X)

        # Pre-multiply by sqrt(weights) — turns standard cosine into weighted cosine
        X_weighted = X * self._weight_sqrt

        self._knn.fit(X_weighted)

        # Precompute normalized popularity scores for blending
        pop = self._df["popularity"].fillna(0).values.astype(float)
        pop_max = pop.max()
        self._pop_norm = pop / pop_max if pop_max > 0 else pop

        self._fitted = True
        return self

    def recommend(self, track_id: str, n: int | None = None) -> list[dict]:
        """Return up to n recommendations for a given track_id."""
        self._check_fitted()
        n = n or self.n_neighbors
        idx = self._find_index(track_id)
        if idx is None:
            return []

        query_genre = str(self._df.iloc[idx].get("track_genre") or "")
        query_artist = str(self._df.iloc[idx].get("artist_name") or "").lower()

        raw_candidates = self._fetch_candidates(idx, n)
        results = self._postprocess(
            raw_candidates,
            n=n,
            query_genre=query_genre,
            exclude_track_id=track_id,
            exclude_artist=query_artist,
        )
        return results

    def recommend_by_mood(
        self, valence: float, energy: float, n: int | None = None
    ) -> list[dict]:
        """Return tracks closest to a (valence, energy) mood point.

        Unknown feature dimensions are set to their dataset mean — which after
        z-score normalization becomes z=0 (meaning «typical/average»), ensuring
        they do not bias the search toward any extreme.
        """
        self._check_fitted()
        n = n or self.n_neighbors

        if self.normalize:
            full = np.array(self._scaler.mean_, copy=True).reshape(1, -1)
            if "valence" in self.features:
                full[0, self.features.index("valence")] = valence
            if "energy" in self.features:
                full[0, self.features.index("energy")] = energy
            point_transformed = self._scaler.transform(full) * self._weight_sqrt
        else:
            full = np.zeros((1, len(self.features)))
            if "valence" in self.features:
                full[0, self.features.index("valence")] = valence
            if "energy" in self.features:
                full[0, self.features.index("energy")] = energy
            point_transformed = full * self._weight_sqrt

        k = min(n * CANDIDATE_MULTIPLIER, len(self._df))
        distances, indices = self._knn.kneighbors(point_transformed, n_neighbors=k)

        # For mood-based: popularity blend only (no genre filter since no query track)
        candidates = []
        for dist, i in zip(distances[0], indices[0]):
            pop_score = float(self._pop_norm[i])
            blended = (1.0 - POPULARITY_ALPHA) * float(dist) - POPULARITY_ALPHA * pop_score
            candidates.append((blended, float(dist), int(i)))

        candidates.sort(key=lambda x: x[0])

        results = []
        seen_artists: dict[str, int] = {}
        for _, dist, i in candidates:
            if len(results) >= n:
                break
            artist = str(self._df.iloc[i].get("artist_name") or "").lower()
            if seen_artists.get(artist, 0) >= MAX_PER_ARTIST:
                continue
            seen_artists[artist] = seen_artists.get(artist, 0) + 1
            results.append(self._row_to_dict(self._df.iloc[i], distance=dist))

        return results

    def explain_recommendations(self, track_id: str, n: int = 6) -> dict:
        """Return detailed per-feature explanation for recommendations."""
        self._check_fitted()
        idx = self._find_index(track_id)
        if idx is None:
            return {}

        recs = self.recommend(track_id, n=n)

        # Per-feature dataset statistics
        feature_stats: dict[str, dict] = {}
        for i, f in enumerate(self.features):
            col = self._df[f]
            entry: dict = {
                "mean":  round(float(col.mean()), 4),
                "std":   round(float(col.std()), 4),
                "min":   round(float(col.min()), 4),
                "max":   round(float(col.max()), 4),
                "weight": round(self.weights.get(f, 1.0), 3),
            }
            if self.normalize:
                entry["scaler_mean"]  = round(float(self._scaler.mean_[i]), 4)
                entry["scaler_scale"] = round(float(self._scaler.scale_[i]), 4)
            feature_stats[f] = entry

        # Query track's per-feature raw + z values
        query_row = self._df.iloc[idx]
        query_features: dict[str, dict] = {}
        for i, f in enumerate(self.features):
            raw = float(query_row[f]) if not math.isnan(float(query_row[f])) else 0.0
            entry = {"raw": round(raw, 4)}
            if self.normalize:
                mu = float(self._scaler.mean_[i])
                sig = float(self._scaler.scale_[i])
                z = (raw - mu) / sig
                entry["z"] = round(z, 4)
                entry["z_weighted"] = round(z * float(self._weight_sqrt[i]), 4)
            query_features[f] = entry

        # Per-recommendation feature-level distance breakdown
        rec_details = []
        for rec in recs:
            rec_idx = self._find_index(rec["track_id"])
            if rec_idx is None:
                continue
            rec_row = self._df.iloc[rec_idx]

            contributions: dict[str, dict] = {}
            total_sq = 0.0

            for i, f in enumerate(self.features):
                raw_a = float(query_row[f]) if not math.isnan(float(query_row[f])) else 0.0
                raw_b = float(rec_row[f]) if not math.isnan(float(rec_row[f])) else 0.0
                raw_diff = abs(raw_a - raw_b)

                if self.normalize:
                    mu  = float(self._scaler.mean_[i])
                    sig = float(self._scaler.scale_[i])
                    z_a = (raw_a - mu) / sig
                    z_b = (raw_b - mu) / sig
                    z_diff = abs(z_a - z_b)
                    sq = (float(self._weight_sqrt[i]) * z_diff) ** 2
                else:
                    z_diff = raw_diff
                    sq = (float(self._weight_sqrt[i]) * raw_diff) ** 2

                total_sq += sq
                contributions[f] = {
                    "raw_a":    round(raw_a, 4),
                    "raw_b":    round(raw_b, 4),
                    "raw_diff": round(raw_diff, 4),
                    "z_diff":   round(z_diff, 4),
                    "sq_contrib": round(sq, 6),
                    "pct": 0.0,
                }

            for f in contributions:
                contributions[f]["pct"] = (
                    round(contributions[f]["sq_contrib"] / total_sq * 100, 1)
                    if total_sq > 0 else 0.0
                )

            rec_details.append({
                "track":              rec,
                "total_distance":     rec.get("distance"),
                "feature_contributions": contributions,
            })

        return {
            "query": self._row_to_dict(query_row),
            "algorithm": {
                "features":    self.features,
                "weights":     {f: round(self.weights.get(f, 1.0), 3) for f in self.features},
                "normalize":   self.normalize,
                "metric":      "weighted_cosine + genre_boost + popularity_blend",
                "n_neighbors": n,
                "formula":     "cosine(w·z(a), w·z(b)) × genre_factor − α·popularity",
            },
            "feature_stats":   feature_stats,
            "query_features":  query_features,
            "recommendations": rec_details,
        }

    def all_tracks(self) -> list[dict]:
        """Return all tracks with audio features (for visualisation)."""
        self._check_fitted()
        return [self._row_to_dict(row) for _, row in self._df.iterrows()]

    def get_track(self, track_id: str) -> dict | None:
        self._check_fitted()
        idx = self._find_index(track_id)
        if idx is None:
            return None
        return self._row_to_dict(self._df.iloc[idx])

    def tracks_by_genre(
        self, genre: str, page: int = 1, per_page: int = 50
    ) -> tuple[list[dict], int]:
        self._check_fitted()
        if "track_genre" not in self._df.columns:
            return [], 0
        filtered = self._df[self._df["track_genre"] == genre].sort_values(
            "popularity", ascending=False
        )
        total = len(filtered)
        start = (page - 1) * per_page
        rows = filtered.iloc[start : start + per_page]
        return [self._row_to_dict(row) for _, row in rows.iterrows()], total

    def genre_counts(self) -> list[dict]:
        self._check_fitted()
        if "track_genre" not in self._df.columns:
            return []
        counts = self._df["track_genre"].value_counts()
        return [{"name": str(g), "count": int(c)} for g, c in counts.items()]

    def top_tracks(self, n: int = 20) -> list[dict]:
        self._check_fitted()
        rows = self._df.sort_values("popularity", ascending=False).head(n)
        return [self._row_to_dict(row) for _, row in rows.iterrows()]

    def stats(self) -> dict:
        self._check_fitted()
        result: dict = {
            "total_tracks":  len(self._df),
            "features_used": self.features,
            "normalize":     self.normalize,
        }
        for f in ALL_FEATURES:
            if f in self._df.columns:
                result[f"{f}_mean"] = round(float(self._df[f].mean()), 4)
                result[f"{f}_std"]  = round(float(self._df[f].std()), 4)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_candidates(self, idx: int, n: int) -> list[tuple[float, int]]:
        """Fetch CANDIDATE_MULTIPLIER*n nearest neighbors by cosine distance."""
        X = self._get_feature_matrix()
        k = min(n * CANDIDATE_MULTIPLIER + 1, len(self._df))
        distances, indices = self._knn.kneighbors(X[idx : idx + 1], n_neighbors=k)
        return [
            (float(dist), int(i))
            for dist, i in zip(distances[0], indices[0])
            if int(i) != idx
        ]

    def _postprocess(
        self,
        candidates: list[tuple[float, int]],
        n: int,
        query_genre: str,
        exclude_track_id: str,
        exclude_artist: str,
    ) -> list[dict]:
        """
        Re-rank candidates with:
          1. Genre boost  — same genre → distance × GENRE_SAME_FACTOR
          2. Popularity blend — subtract α * normalized_popularity from score
          3. Artist diversity — max MAX_PER_ARTIST tracks per artist
        """
        scored: list[tuple[float, float, int]] = []  # (blended_score, raw_dist, idx)

        for raw_dist, i in candidates:
            row = self._df.iloc[i]

            # 1. Genre boost
            track_genre = str(row.get("track_genre") or "")
            if query_genre and track_genre:
                genre_factor = GENRE_SAME_FACTOR if track_genre == query_genre else GENRE_CROSS_FACTOR
            else:
                genre_factor = 1.0
            dist_boosted = raw_dist * genre_factor

            # 2. Popularity blend
            pop_score = float(self._pop_norm[i])
            blended = (1.0 - POPULARITY_ALPHA) * dist_boosted - POPULARITY_ALPHA * pop_score

            scored.append((blended, raw_dist, i))

        scored.sort(key=lambda x: x[0])

        # 3. Artist diversity filter
        results: list[dict] = []
        seen_artists: dict[str, int] = {}

        for _, raw_dist, i in scored:
            if len(results) >= n:
                break
            row = self._df.iloc[i]
            if str(row.get("track_id")) == exclude_track_id:
                continue
            artist = str(row.get("artist_name") or "").lower()
            if seen_artists.get(artist, 0) >= MAX_PER_ARTIST:
                continue
            seen_artists[artist] = seen_artists.get(artist, 0) + 1
            results.append(self._row_to_dict(row, distance=raw_dist))

        return results

    @staticmethod
    def _row_to_dict(row, distance: float | None = None) -> dict:
        """Serialize a DataFrame row to a JSON-safe dict."""

        def sf(key: str, default: float = 0.0) -> float:
            val = row.get(key)
            if val is None:
                return default
            try:
                v = float(val)
                return default if (math.isnan(v) or math.isinf(v)) else round(v, 4)
            except (TypeError, ValueError):
                return default

        return {
            "track_id":        str(row["track_id"]),
            "track_name":      str(row["track_name"]),
            "artist_name":     str(row["artist_name"]),
            "valence":         sf("valence"),
            "energy":          sf("energy"),
            "danceability":    sf("danceability"),
            "acousticness":    sf("acousticness"),
            "tempo":           sf("tempo"),
            "instrumentalness": sf("instrumentalness"),
            "speechiness":     sf("speechiness"),
            "liveness":        sf("liveness"),
            "distance":        round(float(distance), 4) if distance is not None else None,
            "preview_url":     _safe(row.get("preview_url")),
            "album_image":     _safe(row.get("album_image")),
            "external_url":    _safe(row.get("external_url")),
            "popularity":      int(row.get("popularity") or 0),
            "track_genre":     str(row.get("track_genre") or ""),
        }

    def _check_fitted(self) -> None:
        if not self._fitted:
            raise RuntimeError("Call .fit() before making recommendations.")

    def _find_index(self, track_id: str) -> int | None:
        matches = self._df.index[self._df["track_id"] == track_id].tolist()
        return matches[0] if matches else None

    def _get_feature_matrix(self) -> np.ndarray:
        X = self._df[self.features].fillna(0.0).values.astype(float)
        if self.normalize:
            X = self._scaler.transform(X)
        return X * self._weight_sqrt
