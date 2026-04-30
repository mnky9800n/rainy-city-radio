"""Entry point for the rainy-city-radio service.

asyncio orchestrator that wires together the music feeder (writes PCM to a FIFO)
and the streamer (one ffmpeg subprocess that mixes everything and pushes to
YouTube live or — in dry-run — a local FLV file).

Usage:
    # Dry-run: write to out/live_test.flv instead of YouTube. Best for iterating.
    python -m rcr.main --dry-run --duration 60

    # Live: read YOUTUBE_STREAM_KEY from env and push to YouTube RTMP.
    set -a; source .env; set +a
    python -m rcr.main
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from rcr.music.feeder import MusicFeeder
from rcr.streamer import StreamConfig, Streamer, youtube_target_from_env

log = logging.getLogger("rcr")

DEFAULT_FIFO = Path("/tmp/rcr/music.fifo")
DEFAULT_BG = Path("assets/stream_bg.png")
DEFAULT_AMBIENT = Path("assets/ambient_rain.mp3")
DEFAULT_MUSIC_DIR = Path("music")
DEFAULT_DRY_RUN_OUT = Path("out/live_test.flv")


def ensure_fifo(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Could be a stale FIFO from a previous run; recreate to be safe.
        path.unlink()
    os.mkfifo(path)


async def run(
    bg: Path,
    ambient: Path,
    music_dir: Path,
    fifo: Path,
    output_target: str,
    duration: float | None,
) -> None:
    ensure_fifo(fifo)

    feeder = MusicFeeder(music_dir, fifo)
    streamer = Streamer(StreamConfig(
        bg_path=bg,
        music_fifo=fifo,
        ambient_path=ambient,
        output_target=output_target,
    ))

    feeder_task = asyncio.create_task(asyncio.to_thread(feeder.run), name="feeder")
    streamer_task = asyncio.create_task(streamer.run(), name="streamer")

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop():
        log.info("stop requested")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    if duration is not None:
        async def _autostop():
            await asyncio.sleep(duration)
            log.info("--duration elapsed, stopping")
            stop_event.set()
        asyncio.create_task(_autostop(), name="autostop")

    await stop_event.wait()

    feeder.stop()
    await streamer.stop()
    # The feeder may be blocked in fifo.write() until ffmpeg drains; give it a
    # moment, then move on. The thread is daemonic-by-virtue-of-being-a-task.
    try:
        await asyncio.wait_for(feeder_task, timeout=5.0)
    except asyncio.TimeoutError:
        log.warning("feeder thread didn't exit cleanly within 5s")
    await streamer_task


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bg", type=Path, default=DEFAULT_BG)
    p.add_argument("--ambient", type=Path, default=DEFAULT_AMBIENT)
    p.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
    p.add_argument("--fifo", type=Path, default=DEFAULT_FIFO)
    p.add_argument("--dry-run", action="store_true",
                   help="Write FLV to out/live_test.flv instead of YouTube RTMP.")
    p.add_argument("--dry-run-out", type=Path, default=DEFAULT_DRY_RUN_OUT)
    p.add_argument("--duration", type=float, default=None,
                   help="Stop automatically after N seconds (handy with --dry-run).")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    for path in (args.bg, args.ambient, args.music_dir):
        if not path.exists():
            raise SystemExit(f"missing required path: {path}")

    if args.dry_run:
        args.dry_run_out.parent.mkdir(parents=True, exist_ok=True)
        target = str(args.dry_run_out)
        log.info("dry-run: writing to %s", target)
    else:
        target = youtube_target_from_env()
        log.info("live: pushing to YouTube RTMP")

    asyncio.run(run(
        bg=args.bg,
        ambient=args.ambient,
        music_dir=args.music_dir,
        fifo=args.fifo,
        output_target=target,
        duration=args.duration,
    ))


if __name__ == "__main__":
    main()
