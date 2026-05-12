"""Voice feeder: keeps /tmp/rcr/voice.fifo continuously fed.

The streaming ffmpeg reads s16le PCM from this FIFO as a third audio input
and uses it both as a heard signal and as the sidechain trigger that ducks
music. That means **the FIFO must always have bytes flowing**: if the writer
ever blocks for long, the sidechain detector starves and ffmpeg's filter
graph stalls. So this feeder writes silence frames forever by default; when
Jennifer has something to say, callers push raw PCM bytes onto its queue and
the feeder plays those instead of silence for that interval.

Symmetry with MusicFeeder: blocking IO held by a thread, opens the FIFO once
at startup, never closes it until stop(). PCM format must match the streamer
exactly — 48kHz stereo s16le.
"""

from __future__ import annotations

import logging
import queue
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # s16le

# 100ms silence chunks: small enough that an enqueued speech burst plays
# within ~one chunk of latency, large enough that the write loop's overhead
# is invisible. 48000 * 2ch * 2B * 0.1s = 19_200 bytes.
SILENCE_CHUNK_S = 0.1
SILENCE_CHUNK = b"\x00" * int(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * SILENCE_CHUNK_S)


class VoiceFeeder:
    def __init__(self, fifo_path: Path):
        self.fifo_path = fifo_path
        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def enqueue_pcm(self, pcm: bytes) -> None:
        """Queue a chunk of s16le PCM at 48kHz stereo to be played as voice.

        Thread-safe. Silence resumes automatically once the queue drains.
        """
        if pcm:
            self._queue.put(pcm)

    def run(self) -> None:
        """Open the FIFO and keep it fed until stop() is called.

        Opening for write blocks until ffmpeg attaches as a reader; that's the
        sync point that lines the pipeline up at startup.
        """
        log.info("opening %s for write (blocks until ffmpeg attaches)…", self.fifo_path)
        with open(self.fifo_path, "wb") as fifo:
            log.info("voice FIFO open; writing silence")
            while not self._stop:
                try:
                    pcm = self._queue.get_nowait()
                except queue.Empty:
                    pcm = SILENCE_CHUNK
                try:
                    fifo.write(pcm)
                    fifo.flush()
                except BrokenPipeError:
                    log.warning("voice FIFO reader went away (ffmpeg died?)")
                    self._stop = True
                    return
