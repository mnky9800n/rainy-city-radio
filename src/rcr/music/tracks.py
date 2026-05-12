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
    fictional_artist    NIM-imagined artist name (Suno tracks); None for CC
                        tracks where a real artist is known
    ingest_version      schema version, lets us re-ingest selectively if the
                        algorithm changes meaningfully
    ingested_at         ISO timestamp; informational

Optional Creative-Commons metadata (set for tracks pulled from external CC
sources; None for in-house/Suno tracks):

    real_title          on-record title from the source
    real_artist         on-record artist
    release             album / EP / netlabel release name
    source_url          page where the track lives (for verification + DJ patter)
    license             exact CC license string (e.g. "CC BY-NC-ND 3.0")
    attribution         the ready-to-display credit Jennifer / the channel
                        description can use verbatim

Tracks without a sidecar (or with a sidecar missing the required audio-
analysis fields) are excluded from selection — see selector.py.
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
    # Optional: in-universe NIM-imagined artist (Suno tracks). None for tracks
    # with a known real_artist from a CC source.
    fictional_artist: str | None = None
    # Optional Creative-Commons attribution metadata. See module docstring.
    real_title: str | None = None
    real_artist: str | None = None
    release: str | None = None
    source_url: str | None = None
    license: str | None = None
    attribution: str | None = None
    ingest_version: int = INGEST_VERSION
    ingested_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def display_artist(self) -> str | None:
        """The name Jennifer should use on-air: real_artist for CC tracks,
        fictional_artist for Suno tracks, or None if neither is set."""
        return self.real_artist or self.fictional_artist


def sidecar_path(mp3: Path) -> Path:
    return mp3.with_suffix(".json")


def save(track: Track) -> None:
    data = asdict(track)
    data["path"] = str(track.path.name)  # store as bare filename, not absolute
    data["mood"] = list(track.mood)
    sidecar_path(track.path).write_text(json.dumps(data, indent=2) + "\n")


def _opt_str(data: dict, key: str) -> str | None:
    v = data.get(key)
    if v is None or v == "":
        return None
    return str(v)


def load(mp3: Path) -> Track | None:
    """Load the sidecar for an mp3, or None if missing/invalid/old version.

    Returns None for sidecars that are missing the required audio-analysis
    fields — that's the "downloaded but not yet ingested" state for CC tracks.
    Running `python -m rcr.tools.ingest_track --all` fills those in and the
    track becomes selectable.
    """
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
            fictional_artist=_opt_str(data, "fictional_artist"),
            real_title=_opt_str(data, "real_title"),
            real_artist=_opt_str(data, "real_artist"),
            release=_opt_str(data, "release"),
            source_url=_opt_str(data, "source_url"),
            license=_opt_str(data, "license"),
            attribution=_opt_str(data, "attribution"),
            ingest_version=int(data["ingest_version"]),
            ingested_at=str(data["ingested_at"]),
        )
    except (KeyError, ValueError, TypeError):
        return None


def load_cc_metadata(mp3: Path) -> dict[str, str | None]:
    """Read just the CC-attribution fields from a sidecar (if any), without
    requiring the audio-analysis fields. Used by ingest to carry CC metadata
    forward across re-tagging."""
    sp = sidecar_path(mp3)
    if not sp.exists():
        return {}
    try:
        data = json.loads(sp.read_text())
    except json.JSONDecodeError:
        return {}
    return {
        key: _opt_str(data, key)
        for key in ("real_title", "real_artist", "release",
                    "source_url", "license", "attribution")
    }


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
