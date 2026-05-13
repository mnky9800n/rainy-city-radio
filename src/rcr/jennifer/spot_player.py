"""Play a pre-baked Jennifer mp3 by decoding it to PCM and feeding the voice FIFO.

The streaming ffmpeg expects voice as 48kHz stereo s16le on /tmp/rcr/voice.fifo.
ElevenLabs returns mono mp3 at 44.1kHz, so we run a per-spot ffmpeg subprocess
to decode + resample + upmix and capture the resulting PCM in memory. The
buffer is small (a 12-second spot is ~2.3MB) so we hold the whole thing rather
than streaming it, which guarantees no silence gaps mid-utterance: a single
queue.put() onto VoiceFeeder writes the entire spot in one fifo.write() call.

Call `play_mp3(feeder, path)` from asyncio code; it offloads the decode to a
thread and awaits the playback duration so the scheduler can serialize spots.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from rcr.audio_format import BYTES_PER_SECOND, CHANNELS, SAMPLE_RATE
from rcr.jennifer.feeder import VoiceFeeder

log = logging.getLogger(__name__)

# Tiny pad after enqueue so the next scheduled action doesn't crowd the tail
# of this one — covers the ~100ms of silence-frame latency in the feeder loop.
TRAILING_PAD_S = 0.15


class SpotPlayError(RuntimeError):
    pass


def decode_to_pcm(mp3_path: Path) -> bytes:
    """Synchronously decode `mp3_path` to s16le 48kHz stereo PCM bytes."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(mp3_path),
            "-vn",
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        raise SpotPlayError(f"ffmpeg failed decoding {mp3_path}: {err[:300]}")
    if not proc.stdout:
        raise SpotPlayError(f"ffmpeg produced no PCM from {mp3_path}")
    return proc.stdout


async def play_mp3(feeder: VoiceFeeder, mp3_path: Path) -> None:
    """Decode `mp3_path` and play it through the voice FIFO. Awaits its duration."""
    pcm = await asyncio.to_thread(decode_to_pcm, mp3_path)
    duration_s = len(pcm) / BYTES_PER_SECOND
    log.info("voice spot: %s (%.1fs)", mp3_path.name, duration_s)
    feeder.enqueue_pcm(pcm)
    await asyncio.sleep(duration_s + TRAILING_PAD_S)
