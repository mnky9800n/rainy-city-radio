"""Music feeder: streams decoded PCM bytes into /tmp/rcr/music.fifo continuously.

The streaming ffmpeg reads s16le PCM from this FIFO. We can't simply concatenate
mp3 bytes, because each track has its own header and ffmpeg would re-init the
decoder on every track boundary. Instead, we run a *per-track* ffmpeg that
decodes each mp3 to raw PCM, and we keep the FIFO's write end open across
tracks so the consumer never sees EOF.

Behaviour:
    - Glob music/*.mp3 fresh on every loop iteration so dropping new files in
      lets them join the rotation (this is the v1 of M2's drop-folder ingest —
      proper auto-tagging arrives in M2 itself).
    - Random shuffle, infinite loop.
    - If the directory is empty, write silence so ffmpeg doesn't starve.

This is blocking IO; run it from a thread (asyncio.to_thread).
"""

from __future__ import annotations

import logging
import random
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # s16le
PCM_CHUNK = 8192

# Bytes-per-second of PCM at 48k stereo s16le = 192_000
SILENCE_CHUNK_S = 0.25
SILENCE_CHUNK = b"\x00" * int(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * SILENCE_CHUNK_S)


class MusicFeeder:
    def __init__(self, music_dir: Path, fifo_path: Path):
        self.music_dir = music_dir
        self.fifo_path = fifo_path
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        """Open the FIFO for writing and never close it until stop() is called.

        Opening for write blocks until a reader appears (ffmpeg in our case);
        that's the synchronization point that gets the pipeline aligned.
        """
        log.info("opening %s for write (blocks until ffmpeg attaches)…", self.fifo_path)
        with open(self.fifo_path, "wb") as fifo:
            log.info("FIFO open; starting playback")
            for track in self._iter_tracks():
                if self._stop:
                    return
                self._play_track(track, fifo)

    def _iter_tracks(self):
        """Infinite shuffle of mp3s in music_dir, re-globbed each pass."""
        while not self._stop:
            tracks = sorted(self.music_dir.glob("*.mp3"))
            if not tracks:
                log.warning("no mp3s in %s; writing silence", self.music_dir)
                yield None  # signal: emit silence for a bit
                continue
            random.shuffle(tracks)
            for t in tracks:
                yield t

    def _play_track(self, track: Path | None, fifo) -> None:
        if track is None:
            # No music available — keep the consumer fed with silence so ffmpeg
            # doesn't block forever on read.
            for _ in range(int(5.0 / SILENCE_CHUNK_S)):  # ~5s of silence
                if self._stop:
                    return
                fifo.write(SILENCE_CHUNK)
                fifo.flush()
            return

        log.info("now playing: %s", track.name)
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-i", str(track),
                "-vn",  # ignore embedded album art
                "-f", "s16le",
                "-ar", str(SAMPLE_RATE),
                "-ac", str(CHANNELS),
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None
        try:
            while not self._stop:
                chunk = proc.stdout.read(PCM_CHUNK)
                if not chunk:
                    break
                fifo.write(chunk)
                fifo.flush()
        except BrokenPipeError:
            log.warning("FIFO reader went away (ffmpeg died?)")
            self._stop = True
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            if proc.returncode and proc.returncode != 0 and stderr:
                log.warning("decoder for %s exited rc=%s: %s",
                            track.name, proc.returncode, stderr.strip())
