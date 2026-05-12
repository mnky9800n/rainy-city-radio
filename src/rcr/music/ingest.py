"""Combine librosa analysis + NIM tagging into a track sidecar.

The streaming service never calls into this — ingest runs offline, either
one-shot per file (`tools/ingest_track.py`) or via the watcher daemon
(`tools/ingest_watch.py`). It's CPU-heavy (librosa) and network-bound (NIM)
and absolutely must not happen on the streaming path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rcr.music.analyze import analyze
from rcr.music.tracks import MOOD_VOCAB, Track, load_cc_metadata, save
from rcr.nim import NimClient, NimError

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a music librarian for a 24/7 internet radio station called 99X — "
    "rainy-city radio, broadcasting moody electro and rainy-night vibes. "
    "You classify instrumental tracks based on filename, tempo, and intensity."
)

USER_PROMPT_TEMPLATE = """\
Classify this track:
title: {title}
bpm: {bpm:.1f}
onset_strength: {onset:.2f}    (higher = more percussive)
duration_sec: {duration:.0f}

Mood vocabulary (pick 3-5, lowercase, only from this list):
{vocab}

Energy scale:
1 = ambient, sleepy
2 = chill, low-key
3 = mid-tempo, steady groove
4 = energetic, danceable
5 = peak, intense, driving

Invent a fictional artist name in the rainy-city aesthetic — gritty noir,
neon, kung-fu nostalgia, anti-Followers-of-Baal vibe. Plausible band/producer
names only, nothing campy or self-referential.

Return JSON with exactly these keys:
- energy: integer 1-5
- mood: array of 3-5 strings from the vocab
- fictional_artist: string"""


def ingest(mp3: Path, nim: NimClient | None = None) -> Track:
    if not mp3.exists():
        raise FileNotFoundError(mp3)
    if nim is None:
        nim = NimClient.from_env()

    # Carry forward any CC-attribution fields that were written into the
    # sidecar at download time. We do this *before* save() overwrites it.
    cc = load_cc_metadata(mp3)

    log.info("analyzing %s", mp3.name)
    a = analyze(mp3)
    log.info("  bpm=%.1f duration=%.0fs onset=%.2f", a.bpm, a.duration, a.onset_strength)

    # For CC tracks we already have a real artist on record — don't store
    # NIM's invention. We still call _tag() because we want mood + energy.
    use_fictional_artist = cc.get("real_artist") is None

    log.info("tagging via NIM")
    tags = _tag(
        nim, mp3.stem, a.bpm, a.onset_strength, a.duration,
        want_fictional_artist=use_fictional_artist,
    )

    track = Track(
        path=mp3,
        bpm=a.bpm,
        energy=tags["energy"],
        mood=tuple(tags["mood"]),
        duration=a.duration,
        onset_strength=a.onset_strength,
        fictional_artist=tags.get("fictional_artist") if use_fictional_artist else None,
        real_title=cc.get("real_title"),
        real_artist=cc.get("real_artist"),
        release=cc.get("release"),
        source_url=cc.get("source_url"),
        license=cc.get("license"),
        attribution=cc.get("attribution"),
    )
    save(track)
    log.info("  energy=%d mood=%s artist=%r",
             track.energy, ",".join(track.mood), track.display_artist)
    return track


def _tag(
    nim: NimClient,
    title: str,
    bpm: float,
    onset: float,
    duration: float,
    *,
    want_fictional_artist: bool = True,
) -> dict:
    user = USER_PROMPT_TEMPLATE.format(
        title=title,
        bpm=bpm,
        onset=onset,
        duration=duration,
        vocab=", ".join(MOOD_VOCAB),
    )
    raw = nim.chat_json(SYSTEM_PROMPT, user, max_tokens=200, temperature=0.5)
    return _validate(raw, want_fictional_artist=want_fictional_artist)


def _validate(raw: dict, *, want_fictional_artist: bool = True) -> dict:
    try:
        energy = int(raw["energy"])
        mood_in = raw["mood"]
    except (KeyError, ValueError, TypeError) as e:
        raise NimError(f"NIM tag response missing/invalid keys: {e}; raw={raw!r}") from e

    if not 1 <= energy <= 5:
        raise NimError(f"energy out of range: {energy}")
    if not isinstance(mood_in, list):
        raise NimError(f"mood is not a list: {mood_in!r}")

    mood: list[str] = []
    for tag in mood_in:
        t = str(tag).strip().lower()
        if t in MOOD_VOCAB and t not in mood:
            mood.append(t)
    if not 1 <= len(mood) <= 6:
        # Tolerate slight over/under count, but not zero.
        raise NimError(f"mood ended up empty after vocab filter: {mood_in!r}")

    out: dict = {"energy": energy, "mood": mood}

    if want_fictional_artist:
        try:
            artist = str(raw["fictional_artist"]).strip()
        except (KeyError, ValueError, TypeError) as e:
            raise NimError(f"NIM tag response missing fictional_artist: {e}; raw={raw!r}") from e
        if not artist:
            raise NimError("fictional_artist is empty")
        out["fictional_artist"] = artist

    return out
