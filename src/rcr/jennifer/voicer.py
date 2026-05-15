"""ElevenLabs TTS client with aggressive disk cache.

Two production constraints shape this module:

1. **Cost.** ElevenLabs bills per character. Every generated line goes through
   a content-addressed disk cache; subsequent identical requests are free.
2. **Character consistency.** Jennifer always uses the same voice ID + locked
   settings — that's the dataclass default. M4.5 introduces multi-voice
   commercials where OTHER characters (proprietors, PSA announcers, etc.)
   use different voice IDs while Jennifer's voice stays locked. So
   `synthesize()` accepts an optional per-call `voice_id` override that
   bypasses the default; the locked settings still apply to everyone.

Cache key: sha256(text + voice_id + model_id + settings_json). Changing any of
those produces a fresh entry — old cached audio survives in case we revert.
Per-call voice_id overrides change the cache key naturally; the same text in
two voices produces two cache entries.

Hybrid backend dispatch:
    voice_id values starting with `supertonic:` route to the local
    Supertonic backend (see voicer_supertonic.py). Everything else is
    treated as a raw ElevenLabs voice ID. Lets the catalog mix per-character
    backends — Jennifer stays on premium EL for character consistency, while
    the rotating commercial cast (Marlowe, Vince, PSB) uses Supertonic for
    free, unlimited CPU-bound synthesis.
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

# Prefix that marks a voice_id as a Supertonic preset rather than an
# ElevenLabs voice ID. Example: "supertonic:M3" → Supertonic male voice 3.
SUPERTONIC_PREFIX = "supertonic:"

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

    def _supertonic_backend(self):
        """Lazily build the Supertonic voicer the first time it's needed.

        Cached on the dataclass field via direct attribute set — we can't
        use `field(default=None)` since this is a frozen dataclass and the
        ONNX runtime + model file shouldn't load on import. So we stash it
        via `object.__setattr__`.
        """
        existing = getattr(self, "_supertonic", None)
        if existing is not None:
            return existing
        from rcr.jennifer.voicer_supertonic import SupertonicVoicer
        backend = SupertonicVoicer(cache_dir=self.cache_dir)
        object.__setattr__(self, "_supertonic", backend)
        return backend

    def cache_path(self, text: str, *, voice_id: str | None = None) -> Path:
        """Cache-path lookup; pass voice_id to query a non-default voice."""
        effective = voice_id if voice_id is not None else self.voice_id
        if effective.startswith(SUPERTONIC_PREFIX):
            preset = effective[len(SUPERTONIC_PREFIX):]
            return self._supertonic_backend().cache_path(text, voice_id=preset)
        return self.cache_dir / f"{self._key(text, effective)}.mp3"

    def synthesize(self, text: str, *, voice_id: str | None = None) -> Path:
        """Return a path to an mp3 of `text` spoken with the chosen voice.

        Default behavior (voice_id=None) uses the Voicer's configured
        `self.voice_id` — Jennifer's locked voice. Pass `voice_id="..."` to
        synthesize with a different ElevenLabs voice (multi-voice
        commercials, M4.5). The locked voice settings still apply.

        Cache-first: an API call is made only on miss. The returned file is in
        ElevenLabs' default mp3 output format (44.1kHz mono mp3) — callers
        decode it through ffmpeg, which will resample/upmix into the streamer's
        48kHz stereo s16le.
        """
        effective_voice = voice_id if voice_id is not None else self.voice_id
        # Supertonic backend handles its own cache + synth.
        if effective_voice.startswith(SUPERTONIC_PREFIX):
            preset = effective_voice[len(SUPERTONIC_PREFIX):]
            try:
                return self._supertonic_backend().synthesize(text, voice_id=preset)
            except Exception as e:
                # Surface as VoicerError for uniform error handling upstream
                # (produce_commercials catches VoicerError specifically).
                raise VoicerError(f"Supertonic synthesize failed: {e}") from e
        # Otherwise: ElevenLabs path (existing behavior).
        path = self.cache_path(text, voice_id=effective_voice)
        if path.exists() and path.stat().st_size > 0:
            log.debug("voicer cache hit: %s -> %s", text[:40], path.name)
            return path
        log.info("voicer cache miss: synthesizing %d chars (voice=%s)",
                 len(text), effective_voice)
        audio = self._request(text, voice_id=effective_voice)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Write to a temp path then atomic-rename, so a partial write from a
        # crashed/killed process never poisons the cache.
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(audio)
        tmp.rename(path)
        return path

    def _key(self, text: str, voice_id: str) -> str:
        payload = json.dumps(
            {
                "text": text,
                "voice_id": voice_id,
                "model": self.model,
                "settings": self.settings,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _request(self, text: str, *, voice_id: str) -> bytes:
        url = ELEVENLABS_URL.format(voice_id=voice_id)
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
