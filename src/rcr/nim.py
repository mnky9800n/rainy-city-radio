"""NVIDIA NIM client (OpenAI-compatible).

Used twice in this project:
    - At ingest time, to tag a track's energy + mood + invented artist name.
    - At streaming time, to script Jennifer's live patter (M4).

Stateless calls, ≤500-token prompts, ≤150-token completions to stay inside the
free-tier credit cap.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_MODEL = "meta/llama-3.1-8b-instruct"
DEFAULT_TIMEOUT = 30.0


class NimError(RuntimeError):
    pass


@dataclass(frozen=True)
class NimClient:
    api_key: str
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, env_var: str = "NIM_API_KEY", **kw) -> "NimClient":
        key = os.environ.get(env_var)
        if not key:
            raise NimError(f"{env_var} not set")
        return cls(api_key=key, **kw)

    def chat(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 150,
        temperature: float = 0.6,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            r = httpx.post(NIM_URL, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise NimError(f"NIM request failed: {e}") from e
        if r.status_code != 200:
            raise NimError(f"NIM HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise NimError(f"unexpected NIM response shape: {e}") from e

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 200,
        temperature: float = 0.4,
    ) -> dict:
        """Like chat() but parses a JSON object out of the response.

        We don't rely on response_format=json_object — not all NIM-hosted
        models support it. Instead we instruct the model to emit JSON only
        and extract the first JSON object from the text.
        """
        text = self.chat(
            system + "\n\nRespond with a single JSON object and nothing else.",
            user,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return _extract_json(text)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise NimError(f"no JSON object in NIM response: {text!r}")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise NimError(f"NIM response is not valid JSON: {e}; text={text!r}") from e
    if not isinstance(obj, dict):
        raise NimError(f"NIM response root is not an object: {type(obj).__name__}")
    return obj
