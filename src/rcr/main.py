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
import math
import os
import signal
import struct
from pathlib import Path

from rcr.jennifer.feeder import (
    BYTES_PER_SAMPLE as VOICE_BYTES_PER_SAMPLE,
    CHANNELS as VOICE_CHANNELS,
    SAMPLE_RATE as VOICE_SAMPLE_RATE,
    VoiceFeeder,
)
from rcr.jennifer.scheduler import JenniferScheduler
from rcr.music.feeder import MusicFeeder
from rcr.streamer import StreamConfig, Streamer, youtube_target_from_env

log = logging.getLogger("rcr")

DEFAULT_MUSIC_FIFO = Path("/tmp/rcr/music.fifo")
DEFAULT_VOICE_FIFO = Path("/tmp/rcr/voice.fifo")
DEFAULT_BG = Path("assets/stream_bg.png")
DEFAULT_AMBIENT = Path("assets/ambient_rain.mp3")
DEFAULT_MUSIC_DIR = Path("music")
DEFAULT_SPOTS_DIR = Path("jennifer/spots")
DEFAULT_INTROS_DIR = Path("jennifer/track_intros")
DEFAULT_DRY_RUN_OUT = Path("out/live_test.flv")

# Test-tone parameters used only with --voice-test-tone (dry-run verification
# of sidechain ducking — Jennifer's voice isn't wired yet).
TEST_TONE_FREQ_HZ = 440.0
TEST_TONE_DUR_S = 2.0
TEST_TONE_AMPLITUDE = 0.4  # well above duck_threshold=0.03
TEST_TONE_INTERVAL_S = 8.0


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
    spots_dir: Path,
    intros_dir: Path,
    music_fifo: Path,
    voice_fifo: Path,
    output_target: str,
    duration: float | None,
    voice_test_tone: bool,
    no_jennifer: bool,
    no_music: bool,
    test_intros_interval: float | None,
) -> None:
    ensure_fifo(music_fifo)
    ensure_fifo(voice_fifo)

    voice_feeder = VoiceFeeder(voice_fifo)
    streamer = Streamer(StreamConfig(
        bg_path=bg,
        music_fifo=music_fifo,
        voice_fifo=voice_fifo,
        ambient_path=ambient,
        output_target=output_target,
    ))
    jennifer = (
        None if no_jennifer
        else JenniferScheduler(
            voice_feeder, spots_dir=spots_dir, intros_dir=intros_dir,
            test_intros_interval_s=test_intros_interval,
            test_intros_music_dir=music_dir if test_intros_interval else None,
        )
    )
    # MusicFeeder takes the scheduler's callback so it can fire on track
    # changes. In test-intros mode the synthetic-transition loop is the
    # sole source, so we skip wiring the real callback. With --no-jennifer
    # there's no scheduler at all. With --no-music the feeder pumps silence
    # (no transitions either).
    if jennifer is None or test_intros_interval is not None or no_music:
        track_change_cb = None
    else:
        track_change_cb = jennifer.track_change_callback
    music_feeder = MusicFeeder(
        music_dir, music_fifo,
        on_track_change=track_change_cb, silent_mode=no_music,
    )

    music_task = asyncio.create_task(asyncio.to_thread(music_feeder.run), name="music_feeder")
    voice_task = asyncio.create_task(asyncio.to_thread(voice_feeder.run), name="voice_feeder")
    streamer_task = asyncio.create_task(streamer.run(), name="streamer")
    jennifer_task = (
        asyncio.create_task(jennifer.run(), name="jennifer_scheduler")
        if jennifer is not None else None
    )

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

    if voice_test_tone:
        asyncio.create_task(
            _emit_test_tones(voice_feeder, stop_event),
            name="voice_test_tone",
        )

    await stop_event.wait()

    if jennifer is not None:
        jennifer.stop()
    music_feeder.stop()
    voice_feeder.stop()
    await streamer.stop()
    if jennifer_task is not None:
        try:
            await asyncio.wait_for(jennifer_task, timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("jennifer scheduler didn't exit cleanly within 5s")
    # Feeders may be blocked in fifo.write() until ffmpeg drains; give them
    # a moment, then move on. The threads are daemonic-by-virtue-of-being-a-task.
    for name, task in (("music", music_task), ("voice", voice_task)):
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("%s feeder thread didn't exit cleanly within 5s", name)
    await streamer_task


def _sine_pcm(freq_hz: float, dur_s: float, amplitude: float) -> bytes:
    """Generate s16le stereo PCM at VOICE_SAMPLE_RATE for test-tone bursts."""
    n_frames = int(VOICE_SAMPLE_RATE * dur_s)
    peak = int(amplitude * 32767)
    two_pi_f_over_sr = 2.0 * math.pi * freq_hz / VOICE_SAMPLE_RATE
    out = bytearray(n_frames * VOICE_CHANNELS * VOICE_BYTES_PER_SAMPLE)
    pack_into = struct.pack_into
    for i in range(n_frames):
        v = int(peak * math.sin(two_pi_f_over_sr * i))
        offset = i * VOICE_CHANNELS * VOICE_BYTES_PER_SAMPLE
        for ch in range(VOICE_CHANNELS):
            pack_into("<h", out, offset + ch * VOICE_BYTES_PER_SAMPLE, v)
    return bytes(out)


async def _emit_test_tones(feeder: VoiceFeeder, stop_event: asyncio.Event) -> None:
    """Drop a sine burst onto the voice queue every TEST_TONE_INTERVAL_S.

    Lets you verify in dry-run that the sidechain trigger ducks the music
    when the voice channel produces signal. Real Jennifer audio replaces
    this in M3 step 2.
    """
    tone = _sine_pcm(TEST_TONE_FREQ_HZ, TEST_TONE_DUR_S, TEST_TONE_AMPLITUDE)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TEST_TONE_INTERVAL_S)
            return
        except asyncio.TimeoutError:
            pass
        log.info("voice-test-tone: injecting %.1fs sine @ %.0fHz",
                 TEST_TONE_DUR_S, TEST_TONE_FREQ_HZ)
        feeder.enqueue_pcm(tone)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bg", type=Path, default=DEFAULT_BG)
    p.add_argument("--ambient", type=Path, default=DEFAULT_AMBIENT)
    p.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
    p.add_argument("--spots-dir", type=Path, default=DEFAULT_SPOTS_DIR)
    p.add_argument("--intros-dir", type=Path, default=DEFAULT_INTROS_DIR)
    p.add_argument("--music-fifo", type=Path, default=DEFAULT_MUSIC_FIFO)
    p.add_argument("--voice-fifo", type=Path, default=DEFAULT_VOICE_FIFO)
    p.add_argument("--no-jennifer", action="store_true",
                   help="Disable Jennifer scheduler (music-only). Voice FIFO "
                        "still carries silence so the streamer keeps running.")
    p.add_argument("--no-music", action="store_true",
                   help="Voice-content QA mode: music FIFO pumps silence, "
                        "so the only audio is voice + ambient rain. Pair "
                        "with --test-intros-interval to audition intros/"
                        "outros without waiting for natural transitions.")
    p.add_argument("--test-intros-interval", type=float, default=None,
                   help="Dev mode: fire synthetic track-change transitions "
                        "every N seconds, bypassing MusicFeeder timing. Use "
                        "to verify intro/outro bake coverage without waiting "
                        "for real ~3-5min track transitions.")
    p.add_argument("--dry-run", action="store_true",
                   help="Write FLV to out/live_test.flv instead of YouTube RTMP.")
    p.add_argument("--dry-run-out", type=Path, default=DEFAULT_DRY_RUN_OUT)
    p.add_argument("--duration", type=float, default=None,
                   help="Stop automatically after N seconds (handy with --dry-run).")
    p.add_argument("--voice-test-tone", action="store_true",
                   help="Periodically inject a sine burst into voice.fifo so "
                        "you can hear the sidechain ducking work in dry-run.")
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
        spots_dir=args.spots_dir,
        intros_dir=args.intros_dir,
        music_fifo=args.music_fifo,
        voice_fifo=args.voice_fifo,
        output_target=target,
        duration=args.duration,
        voice_test_tone=args.voice_test_tone,
        no_jennifer=args.no_jennifer,
        no_music=args.no_music,
        test_intros_interval=args.test_intros_interval,
    ))


if __name__ == "__main__":
    main()
