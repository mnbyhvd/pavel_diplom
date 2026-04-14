"""
Music recommendation engine — content-based filtering with weighted KNN.

Algorithm:
  1. Represent each track as a point in 8D audio feature space
  2. Z-score normalize all features with StandardScaler
  3. Apply per-feature weights by multiplying normalized values by sqrt(weight)
     → turns standard Euclidean into weighted Euclidean:
       d = sqrt( Σ w_i · (z_i(a) − z_i(b))² )
  4. Find nearest neighbors with sklearn NearestNeighbors (ball_tree, euclidean)

Reference: https://habr.com/ru/publications/585182/
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from features_meta import ALL_FEATURES, FEATURE_WEIGHTS


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
    """Content-based music recommender using weighted Euclidean distance in audio feature space."""

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
        self._knn = NearestNeighbors(metric="euclidean", algorithm="ball_tree")
        self._df: pd.DataFrame | None = None
        self._weight_sqrt: np.ndarray | None = None
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

        # Build sqrt(weight) vector for weighted Euclidean
        w = np.array([self.weights.get(f, 1.0) for f in self.features])
        self._weight_sqrt = np.sqrt(w)

        X = self._df[self.features].fillna(0.0).values.astype(float)

        if self.normalize:
            X = self._scaler.fit_transform(X)

        # Pre-multiply by sqrt(weights) so standard Euclidean = weighted Euclidean
        X_weighted = X * self._weight_sqrt

        self._knn.fit(X_weighted)
        self._fitted = True
        return self

    def recommend(self, track_id: str, n: int | None = None) -> list[dict]:
        """Return up to n recommendations for a given track_id."""
        self._check_fitted()
        n = n or self.n_neighbors
        idx = self._find_index(track_id)
        if idx is None:
            return []

        X = self._get_feature_matrix()
        distances, indices = self._knn.kneighbors(X[idx : idx + 1], n_neighbors=n + 1)

        results = []
        for dist, i in zip(distances[0], indices[0]):
            if int(i) == idx:
                continue
            results.append(self._row_to_dict(self._df.iloc[i], distance=float(dist)))
            if len(results) >= n:
                break
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
            # Start with dataset means so z-score of unknown dims will be 0
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

        distances, indices = self._knn.kneighbors(point_transformed, n_neighbors=n)
        return [
            self._row_to_dict(self._df.iloc[i], distance=float(dist))
            for dist, i in zip(distances[0], indices[0])
        ]

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
                    "pct": 0.0,  # filled below
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
                "metric":      "weighted_euclidean",
                "n_neighbors": n,
                "formula":     "d = √( Σ wᵢ · (zᵢ(a) − zᵢ(b))² )",
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
