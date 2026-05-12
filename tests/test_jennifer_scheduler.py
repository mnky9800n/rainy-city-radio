"""Tests for the Jennifer scheduler's *pure* picker.

The async run loop is not unit-tested (touches wall-clock and the voice FIFO);
we verify category-bias-by-hour and the empty-library fallback here, which is
where the actual behavior lives.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from random import Random

from rcr.jennifer.scheduler import available_spots, pick_spot
from rcr.jennifer.spots import SPOTS, category_for_hour


def fake_bake(tmp_path: Path, *ids: str) -> Path:
    spots_dir = tmp_path / "spots"
    spots_dir.mkdir()
    for i in ids:
        (spots_dir / f"{i}.mp3").write_bytes(b"fake")
    return spots_dir


def test_available_spots_lists_only_existing(tmp_path):
    spots_dir = fake_bake(tmp_path, "station_01", "patter_01")
    avail = available_spots(spots_dir)
    assert set(avail.keys()) == {"station_01", "patter_01"}


def test_available_spots_skips_empty_files(tmp_path):
    spots_dir = tmp_path / "spots"
    spots_dir.mkdir()
    (spots_dir / "station_01.mp3").write_bytes(b"")  # empty = not baked
    (spots_dir / "patter_01.mp3").write_bytes(b"x")
    avail = available_spots(spots_dir)
    assert set(avail.keys()) == {"patter_01"}


def test_pick_spot_returns_none_when_empty(tmp_path):
    avail = available_spots(tmp_path)
    assert pick_spot(avail, hour=12, rng=Random(0)) is None


def test_pick_spot_only_returns_baked_ids(tmp_path):
    spots_dir = fake_bake(tmp_path, "station_01", "lore_dawn_01")
    avail = available_spots(spots_dir)
    rng = Random(0)
    for _ in range(50):
        s = pick_spot(avail, hour=6, rng=rng)
        assert s is not None
        assert s.id in {"station_01", "lore_dawn_01"}


def test_pick_spot_respects_time_of_day_bucket(tmp_path):
    """At 2 a.m., a baked `lore_late_night` should appear noticeably; the
    `lore_dawn` spot should never appear because the dawn bucket has weight 0
    at that hour and isn't even listed as a candidate category."""
    spots_dir = fake_bake(tmp_path, "lore_late_01", "lore_dawn_01")
    avail = available_spots(spots_dir)
    counts = Counter()
    rng = Random(42)
    for _ in range(500):
        s = pick_spot(avail, hour=2, rng=rng)
        counts[s.id] += 1
    assert counts["lore_late_01"] > 0
    assert counts["lore_dawn_01"] == 0


def test_category_for_hour_boundaries():
    assert category_for_hour(0) == "lore_late_night"
    assert category_for_hour(3) == "lore_late_night"
    assert category_for_hour(4) == "lore_dawn"
    assert category_for_hour(7) == "lore_dawn"
    assert category_for_hour(8) == "lore_day"
    assert category_for_hour(16) == "lore_day"
    assert category_for_hour(17) == "lore_dusk"
    assert category_for_hour(21) == "lore_dusk"
    assert category_for_hour(22) == "lore_late_night"
    assert category_for_hour(23) == "lore_late_night"


def test_all_spot_ids_unique():
    ids = [s.id for s in SPOTS]
    assert len(ids) == len(set(ids))
