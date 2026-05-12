"""Download the curated Creative-Commons track pool into music/.

CATALOG holds the curated set. Each entry has the source URL, the real CC
attribution, and either a direct mp3 URL or `None` (in which case we fetch
the source page and scrape the first .mp3 link out of it).

What gets written:
    music/<safe-name>.mp3      the audio file
    music/<safe-name>.json     a *partial* sidecar with only the CC fields

The partial sidecar deliberately omits the audio-analysis fields (bpm /
energy / mood / etc.) so the loader marks the track as "not yet ingested"
and excludes it from playback. Running `python -m rcr.tools.ingest_track
--all` fills in the analysis; the CC fields are preserved across that
re-write (see rcr.music.ingest).

Idempotent: a track whose mp3 already exists is skipped (the sidecar is
re-written to keep CC metadata fresh against this catalog).

Usage:
    uv run python -m rcr.tools.download_cc
    uv run python -m rcr.tools.download_cc --dry-run
    uv run python -m rcr.tools.download_cc --only "Coruscate*"
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

# ccmixter.org serves a Let's Encrypt cert without the intermediate, which
# breaks strict TLS verification in everything that doesn't do AIA-fetching
# (curl, requests, httpx). Modern browsers paper over this; we don't.
# Skipping verify here is a calculated risk: we're downloading public CC mp3s,
# not transmitting secrets. The cost of MITM here is "got a tampered mp3,"
# which we'd notice on first listen. Don't generalize this exception.
TLS_INSECURE_HOSTS = {"ccmixter.org"}

log = logging.getLogger("rcr.download_cc")

DEFAULT_MUSIC_DIR = Path("music")
DOWNLOAD_TIMEOUT_S = 60.0
SCRAPE_TIMEOUT_S = 20.0
INTER_REQUEST_PAUSE_S = 0.5  # be polite to ccmixter / archive.org
CHUNK_BYTES = 64 * 1024

# ccmixter rejects direct mp3 requests as 403 without a Referer pointing at
# the track's source page (hotlink protection). Use a browser UA + Referer
# strategy for ccmixter; other hosts are fine with whatever.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
# Sources curated by separate research agents on 2026-05-12. License + URL
# verified per-track against the source page.
# `mp3_url=None` → fetch source_url and scrape the first .mp3 link.

CATALOG: list[dict[str, Any]] = [
    # --- ccMixter: Coruscate cluster ---------------------------------------
    {
        "filename": "Coruscate - The Fade Out",
        "mp3_url": "https://ccmixter.org/content/Coruscate/Coruscate_-_The_Fade_Out.mp3",
        "real_title": "The Fade Out",
        "real_artist": "Coruscate",
        "release": None,
        "source_url": "https://ccmixter.org/files/Coruscate/70473",
        "license": "CC BY 3.0",
        "attribution": '"The Fade Out" by Coruscate (CC BY 3.0)',
    },
    {
        "filename": "Coruscate - Silence Is Golden",
        "mp3_url": None,
        "real_title": "Silence Is Golden",
        "real_artist": "Coruscate",
        "release": None,
        "source_url": "https://ccmixter.org/files/Coruscate/70496",
        "license": "CC BY 4.0",
        "attribution": '"Silence Is Golden" by Coruscate (CC BY 4.0)',
    },
    {
        "filename": "Coruscate - High-Rise Building",
        "mp3_url": None,
        "real_title": "High-Rise Building",
        "real_artist": "Coruscate",
        "release": None,
        "source_url": "https://ccmixter.org/files/Coruscate/70568",
        "license": "CC BY 4.0",
        "attribution": '"High-Rise Building" by Coruscate (CC BY 4.0)',
    },
    {
        "filename": "Coruscate - Leave Iran Alone (Vocals)",
        "mp3_url": None,
        "real_title": "Leave Iran Alone (Vocals)",
        "real_artist": "Coruscate",
        "release": None,
        "source_url": "https://ccmixter.org/files/Coruscate/70703",
        "license": "CC BY 4.0",
        "attribution": '"Leave Iran Alone (Vocals)" by Coruscate (CC BY 4.0)',
    },
    {
        "filename": "Coruscate - Wedding Of The Damned (Theremin Mix)",
        "mp3_url": None,
        "real_title": "Wedding Of The Damned (Theremin Mix)",
        "real_artist": "Coruscate",
        "release": None,
        "source_url": "https://ccmixter.org/files/Coruscate/70099",
        "license": "CC BY 3.0",
        "attribution": '"Wedding Of The Damned (Theremin Mix)" by Coruscate (CC BY 3.0)',
    },
    {
        "filename": "Michelle Noel - Queen of Karma",
        "mp3_url": "https://ccmixter.org/content/Michelle_Noel/Michelle_Noel_-_Queen_of_Karma_2.mp3",
        "real_title": "Queen of Karma",
        "real_artist": "Michelle Noel",
        "release": None,
        "source_url": "https://ccmixter.org/files/Michelle_Noel/70449",
        "license": "CC BY 4.0",
        "attribution": '"Queen of Karma" by Michelle Noel (CC BY 4.0)',
    },
    {
        "filename": "Coruscate - Queen Of Karma (Orchestra Telapathine Mix)",
        "mp3_url": None,
        "real_title": "Queen Of Karma (Orchestra Telapathine Mix)",
        "real_artist": "Coruscate",
        "release": None,
        "source_url": "https://ccmixter.org/files/Coruscate/70453",
        "license": "CC BY 4.0",
        "attribution": '"Queen Of Karma (Orchestra Telapathine Mix)" by Coruscate, vocals by Michelle Noel (CC BY 4.0)',
    },

    # --- ccMixter: BY-NC remix orbit ---------------------------------------
    {
        "filename": "7OOP3D - Sad Gentleman (Bristol Cut)",
        "mp3_url": None,
        "real_title": "Sad Gentleman (Bristol Cut)",
        "real_artist": "7OOP3D",
        "release": None,
        "source_url": "https://ccmixter.org/files/7OOP3D/70162",
        "license": "CC BY-NC 4.0",
        "attribution": '"Sad Gentleman (Bristol Cut)" by 7OOP3D (CC BY-NC 4.0)',
    },
    {
        "filename": "7OOP3D - Mirage (Blohowiak Downshift)",
        "mp3_url": None,
        "real_title": "Mirage (Blohowiak Downshift)",
        "real_artist": "7OOP3D",
        "release": None,
        "source_url": "https://ccmixter.org/files/7OOP3D/69823",
        "license": "CC BY-NC 4.0",
        "attribution": '"Mirage (Blohowiak Downshift)" by 7OOP3D (CC BY-NC 4.0)',
    },
    {
        "filename": "Soulja Unit - Rainy Daze (Soulja Unit Remix)",
        "mp3_url": None,
        "real_title": "Rainy Daze (Soulja Unit Remix)",
        "real_artist": "Soulja Unit",
        "release": None,
        "source_url": "https://ccmixter.org/files/SouljaUnit/70087",
        "license": "CC BY-NC 4.0",
        "attribution": '"Rainy Daze (Soulja Unit Remix)" by Soulja Unit, original by Coruscate (CC BY-NC 4.0)',
    },
    {
        "filename": "Radioontheshelf - Dreams Made In Valleys",
        "mp3_url": None,
        "real_title": "Dreams Made In Valleys",
        "real_artist": "Radioontheshelf",
        "release": None,
        "source_url": "https://ccmixter.org/files/Radioontheshelf/68338",
        "license": "CC BY-NC 4.0",
        "attribution": '"Dreams Made In Valleys" by Radioontheshelf (CC BY-NC 4.0)',
    },
    {
        "filename": "Radioontheshelf - Disturbed Sleep",
        "mp3_url": None,
        "real_title": "Disturbed Sleep",
        "real_artist": "Radioontheshelf",
        "release": None,
        "source_url": "https://ccmixter.org/files/Radioontheshelf/68357",
        "license": "CC BY-NC 3.0",
        "attribution": '"Disturbed Sleep" by Radioontheshelf (CC BY-NC 3.0)',
    },
    {
        "filename": "Radioontheshelf - When Joan Accepted Her Voices",
        "mp3_url": None,
        "real_title": "When Joan Accepted Her Voices",
        "real_artist": "Radioontheshelf",
        "release": None,
        "source_url": "https://ccmixter.org/files/Radioontheshelf/70655",
        "license": "CC BY-NC 3.0",
        "attribution": '"When Joan Accepted Her Voices" by Radioontheshelf (CC BY-NC 3.0)',
    },
    {
        "filename": "duckett - MelancholiBot Discovers Love (Thunked Up Mix)",
        "mp3_url": None,
        "real_title": "MelancholiBot Discovers Love (Thunked Up Mix)",
        "real_artist": "duckett",
        "release": None,
        "source_url": "https://ccmixter.org/files/duckett/70640",
        "license": "CC BY-NC 4.0",
        "attribution": '"MelancholiBot Discovers Love (Thunked Up Mix)" by duckett (CC BY-NC 4.0)',
    },
    {
        "filename": "raja_ffm - I'll Wait",
        "mp3_url": None,
        "real_title": "I'll Wait",
        "real_artist": "raja_ffm",
        "release": None,
        "source_url": "https://ccmixter.org/files/raja_ffm/69303",
        "license": "CC BY-NC 3.0",
        "attribution": '"I\'ll Wait" by raja_ffm (CC BY-NC 3.0)',
    },
    {
        "filename": "Ben Blohowiak - Life Is But a Dream (Hidden Track Remix)",
        "mp3_url": None,
        "real_title": "Life Is But a Dream (Hidden Track Remix)",
        "real_artist": "Ben Blohowiak",
        "release": None,
        "source_url": "https://ccmixter.org/files/bblohowiak/70634",
        "license": "CC BY-NC 3.0",
        "attribution": '"Life Is But a Dream (Hidden Track Remix)" by Ben Blohowiak (CC BY-NC 3.0)',
    },
    {
        "filename": "JansMusic - Follow (JansMusic Remix)",
        "mp3_url": None,
        "real_title": "Follow (JansMusic Remix)",
        "real_artist": "JansMusic",
        "release": None,
        "source_url": "https://ccmixter.org/files/JansMusic/68980",
        "license": "CC BY-NC 4.0",
        "attribution": '"Follow (JansMusic Remix)" by JansMusic (CC BY-NC 4.0)',
    },

    # --- Dusted Wax Kingdom (archive.org-hosted, predictable URLs) ---------
    {
        "filename": "Domeneko - Femme Fatale",
        "mp3_url": "https://archive.org/download/DWK020/Domeneko_-_03_-_Femme_Fatale.mp3",
        "real_title": "Femme Fatale",
        "real_artist": "Domeneko",
        "release": "Noir",
        "source_url": "https://dustedwax.org/dwk020.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Femme Fatale" by Domeneko, on Noir (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Domeneko - Rain",
        "mp3_url": "https://archive.org/download/DWK020/Domeneko_-_15_-_Rain.mp3",
        "real_title": "Rain",
        "real_artist": "Domeneko",
        "release": "Noir",
        "source_url": "https://dustedwax.org/dwk020.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Rain" by Domeneko, on Noir (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Mr. Moods - The Replicant",
        "mp3_url": "https://archive.org/download/DWK015/Mr_Moods_-_01_-_The_Replicant.mp3",
        "real_title": "The Replicant",
        "real_artist": "Mr. Moods",
        "release": "Cinematic Beats",
        "source_url": "https://dustedwax.org/dwk015.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"The Replicant" by Mr. Moods, on Cinematic Beats (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Mr. Moods - Time Heals Nothing",
        "mp3_url": "https://archive.org/download/DWK015/Mr_Moods_-_02_-_Time_Heals_Nothing.mp3",
        "real_title": "Time Heals Nothing",
        "real_artist": "Mr. Moods",
        "release": "Cinematic Beats",
        "source_url": "https://dustedwax.org/dwk015.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Time Heals Nothing" by Mr. Moods, on Cinematic Beats (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Third Person Lurkin - Churning Vapours",
        "mp3_url": "https://archive.org/download/DWK066/Third_Person_Lurkin_-_02_-_Churning_Vapours.mp3",
        "real_title": "Churning Vapours",
        "real_artist": "Third Person Lurkin",
        "release": "The Nameless City",
        "source_url": "https://dustedwax.org/dwk066.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Churning Vapours" by Third Person Lurkin, on The Nameless City (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Third Person Lurkin - Cyclopean Pylons",
        "mp3_url": "https://archive.org/download/DWK066/Third_Person_Lurkin_-_05_-_Cyclopean_Pylons.mp3",
        "real_title": "Cyclopean Pylons",
        "real_artist": "Third Person Lurkin",
        "release": "The Nameless City",
        "source_url": "https://dustedwax.org/dwk066.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Cyclopean Pylons" by Third Person Lurkin, on The Nameless City (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Esbe - Bluesy Moon",
        "mp3_url": "https://archive.org/download/DWK069/Esbe_-_05_-_Bluesy_Moon.mp3",
        "real_title": "Bluesy Moon",
        "real_artist": "Esbe",
        "release": "Late Night Headphones, Volume 1",
        "source_url": "https://dustedwax.org/dwk069.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Bluesy Moon" by Esbe, on Late Night Headphones, Volume 1 (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Esbe - Dark Shades Of Blue",
        "mp3_url": "https://archive.org/download/DWK069/Esbe_-_15_-_Dark_Shades_Of_Blue.mp3",
        "real_title": "Dark Shades Of Blue",
        "real_artist": "Esbe",
        "release": "Late Night Headphones, Volume 1",
        "source_url": "https://dustedwax.org/dwk069.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Dark Shades Of Blue" by Esbe, on Late Night Headphones, Volume 1 (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Jenova 7 - Dark Water Jazz",
        "mp3_url": "https://archive.org/download/DWK107/Jenova_7_-_01_-_Dark_Water_Jazz.mp3",
        "real_title": "Dark Water Jazz",
        "real_artist": "Jenova 7",
        "release": "Dusted Jazz Volume One",
        "source_url": "https://dustedwax.org/dwk107.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Dark Water Jazz" by Jenova 7, on Dusted Jazz Volume One (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Jenova 7 - A Touch Of Evil",
        "mp3_url": "https://archive.org/download/DWK107/Jenova_7_-_04_-_A_Touch_Of_Evil.mp3",
        "real_title": "A Touch Of Evil",
        "real_artist": "Jenova 7",
        "release": "Dusted Jazz Volume One",
        "source_url": "https://dustedwax.org/dwk107.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"A Touch Of Evil" by Jenova 7, on Dusted Jazz Volume One (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "dustmotes - Curtains Drawn",
        "mp3_url": "https://archive.org/download/DWK210/dustmotes_-_12_-_Curtains_Drawn.mp3",
        "real_title": "Curtains Drawn",
        "real_artist": "dustmotes",
        "release": "Horror Vacui",
        "source_url": "https://dustedwax.org/dwk210.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Curtains Drawn" by dustmotes, on Horror Vacui (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Tracing Arcs - Through A Glass Darkly",
        "mp3_url": "https://archive.org/download/DWK030/Tracing_Arcs_-_05_-_Through_A_Glass_Darkly.mp3",
        "real_title": "Through A Glass Darkly",
        "real_artist": "Tracing Arcs",
        "release": "Fin",
        "source_url": "https://dustedwax.org/dwk030.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Through A Glass Darkly" by Tracing Arcs, on Fin (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Shine of Her Eyes - Slow Motion Colours",
        "mp3_url": "https://archive.org/download/DWK084/Shine_of_Her_Eyes_-_07_-_Slow_Motion_Colours.mp3",
        "real_title": "Slow Motion Colours",
        "real_artist": "Shine of Her Eyes",
        "release": "Slow Motion Colours",
        "source_url": "https://dustedwax.org/dwk084.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Slow Motion Colours" by Shine of Her Eyes, on Slow Motion Colours (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Mista 93 - Illusion",
        "mp3_url": "https://archive.org/download/DWK124/Mista_93_-_05_-_Illusion.mp3",
        "real_title": "Illusion",
        "real_artist": "Mista 93",
        "release": "Mystic Renaissance",
        "source_url": "https://dustedwax.org/dwk124.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Illusion" by Mista 93, on Mystic Renaissance (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
    {
        "filename": "Mizontiq - Days Of Daze",
        "mp3_url": "https://archive.org/download/DWK116/Mizontiq_-_02_-_Days_Of_Daze.mp3",
        "real_title": "Days Of Daze",
        "real_artist": "Mizontiq",
        "release": "A Room Without Mirrors",
        "source_url": "https://dustedwax.org/dwk116.html",
        "license": "CC BY-NC-ND 3.0",
        "attribution": '"Days Of Daze" by Mizontiq, on A Room Without Mirrors (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_FN_CHARS = set('/\\:*?"<>|\0')


def slugify_filename(s: str) -> str:
    out = "".join(c if c not in _FORBIDDEN_FN_CHARS else "" for c in s)
    out = " ".join(out.split())
    return out.strip() or "unnamed"


_MP3_LINK_RE = re.compile(r"https?://[^\s\"'<>]+?\.mp3", re.IGNORECASE)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _client_for(url: str, secure: httpx.Client, insecure: httpx.Client) -> httpx.Client:
    """Pick the right httpx client for a URL based on TLS_INSECURE_HOSTS."""
    if _host(url) in TLS_INSECURE_HOSTS:
        return insecure
    return secure


def _headers_for(url: str, referer: str | None) -> dict[str, str]:
    """Per-host request headers. ccmixter needs a browser UA + Referer."""
    host = _host(url)
    if host == "ccmixter.org":
        h = {"User-Agent": BROWSER_USER_AGENT}
        if referer:
            h["Referer"] = referer
        return h
    return {"User-Agent": "rainy-city-radio/cc-fetch (homebase)"}


def resolve_mp3_from_page(secure: httpx.Client, insecure: httpx.Client, source_url: str) -> str | None:
    """Fetch source_url and return the first .mp3 link found in the HTML."""
    client = _client_for(source_url, secure, insecure)
    try:
        r = client.get(
            source_url, follow_redirects=True, timeout=SCRAPE_TIMEOUT_S,
            headers=_headers_for(source_url, referer=None),
        )
    except httpx.HTTPError as e:
        log.warning("  scrape failed for %s: %s", source_url, e)
        return None
    if r.status_code != 200:
        log.warning("  scrape got HTTP %s for %s", r.status_code, source_url)
        return None
    matches = _MP3_LINK_RE.findall(r.text)
    return matches[0] if matches else None


def download_mp3(
    secure: httpx.Client, insecure: httpx.Client, url: str, dest: Path,
    *, referer: str | None,
) -> int:
    """Stream `url` to `dest` atomically. Returns bytes written."""
    client = _client_for(url, secure, insecure)
    tmp = dest.with_suffix(dest.suffix + ".part")
    written = 0
    with client.stream(
        "GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S,
        headers=_headers_for(url, referer=referer),
    ) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=CHUNK_BYTES):
                f.write(chunk)
                written += len(chunk)
    if written == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"empty download from {url}")
    tmp.rename(dest)
    return written


def write_sidecar(entry: dict[str, Any], path: Path) -> None:
    """Write the partial sidecar — only CC fields, no audio analysis yet."""
    data = {
        "real_title": entry["real_title"],
        "real_artist": entry["real_artist"],
        "release": entry.get("release"),
        "source_url": entry["source_url"],
        "license": entry["license"],
        "attribution": entry["attribution"],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_entry(
    secure: httpx.Client,
    insecure: httpx.Client,
    entry: dict[str, Any],
    music_dir: Path,
    *,
    dry_run: bool,
) -> str:
    fname = slugify_filename(entry["filename"])
    mp3_path = music_dir / f"{fname}.mp3"
    sidecar_p = music_dir / f"{fname}.json"

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
        log.info("  mp3 already on disk, skipping download")
        # Still refresh the sidecar in case the catalog metadata changed.
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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
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

    args.music_dir.mkdir(parents=True, exist_ok=True)

    entries = CATALOG
    if args.only:
        entries = [e for e in CATALOG if fnmatch.fnmatch(e["filename"], args.only)]
        if not entries:
            log.error("no catalog entries match --only %r", args.only)
            return 2

    log.info("processing %d catalog entr%s", len(entries),
             "y" if len(entries) == 1 else "ies")

    counters: dict[str, int] = {}
    # Headers are set per-request in _headers_for(); clients here are bare.
    with httpx.Client() as secure, \
         httpx.Client(verify=False) as insecure:
        for i, entry in enumerate(entries, 1):
            log.info("[%d/%d] %s — %s", i, len(entries),
                     entry["real_artist"], entry["real_title"])
            outcome = process_entry(secure, insecure, entry, args.music_dir, dry_run=args.dry_run)
            counters[outcome.split(":", 1)[0]] = counters.get(outcome.split(":", 1)[0], 0) + 1
            # Don't pause on errors or skips — only after actual network hits.
            if outcome in ("downloaded", "dry-run") and i < len(entries):
                time.sleep(INTER_REQUEST_PAUSE_S)

    log.info("done: %s", counters)
    failures = sum(v for k, v in counters.items() if k == "fail")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
