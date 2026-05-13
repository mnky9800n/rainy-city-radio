"""Music feeder: streams decoded PCM bytes into /tmp/rcr/music.fifo continuously.

The streaming ffmpeg reads s16le PCM from this FIFO. We can't simply concatenate
mp3 bytes, because each track has its own header and ffmpeg would re-init the
decoder on every track boundary. Instead, we run a *per-track* ffmpeg that
decodes each mp3 to raw PCM, and we keep the FIFO's write end open across
tracks so the consumer never sees EOF.

Track selection (M2):
    - Re-load the tagged library on every iteration (load_library skips mp3s
      that don't yet have a sidecar — that's how the watcher-based drop-folder
      ingest stays decoupled from playback).
    - Pick the next track via the pure selector: ring-buffer dedupe + BPM/
      energy continuity + soft 40-min mood arc.
    - If no tagged tracks exist, write silence so ffmpeg doesn't starve.
      (This is the "fresh checkout, ingest still running" case — the watcher
      will catch up and tracks will start appearing.)

This is blocking IO; run it from a thread (asyncio.to_thread).
"""

from __future__ import annotations

import logging
import random
import subprocess
from collections import deque
from pathlib import Path
from typing import Callable

from rcr.audio_format import CHANNELS, SAMPLE_RATE, silence_bytes
from rcr.music.selector import ArcState, recent_n, select
from rcr.music.tracks import Track, load_library

# Callback fired from the music-feeder thread *before* a new track's PCM
# starts flowing into the FIFO. Receives (previous_track, new_track). The
# previous is None for the very first track of a run. Implementations must
# be quick + thread-safe — the feeder is on a blocking IO loop and the
# event is delivered synchronously. Use loop.call_soon_threadsafe to bridge
# back to asyncio.
TrackChangeCallback = Callable[["Track | None", "Track"], None]

log = logging.getLogger(__name__)

PCM_CHUNK = 8192

# Coarse silence chunks (250ms) for the no-library / silent-mode paths.
# Voice feeder uses smaller chunks for lower speech-injection latency.
SILENCE_CHUNK_S = 0.25
SILENCE_CHUNK = silence_bytes(SILENCE_CHUNK_S)
NO_LIBRARY_SLEEP_S = 5.0


class MusicFeeder:
    def __init__(
        self,
        music_dir: Path,
        fifo_path: Path,
        *,
        rng: random.Random | None = None,
        on_track_change: TrackChangeCallback | None = None,
        silent_mode: bool = False,
    ):
        self.music_dir = music_dir
        self.fifo_path = fifo_path
        self._rng = rng or random.Random()
        self._arc = ArcState()
        # Ring buffer is sized for "up to 10 recent" per the architecture; the
        # selector slices it dynamically based on library size each call.
        self._ring: deque[Path] = deque(maxlen=10)
        self._last: Track | None = None
        self._stop = False
        self._on_track_change = on_track_change
        # Voice-content QA mode: pump silence on the music FIFO so the
        # stream's audio is ambient rain + Jennifer only, no songs. The
        # streamer's filter graph stays unchanged; sidechain just never has
        # music to duck. No track-change events fire (silence has no
        # transitions).
        self._silent_mode = silent_mode

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
            if self._silent_mode:
                log.info("music feeder: silent-mode QA path (no songs will play)")
                while not self._stop:
                    self._emit_silence(fifo, NO_LIBRARY_SLEEP_S)
                return
            while not self._stop:
                track = self._next_track()
                if track is None:
                    self._emit_silence(fifo, NO_LIBRARY_SLEEP_S)
                    continue
                self._ring.append(track.path)
                # Fire the change event *before* playback begins — gives the
                # Jennifer scheduler a window to pick an outro for `_last`
                # and an intro for `track`, then enqueue them onto the voice
                # FIFO. The first few seconds of `track` will play under
                # Jennifer's voice via the existing sidechain ducking.
                if self._on_track_change is not None:
                    try:
                        self._on_track_change(self._last, track)
                    except Exception:
                        # Don't let a buggy callback stall playback.
                        log.exception("on_track_change callback raised")
                self._play_track(track, fifo)
                self._last = track

    def _next_track(self) -> Track | None:
        library = load_library(self.music_dir)
        if not library:
            log.warning("no tagged tracks in %s; writing silence", self.music_dir)
            return None
        # Trim the ring buffer to the dynamic recent-N for this library size,
        # so the selector's view of "recent" matches its formula. The deque
        # itself is bounded at maxlen=10; this just keeps it from referencing
        # paths that are no longer relevant after library shrinkage.
        n_recent = recent_n(len(library))
        while len(self._ring) > n_recent:
            self._ring.popleft()
        return select(library, self._ring, self._last, self._arc, self._rng)

    def _emit_silence(self, fifo, seconds: float) -> None:
        for _ in range(int(seconds / SILENCE_CHUNK_S)):
            if self._stop:
                return
            try:
                fifo.write(SILENCE_CHUNK)
                fifo.flush()
            except BrokenPipeError:
                log.warning("FIFO reader went away while writing silence")
                self._stop = True
                return

    def _play_track(self, track: Track, fifo) -> None:
        log.info("now playing: %s [%s] bpm=%.0f energy=%d mood=%s",
                 track.name, track.fictional_artist, track.bpm, track.energy,
                 ",".join(track.mood))
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-i", str(track.path),
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
