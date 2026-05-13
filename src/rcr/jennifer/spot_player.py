"""mp3 → PCM decode primitive for voice segments.

The streaming ffmpeg expects voice as 48kHz stereo s16le on /tmp/rcr/voice.fifo.
ElevenLabs returns mono mp3 at 44.1kHz, so we run a per-segment ffmpeg
subprocess to decode + resample + upmix and capture the resulting PCM in
memory. The buffer is small (a 12-second spot is ~2.3MB) so we hold the
whole thing rather than streaming it, which guarantees no silence gaps
mid-utterance: a single queue.put() onto VoiceFeeder writes the entire
segment in one fifo.write() call.

Playback orchestration (decode → enqueue → await drain) lives in
`rcr.jennifer.player.JenniferPlayer`. This module is the decode primitive
that the player uses.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from rcr.audio_format import CHANNELS, SAMPLE_RATE

log = logging.getLogger(__name__)

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
