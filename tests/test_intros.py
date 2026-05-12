"""Tests for the intro/outro template module + scheduler pick_baked helper.

Templates are pure (Track) -> str|None functions, so they're trivially
exhaustively testable. The pick_baked_intro_or_outro helper depends on the
filesystem; we drive it with a fake intros dir + seeded RNG.
"""

from __future__ import annotations

from pathlib import Path
from random import Random

import pytest

from rcr.jennifer.intros import (
    ALL_TEMPLATES,
    INTROS,
    OUTROS,
    applicable,
    intro_release,
    intro_simple,
    outro_release,
    outro_simple,
)
from rcr.jennifer.scheduler import pick_baked_intro_or_outro
from rcr.music.tracks import Track


def make_cc_track(name: str = "Domeneko - Rain") -> Track:
    return Track(
        path=Path(f"music/{name}.mp3"),
        bpm=78.0, energy=2, mood=("chill", "melancholy", "rainy"),
        duration=180.0, onset_strength=0.5,
        real_title="Rain", real_artist="Domeneko", release="Noir",
        license="CC BY-NC-ND 3.0",
        attribution='"Rain" by Domeneko, on Noir (Dusted Wax Kingdom, CC BY-NC-ND 3.0)',
    )


def make_suno_track(name: str = "Bridge Street Run") -> Track:
    return Track(
        path=Path(f"music/{name}.mp3"),
        bpm=129.0, energy=4, mood=("chill", "rainy"),
        duration=180.0, onset_strength=0.6,
        fictional_artist="Kaito Yamato",
    )


def make_bare_track() -> Track:
    return Track(
        path=Path("music/Mystery.mp3"),
        bpm=100.0, energy=3, mood=(),
        duration=180.0, onset_strength=0.5,
    )


# ---------------------------------------------------------------------------
# Template purity + field-dependence
# ---------------------------------------------------------------------------

def test_all_template_ids_unique():
    ids = [t.id for t in ALL_TEMPLATES]
    assert len(ids) == len(set(ids))


def test_intros_and_outros_disjoint():
    assert {t.id for t in INTROS}.isdisjoint({t.id for t in OUTROS})


def test_templates_deterministic():
    """Same Track in → same string out across many calls (cache-key contract)."""
    t = make_cc_track()
    for tmpl in ALL_TEMPLATES:
        first = tmpl.render(t)
        for _ in range(10):
            assert tmpl.render(t) == first


def test_release_templates_skip_without_release():
    suno = make_suno_track()  # no release
    assert intro_release(suno) is None
    assert outro_release(suno) is None


def test_release_templates_fire_with_release():
    cc = make_cc_track()
    out = intro_release(cc)
    assert out is not None
    assert "Noir" in out
    assert "Rain" in out
    assert "Domeneko" in out


def test_simple_templates_use_display_artist_fallback():
    """Suno tracks have fictional_artist; templates should use it via display_artist."""
    suno = make_suno_track()
    out = intro_simple(suno)
    assert out is not None
    assert "Kaito Yamato" in out
    assert "Bridge Street Run" in out


def test_simple_templates_use_real_artist_for_cc():
    cc = make_cc_track()
    out = outro_simple(cc)
    assert out is not None
    assert "Domeneko" in out
    assert "Rain" in out


def test_all_templates_skip_when_no_artist():
    """A track with neither real_artist nor fictional_artist has display_artist=None.
    Every template must return None gracefully — never raise."""
    bare = make_bare_track()
    for tmpl in ALL_TEMPLATES:
        out = tmpl.render(bare)
        assert out is None, f"{tmpl.id} produced {out!r} for a bare track"


def test_applicable_filters_correctly():
    cc = make_cc_track()
    suno = make_suno_track()
    bare = make_bare_track()
    # CC track has release + mood + artist → every template applies
    assert len(applicable(cc)) == len(ALL_TEMPLATES)
    # Suno track lacks release → release templates skip
    suno_apps = {t.id for t in applicable(suno)}
    assert "intro_release" not in suno_apps
    assert "outro_release" not in suno_apps
    assert "intro_simple" in suno_apps
    # Bare track → nothing applies
    assert applicable(bare) == ()


def test_applicable_kind_filter():
    cc = make_cc_track()
    intros_only = applicable(cc, "intro")
    assert all(t.kind == "intro" for t in intros_only)
    outros_only = applicable(cc, "outro")
    assert all(t.kind == "outro" for t in outros_only)


# ---------------------------------------------------------------------------
# pick_baked_intro_or_outro
# ---------------------------------------------------------------------------

def fake_bake(tmp_path: Path, track_name: str, template_ids: list[str]) -> Path:
    intros_dir = tmp_path / "track_intros"
    intros_dir.mkdir(exist_ok=True)
    for tid in template_ids:
        (intros_dir / f"{track_name}__{tid}.mp3").write_bytes(b"fake-mp3")
    return intros_dir


def test_pick_baked_returns_none_when_dir_missing(tmp_path):
    t = make_cc_track()
    result = pick_baked_intro_or_outro(t, "intro", tmp_path / "nope", Random(0))
    assert result is None


def test_pick_baked_returns_none_when_no_matches(tmp_path):
    """Track has no baked content yet."""
    t = make_cc_track()
    intros_dir = fake_bake(tmp_path, "Some Other Track", ["intro_simple"])
    assert pick_baked_intro_or_outro(t, "intro", intros_dir, Random(0)) is None


def test_pick_baked_filters_by_kind(tmp_path):
    """Only intros should be picked when kind='intro'."""
    intros_dir = fake_bake(
        tmp_path, "Domeneko - Rain",
        ["intro_simple", "intro_mood", "outro_simple", "outro_mood"],
    )
    t = make_cc_track()
    rng = Random(0)
    # Try many times — should never return an outro file
    for _ in range(50):
        result = pick_baked_intro_or_outro(t, "intro", intros_dir, rng)
        assert result is not None
        assert "__intro_" in result.name


def test_pick_baked_returns_one_of_the_baked_options(tmp_path):
    intros_dir = fake_bake(
        tmp_path, "Domeneko - Rain",
        ["intro_simple", "intro_mood", "intro_vibe"],
    )
    t = make_cc_track()
    seen = set()
    rng = Random(0)
    for _ in range(50):
        result = pick_baked_intro_or_outro(t, "intro", intros_dir, rng)
        assert result is not None
        seen.add(result.name)
    # Over 50 picks with 3 options, RNG should hit each at least once
    assert len(seen) == 3


def test_pick_baked_rejects_empty_files(tmp_path):
    """A zero-byte mp3 is treated as not-baked (mid-write or failed download)."""
    intros_dir = tmp_path / "track_intros"
    intros_dir.mkdir()
    (intros_dir / "Domeneko - Rain__intro_simple.mp3").write_bytes(b"")
    (intros_dir / "Domeneko - Rain__intro_mood.mp3").write_bytes(b"x")
    t = make_cc_track()
    rng = Random(0)
    for _ in range(20):
        result = pick_baked_intro_or_outro(t, "intro", intros_dir, rng)
        assert result is not None
        assert "intro_mood" in result.name  # only the non-empty one


def test_pick_baked_invalid_kind_raises():
    with pytest.raises(ValueError):
        pick_baked_intro_or_outro(make_cc_track(), "preview", Path("."), Random(0))
