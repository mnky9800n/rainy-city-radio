"""Download the curated commercial-bed track pool into jennifer/commercial_beds/.

These are short instrumental loops that go *under* commercial voice
tracks during M4.5 talk-breaks. The catalog below was curated by a
research agent on 2026-05-13; every track has a verified license
declaration and a direct or scrape-resolvable mp3 URL.

Reuses the HTTP fetching helpers from `download_cc` (TLS-insecure-hosts
list, per-host headers, atomic stream-write). Bed-specific differences:

    music/<filename>.json       — CC music sidecars (handled by download_cc)
    jennifer/commercial_beds/   — CC bed sidecars (handled here)

Sidecars carry the same CC-attribution fields plus bed-specific metadata
(moods tuple, approximate length, loop-point hint).

Usage:
    uv run python -m rcr.tools.download_beds
    uv run python -m rcr.tools.download_beds --dry-run
    uv run python -m rcr.tools.download_beds --only "Kevin MacLeod*"
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from rcr.tools.download_cc import (
    BROWSER_USER_AGENT,  # noqa: F401  re-exported just to keep the import side-effect
    INTER_REQUEST_PAUSE_S,
    download_mp3,
    resolve_mp3_from_page,
    slugify_filename,
)

log = logging.getLogger("rcr.download_beds")

DEFAULT_BEDS_DIR = Path("jennifer/commercial_beds")


# ---------------------------------------------------------------------------
# Catalog — 15 verified CC instrumental beds (research agent, 2026-05-13)
# ---------------------------------------------------------------------------
# Source URLs are page URLs; mp3_url=None means "scrape the page for the
# first .mp3 link." The fetch helpers handle that fallback.
#
# `moods` lists every bed_mood this track can cover; the production
# mixing tool will match commercials' bed_mood field against this tuple.

CATALOG: list[dict[str, Any]] = [
    # --- Eric Skiff — Resistor Anthems (CC BY 4.0) -----------------------
    {
        "filename": "Eric Skiff - A Night Of Dizzy Spells",
        "mp3_url": "https://ericskiff.com/music/Resistor%20Anthems/01%20A%20Night%20Of%20Dizzy%20Spells.mp3",
        "source_url": "https://ericskiff.com/music/",
        "title": "A Night Of Dizzy Spells",
        "artist": "Eric Skiff",
        "license": "CC BY 4.0",
        "attribution": '"A Night Of Dizzy Spells" by Eric Skiff (CC BY 4.0) — Resistor Anthems — ericskiff.com/music',
        "approx_length_s": 114,
        "moods": ("playful", "warm", "99x", "uplifting"),
    },
    {
        "filename": "Eric Skiff - Chibi Ninja",
        "mp3_url": "https://ericskiff.com/music/Resistor%20Anthems/03%20Chibi%20Ninja.mp3",
        "source_url": "https://ericskiff.com/music/",
        "title": "Chibi Ninja",
        "artist": "Eric Skiff",
        "license": "CC BY 4.0",
        "attribution": '"Chibi Ninja" by Eric Skiff (CC BY 4.0) — Resistor Anthems — ericskiff.com/music',
        "approx_length_s": 123,
        "moods": ("playful", "warm", "99x", "uplifting"),
    },
    {
        "filename": "Eric Skiff - Underclocked",
        "mp3_url": "https://ericskiff.com/music/Resistor%20Anthems/02%20Underclocked%20(underunderclocked%20mix).mp3",
        "source_url": "https://ericskiff.com/music/",
        "title": "Underclocked (underunderclocked mix)",
        "artist": "Eric Skiff",
        "license": "CC BY 4.0",
        "attribution": '"Underclocked (underunderclocked mix)" by Eric Skiff (CC BY 4.0) — Resistor Anthems — ericskiff.com/music',
        "approx_length_s": 189,
        "moods": ("chill", "lo-fi", "playful", "atmospheric"),
    },

    # --- Komiku — Captain Glouglou's Incredible Week OST (CC0 1.0) -------
    {
        "filename": "Komiku - Home",
        "mp3_url": None,
        "source_url": "https://freemusicarchive.org/music/Komiku/Captain_Glouglous_Incredible_Week_Soundtrack/Home_1105",
        "title": "Home",
        "artist": "Komiku",
        "license": "CC0 1.0",
        "attribution": '"Home" by Komiku (CC0 1.0 — public domain, no attribution required)',
        "approx_length_s": 66,
        "moods": ("playful", "warm", "99x", "uplifting"),
    },
    {
        "filename": "Komiku - Beach",
        "mp3_url": None,
        "source_url": "https://freemusicarchive.org/music/Komiku/Captain_Glouglous_Incredible_Week_Soundtrack/plage",
        "title": "Beach",
        "artist": "Komiku",
        "license": "CC0 1.0",
        "attribution": '"Beach" by Komiku (CC0 1.0 — public domain, no attribution required)',
        "approx_length_s": 60,
        "moods": ("light", "chill", "playful", "atmospheric"),
    },

    # --- Kevin MacLeod / incompetech.com (CC BY 3.0 / 4.0) ----------------
    {
        "filename": "Kevin MacLeod - Airport Lounge",
        "mp3_url": "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Airport%20Lounge.mp3",
        "source_url": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1100806",
        "title": "Airport Lounge",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "attribution": '"Airport Lounge" by Kevin MacLeod (CC BY 4.0) — incompetech.com',
        "approx_length_s": 307,
        "moods": ("lounge", "smooth-jazz", "cheesy"),
    },
    {
        "filename": "Kevin MacLeod - Backbay Lounge",
        "mp3_url": "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Backbay%20Lounge.mp3",
        "source_url": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1700068",
        "title": "Backbay Lounge",
        "artist": "Kevin MacLeod",
        "license": "CC BY 3.0",
        "attribution": '"Backbay Lounge" by Kevin MacLeod (CC BY 3.0) — incompetech.com',
        "approx_length_s": 267,
        "moods": ("jazzy", "noir", "lounge", "warm"),
    },
    {
        "filename": "Kevin MacLeod - Bossa Antigua",
        "mp3_url": "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Bossa%20Antigua.mp3",
        "source_url": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1700069",
        "title": "Bossa Antigua",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "attribution": '"Bossa Antigua" by Kevin MacLeod (CC BY 4.0) — incompetech.com',
        "approx_length_s": 283,
        "moods": ("jazzy", "noir", "lounge", "warm"),
    },
    {
        "filename": "Kevin MacLeod - Local Forecast - Elevator",
        "mp3_url": "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Local%20Forecast%20-%20Elevator.mp3",
        "source_url": "https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1300012",
        "title": "Local Forecast - Elevator",
        "artist": "Kevin MacLeod",
        "license": "CC BY 3.0",
        "attribution": '"Local Forecast - Elevator" by Kevin MacLeod (CC BY 3.0) — incompetech.com',
        "approx_length_s": 200,
        "moods": ("cheesy", "lounge", "smooth-jazz"),
    },
    {
        "filename": "Kevin MacLeod - Sneaky Snitch",
        "mp3_url": "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Sneaky%20Snitch.mp3",
        "source_url": "https://incompetech.com/music/royalty-free/index.html?keywords=sneaky+snitch",
        "title": "Sneaky Snitch",
        "artist": "Kevin MacLeod",
        "license": "CC BY 4.0",
        "attribution": '"Sneaky Snitch" by Kevin MacLeod (CC BY 4.0) — incompetech.com',
        "approx_length_s": 144,
        "moods": ("noir", "sparse", "jazzy"),
    },

    # --- Chris Zabriskie (CC BY 4.0) -------------------------------------
    {
        "filename": "Chris Zabriskie - Cylinder Four",
        "mp3_url": None,
        "source_url": "https://chriszabriskie.com/cylinders/",
        "title": "Cylinder Four",
        "artist": "Chris Zabriskie",
        "license": "CC BY 4.0",
        "attribution": '"Cylinder Four" by Chris Zabriskie (CC BY 4.0) — chriszabriskie.com',
        "approx_length_s": 195,
        "moods": ("calm", "serious", "institutional", "sparse", "atmospheric", "rainy"),
    },
    {
        "filename": "Chris Zabriskie - Cylinder Five",
        "mp3_url": None,
        "source_url": "https://chriszabriskie.com/cylinders/",
        "title": "Cylinder Five",
        "artist": "Chris Zabriskie",
        "license": "CC BY 4.0",
        "attribution": '"Cylinder Five" by Chris Zabriskie (CC BY 4.0) — chriszabriskie.com',
        "approx_length_s": 173,
        "moods": ("atmospheric", "rainy", "sparse", "chill", "lo-fi"),
    },
    {
        "filename": "Chris Zabriskie - I Am Running Down the Long Hallway of Viewmont Elementary",
        "mp3_url": None,
        "source_url": "https://chriszabriskie.com/honor/",
        "title": "I Am Running Down the Long Hallway of Viewmont Elementary",
        "artist": "Chris Zabriskie",
        "license": "CC BY 4.0",
        "attribution": '"I Am Running Down the Long Hallway of Viewmont Elementary" by Chris Zabriskie (CC BY 4.0) — chriszabriskie.com',
        "approx_length_s": 210,
        "moods": ("atmospheric", "sparse", "institutional", "calm"),
    },

    # --- Kai Engel — Idea (CC BY-NC 4.0, NC-flag) ------------------------
    {
        "filename": "Kai Engel - Touch the Darkness",
        "mp3_url": None,
        "source_url": "https://freemusicarchive.org/music/Kai_Engel/Idea/Kai_Engel_-_03_-_Touch_the_Darkness",
        "title": "Touch the Darkness",
        "artist": "Kai Engel",
        "license": "CC BY-NC 4.0",
        "attribution": '"Touch the Darkness" by Kai Engel (CC BY-NC 4.0) — freemusicarchive.org/music/Kai_Engel',
        "approx_length_s": 210,
        "moods": ("noir", "sparse", "atmospheric", "calm"),
    },
    {
        "filename": "Kai Engel - Remedy for Melancholy",
        "mp3_url": None,
        "source_url": "https://freemusicarchive.org/music/Kai_Engel/Idea/Kai_Engel_-_05_-_Remedy_for_Melancholy",
        "title": "Remedy for Melancholy",
        "artist": "Kai Engel",
        "license": "CC BY-NC 4.0",
        "attribution": '"Remedy for Melancholy" by Kai Engel (CC BY-NC 4.0) — freemusicarchive.org/music/Kai_Engel',
        "approx_length_s": 180,
        "moods": ("chill", "lo-fi", "atmospheric", "rainy", "sparse"),
    },
]


# ---------------------------------------------------------------------------
# Side-car
# ---------------------------------------------------------------------------

def write_sidecar(entry: dict[str, Any], path: Path) -> None:
    """Write the partial sidecar — only CC + bed-classification fields."""
    data = {
        "title": entry["title"],
        "artist": entry["artist"],
        "source_url": entry["source_url"],
        "license": entry["license"],
        "attribution": entry["attribution"],
        "approx_length_s": entry["approx_length_s"],
        "moods": list(entry["moods"]),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Process one entry
# ---------------------------------------------------------------------------

def process_entry(
    secure: httpx.Client, insecure: httpx.Client,
    entry: dict[str, Any], beds_dir: Path, *, dry_run: bool,
) -> str:
    fname = slugify_filename(entry["filename"])
    mp3_path = beds_dir / f"{fname}.mp3"
    sidecar_p = beds_dir / f"{fname}.json"

    mp3_url = entry.get("mp3_url")
    if not mp3_url:
        if dry_run:
            log.info("[dry-run] would scrape mp3 from %s", entry["source_url"])
            return "dry-run"
        log.info("  scraping mp3 link from %s", entry["source_url"])
        mp3_url = resolve_mp3_from_page(secure, insecure, entry["source_url"])
        if not mp3_url:
            return "fail:no-mp3-url"

    if dry_run:
        log.info("[dry-run] %s → %s", mp3_url, mp3_path)
        return "dry-run"

    if mp3_path.exists() and mp3_path.stat().st_size > 0:
        log.info("  mp3 already on disk; refreshing sidecar only")
        write_sidecar(entry, sidecar_p)
        return "skip-existing"

    try:
        n = download_mp3(secure, insecure, mp3_url, mp3_path,
                         referer=entry["source_url"])
    except Exception as e:
        log.error("  download failed: %s", e)
        return f"fail:{e}"

    write_sidecar(entry, sidecar_p)
    log.info("  downloaded %.1f KB → %s", n / 1024, mp3_path.name)
    return "downloaded"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--beds-dir", type=Path, default=DEFAULT_BEDS_DIR)
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would be downloaded; don't fetch.")
    p.add_argument("--only", default=None,
                   help="Only process entries whose filename matches this fnmatch glob.")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    args.beds_dir.mkdir(parents=True, exist_ok=True)

    entries = CATALOG
    if args.only:
        entries = [e for e in CATALOG if fnmatch.fnmatch(e["filename"], args.only)]
        if not entries:
            log.error("no catalog entries match --only %r", args.only)
            return 2

    log.info("processing %d bed entr%s", len(entries),
             "y" if len(entries) == 1 else "ies")

    counters: dict[str, int] = {}
    with httpx.Client() as secure, httpx.Client(verify=False) as insecure:
        for i, entry in enumerate(entries, 1):
            log.info("[%d/%d] %s — %s", i, len(entries),
                     entry["artist"], entry["title"])
            outcome = process_entry(
                secure, insecure, entry, args.beds_dir, dry_run=args.dry_run,
            )
            bucket = outcome.split(":", 1)[0]
            counters[bucket] = counters.get(bucket, 0) + 1
            if outcome in ("downloaded", "dry-run") and i < len(entries):
                time.sleep(INTER_REQUEST_PAUSE_S)

    log.info("done: %s", counters)
    failures = sum(v for k, v in counters.items() if k == "fail")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
