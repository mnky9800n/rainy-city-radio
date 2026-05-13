"""Canonical audio-format constants for the streamer and its feeders.

These four values MUST stay in sync across the streamer's ffmpeg invocation,
the music feeder's per-track decode, the voice feeder's silence loop, and
the spot player's mp3→PCM conversion. If any of them drift, ffmpeg's filter
graph silently produces no audio or garbled audio — it doesn't error out.

s16le @ 48kHz stereo is the project-wide format. YouTube's RTMP ingest is
re-encoded to AAC anyway, so the choice is dictated by ElevenLabs' decoded
output (which we resample/upmix through ffmpeg from 44.1kHz mono mp3) and
what `amix` will happily accept.
"""

from __future__ import annotations

SAMPLE_RATE = 48000        # Hz
CHANNELS = 2               # stereo
BYTES_PER_SAMPLE = 2       # s16le → 2 bytes per sample per channel
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE  # 192_000


def silence_bytes(duration_s: float) -> bytes:
    """Return s16le silence of `duration_s` at the configured rate / channels.

    Used by both feeders to construct their idle-time chunks. They pick
    different chunk durations (music feeder uses larger chunks, voice
    feeder smaller for lower speech-injection latency) but the underlying
    byte recipe is the same.
    """
    return b"\x00" * int(BYTES_PER_SECOND * duration_s)
