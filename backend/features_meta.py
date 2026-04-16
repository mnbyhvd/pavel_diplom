"""
Audio feature metadata for the recommendation engine.
Defines feature descriptions, weights, colors and display configuration.
"""

from __future__ import annotations

# All features available for recommendation, in priority order
ALL_FEATURES = [
    "valence",
    "energy",
    "danceability",
    "acousticness",
    "tempo",
    "instrumentalness",
    "speechiness",
    "liveness",
]

# Per-feature weights for weighted Euclidean distance
# Higher weight = feature contributes more to similarity calculation
# Tuned based on perceptual importance for music similarity
FEATURE_WEIGHTS: dict[str, float] = {
    "valence":          2.0,   # mood / emotional tone — strongest perceptual cue
    "energy":           2.0,   # perceived intensity — equally dominant
    "danceability":     1.5,   # rhythm feel — highly noticeable
    "acousticness":     1.5,   # acoustic vs electronic — immediately audible
    "tempo":            1.0,   # BPM — noticeable but correlated with energy
    "instrumentalness": 1.2,   # vocal vs instrumental — clear perceptual difference
    "speechiness":      0.4,   # spoken-word detector — adds noise, reduce weight
    "liveness":         0.3,   # live recording artifact — irrelevant to similarity
}

# Display metadata for each feature
FEATURE_META: dict[str, dict] = {
    "valence": {
        "label": "Valence",
        "ru_label": "Позитивность",
        "description": (
            "Музыкальная «позитивность» трека. Высокое значение — радостный, весёлый, эйфоричный; "
            "низкое — грустный, депрессивный, меланхоличный."
        ),
        "color": "#a78bfa",
        "unit": "0–1",
        "icon": "☀️",
        "range_hint": "грустный → радостный",
    },
    "energy": {
        "label": "Energy",
        "ru_label": "Энергия",
        "description": (
            "Воспринимаемая интенсивность и активность трека. "
            "Высокое значение у быстрых, громких, плотных треков (death metal ~0.9); "
            "низкое у тихих, медитативных (Бах ~0.1)."
        ),
        "color": "#f87171",
        "unit": "0–1",
        "icon": "⚡",
        "range_hint": "тихий → интенсивный",
    },
    "danceability": {
        "label": "Danceability",
        "ru_label": "Танцевальность",
        "description": (
            "Насколько удобен трек для танцев: стабильность темпа, сила ударных, ритмический рисунок. "
            "0 = совсем не танцевальный; 1 = идеален для дискотеки."
        ),
        "color": "#38bdf8",
        "unit": "0–1",
        "icon": "💃",
        "range_hint": "не для танцев → танцпол",
    },
    "acousticness": {
        "label": "Acousticness",
        "ru_label": "Акустичность",
        "description": (
            "Вероятность того, что трек акустический (живые инструменты без электронного усиления). "
            "1.0 = высокая уверенность в акустичности."
        ),
        "color": "#34d399",
        "unit": "0–1",
        "icon": "🎸",
        "range_hint": "электронный → акустический",
    },
    "tempo": {
        "label": "Tempo",
        "ru_label": "Темп",
        "description": (
            "Темп трека в ударах в минуту (BPM). "
            "Медленные баллады ~60–80 BPM, средние — 90–120 BPM, быстрые танцевальные — 120–180 BPM."
        ),
        "color": "#fb923c",
        "unit": "BPM",
        "icon": "🥁",
        "range_hint": "медленно → быстро",
    },
    "instrumentalness": {
        "label": "Instrumentalness",
        "ru_label": "Инструментальность",
        "description": (
            "Предсказывает отсутствие вокала в треке. "
            "Значения > 0.5 указывают на инструментальный трек; ближе к 1.0 — высокая уверенность."
        ),
        "color": "#e879f9",
        "unit": "0–1",
        "icon": "🎻",
        "range_hint": "с вокалом → инструментальный",
    },
    "speechiness": {
        "label": "Speechiness",
        "ru_label": "Речевитость",
        "description": (
            "Наличие разговорной речи в треке. "
            "> 0.66 = речевой (подкаст, аудиокнига); 0.33–0.66 = микс (рэп); "
            "< 0.33 = музыка без выраженной речи."
        ),
        "color": "#facc15",
        "unit": "0–1",
        "icon": "🎙️",
        "range_hint": "музыка → речь",
    },
    "liveness": {
        "label": "Liveness",
        "ru_label": "Живость",
        "description": (
            "Вероятность живого выступления: шум аудитории, реверберация зала. "
            "> 0.8 означает высокую вероятность live-записи."
        ),
        "color": "#4ade80",
        "unit": "0–1",
        "icon": "🎤",
        "range_hint": "студийная → живая",
    },
}
