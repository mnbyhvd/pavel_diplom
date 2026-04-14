"""
SQLAlchemy model for the tracks table.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from database import Base


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (UniqueConstraint("track_id", name="uq_tracks_track_id"),)

    id           = Column(BigInteger, primary_key=True, autoincrement=True)
    track_id     = Column(String(64), nullable=False, index=True)
    track_name   = Column(Text, nullable=False)
    artist_name  = Column(Text, nullable=False)
    popularity   = Column(Integer, default=0)

    # ── Audio features ──────────────────────────────────────────
    valence          = Column(Float)
    energy           = Column(Float)
    danceability     = Column(Float)
    acousticness     = Column(Float)
    tempo            = Column(Float)
    instrumentalness = Column(Float)
    speechiness      = Column(Float)
    liveness         = Column(Float)

    # ── Metadata ────────────────────────────────────────────────
    track_genre  = Column(Text)
    preview_url  = Column(Text)
    album_image  = Column(Text)
    external_url = Column(Text)
