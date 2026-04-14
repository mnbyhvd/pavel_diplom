"""
Download and prepare Spotify dataset from Kaggle.

Dataset: https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset
  ~114 000 tracks, all audio features including valence, energy, danceability etc.

Usage:
    # Option 1 — через kaggle CLI (нужен ~/.kaggle/kaggle.json)
    python load_kaggle_dataset.py --kaggle

    # Option 2 — если скачал вручную (CSV файл лежит рядом)
    python load_kaggle_dataset.py --file path/to/dataset.csv

    # Option 3 — альтернативный датасет
    python load_kaggle_dataset.py --file path/to/tracks.csv --alt
"""

import argparse
import sys
import subprocess
from pathlib import Path

import pandas as pd

OUTPUT_PATH = Path(__file__).parent / "data" / "tracks.csv"

# Колонки которые нам нужны (разные датасеты могут называть их по-разному)
COLUMN_ALIASES = {
    "track_id":    ["track_id", "id"],
    "track_name":  ["track_name", "name", "song_name"],
    "artist_name": ["artist_name", "artists", "artist", "artist(s)_name"],
    "popularity":  ["popularity"],
    "valence":     ["valence"],
    "energy":      ["energy"],
    "danceability":["danceability"],
    "acousticness":["acousticness"],
    "tempo":       ["tempo"],
    "loudness":    ["loudness"],
    "speechiness": ["speechiness"],
    "instrumentalness": ["instrumentalness"],
    "liveness":    ["liveness"],
    "track_genre": ["track_genre", "genre", "genres"],
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to our standard names."""
    rename_map = {}
    cols_lower = {c.lower(): c for c in df.columns}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in cols_lower:
                rename_map[cols_lower[alias.lower()]] = target
                break
    df = df.rename(columns=rename_map)
    return df


def load_and_clean(path: str) -> pd.DataFrame:
    print(f"Читаем: {path}")
    df = pd.read_csv(path)
    print(f"Строк до очистки: {len(df)}, колонки: {list(df.columns)}")

    df = normalize_columns(df)

    # Обязательные колонки
    required = ["track_id", "track_name", "artist_name", "valence", "energy"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"ОШИБКА: не найдены колонки {missing}")
        print(f"Доступные колонки: {list(df.columns)}")
        sys.exit(1)

    # Добавим пустые опциональные колонки
    for col in ["preview_url", "album_image", "external_url"]:
        if col not in df.columns:
            df[col] = None

    df = df.dropna(subset=["track_id", "track_name", "artist_name", "valence", "energy"])
    df = df.drop_duplicates(subset=["track_id"])

    # Оставим только нужные колонки
    keep = [c for c in [
        "track_id", "track_name", "artist_name", "popularity",
        "valence", "energy", "danceability", "acousticness",
        "tempo", "loudness", "speechiness", "instrumentalness",
        "liveness", "track_genre", "preview_url", "album_image", "external_url"
    ] if c in df.columns]

    df = df[keep]
    print(f"Строк после очистки: {len(df)}")
    return df


def download_kaggle(dataset: str = "maharshipandya/-spotify-tracks-dataset"):
    tmp = Path("/tmp/spotify_kaggle")
    tmp.mkdir(exist_ok=True)
    print(f"Скачиваем датасет {dataset}...")
    result = subprocess.run(
        ["kaggle", "datasets", "download", "-d", dataset, "-p", str(tmp), "--unzip"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("Ошибка kaggle CLI:")
        print(result.stderr)
        sys.exit(1)
    csvs = list(tmp.glob("*.csv"))
    if not csvs:
        print("CSV файл не найден после распаковки")
        sys.exit(1)
    return str(csvs[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kaggle", action="store_true", help="Скачать через kaggle CLI")
    parser.add_argument("--file", type=str, help="Путь к уже скачанному CSV")
    parser.add_argument("--dataset", default="maharshipandya/-spotify-tracks-dataset",
                        help="Kaggle dataset slug")
    parser.add_argument("--sample", type=int, default=0,
                        help="Взять N случайных строк (0 = все)")
    args = parser.parse_args()

    if args.kaggle:
        csv_path = download_kaggle(args.dataset)
    elif args.file:
        csv_path = args.file
    else:
        parser.print_help()
        print("\nДля ручного скачивания:")
        print("  1. Зайди на https://www.kaggle.com/datasets/maharshipandya/-spotify-tracks-dataset")
        print("  2. Скачай CSV файл")
        print("  3. Запусти: python load_kaggle_dataset.py --file путь/к/файлу.csv")
        sys.exit(0)

    df = load_and_clean(csv_path)

    if args.sample and args.sample < len(df):
        df = df.sample(n=args.sample, random_state=42)
        print(f"Выборка: {len(df)} треков")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nСохранено {len(df)} треков -> {OUTPUT_PATH}")
    print("\nПримеры:")
    print(df[["track_name", "artist_name", "valence", "energy"]].head(5).to_string(index=False))
    print("\nТеперь перезапусти сервер: python run.py")


if __name__ == "__main__":
    main()
