"""
Music recommendation engine — genre-stratified weighted KNN.

Algorithm (v3):
  1. Z-score normalize all features with StandardScaler
  2. Apply per-feature weights (sqrt trick for weighted Euclidean)
  3. Build TWO KNN indices (ball_tree, euclidean):
       - _knn_genre: one index per genre (fast same-genre lookup)
       - _knn_global: full dataset index (cross-genre fallback)
  4. Recommendation pipeline:
       a. Fetch SAME_GENRE_RATIO of candidates from the query track's genre index
       b. Fill remainder from global index
       c. Deduplicate by track_id (Kaggle dataset has duplicate track_ids across genres)
       d. Artist diversity cap (MAX_PER_ARTIST per artist)
  5. Popularity blend (small α) as a tiebreaker only

Why Euclidean (not cosine) on z-scored data:
  After StandardScaler, each feature has mean=0 and std=1.
  Euclidean distance in this space equals the L2 norm of differences,
  which has a clear geometric interpretation: "how far apart are the tracks
  in the standardized feature space". Cosine measures angle from origin,
  which is misleading when vectors have both positive and negative components
  (as z-scored features do).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from features_meta import ALL_FEATURES, FEATURE_WEIGHTS

# --- Tuning constants -------------------------------------------------------

# Fraction of recommendations to pull from same-genre index (rest from global)
SAME_GENRE_RATIO = 0.70

# Popularity as a tiebreaker only — very small alpha
POPULARITY_ALPHA = 0.04

# Artist diversity
MAX_PER_ARTIST = 2

# Extra candidates multiplier before final filtering
CANDIDATE_MULTIPLIER = 5

# Minimum genre pool size to use genre-stratified search
MIN_GENRE_POOL = 50


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
    """Genre-stratified weighted KNN recommender."""

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
        self._knn_global = NearestNeighbors(metric="euclidean", algorithm="ball_tree")
        # genre -> (NearestNeighbors, global_indices)
        self._knn_genre: dict[str, tuple[NearestNeighbors, np.ndarray]] = {}

        self._df: pd.DataFrame | None = None
        self._weight_sqrt: np.ndarray | None = None
        self._X_weighted: np.ndarray | None = None   # cached weighted matrix
        self._pop_norm: np.ndarray | None = None
        self._fitted = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(self, df: pd.DataFrame) -> "MusicRecommender":
        """Fit the model on a track DataFrame."""
        df = df.copy()
        self._df = df.reset_index(drop=True)

        self.features = [f for f in self.features if f in self._df.columns]

        w = np.array([self.weights.get(f, 1.0) for f in self.features])
        self._weight_sqrt = np.sqrt(w)

        X = self._df[self.features].fillna(0.0).values.astype(float)
        if self.normalize:
            X = self._scaler.fit_transform(X)

        self._X_weighted = X * self._weight_sqrt

        # Global KNN
        self._knn_global.fit(self._X_weighted)

        # Per-genre KNN indices
        if "track_genre" in self._df.columns:
            for genre, group in self._df.groupby("track_genre"):
                if len(group) < MIN_GENRE_POOL:
                    continue
                g_indices = group.index.to_numpy()
                X_g = self._X_weighted[g_indices]
                knn_g = NearestNeighbors(metric="euclidean", algorithm="ball_tree")
                knn_g.fit(X_g)
                self._knn_genre[str(genre)] = (knn_g, g_indices)

        # Normalized popularity for tiebreaking
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

        query_vec = self._X_weighted[idx : idx + 1]
        query_genre = str(self._df.iloc[idx].get("track_genre") or "")

        want_genre = max(1, round(n * SAME_GENRE_RATIO))
        want_global = n - want_genre

        # ── Collect candidates ─────────────────────────────────────
        candidates: dict[int, float] = {}   # global_idx → distance

        # 1. Same-genre candidates
        if query_genre in self._knn_genre:
            knn_g, g_indices = self._knn_genre[query_genre]
            k_g = min(want_genre * CANDIDATE_MULTIPLIER, len(g_indices))
            dists, local_idxs = knn_g.kneighbors(query_vec, n_neighbors=k_g)
            for d, li in zip(dists[0], local_idxs[0]):
                gi = int(g_indices[li])
                if gi != idx:
                    candidates[gi] = float(d)

        # 2. Global candidates to fill remainder / fallback
        k_glob = min((want_global + want_genre) * CANDIDATE_MULTIPLIER, len(self._df))
        dists_g, idxs_g = self._knn_global.kneighbors(query_vec, n_neighbors=k_glob)
        for d, gi in zip(dists_g[0], idxs_g[0]):
            gi = int(gi)
            if gi != idx and gi not in candidates:
                candidates[gi] = float(d)

        # ── Post-process ──────────────────────────────────────────
        return self._postprocess(candidates, n=n, exclude_track_id=track_id)

    def recommend_by_mood(
        self, valence: float, energy: float, n: int | None = None
    ) -> list[dict]:
        """Return tracks closest to a (valence, energy) mood point."""
        self._check_fitted()
        n = n or self.n_neighbors

        if self.normalize:
            full = np.array(self._scaler.mean_, copy=True).reshape(1, -1)
            if "valence" in self.features:
                full[0, self.features.index("valence")] = valence
            if "energy" in self.features:
                full[0, self.features.index("energy")] = energy
            point = self._scaler.transform(full) * self._weight_sqrt
        else:
            full = np.zeros((1, len(self.features)))
            if "valence" in self.features:
                full[0, self.features.index("valence")] = valence
            if "energy" in self.features:
                full[0, self.features.index("energy")] = energy
            point = full * self._weight_sqrt

        k = min(n * CANDIDATE_MULTIPLIER, len(self._df))
        dists, idxs = self._knn_global.kneighbors(point, n_neighbors=k)

        candidates = {int(gi): float(d) for d, gi in zip(dists[0], idxs[0])}
        return self._postprocess(candidates, n=n, exclude_track_id=None)

    def explain_recommendations(self, track_id: str, n: int = 6) -> dict:
        """Return detailed per-feature explanation for recommendations."""
        self._check_fitted()
        idx = self._find_index(track_id)
        if idx is None:
            return {}

        recs = self.recommend(track_id, n=n)

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
                    "raw_a":      round(raw_a, 4),
                    "raw_b":      round(raw_b, 4),
                    "raw_diff":   round(raw_diff, 4),
                    "z_diff":     round(z_diff, 4),
                    "sq_contrib": round(sq, 6),
                    "pct":        0.0,
                }
            for f in contributions:
                contributions[f]["pct"] = (
                    round(contributions[f]["sq_contrib"] / total_sq * 100, 1)
                    if total_sq > 0 else 0.0
                )
            rec_details.append({
                "track":                 rec,
                "total_distance":        rec.get("distance"),
                "feature_contributions": contributions,
            })

        return {
            "query": self._row_to_dict(query_row),
            "algorithm": {
                "features":    self.features,
                "weights":     {f: round(self.weights.get(f, 1.0), 3) for f in self.features},
                "normalize":   self.normalize,
                "metric":      "weighted_euclidean (z-scored) + genre-stratified",
                "n_neighbors": n,
                "formula":     "d = √( Σ wᵢ · (zᵢ(a) − zᵢ(b))² )",
            },
            "feature_stats":   feature_stats,
            "query_features":  query_features,
            "recommendations": rec_details,
        }

    def all_tracks(self) -> list[dict]:
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

    def _postprocess(
        self,
        candidates: dict[int, float],
        n: int,
        exclude_track_id: str | None,
    ) -> list[dict]:
        """
        Re-rank candidates:
          1. Popularity tiebreaker (very small α)
          2. Deduplicate by Spotify track_id (same song, different genre row)
          3. Artist diversity cap
        """
        # Score = distance − α*popularity  (lower is better)
        scored = []
        for gi, dist in candidates.items():
            pop = float(self._pop_norm[gi])
            score = (1.0 - POPULARITY_ALPHA) * dist - POPULARITY_ALPHA * pop
            scored.append((score, dist, gi))
        scored.sort(key=lambda x: x[0])

        results: list[dict] = []
        seen_track_ids: set[str] = set()
        seen_artists: dict[str, int] = {}

        for _, dist, gi in scored:
            if len(results) >= n:
                break
            row = self._df.iloc[gi]
            tid = str(row["track_id"])
            if exclude_track_id and tid == exclude_track_id:
                continue
            # Deduplicate same Spotify track_id (multiple genre rows for same song)
            if tid in seen_track_ids:
                continue
            seen_track_ids.add(tid)
            # Artist diversity
            artist = str(row.get("artist_name") or "").lower()
            if seen_artists.get(artist, 0) >= MAX_PER_ARTIST:
                continue
            seen_artists[artist] = seen_artists.get(artist, 0) + 1
            results.append(self._row_to_dict(row, distance=dist))

        return results

    @staticmethod
    def _row_to_dict(row, distance: float | None = None) -> dict:
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
            "track_id":         str(row["track_id"]),
            "track_name":       str(row["track_name"]),
            "artist_name":      str(row["artist_name"]),
            "valence":          sf("valence"),
            "energy":           sf("energy"),
            "danceability":     sf("danceability"),
            "acousticness":     sf("acousticness"),
            "tempo":            sf("tempo"),
            "instrumentalness": sf("instrumentalness"),
            "speechiness":      sf("speechiness"),
            "liveness":         sf("liveness"),
            "distance":         round(float(distance), 4) if distance is not None else None,
            "preview_url":      _safe(row.get("preview_url")),
            "album_image":      _safe(row.get("album_image")),
            "external_url":     _safe(row.get("external_url")),
            "popularity":       int(row.get("popularity") or 0),
            "track_genre":      str(row.get("track_genre") or ""),
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
