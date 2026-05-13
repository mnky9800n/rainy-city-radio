"""Voice feeder: keeps /tmp/rcr/voice.fifo continuously fed.

The streaming ffmpeg reads s16le PCM from this FIFO as a third audio input
and uses it both as a heard signal and as the sidechain trigger that ducks
music. That means **the FIFO must always have bytes flowing**: if the writer
ever blocks for long, the sidechain detector starves and ffmpeg's filter
graph stalls. So this feeder writes silence frames forever by default; when
Jennifer has something to say, callers push raw PCM bytes onto its queue and
the feeder plays those instead of silence for that interval.

Two enqueue APIs:
    - `enqueue_pcm(pcm)`           : fire-and-forget (test-tone path, etc.)
    - `enqueue_pcm_with_ack(pcm)`  : returns an asyncio.Future that resolves
                                     when the chunk has been written into the
                                     FIFO. Used by JenniferPlayer to know
                                     when a voice segment is actually done
                                     playing instead of sleeping a guess.

The ack timing: `fifo.write()` returns when the kernel has accepted all
bytes for delivery; with a pipe backpressured at realtime by ffmpeg, that
happens ~one pipe-buffer's worth (~340ms at 192KB/s for the default 64KB
Linux pipe) BEFORE the audio actually finishes playing. The natural
backpressure of the next chunk's write(), starting at the moment the ack
fires, takes that long to drain — so segments queued back-to-back align
seamlessly without any artificial pad.

Symmetry with MusicFeeder: blocking IO held by a thread, opens the FIFO once
at startup, never closes it until stop(). PCM format must match the streamer
exactly — 48kHz stereo s16le.
"""

from __future__ import annotations

import asyncio
import logging
import queue
from pathlib import Path

from rcr.audio_format import silence_bytes

log = logging.getLogger(__name__)

# 100ms silence chunks: small enough that an enqueued speech burst plays
# within ~one chunk of latency, large enough that the write loop's overhead
# is invisible. 48000 * 2ch * 2B * 0.1s = 19_200 bytes.
SILENCE_CHUNK_S = 0.1
SILENCE_CHUNK = silence_bytes(SILENCE_CHUNK_S)

# Each queue entry is (pcm_bytes, optional done-future). When the feeder
# writes the chunk, it resolves the future (or raises on BrokenPipe).
_QueueEntry = tuple[bytes, "asyncio.Future[None] | None"]


class VoiceFeeder:
    def __init__(self, fifo_path: Path):
        self.fifo_path = fifo_path
        self._queue: queue.Queue[_QueueEntry] = queue.Queue()
        self._stop = False
        # Loop ref captured by the orchestrator so the feeder thread can
        # signal asyncio futures from inside its blocking write loop.
        # enqueue_pcm_with_ack requires this to be set first.
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the asyncio loop for cross-thread future resolution.

        Must be called before any `enqueue_pcm_with_ack`. JenniferScheduler
        does this from its `run()` method using `asyncio.get_running_loop()`.
        """
        self._loop = loop

    def stop(self) -> None:
        self._stop = True

    def enqueue_pcm(self, pcm: bytes) -> None:
        """Fire-and-forget: queue a chunk of PCM, no playback ack.

        Used by the test-tone path and anything else that doesn't need to
        know when the bytes have been consumed.
        """
        if pcm:
            self._queue.put((pcm, None))

    def enqueue_pcm_with_ack(self, pcm: bytes) -> "asyncio.Future[None]":
        """Queue PCM and return a Future that resolves once the bytes have
        been written into the FIFO (= kernel-accepted, ~one pipe-buffer
        ahead of audible playback completion).

        Raises if `set_event_loop()` hasn't been called yet — we need a
        loop reference to schedule the future-set from the feeder thread.
        """
        if self._loop is None:
            raise RuntimeError(
                "VoiceFeeder.set_event_loop() must be called before "
                "enqueue_pcm_with_ack(); JenniferScheduler.run() does this."
            )
        fut: asyncio.Future[None] = self._loop.create_future()
        if not pcm:
            # Empty input: resolve immediately rather than stranding the
            # caller awaiting a future that will never be written.
            self._loop.call_soon_threadsafe(fut.set_result, None)
            return fut
        self._queue.put((pcm, fut))
        return fut

    def _signal(self, fut: "asyncio.Future[None] | None", exc: BaseException | None = None) -> None:
        if fut is None or self._loop is None:
            return
        if exc is not None:
            self._loop.call_soon_threadsafe(fut.set_exception, exc)
        else:
            self._loop.call_soon_threadsafe(fut.set_result, None)

    def run(self) -> None:
        """Open the FIFO and keep it fed until stop() is called.

        Opening for write blocks until ffmpeg attaches as a reader; that's the
        sync point that lines the pipeline up at startup.
        """
        log.info("opening %s for write (blocks until ffmpeg attaches)…", self.fifo_path)
        with open(self.fifo_path, "wb") as fifo:
            log.info("voice FIFO open; writing silence")
            while not self._stop:
                done: asyncio.Future[None] | None
                try:
                    pcm, done = self._queue.get_nowait()
                except queue.Empty:
                    pcm, done = SILENCE_CHUNK, None
                try:
                    fifo.write(pcm)
                    fifo.flush()
                except BrokenPipeError:
                    log.warning("voice FIFO reader went away (ffmpeg died?)")
                    self._stop = True
                    # Don't strand the awaiting caller on shutdown.
                    self._signal(done, BrokenPipeError("voice FIFO closed"))
                    return
                self._signal(done)
