"""Tests for the NIM commercial-text generator's pure pieces.

The NIM call itself is networked + non-deterministic; we don't test it here.
We test the validation (cleans up NIM's frequent off-spec returns) and the
output emitter (must produce importable Python).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from rcr.nim import NimError
from rcr.tools.generate_commercial_texts import (
    CATEGORIES,
    _validate_batch,
    emit_python_module,
)


def _import_generated(path: Path, name: str):
    """Import a dynamically-written Python module by file path.

    Registers it in sys.modules first so the file's `@dataclass`
    decorator can resolve the `Literal[...]` annotation via
    `sys.modules[cls.__module__].__dict__`.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


# ---------------------------------------------------------------------------
# _validate_batch
# ---------------------------------------------------------------------------

def test_validate_keeps_valid_items():
    spec = CATEGORIES["A"]
    items = _validate_batch(
        {
            "commercials": [
                {
                    "text": "Whale Bait Cafe on Industrial — open all night, "
                            "free WiFi, possibly haunted.",
                    "bed_mood": "noir",
                },
                {
                    "text": "Marlowe's All-Night Vinyl on 6th and Drizzle. "
                            "If we played it this week, we have it. Probably.",
                    "bed_mood": "jazzy",
                },
            ]
        },
        spec,
    )
    assert len(items) == 2
    assert all("text" in i and "bed_mood" in i for i in items)


def test_validate_drops_short_items():
    spec = CATEGORIES["A"]
    items = _validate_batch(
        {
            "commercials": [
                {"text": "short", "bed_mood": "noir"},
                {"text": "ok-length commercial that is long enough to keep around.",
                 "bed_mood": "noir"},
            ]
        },
        spec,
    )
    assert len(items) == 1


def test_validate_drops_missing_text():
    spec = CATEGORIES["A"]
    items = _validate_batch(
        {"commercials": [{"bed_mood": "noir"}, {"text": "", "bed_mood": "noir"}]},
        spec,
    )
    assert items == []


def test_validate_falls_back_unknown_bed_mood():
    spec = CATEGORIES["A"]
    items = _validate_batch(
        {
            "commercials": [
                {
                    "text": "Long enough text to pass the length check, with a "
                            "bed_mood we don't recognize.",
                    "bed_mood": "psychedelic-funk",  # not in spec.bed_moods
                }
            ]
        },
        spec,
    )
    assert len(items) == 1
    # Falls back to first bed_mood in the spec's vocab.
    assert items[0]["bed_mood"] == spec.bed_moods[0]


def test_validate_raises_on_wrong_root_shape():
    """If NIM returns something other than {'commercials': [...]}, raise."""
    spec = CATEGORIES["A"]
    with pytest.raises(NimError):
        _validate_batch({"items": []}, spec)
    with pytest.raises(NimError):
        _validate_batch({"commercials": "not a list"}, spec)


def test_validate_skips_non_dict_items():
    spec = CATEGORIES["A"]
    items = _validate_batch(
        {
            "commercials": [
                "a string instead of an object",
                {"text": "valid entry that is plenty long enough to keep.",
                 "bed_mood": "noir"},
            ]
        },
        spec,
    )
    assert len(items) == 1


# ---------------------------------------------------------------------------
# emit_python_module
# ---------------------------------------------------------------------------

def test_emit_produces_importable_module(tmp_path):
    out = tmp_path / "generated.py"
    by_cat = {
        "A": [
            {"text": "Whale Bait Cafe on Industrial, terrible coffee, free WiFi.",
             "bed_mood": "noir"},
        ],
        "B": [
            {"text": "This is a message from the Rainy-City Public Safety Bureau. "
                     "Avoid 7th Street after dark.",
             "bed_mood": "serious"},
        ],
    }
    n = emit_python_module(out, by_cat)
    assert n == 2

    mod = _import_generated(out, "test_gen_importable")
    assert hasattr(mod, "COMMERCIALS")
    assert hasattr(mod, "Commercial")
    assert len(mod.COMMERCIALS) == 2
    business, psa = mod.COMMERCIALS
    assert business.category == "business"
    assert business.character == "Marlowe"
    assert business.voice_id == CATEGORIES["A"].voice_id
    assert psa.category == "psa"
    assert psa.character == "Rainy-City Public Safety Bureau"


def test_emit_handles_tricky_text_with_quotes(tmp_path):
    """NIM emits all kinds of awkward strings: trailing double-quotes,
    embedded triple-quotes, single+double mixes. The generated file must
    parse for ALL of them and the text must round-trip exactly."""
    out = tmp_path / "g.py"
    awkward_texts = [
        # Trailing double-quote (the crash this test was added to prevent):
        'A long enough commercial that ends with a quoted phrase. "Music for the soul."',
        # Embedded triple-quote:
        'Long enough text with """embedded triple quotes""" mid-line.',
        # Mixed quote types:
        "Long enough text with both 'single' and \"double\" quotes inline.",
        # Apostrophe + newline:
        "Long enough text with\nan apostrophe in Marlowe's signoff.",
    ]
    by_cat = {
        "A": [{"text": t, "bed_mood": "noir"} for t in awkward_texts],
    }
    emit_python_module(out, by_cat)
    mod = _import_generated(out, "test_gen_awkward")  # SyntaxError = test fails
    assert len(mod.COMMERCIALS) == len(awkward_texts)
    for original, c in zip(awkward_texts, mod.COMMERCIALS):
        assert c.text == original, f"text round-trip mismatch: {c.text!r} != {original!r}"
