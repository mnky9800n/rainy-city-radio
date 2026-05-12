"""Bake the per-track intros and outros into jennifer/track_intros/.

For every tagged track in `music/`, every applicable intro/outro template
in `rcr.jennifer.intros` renders text, runs through the voicer, and
lands as an mp3 named:

    jennifer/track_intros/<track-stem>__<template-id>.mp3

Idempotent: the voicer's sha256 cache means re-running for a track whose
metadata hasn't changed is free (no API call). Adding a new template
re-bakes only the new template's lines. Re-tagging a track may change
its mood/release/etc. and therefore re-bake the templates that reference
those fields.

Usage:
    set -a; source .env; set +a
    uv run python -m rcr.tools.generate_intros           # bake everything
    uv run python -m rcr.tools.generate_intros --dry-run # plan only
    uv run python -m rcr.tools.generate_intros --only "Domeneko*"
"""

from __future__ import annotations

import argparse
import fnmatch
import logging
import shutil
import sys
from pathlib import Path

from rcr.jennifer.intros import ALL_TEMPLATES
from rcr.jennifer.voicer import Voicer, VoicerError
from rcr.music.tracks import Track, load_library

log = logging.getLogger("rcr.generate_intros")

DEFAULT_MUSIC_DIR = Path("music")
DEFAULT_INTROS_DIR = Path("jennifer/track_intros")


def _filename_for(track: Track, template_id: str) -> str:
    return f"{track.name}__{template_id}.mp3"


def _plan(tracks: list[Track]) -> list[tuple[Track, str, str]]:
    """Build the (track, template_id, text) list of work to do."""
    plan: list[tuple[Track, str, str]] = []
    for t in tracks:
        for tmpl in ALL_TEMPLATES:
            text = tmpl.render(t)
            if text is not None:
                plan.append((t, tmpl.id, text))
    return plan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
    p.add_argument("--intros-dir", type=Path, default=DEFAULT_INTROS_DIR)
    p.add_argument("--only", default=None,
                   help="Only process tracks whose filename stem matches this fnmatch glob.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the bake plan; do not call ElevenLabs.")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    tracks = load_library(args.music_dir)
    if args.only:
        tracks = [t for t in tracks if fnmatch.fnmatch(t.name, args.only)]
    if not tracks:
        log.error("no tagged tracks in %s (matching --only filter)", args.music_dir)
        return 2

    plan = _plan(tracks)
    total_chars = sum(len(text) for _, _, text in plan)
    log.info(
        "plan: %d tracks → %d intros/outros, %d chars total "
        "(counts against ElevenLabs monthly quota only on cache miss)",
        len(tracks), len(plan), total_chars,
    )

    if args.dry_run:
        for t, tmpl_id, text in plan:
            log.info("[dry-run] %s :: %s :: %r", t.name, tmpl_id, text)
        return 0

    args.intros_dir.mkdir(parents=True, exist_ok=True)

    try:
        voicer = Voicer.from_env()
    except VoicerError as e:
        log.error("%s", e)
        return 2

    baked = 0
    skipped = 0
    failed = 0
    for t, tmpl_id, text in plan:
        dest = args.intros_dir / _filename_for(t, tmpl_id)
        had_cache = voicer.cache_path(text).exists()
        try:
            src = voicer.synthesize(text)
        except VoicerError as e:
            log.error("synthesize failed for %s/%s: %s", t.name, tmpl_id, e)
            failed += 1
            continue
        if had_cache and dest.exists() and dest.stat().st_size == src.stat().st_size:
            skipped += 1
            continue
        shutil.copyfile(src, dest)
        baked += 1
        log.info("baked %s :: %s", t.name, tmpl_id)

    log.info("done: %d baked, %d up-to-date, %d failed, %d total",
             baked, skipped, failed, len(plan))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
