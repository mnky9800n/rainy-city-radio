"""Production-mix every commercial into voice + bed → jennifer/commercials/<id>.mp3.

This is the canonical M4.5 commercial bake. For each Commercial in
`rcr.jennifer.commercials.COMMERCIALS`:

  1. Synthesize voice via Voicer (per-call voice_id override picks the
     right in-universe character). Cache-or-API.
  2. Pick a bed whose `moods` tuple contains `commercial.bed_mood`.
     Selection is **deterministic** on commercial.id (sha256 → modulo),
     so the same commercial always mixes against the same bed across
     re-runs — no surprise re-bakes when the bed library grows.
  3. ffmpeg-mix: voice at unity, bed at `BED_VOLUME_DB` (default -10dB),
     `amix duration=first` so the mix ends with the voice (bed gets
     cut off cleanly). Bed is `-stream_loop -1`-ed in case it's shorter
     than the voice line.
  4. Write the mixed mp3 to `jennifer/commercials/<id>.mp3`, overriding
     whatever voice-only version `bake_commercials.py` left there.

Always overwrites. Re-running is cheap if voices are already in the
voicer cache — the only work is the ffmpeg mix, ~few seconds for the
whole catalog.

Usage:
    set -a; source .env; set +a
    uv run python -m rcr.tools.produce_commercials
    uv run python -m rcr.tools.produce_commercials --dry-run
    uv run python -m rcr.tools.produce_commercials --only business_001
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rcr.jennifer.commercials import COMMERCIALS, Commercial
from rcr.jennifer.voicer import Voicer, VoicerError

log = logging.getLogger("rcr.produce_commercials")

DEFAULT_COMMERCIALS_DIR = Path("jennifer/commercials")
DEFAULT_BEDS_DIR = Path("jennifer/commercial_beds")

# How quiet the bed sits under the voice. -10dB is a clear "voice leads
# the mix" relationship while still leaving the bed audible.
BED_VOLUME_DB = -10.0


# ---------------------------------------------------------------------------
# Bed library
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BedInfo:
    path: Path
    title: str
    artist: str
    moods: tuple[str, ...]
    attribution: str


def load_beds(beds_dir: Path) -> list[BedInfo]:
    """Load all baked beds from `beds_dir` by walking JSON sidecars.

    Skips entries whose mp3 is missing or zero-byte. Returns a sorted
    list (by filename) so per-commercial bed selection is deterministic
    across runs that operate on the same library.
    """
    if not beds_dir.exists():
        return []
    out: list[BedInfo] = []
    for json_path in sorted(beds_dir.glob("*.json")):
        mp3_path = json_path.with_suffix(".mp3")
        if not mp3_path.exists() or mp3_path.stat().st_size == 0:
            continue
        try:
            data = json.loads(json_path.read_text())
        except json.JSONDecodeError:
            log.warning("skipping unreadable bed sidecar %s", json_path)
            continue
        out.append(BedInfo(
            path=mp3_path,
            title=str(data.get("title", json_path.stem)),
            artist=str(data.get("artist", "?")),
            moods=tuple(data.get("moods") or ()),
            attribution=str(data.get("attribution", "")),
        ))
    return out


def _stable_index(key: str, modulo: int) -> int:
    """Deterministic 0..modulo-1 from a string key (sha256 prefix)."""
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(h[:16], 16) % modulo


def pick_bed(commercial: Commercial, beds: list[BedInfo]) -> BedInfo | None:
    """Pick a bed for `commercial`, preferring those whose moods include
    `commercial.bed_mood`. Falls back to any bed if no mood-match exists.
    Deterministic on `commercial.id` so re-runs are stable."""
    if not beds:
        return None
    matching = [b for b in beds if commercial.bed_mood in b.moods]
    pool = matching if matching else beds
    if not matching:
        log.debug(
            "no bed matches mood %r for %s; falling back to whole library",
            commercial.bed_mood, commercial.id,
        )
    return pool[_stable_index(commercial.id, len(pool))]


# ---------------------------------------------------------------------------
# ffmpeg mixing
# ---------------------------------------------------------------------------

def mix_voice_and_bed(voice_mp3: Path, bed_mp3: Path, out_mp3: Path) -> None:
    """Run ffmpeg to mix `voice_mp3` (unity) + `bed_mp3` (BED_VOLUME_DB,
    looped) into `out_mp3`. Mix ends when voice ends.

    Raises CalledProcessError on ffmpeg failure; caller decides whether
    to skip-and-log or abort the batch.
    """
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_mp3.with_suffix(out_mp3.suffix + ".part")
    filter_complex = (
        f"[1:a]volume={BED_VOLUME_DB}dB[bed_quiet];"
        f"[0:a][bed_quiet]amix=inputs=2:duration=first:dropout_transition=0[mix]"
    )
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(voice_mp3),
        "-stream_loop", "-1",
        "-i", str(bed_mp3),
        "-filter_complex", filter_complex,
        "-map", "[mix]",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(tmp),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    tmp.rename(out_mp3)


# ---------------------------------------------------------------------------
# Per-commercial production
# ---------------------------------------------------------------------------

def _output_is_fresh(out_mp3: Path, voice_mp3: Path, bed_mp3: Path) -> bool:
    """True if `out_mp3` is newer than both inputs (make-style staleness)."""
    if not out_mp3.exists() or out_mp3.stat().st_size == 0:
        return False
    out_mtime = out_mp3.stat().st_mtime
    return all(
        src.exists() and src.stat().st_mtime <= out_mtime
        for src in (voice_mp3, bed_mp3)
    )


def produce_one(
    commercial: Commercial,
    voicer: Voicer,
    beds: list[BedInfo],
    commercials_dir: Path,
    *,
    force: bool = False,
) -> str:
    """Outcome string: 'mixed' / 'skip-fresh' / 'skip-no-bed' / 'fail-voice' / 'fail-mix'.

    With `force=False` (default): if the output mp3 already exists and is
    newer than both the voice and bed sources, skip — cron'd hourly runs
    against a fully-produced catalog become near-instant cache passes.
    """
    out_mp3 = commercials_dir / f"{commercial.id}.mp3"

    bed = pick_bed(commercial, beds)
    if bed is None:
        log.warning("no beds available for %s; skipping", commercial.id)
        return "skip-no-bed"

    # Pre-check: if output is already fresh against this bed AND we already
    # have a voice mp3 in the cache, skip without hitting ElevenLabs at all.
    # (This is the hot path under cron — most invocations find everything
    # already produced.)
    if not force:
        cached_voice = voicer.cache_path(commercial.text, voice_id=commercial.voice_id)
        if cached_voice.exists() and _output_is_fresh(out_mp3, cached_voice, bed.path):
            return "skip-fresh"

    try:
        voice_mp3 = voicer.synthesize(commercial.text, voice_id=commercial.voice_id)
    except VoicerError as e:
        log.error("voice synthesize failed for %s: %s", commercial.id, e)
        return "fail-voice"

    if not force and _output_is_fresh(out_mp3, voice_mp3, bed.path):
        return "skip-fresh"

    try:
        mix_voice_and_bed(voice_mp3, bed.path, out_mp3)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        log.error("ffmpeg mix failed for %s: %s", commercial.id, err[:300])
        return "fail-mix"

    log.info(
        "produced %s (%s) bed=%r [%s]",
        commercial.id, commercial.character, bed.title, commercial.bed_mood,
    )
    return "mixed"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commercials-dir", type=Path, default=DEFAULT_COMMERCIALS_DIR)
    p.add_argument("--beds-dir", type=Path, default=DEFAULT_BEDS_DIR)
    p.add_argument("--only", nargs="+", default=None,
                   help="One or more glob patterns matching commercial ids.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print picks; don't synthesize or mix.")
    p.add_argument("--force", action="store_true",
                   help="Re-mix even if outputs are fresh against inputs.")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    commercials = list(COMMERCIALS)
    if args.only:
        commercials = [c for c in commercials
                       if any(fnmatch.fnmatch(c.id, pat) for pat in args.only)]
    if not commercials:
        log.error("no commercials match --only %r", args.only)
        return 2

    beds = load_beds(args.beds_dir)
    log.info("commercials=%d  beds=%d", len(commercials), len(beds))
    if not beds:
        log.error(
            "no beds in %s — run `uv run python -m rcr.tools.download_beds` first",
            args.beds_dir,
        )
        return 2

    if args.dry_run:
        for c in commercials:
            bed = pick_bed(c, beds)
            bed_desc = (
                f"{bed.artist} — {bed.title}" if bed is not None else "<none>"
            )
            log.info("[dry-run] %s (%s, mood=%s) → bed: %s",
                     c.id, c.character, c.bed_mood, bed_desc)
        return 0

    try:
        voicer = Voicer.from_env()
    except VoicerError as e:
        log.error("%s", e)
        return 2

    counters: dict[str, int] = {}
    for c in commercials:
        outcome = produce_one(c, voicer, beds, args.commercials_dir,
                              force=args.force)
        counters[outcome] = counters.get(outcome, 0) + 1

    log.info("done: %s", counters)
    failures = sum(v for k, v in counters.items() if k.startswith("fail"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
