"""One-off ingest: tag a single mp3 (or every untagged mp3 in music/).

Usage:
    python -m rcr.tools.ingest_track music/foo.mp3
    python -m rcr.tools.ingest_track --all          # every untagged mp3 in music/
    python -m rcr.tools.ingest_track --all --force  # re-tag everything

Reads NIM_API_KEY from env. Source .env first:
    set -a; source .env; set +a
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rcr.music.ingest import ingest
from rcr.music.tracks import load, sidecar_path, untagged
from rcr.nim import NimClient, NimError

log = logging.getLogger("rcr.tools.ingest_track")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mp3", nargs="?", type=Path, help="Single mp3 to ingest.")
    p.add_argument("--all", action="store_true",
                   help="Ingest every untagged mp3 in --music-dir.")
    p.add_argument("--force", action="store_true",
                   help="Re-tag even if a sidecar already exists.")
    p.add_argument("--music-dir", type=Path, default=Path("music"))
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not args.mp3 and not args.all:
        p.error("pass an mp3 path or --all")
    if args.mp3 and args.all:
        p.error("--all is mutually exclusive with a single mp3 path")

    nim = NimClient.from_env()

    if args.mp3:
        targets = [args.mp3]
    elif args.force:
        targets = sorted(args.music_dir.glob("*.mp3"))
    else:
        targets = untagged(args.music_dir)

    if not targets:
        log.info("nothing to do — every mp3 in %s is already tagged", args.music_dir)
        return

    log.info("ingesting %d track(s)", len(targets))
    failed: list[Path] = []
    for mp3 in targets:
        if not args.force and load(mp3) is not None:
            log.info("skipping %s (already tagged; pass --force to re-tag)", mp3.name)
            continue
        try:
            ingest(mp3, nim=nim)
        except NimError as e:
            log.error("%s: NIM tagging failed: %s", mp3.name, e)
            failed.append(mp3)
        except Exception as e:
            log.error("%s: ingest failed: %s", mp3.name, e)
            failed.append(mp3)

    if failed:
        log.warning("failed: %s", ", ".join(p.name for p in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
