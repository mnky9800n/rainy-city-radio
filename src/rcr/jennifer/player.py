"""Voice playback orchestrator. Owns voice-queue access for the scheduler.

`JenniferScheduler` decides WHAT to play — which spot, which transition
segments, which talk-break sequence (M4.5). `JenniferPlayer` decides HOW:
decode the mp3, enqueue the PCM onto `VoiceFeeder`, await it being written
to the FIFO. Concentrates decode + enqueue + drain-wait in one object so
future features (M4.5 talk-break sequences) can compose cleanly without
the scheduler reaching across module boundaries into the feeder.

"Done playing" is signaled via `VoiceFeeder.enqueue_pcm_with_ack`, which
returns an `asyncio.Future` that resolves when the FIFO write completes.
That's not "audio finished" exactly — there's ~one pipe-buffer's worth
(~340ms) still in flight — but the next enqueue's write() naturally
backpressures on the same buffer, so back-to-back segments align without
any artificial pad. For periodic spots fired by independent tasks, the
~340ms overhang is below human perceptibility.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from rcr.audio_format import BYTES_PER_SECOND
from rcr.jennifer.feeder import VoiceFeeder
from rcr.jennifer.spot_player import decode_to_pcm

log = logging.getLogger(__name__)


class JenniferPlayer:
    """Plays voice mp3s through the streamer's voice FIFO.

    Only entry point for scheduler code that needs to put audio on-air.
    Anything else that wants to play voice should go through this object.
    """

    def __init__(self, voice_feeder: VoiceFeeder):
        self.voice_feeder = voice_feeder

    async def play_mp3(self, mp3_path: Path) -> None:
        """Play one voice mp3. Returns when the bytes are in the FIFO."""
        pcm = await asyncio.to_thread(decode_to_pcm, mp3_path)
        duration_s = len(pcm) / BYTES_PER_SECOND
        log.info("voice: %s (%.1fs)", mp3_path.name, duration_s)
        try:
            await self.voice_feeder.enqueue_pcm_with_ack(pcm)
        except BrokenPipeError:
            log.warning("voice playback aborted: FIFO closed (%s)", mp3_path.name)

    async def play_sequence(self, mp3_paths: list[Path]) -> None:
        """Play multiple voice mp3s back-to-back. Returns when all are done.

        Used today for transition outro→intro pairs. M4.5 talk-break
        segments will use the same primitive for longer sequences
        (commercial-1 → station-id → commercial-2 → outro into next track).
        """
        for path in mp3_paths:
            await self.play_mp3(path)
