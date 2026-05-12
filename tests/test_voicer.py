"""Voicer cache behavior — no real ElevenLabs traffic.

The voicer wraps httpx.post; we monkeypatch that to count calls and synthesize
fake mp3 bytes. The point of these tests is to verify that the disk cache
short-circuits identical calls and that changing inputs invalidates correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rcr.jennifer.voicer as voicer_mod
from rcr.jennifer.voicer import Voicer, VoicerError


FAKE_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00fake-mp3-bytes"


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = FAKE_MP3, text: str = ""):
        self.status_code = status
        self.content = content
        self.text = text


@pytest.fixture
def stub_http(monkeypatch):
    calls: list[dict] = []

    def fake_post(url, *, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse()

    monkeypatch.setattr(voicer_mod.httpx, "post", fake_post)
    return calls


def make_voicer(tmp_path: Path) -> Voicer:
    return Voicer(api_key="k", voice_id="v", cache_dir=tmp_path / "voices")


def test_synthesize_writes_mp3_to_cache(tmp_path, stub_http):
    v = make_voicer(tmp_path)
    out = v.synthesize("hello rainy city")
    assert out.exists()
    assert out.read_bytes() == FAKE_MP3
    assert out.parent == tmp_path / "voices"
    assert out.suffix == ".mp3"
    assert len(stub_http) == 1


def test_synthesize_caches_repeat_calls(tmp_path, stub_http):
    v = make_voicer(tmp_path)
    p1 = v.synthesize("same text")
    p2 = v.synthesize("same text")
    assert p1 == p2
    assert len(stub_http) == 1  # second call served from disk


def test_different_text_different_cache_entry(tmp_path, stub_http):
    v = make_voicer(tmp_path)
    a = v.synthesize("line A")
    b = v.synthesize("line B")
    assert a != b
    assert len(stub_http) == 2


def test_different_voice_id_different_cache_entry(tmp_path, stub_http):
    v1 = Voicer(api_key="k", voice_id="voice-1", cache_dir=tmp_path / "voices")
    v2 = Voicer(api_key="k", voice_id="voice-2", cache_dir=tmp_path / "voices")
    p1 = v1.synthesize("same text")
    p2 = v2.synthesize("same text")
    assert p1 != p2
    assert len(stub_http) == 2


def test_partial_write_is_atomic(tmp_path, monkeypatch):
    """If the API call fails after a partial write would have happened, the
    cache must not contain a half-baked .mp3 file that a later run trusts."""
    v = make_voicer(tmp_path)

    def boom(url, **kw):
        return FakeResponse(status=500, content=b"", text="kaboom")

    monkeypatch.setattr(voicer_mod.httpx, "post", boom)
    with pytest.raises(VoicerError):
        v.synthesize("will fail")
    # No leftover .mp3 (or .part) for this text:
    files = list((tmp_path / "voices").glob("*")) if (tmp_path / "voices").exists() else []
    assert files == []


def test_from_env_missing_keys(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    with pytest.raises(VoicerError):
        Voicer.from_env()


def test_from_env_picks_up_both(monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
    v = Voicer.from_env(cache_dir=tmp_path / "voices")
    assert v.api_key == "k"
    assert v.voice_id == "v"
