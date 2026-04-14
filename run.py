"""
Start the Music Recommender web application.

    python run.py
    python run.py --port 8080 --reload
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Run Music Recommender")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args()

    print(f"\n  Music Recommender")
    print(f"  URL: http://{args.host}:{args.port}")
    print(f"  API docs: http://{args.host}:{args.port}/docs\n")

    uvicorn.run(
        "app:app",
        app_dir=str(Path(__file__).parent / "backend"),
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
