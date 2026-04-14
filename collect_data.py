"""
Standalone script to collect tracks from Spotify and save to data/tracks.csv.

Usage:
    python collect_data.py
    python collect_data.py --genres pop rock --limit 100
    python collect_data.py --all-genres --limit 50

Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

sys.path.insert(0, str(Path(__file__).parent / "backend"))
from spotify_client import SpotifyCollector

DATA_PATH = Path(__file__).parent / "data" / "tracks.csv"


def main():
    parser = argparse.ArgumentParser(description="Collect Spotify tracks dataset")
    parser.add_argument("--genres", nargs="+", help="Specific genres (default: all)")
    parser.add_argument("--limit", type=int, default=100, help="Tracks per genre (default: 100)")
    parser.add_argument("--all-genres", action="store_true", help="Use all available genres")
    args = parser.parse_args()

    client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("ERROR: Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env file")
        sys.exit(1)

    collector = SpotifyCollector(client_id, client_secret)

    genres = args.genres
    if args.all_genres or not genres:
        genres = collector.get_available_genres()
        print(f"Using all {len(genres)} available genres")
    else:
        print(f"Using genres: {genres}")

    print(f"Collecting {args.limit} tracks per genre…")
    df = collector.collect_tracks(
        genres=genres,
        tracks_per_genre=args.limit,
        output_path=DATA_PATH,
    )
    print(f"\nDone! Collected {len(df)} unique tracks -> {DATA_PATH}")
    print(df[["track_name", "artist_name", "valence", "energy"]].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
