"""Build placeholder commercial mp3s without spending ElevenLabs quota.

Smoke-testing the M4.5 talk-break path needs *some* mp3 in
`jennifer/commercials/<id>.mp3` per commercial.id, but the real bake
costs EL characters we don't have right now. This tool grabs existing
voice mp3s from `jennifer/spots/` (already baked), pairs each with a
bed from `jennifer/commercial_beds/`, and produces fake commercial
files for the first N entries in the catalog.

The audio won't say what the real commercial would — it's whatever the
spots happen to say — but the *plumbing* (talk-break duration probe,
music-FIFO pause, commercial playback alone) is fully exercised.

Delete the output dir contents and re-run `produce_commercials` once
real voice quota is available.

Usage:
    uv run python -m rcr.tools.fake_commercials_for_testing
    uv run python -m rcr.tools.fake_commercials_for_testing --count 5
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from rcr.jennifer.commercials import COMMERCIALS
from rcr.tools.produce_commercials import (
    load_beds,
    mix_voice_and_bed,
    pick_bed,
)

log = logging.getLogger("rcr.fake_commercials")

DEFAULT_SPOTS_DIR = Path("jennifer/spots")
DEFAULT_BEDS_DIR = Path("jennifer/commercial_beds")
DEFAULT_OUTPUT_DIR = Path("jennifer/commercials")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spots-dir", type=Path, default=DEFAULT_SPOTS_DIR)
    p.add_argument("--beds-dir", type=Path, default=DEFAULT_BEDS_DIR)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--count", type=int, default=None,
                   help="How many fake commercials to make (default: as many "
                        "as we have spot mp3s).")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    spots = sorted(args.spots_dir.glob("*.mp3"))
    if not spots:
        log.error("no spot mp3s in %s — bake spots first", args.spots_dir)
        return 2

    beds = load_beds(args.beds_dir)
    if not beds:
        log.error("no beds in %s — run download_beds first", args.beds_dir)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)

    n_target = args.count if args.count is not None else len(spots)
    n_to_make = min(n_target, len(spots), len(COMMERCIALS))
    log.info(
        "making %d fake commercials (have %d spots, %d catalog entries, %d beds)",
        n_to_make, len(spots), len(COMMERCIALS), len(beds),
    )

    made = 0
    failed = 0
    for i, commercial in enumerate(COMMERCIALS[:n_to_make]):
        voice_mp3 = spots[i % len(spots)]
        bed = pick_bed(commercial, beds)
        if bed is None:
            log.warning("no bed for %s; skipping", commercial.id)
            continue
        out = args.output_dir / f"{commercial.id}.mp3"
        try:
            mix_voice_and_bed(voice_mp3, bed.path, out)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="replace").strip() if e.stderr else ""
            log.error("ffmpeg mix failed for %s: %s", commercial.id, err[:200])
            failed += 1
            continue
        log.info("faked %s ← spot=%s + bed=%s",
                 commercial.id, voice_mp3.name, bed.title)
        made += 1

    log.info("done: %d made, %d failed", made, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
