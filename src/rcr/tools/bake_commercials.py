"""Bake the curated commercial pool into jennifer/commercials/<id>.mp3.

For each `Commercial` in `rcr.jennifer.commercials.COMMERCIALS`:
    1. Call Voicer.synthesize(text, voice_id=commercial.voice_id) — multi-voice
       support is exactly why the per-call voice_id override exists.
    2. Copy the resulting cache mp3 into `jennifer/commercials/<id>.mp3`.

Voice-only for now. M4.5 step 5 ("production mixing tool") layers a music
bed under the voice; until that exists the commercials play as voice
over the streamer's ambient rain bed, which still sounds passable.

Idempotent like the other bake tools: re-runs hit the voicer's sha256
cache (no API call, no ElevenLabs character cost). New entries in
commercials.py bake on the next run; existing-and-unchanged ones are
copy-only.

Usage:
    set -a; source .env; set +a
    uv run python -m rcr.tools.bake_commercials
    uv run python -m rcr.tools.bake_commercials --dry-run
    uv run python -m rcr.tools.bake_commercials --only business_001
"""

from __future__ import annotations

import argparse
import fnmatch
import logging
import shutil
import sys
from pathlib import Path

from rcr.jennifer.commercials import COMMERCIALS, Commercial
from rcr.jennifer.voicer import Voicer, VoicerError

log = logging.getLogger("rcr.bake_commercials")

DEFAULT_COMMERCIALS_DIR = Path("jennifer/commercials")


def _select(ids_filter: list[str] | None) -> list[Commercial]:
    if not ids_filter:
        return list(COMMERCIALS)
    selected: list[Commercial] = []
    for c in COMMERCIALS:
        if any(fnmatch.fnmatch(c.id, pat) for pat in ids_filter):
            selected.append(c)
    return selected


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commercials-dir", type=Path, default=DEFAULT_COMMERCIALS_DIR)
    p.add_argument("--only", nargs="+", default=None,
                   help="One or more glob patterns matching commercial ids "
                        "(e.g. 'business_*', 'psa_001').")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be baked; do not call ElevenLabs.")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    commercials = _select(args.only)
    if not commercials:
        log.error("no commercials matched --only %r", args.only)
        return 2

    total_chars = sum(len(c.text) for c in commercials)
    log.info(
        "plan: %d commercials, %d chars total (counts against ElevenLabs "
        "monthly quota only on cache miss)",
        len(commercials), total_chars,
    )

    if args.dry_run:
        for c in commercials:
            log.info("[dry-run] %s (%s, voice=%s): %r",
                     c.id, c.category, c.voice_id, c.text[:80])
        return 0

    args.commercials_dir.mkdir(parents=True, exist_ok=True)

    try:
        voicer = Voicer.from_env()
    except VoicerError as e:
        log.error("%s", e)
        return 2

    baked = 0
    cached_hits = 0
    failed = 0
    for c in commercials:
        dest = args.commercials_dir / f"{c.id}.mp3"
        had_cache_before = voicer.cache_path(c.text, voice_id=c.voice_id).exists()
        try:
            src = voicer.synthesize(c.text, voice_id=c.voice_id)
        except VoicerError as e:
            log.error("synthesize failed for %s: %s", c.id, e)
            failed += 1
            continue
        if had_cache_before:
            cached_hits += 1
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            continue
        shutil.copyfile(src, dest)
        baked += 1
        log.info("baked %s (%s) -> %s", c.id, c.character, dest.name)

    log.info("done: %d copied, %d already-cached, %d failed, total=%d",
             baked, cached_hits, failed, len(commercials))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
