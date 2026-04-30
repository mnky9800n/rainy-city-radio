"""Track sidecar schema + IO.

Each mp3 in `music/` has a JSON sidecar at the same path with a `.json` suffix
swapped in (e.g. `Bridge Street Run.mp3` ↔ `Bridge Street Run.json`).
The sidecar holds the offline-computed metadata the selector needs:

    bpm                 librosa.beat.beat_track
    energy              1..5, NIM-tagged
    mood                free-form descriptive tags from a fixed vocab
    duration            seconds (librosa)
    onset_strength      mean onset envelope (librosa) — fed to NIM as a
                        non-title-based intensity hint
    fictional_artist    NIM-imagined artist name for Jennifer to attribute
    ingest_version      schema version, lets us re-ingest selectively if the
                        algorithm changes meaningfully
    ingested_at         ISO timestamp; informational

Tracks without a sidecar are excluded from selection — see selector.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

INGEST_VERSION = 1

# Fixed mood vocabulary fed to NIM. Free-form would drift; a vocab keeps
# Jennifer's commentary and the arc selector predictable.
MOOD_VOCAB = (
    "chill", "dreamy", "rainy", "cinematic", "uplifting", "melancholy",
    "groovy", "driving", "peak", "intense", "playful", "menacing",
)


@dataclass(frozen=True)
class Track:
    path: Path
    bpm: float
    energy: int               # 1..5
    mood: tuple[str, ...]
    duration: float
    onset_strength: float
    fictional_artist: str
    ingest_version: int = INGEST_VERSION
    ingested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def name(self) -> str:
        return self.path.stem


def sidecar_path(mp3: Path) -> Path:
    return mp3.with_suffix(".json")


def save(track: Track) -> None:
    data = asdict(track)
    data["path"] = str(track.path.name)  # store as bare filename, not absolute
    data["mood"] = list(track.mood)
    sidecar_path(track.path).write_text(json.dumps(data, indent=2) + "\n")


def load(mp3: Path) -> Track | None:
    """Load the sidecar for an mp3, or None if missing/invalid/old version."""
    sp = sidecar_path(mp3)
    if not sp.exists():
        return None
    try:
        data = json.loads(sp.read_text())
    except json.JSONDecodeError:
        return None
    if data.get("ingest_version") != INGEST_VERSION:
        return None
    try:
        return Track(
            path=mp3,
            bpm=float(data["bpm"]),
            energy=int(data["energy"]),
            mood=tuple(data["mood"]),
            duration=float(data["duration"]),
            onset_strength=float(data["onset_strength"]),
            fictional_artist=str(data["fictional_artist"]),
            ingest_version=int(data["ingest_version"]),
            ingested_at=str(data["ingested_at"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def load_library(music_dir: Path) -> list[Track]:
    """Return all tagged tracks in music_dir. Untagged tracks are silently skipped."""
    out: list[Track] = []
    for mp3 in sorted(music_dir.glob("*.mp3")):
        t = load(mp3)
        if t is not None:
            out.append(t)
    return out


def untagged(music_dir: Path) -> list[Path]:
    return [mp3 for mp3 in sorted(music_dir.glob("*.mp3")) if load(mp3) is None]
