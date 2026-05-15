"""Supertonic backend for the Voicer: local CPU-based TTS, no per-character cost.

Why we need a second backend:
    ElevenLabs free tier caps us at 10K characters per month. Every novel
    line we ship — track intros, commercials, future reactive Jennifer
    content — eats characters. The catalog naturally grows faster than
    the quota refreshes. Supertonic is on-device, ONNX-based, runs at ~1.4x
    realtime on a 1-vCPU droplet, and has no per-call cost. CPU is the
    only currency.

How it slots in:
    `Voicer.synthesize(text, voice_id=...)` dispatches by voice_id prefix.
    Raw IDs (`cgSgspJ2msm6clMCkdW9`) route to ElevenLabs (this module's
    sibling). The `supertonic:M3` form routes here. Cache key sha256
    includes the full voice_id, so EL and Supertonic entries never collide
    in `jennifer/voices/`.

What the user gets:
    Per-character voice variety in commercials and (eventually) reactive
    content, baked for free. The voice quality is good-not-premium —
    Jennifer's locked-character ElevenLabs voice stays on ElevenLabs; the
    rotating cast (Marlowe, Vince Vance, the Rainy-City Public Safety
    Bureau) lives here.

Output format:
    Synthesizes to 24kHz mono via Supertonic, then ffmpeg-encodes to
    mp3 96kbps so the file shape matches everything else under
    `jennifer/voices/`. The decode-to-pcm path in spot_player.py
    resamples to 48kHz stereo at playback time, unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

# Native sample rate from Supertonic — used both when writing the
# intermediate WAV and as ffmpeg's input rate.
SUPERTONIC_SAMPLE_RATE = 24_000
SUPERTONIC_MODEL = "supertonic-3"  # current best multilingual model

# Valid voice presets the Supertonic v3 model exposes. Used only for
# input validation; the underlying API accepts arbitrary names and
# would 500 on garbage, but we'd rather error loud at our boundary.
VALID_VOICES = frozenset({"M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"})


class SupertonicVoicerError(RuntimeError):
    pass


@dataclass
class SupertonicVoicer:
    """Local TTS via Supertonic. Disk cache compatible with the EL voicer.

    Cache key includes the model name + voice preset so future model
    upgrades don't trash existing audio — they just produce fresh entries.
    """
    cache_dir: Path
    model: str = SUPERTONIC_MODEL

    def __post_init__(self) -> None:
        # TTS instance is lazy — instantiation downloads the model on first
        # run (~50MB), and we don't want to pay that on every import. Only
        # constructed on the first synthesize() call.
        self._tts = None

    def _ensure_tts(self):
        if self._tts is None:
            from supertonic import TTS
            log.info("loading Supertonic TTS model=%s (auto-downloads on first run)",
                     self.model)
            self._tts = TTS(model=self.model)
            log.info("Supertonic TTS loaded")
        return self._tts

    def cache_path(self, text: str, *, voice_id: str) -> Path:
        return self.cache_dir / f"{self._key(text, voice_id)}.mp3"

    def synthesize(self, text: str, *, voice_id: str) -> Path:
        """Cache-or-synthesize; returns the path to a baked mp3.

        Raises SupertonicVoicerError on bad voice name, model load failure,
        or ffmpeg conversion failure. Mimics Voicer.synthesize's interface
        so the dispatcher can hand callers the same path semantics.
        """
        if voice_id not in VALID_VOICES:
            raise SupertonicVoicerError(
                f"unknown Supertonic voice {voice_id!r}; "
                f"valid: {sorted(VALID_VOICES)}"
            )
        path = self.cache_path(text, voice_id=voice_id)
        if path.exists() and path.stat().st_size > 0:
            log.debug("supertonic cache hit: %s -> %s", text[:40], path.name)
            return path
        log.info("supertonic cache miss: synthesizing %d chars (voice=%s)",
                 len(text), voice_id)
        wav_bytes = self._synthesize_wav(text, voice_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part.mp3")
        self._wav_bytes_to_mp3(wav_bytes, tmp)
        tmp.rename(path)
        return path

    def _synthesize_wav(self, text: str, voice_id: str) -> bytes:
        """Run Supertonic, get back a wav-encoded bytes blob."""
        import numpy as np
        tts = self._ensure_tts()
        style = tts.get_voice_style(voice_id)
        wav, _dur = tts.synthesize(text, voice_style=style)
        # wav is (1, num_samples) float32 in [-1, 1]; pack as s16le mono.
        samples = (wav[0] * 32767).astype(np.int16)
        # Use a tempfile-via-bytesio approach via wave (no temp file needed).
        import io
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SUPERTONIC_SAMPLE_RATE)
            wf.writeframes(samples.tobytes())
        return buf.getvalue()

    def _wav_bytes_to_mp3(self, wav_bytes: bytes, mp3_path: Path) -> None:
        """ffmpeg pipes wav stdin → mp3 file. 96kbps mono is plenty for voice."""
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "wav", "-i", "pipe:0",
                "-c:a", "libmp3lame", "-b:a", "96k", "-ac", "1",
                "-f", "mp3", str(mp3_path),
            ],
            input=wav_bytes, capture_output=True, check=False,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            raise SupertonicVoicerError(
                f"ffmpeg mp3 encode failed: {err[:300]}"
            )

    def _key(self, text: str, voice_id: str) -> str:
        payload = json.dumps(
            {
                "backend": "supertonic",
                "model": self.model,
                "voice_id": voice_id,
                "text": text,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
