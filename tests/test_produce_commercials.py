"""Tests for produce_commercials' pure pieces.

The ffmpeg mixing is an integration concern (we'd need real audio files
to verify output sanity). Here we test:
  - load_beds skips broken entries cleanly
  - pick_bed prefers mood-matching beds, falls back when none match
  - bed selection is deterministic on commercial.id (re-runs stable)
"""

from __future__ import annotations

import json
from pathlib import Path

from rcr.jennifer.commercials import Commercial
from rcr.tools.produce_commercials import (
    BedInfo,
    _stable_index,
    load_beds,
    pick_bed,
)


def make_commercial(id: str, bed_mood: str = "noir") -> Commercial:
    return Commercial(
        id=id, category="business", character="Marlowe",
        voice_id="JBFqnCBsd6RMkjVDRZzb", bed_mood=bed_mood,
        text="Welcome to Test Cafe.",
    )


def write_bed(beds_dir: Path, filename: str, moods: list[str]) -> None:
    beds_dir.mkdir(exist_ok=True)
    (beds_dir / f"{filename}.mp3").write_bytes(b"fake-mp3")
    (beds_dir / f"{filename}.json").write_text(json.dumps({
        "title": filename,
        "artist": "Test Artist",
        "moods": moods,
        "attribution": f'"{filename}" Test (CC BY 4.0)',
    }))


# ---------------------------------------------------------------------------
# load_beds
# ---------------------------------------------------------------------------

def test_load_beds_returns_empty_when_dir_missing(tmp_path):
    assert load_beds(tmp_path / "nope") == []


def test_load_beds_returns_empty_when_dir_empty(tmp_path):
    (tmp_path / "beds").mkdir()
    assert load_beds(tmp_path / "beds") == []


def test_load_beds_reads_sidecars(tmp_path):
    beds_dir = tmp_path / "beds"
    write_bed(beds_dir, "Bed One", ["noir", "jazzy"])
    write_bed(beds_dir, "Bed Two", ["lounge"])
    beds = load_beds(beds_dir)
    assert len(beds) == 2
    titles = sorted(b.title for b in beds)
    assert titles == ["Bed One", "Bed Two"]


def test_load_beds_skips_empty_mp3(tmp_path):
    """Half-baked mp3 (0 bytes) treated as not-available."""
    beds_dir = tmp_path / "beds"
    beds_dir.mkdir()
    (beds_dir / "Empty.mp3").write_bytes(b"")
    (beds_dir / "Empty.json").write_text(json.dumps({
        "title": "Empty", "artist": "X", "moods": ["noir"], "attribution": "",
    }))
    write_bed(beds_dir, "Good", ["noir"])
    beds = load_beds(beds_dir)
    assert [b.title for b in beds] == ["Good"]


def test_load_beds_skips_unreadable_sidecar(tmp_path):
    """Malformed JSON sidecar: skip rather than crash the whole tool."""
    beds_dir = tmp_path / "beds"
    beds_dir.mkdir()
    (beds_dir / "Broken.mp3").write_bytes(b"x")
    (beds_dir / "Broken.json").write_text("{not valid json")
    write_bed(beds_dir, "Good", ["noir"])
    beds = load_beds(beds_dir)
    assert [b.title for b in beds] == ["Good"]


# ---------------------------------------------------------------------------
# pick_bed
# ---------------------------------------------------------------------------

def test_pick_bed_returns_none_when_no_beds():
    assert pick_bed(make_commercial("x"), []) is None


def test_pick_bed_prefers_mood_matching():
    """When a bed matches the mood, it should be picked over non-matching."""
    matching = BedInfo(Path("a.mp3"), "Match", "A", ("noir",), "")
    nonmatching = BedInfo(Path("b.mp3"), "Other", "B", ("lounge",), "")
    c = make_commercial("biz_001", bed_mood="noir")
    pick = pick_bed(c, [matching, nonmatching])
    assert pick is matching


def test_pick_bed_picks_one_of_multiple_mood_matches():
    """Multiple beds match — pick must be one of them (not the fallback)."""
    a = BedInfo(Path("a.mp3"), "A", "X", ("noir",), "")
    b = BedInfo(Path("b.mp3"), "B", "X", ("noir",), "")
    c = BedInfo(Path("c.mp3"), "C", "X", ("lounge",), "")
    pick = pick_bed(make_commercial("x", "noir"), [a, b, c])
    assert pick in (a, b)


def test_pick_bed_falls_back_when_no_mood_match():
    """No bed has the requested mood — return any bed rather than None."""
    a = BedInfo(Path("a.mp3"), "A", "X", ("warm",), "")
    b = BedInfo(Path("b.mp3"), "B", "X", ("lounge",), "")
    pick = pick_bed(make_commercial("x", bed_mood="cult-summoning"), [a, b])
    assert pick in (a, b)


def test_pick_bed_is_deterministic_on_commercial_id():
    """Same commercial id + same bed list → always the same bed.

    Cache friendly + means re-runs don't produce different mixes for
    the same commercial after the bed library grows."""
    beds = [
        BedInfo(Path(f"{i}.mp3"), f"Bed {i}", "X", ("noir",), "")
        for i in range(8)
    ]
    c = make_commercial("biz_042", "noir")
    first = pick_bed(c, beds)
    for _ in range(20):
        assert pick_bed(c, beds) is first


def test_pick_bed_different_ids_different_picks():
    """Across many commercials we should see at least 2 distinct beds —
    otherwise the deterministic hash is degenerate."""
    beds = [
        BedInfo(Path(f"{i}.mp3"), f"Bed {i}", "X", ("noir",), "")
        for i in range(4)
    ]
    seen = set()
    for i in range(20):
        c = make_commercial(f"biz_{i:03d}", "noir")
        seen.add(pick_bed(c, beds))
    assert len(seen) > 1


# ---------------------------------------------------------------------------
# _stable_index
# ---------------------------------------------------------------------------

def test_stable_index_within_range():
    for key in ("a", "biz_001", "psa_042"):
        for mod in (1, 4, 17, 100):
            assert 0 <= _stable_index(key, mod) < mod


def test_stable_index_is_deterministic():
    assert _stable_index("foo", 10) == _stable_index("foo", 10)


def test_stable_index_varies_by_key():
    samples = {_stable_index(f"k{i}", 100) for i in range(50)}
    assert len(samples) > 5  # not pathologically clustered
