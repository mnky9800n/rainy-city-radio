"""ElevenLabs TTS client with aggressive disk cache.

Two production constraints shape this module:

1. **Cost.** ElevenLabs bills per character. Every generated line goes through
   a content-addressed disk cache; subsequent identical requests are free.
2. **Character consistency.** The voice ID and the four "voice settings"
   (stability / similarity_boost / style / use_speaker_boost) are locked
   module-wide. Changing them mid-run drifts Jennifer's sound, which is the
   one thing that would make her stop feeling like the same character.

Cache key: sha256(text + voice_id + model_id + settings_json). Changing any of
those produces a fresh entry — old cached audio survives in case we revert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_TIMEOUT = 60.0
DEFAULT_CACHE_DIR = Path("jennifer/voices")

# Locked voice settings. Do not vary at runtime — see module docstring.
LOCKED_SETTINGS: dict[str, float | bool] = {
    "stability": 0.55,
    "similarity_boost": 0.80,
    "style": 0.15,
    "use_speaker_boost": True,
}


class VoicerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Voicer:
    api_key: str
    voice_id: str
    cache_dir: Path = DEFAULT_CACHE_DIR
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    settings: dict[str, float | bool] = field(default_factory=lambda: dict(LOCKED_SETTINGS))

    @classmethod
    def from_env(
        cls,
        *,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        key_var: str = "ELEVENLABS_API_KEY",
        voice_var: str = "ELEVENLABS_VOICE_ID",
    ) -> "Voicer":
        key = os.environ.get(key_var)
        voice = os.environ.get(voice_var)
        missing = [v for v, val in ((key_var, key), (voice_var, voice)) if not val]
        if missing:
            raise VoicerError(
                f"missing env var(s): {', '.join(missing)} — "
                "`set -a; source .env; set +a` before running"
            )
        return cls(api_key=key, voice_id=voice, cache_dir=cache_dir)

    def cache_path(self, text: str) -> Path:
        return self.cache_dir / f"{self._key(text)}.mp3"

    def synthesize(self, text: str) -> Path:
        """Return a path to an mp3 of `text` spoken in Jennifer's voice.

        Cache-first: an API call is made only on miss. The returned file is in
        ElevenLabs' default mp3 output format (44.1kHz mono mp3) — callers
        decode it through ffmpeg, which will resample/upmix into the streamer's
        48kHz stereo s16le.
        """
        path = self.cache_path(text)
        if path.exists() and path.stat().st_size > 0:
            log.debug("voicer cache hit: %s -> %s", text[:40], path.name)
            return path
        log.info("voicer cache miss: synthesizing %d chars", len(text))
        audio = self._request(text)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp path then atomic-rename, so a partial write from a
        # crashed/killed process never poisons the cache.
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(audio)
        tmp.rename(path)
        return path

    def _key(self, text: str) -> str:
        payload = json.dumps(
            {
                "text": text,
                "voice_id": self.voice_id,
                "model": self.model,
                "settings": self.settings,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _request(self, text: str) -> bytes:
        url = ELEVENLABS_URL.format(voice_id=self.voice_id)
        payload = {
            "text": text,
            "model_id": self.model,
            "voice_settings": self.settings,
        }
        headers = {
            "xi-api-key": self.api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        }
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise VoicerError(f"ElevenLabs request failed: {e}") from e
        if r.status_code != 200:
            raise VoicerError(
                f"ElevenLabs HTTP {r.status_code}: {r.text[:300]}"
            )
        if not r.content:
            raise VoicerError("ElevenLabs returned empty body")
        return r.content
