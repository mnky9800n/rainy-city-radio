"""Bake the static Jennifer spot pool into jennifer/spots/<id>.mp3.

Run this once after editing src/rcr/jennifer/spots.py, or after a fresh
checkout on a new host. Idempotent: the voicer cache is content-addressed, so
unchanged spots cost zero ElevenLabs characters on re-runs.

Usage:
    set -a; source .env; set +a
    python -m rcr.tools.generate_spots                  # bake everything
    python -m rcr.tools.generate_spots --dry-run        # show plan, no API
    python -m rcr.tools.generate_spots --ids station_01 # bake just one
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from rcr.jennifer.spots import SPOTS, Spot, by_id
from rcr.jennifer.voicer import Voicer, VoicerError

log = logging.getLogger("rcr.generate_spots")

DEFAULT_SPOTS_DIR = Path("jennifer/spots")


def _select(ids: list[str] | None) -> list[Spot]:
    if not ids:
        return list(SPOTS)
    return [by_id(i) for i in ids]


def _is_fresh(dest: Path, cache_src: Path) -> bool:
    """True if `dest` already contains the same bytes as `cache_src`."""
    if not dest.exists() or dest.stat().st_size != cache_src.stat().st_size:
        return False
    return dest.read_bytes() == cache_src.read_bytes()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spots-dir", type=Path, default=DEFAULT_SPOTS_DIR)
    p.add_argument("--ids", nargs="+", default=None,
                   help="Only bake these spot ids (default: all).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be done; do not call ElevenLabs.")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    try:
        spots = _select(args.ids)
    except KeyError as e:
        log.error("unknown spot id: %s", e.args[0])
        return 2

    total_chars = sum(len(s.text) for s in spots)
    log.info("plan: %d spots, %d chars total (counts against ElevenLabs "
             "monthly quota only on cache miss)", len(spots), total_chars)

    args.spots_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        for s in spots:
            log.info("[dry-run] %s (%s, %d chars): %r",
                     s.id, s.category, len(s.text), s.text[:60])
        return 0

    try:
        voicer = Voicer.from_env()
    except VoicerError as e:
        log.error("%s", e)
        return 2

    baked = 0
    cached_hits = 0
    for s in spots:
        dest = args.spots_dir / f"{s.id}.mp3"
        cache_path = voicer.cache_path(s.text)
        had_cache_before = cache_path.exists()
        try:
            src = voicer.synthesize(s.text)
        except VoicerError as e:
            log.error("synthesize failed for %s: %s", s.id, e)
            continue
        if had_cache_before:
            cached_hits += 1
        if _is_fresh(dest, src):
            log.debug("up-to-date: %s", dest)
            continue
        shutil.copyfile(src, dest)
        baked += 1
        log.info("baked %s -> %s", s.id, dest)

    log.info("done: %d copied, %d already-cached, total spots=%d",
             baked, cached_hits, len(spots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
