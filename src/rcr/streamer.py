"""Long-running ffmpeg subprocess that does ALL the streaming work.

ONE ffmpeg process is the architectural invariant — Python feeds it via FIFOs and
never mixes audio itself. This module owns that subprocess: builds the command
line, spawns it, and restarts it if it dies.

M3 inputs:
    [0] Looped PNG (the pre-rendered 1920x1080 stream background, ~2fps).
    [1] s16le PCM stereo at 48kHz read from /tmp/rcr/music.fifo.
    [2] Ambient rain bed, looped indefinitely.
    [3] s16le PCM stereo at 48kHz read from /tmp/rcr/voice.fifo. The voice
        feeder writes silence frames whenever Jennifer isn't speaking, so this
        input is always producing a signal — never EOF until shutdown.

Filter graph:
    [voice] is asplit into [voice_main] (heard in the mix) and [voice_sc]
    (sidechain trigger). [voice_sc] drives sidechaincompress on [music] to
    duck music ~12dB whenever Jennifer speaks. [bed] is the ambient rain at
    -25dB. The final mix is ducked_music + voice + bed.

Output: FLV. RTMP to YouTube live, OR a local .flv file when in dry-run mode
(safer for iteration — no risk of burning YouTube live attempts).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

YOUTUBE_RTMP = "rtmp://a.rtmp.youtube.com/live2"
SAMPLE_RATE = 48000
CHANNELS = 2
RESTART_BACKOFF_S = 3.0


@dataclass(frozen=True)
class StreamConfig:
    bg_path: Path
    music_fifo: Path
    voice_fifo: Path
    ambient_path: Path
    output_target: str  # full URL or file path
    bed_volume_db: float = -25.0
    # Sidechain compressor parameters. Threshold is in linear amplitude
    # (0..1); 0.03 ≈ -30dB — well above silence-frame noise floor but below
    # any real Jennifer-level speech, so the trigger fires cleanly.
    duck_threshold: float = 0.03
    duck_ratio: float = 8.0
    duck_attack_ms: float = 50.0
    # Release is intentionally long. 500ms lets inter-word breath pauses
    # un-duck the music (mid-sentence pops), which sounds amateurish on a
    # broadcast. ~2.5s holds the duck through normal speech rhythm and only
    # releases once Jennifer has actually finished a phrase.
    duck_release_ms: float = 2500.0
    # Pinned video bitrate. Without it x264 picks ~2 Mbps for a stillimage,
    # which YouTube flags as below their 1080p30 minimum (and may quality-gate
    # the broadcast). 3500 Kbps clears that gate while keeping us at ~57% of
    # the 2 TB/month DO bandwidth cap (see CLAUDE.md Constraints).
    video_bitrate_kbps: int = 3500


class Streamer:
    """Owns the ffmpeg subprocess. Restarts it on unclean exit."""

    def __init__(self, cfg: StreamConfig):
        self.cfg = cfg
        self._proc: asyncio.subprocess.Process | None = None
        self._stop = asyncio.Event()

    def build_cmd(self) -> list[str]:
        c = self.cfg
        filter_complex = (
            # Voice splits into a "heard" copy and a sidechain-trigger copy.
            f"[3:a]asplit=2[voice_main][voice_sc];"
            # Music is compressed when voice is present (~12dB for ratio=8 at
            # the configured threshold). Music input is the *main* into
            # sidechaincompress; voice_sc is the trigger.
            f"[1:a][voice_sc]sidechaincompress="
            f"threshold={c.duck_threshold}:ratio={c.duck_ratio}:"
            f"attack={c.duck_attack_ms}:release={c.duck_release_ms}[ducked_music];"
            f"[2:a]volume={c.bed_volume_db}dB[bed];"
            # duration=first keeps the mix tied to the music FIFO's lifetime;
            # voice + bed are continuous so they never end on their own.
            f"[ducked_music][voice_main][bed]"
            f"amix=inputs=3:duration=first:dropout_transition=0[mix]"
        )
        # `-re` pegs each input to its native rate so ffmpeg consumes — and
        # therefore produces — at wall-clock pace. Without it the streamer
        # races ahead of realtime (fine for a file, fatal for RTMP).
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-y",  # overwrite output file in dry-run mode; harmless for RTMP.
            # [0] static background — looped at 2fps. Near-zero libx264 cost.
            "-thread_queue_size", "512",
            "-re",
            "-loop", "1",
            "-framerate", "2",
            "-i", str(c.bg_path),
            # [1] music PCM via FIFO
            "-thread_queue_size", "512",
            "-re",
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-i", str(c.music_fifo),
            # [2] ambient bed, looped forever
            "-thread_queue_size", "512",
            "-re",
            "-stream_loop", "-1",
            "-i", str(c.ambient_path),
            # [3] voice PCM via FIFO. The voice feeder writes silence when
            # Jennifer isn't speaking, so this input never EOFs.
            "-thread_queue_size", "512",
            "-re",
            "-f", "s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-i", str(c.voice_fifo),
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[mix]",
            # Video: still image — ultrafast preset and a 4-frame GOP at 2fps
            # is plenty for a non-moving frame. Bitrate is pinned (CBR-ish via
            # -b:v + -maxrate + -bufsize) so YouTube doesn't quality-gate us;
            # x264 just pads with extra-keyframe data, near-zero CPU cost.
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "stillimage",
            "-pix_fmt", "yuv420p",
            "-r", "2",
            "-g", "4",
            "-b:v", f"{c.video_bitrate_kbps}k",
            "-maxrate", f"{c.video_bitrate_kbps}k",
            "-bufsize", f"{c.video_bitrate_kbps * 2}k",
            # Audio: AAC 128k stereo @ 48kHz (matches source rate; no resample).
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-f", "flv",
            c.output_target,
        ]

    async def run(self) -> None:
        """Run ffmpeg, restart on unclean exit, until stop() is called."""
        while not self._stop.is_set():
            cmd = self.build_cmd()
            log.info("starting ffmpeg: %s", _redact_cmd(cmd))
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=None,  # let ffmpeg's warnings/errors hit our stderr
            )
            rc = await self._proc.wait()
            self._proc = None
            if self._stop.is_set():
                log.info("ffmpeg exited (rc=%s) after stop requested", rc)
                return
            log.warning("ffmpeg exited unexpectedly (rc=%s); restarting in %.1fs",
                        rc, RESTART_BACKOFF_S)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=RESTART_BACKOFF_S)
                return  # stop fired during backoff
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.send_signal(signal.SIGTERM)
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                log.warning("ffmpeg didn't exit on SIGTERM, killing")
                self._proc.kill()
                await self._proc.wait()


def youtube_target_from_env(env_var: str = "YOUTUBE_STREAM_KEY") -> str:
    key = os.environ.get(env_var)
    if not key:
        raise RuntimeError(
            f"{env_var} not set — `set -a; source .env; set +a` before running"
        )
    return f"{YOUTUBE_RTMP}/{key}"


def _redact_cmd(cmd: list[str]) -> str:
    """Render the ffmpeg command with the YouTube key redacted, for logging."""
    out: list[str] = []
    for arg in cmd:
        if arg.startswith(YOUTUBE_RTMP + "/"):
            out.append(YOUTUBE_RTMP + "/<redacted>")
        else:
            out.append(arg)
    return " ".join(out)
