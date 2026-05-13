"""Bulk-generate commercial text via NIM (offline; for human curation).

Writes a draft Python module of `Commercial` entries to
`src/rcr/jennifer/commercials_generated.py`. The user reviews, prunes,
edits, and copies keepers into the curated `commercials.py` (not auto-
maintained — too easy to clobber curated content with regeneration).

Each batch call asks NIM for ~10 commercials of a single category at a
time. 5 calls per category × 5 categories = ~250 generated lines. Each
call is ~1100 tokens (prompt + completion); 25 calls ≈ 27.5K tokens,
which fits comfortably in NIM's free tier.

Why offline NIM not live: commercials are deterministic content — same
intent, same NIM seed, same ElevenLabs voice = same cached audio
forever. Generating live during the stream would burn ElevenLabs
credits unnecessarily.

Usage:
    set -a; source .env; set +a
    uv run python -m rcr.tools.generate_commercial_texts             # all 5 categories
    uv run python -m rcr.tools.generate_commercial_texts --only A     # one category
    uv run python -m rcr.tools.generate_commercial_texts --per-call 5 --batches 2  # smaller run

The output module is overwritten each invocation; back up the prior run
manually if you want to keep it.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rcr.nim import NimClient, NimError

log = logging.getLogger("rcr.generate_commercial_texts")

DEFAULT_OUTPUT = Path("src/rcr/jennifer/commercials_generated.py")
DEFAULT_LORE = Path("docs/lore.md")
DEFAULT_PER_CALL = 10
DEFAULT_BATCHES = 5  # → 50 commercials per category

CategoryKey = Literal["A", "B", "C", "D", "E"]


# ---------------------------------------------------------------------------
# Category → in-universe voice + prompt
# ---------------------------------------------------------------------------
# Voice IDs match the locked roster in
# memory/project_commercials_inverse.md.

@dataclass(frozen=True)
class CategorySpec:
    key: CategoryKey
    name: str                  # the catalog-side category label
    character: str             # in-universe character name
    voice_id: str              # ElevenLabs voice ID
    bed_moods: tuple[str, ...] # suggested bed moods NIM can choose from
    direction: str             # the brief NIM gets


CATEGORIES: dict[CategoryKey, CategorySpec] = {
    "A": CategorySpec(
        key="A",
        name="business",
        character="Marlowe",
        voice_id="JBFqnCBsd6RMkjVDRZzb",  # George
        bed_moods=("noir", "jazzy", "lo-fi", "warm", "chill"),
        direction=(
            "Generate {n} commercials for fake LOCAL BUSINESSES in rainy-city, "
            "voiced by MARLOWE.\n\n"
            "Marlowe is the proprietor archetype — warm, weathered, "
            "'neighborhood character' energy. Different Marlowe reads can be "
            "different businesses, but the SAME warm, taking-his-time tone.\n\n"
            "Plausible rainy-city businesses (invent more in this spirit): "
            "the bodega on 4th, Marlowe's All-Night Vinyl on 6th and Drizzle, "
            "Whale Bait Café on Industrial, all-night dim sum on Canal Drive, "
            "the late-night tarot reader on 7th, hardware stores, ramen "
            "joints, used bookshops, laundromats that double as kung-fu dojos. "
            "Use concrete street names, neighborhood landmarks, weather-soaked "
            "details. Each business has its own name — vary them.\n\n"
            "Each commercial should:\n"
            "- Open with the business name and a location detail\n"
            "- Describe what makes it special in 1-2 sentences (the kind of "
            "  detail a real proprietor would mention — not generic ad copy)\n"
            "- Have a soft tagline / signoff in Marlowe's voice\n"
            "- 30-50 words, ~15-30 seconds when spoken\n"
        ),
    ),
    "B": CategorySpec(
        key="B",
        name="psa",
        character="Rainy-City Public Safety Bureau",
        voice_id="EXAVITQu4vr4xnSDxMaL",  # Sarah
        bed_moods=("serious", "calm", "sparse", "ominous", "neutral"),
        direction=(
            "Generate {n} ANTI-CULT PUBLIC SERVICE ANNOUNCEMENTS for "
            "rainy-city, voiced by the RAINY-CITY PUBLIC SAFETY BUREAU.\n\n"
            "Mature, reassuring, confident — the voice of a real city "
            "institution doing its job in unusual circumstances. Earnest, "
            "never campy. The PSAs treat the Followers of Baal the way a "
            "real municipal bureau would treat any organized threat: "
            "matter-of-factly, with practical advice.\n\n"
            "Topics (vary widely):\n"
            "- Black-hood sightings near the 7th street caverns — what to do\n"
            "- Pits of hell opening on unfamiliar blocks — reporting protocol\n"
            "- Suspicious activity at the canal mouth\n"
            "- Reminders about civil defense, evacuation routes, lost & found\n"
            "- Neighborhood-watch tips, community resilience\n"
            "- Reporting cult symbols on public buildings\n\n"
            "Each PSA should:\n"
            "- Open with 'This is a message from the Rainy-City Public "
            "  Safety Bureau' or similar institutional framing\n"
            "- State the concern matter-of-factly\n"
            "- Give practical advice (specific phone-number-free actions)\n"
            "- Close with a reassurance, signoff, or call-to-attention\n"
            "- 30-60 words, ~20-30 seconds spoken\n"
        ),
    ),
    "C": CategorySpec(
        key="C",
        name="meta",
        character="Jennifer",
        voice_id="cgSgspJ2msm6clMCkdW9",  # Jessica (Jennifer's locked voice)
        bed_moods=("warm", "playful", "atmospheric", "99x", "uplifting"),
        direction=(
            "Generate {n} SELF-REFERENTIAL META commercials for the "
            "rainy-city universe, voiced by JENNIFER herself.\n\n"
            "These are spots Jennifer voices that aren't songs and aren't "
            "regular DJ chatter — they're like in-universe promos: for the "
            "arcade game 'Streets of Rainy-City' (in which she's the "
            "protagonist), 99X merch fantasies (the t-shirt she imagines "
            "existing), upcoming hypothetical events, shoutouts to the "
            "city itself, sponsored-read style ads for fictional local "
            "businesses where she's explicitly endorsing ('hi guys this is "
            "DJ Jennifer about Whale Tail Coffee').\n\n"
            "Tone: warm, wry, earnest about the city, tongue-in-cheek about "
            "the bit. Never mean. Pizza-getting-cold callbacks welcome but "
            "not in every spot.\n\n"
            "Each spot:\n"
            "- Clearly Jennifer's voice (could open with 'hi listeners' or "
            "  similar)\n"
            "- 20-50 words\n"
            "- Self-contained — doesn't reference other commercials\n"
        ),
    ),
    "D": CategorySpec(
        key="D",
        name="utility",
        character="Jennifer",
        voice_id="cgSgspJ2msm6clMCkdW9",  # Jessica
        bed_moods=("atmospheric", "light", "neutral", "rainy", "subtle"),
        direction=(
            "Generate {n} CITY UTILITY / public-radio-style spots, voiced "
            "by JENNIFER.\n\n"
            "These are her wry takes on the kinds of things real radio "
            "stations announce: traffic updates, weather reports, brief "
            "news, lost-and-found, community reminders. All in-universe — "
            "rainy-city weather is mostly rain (always), traffic involves "
            "flooded blocks or canal closures, news involves the cult "
            "situation indirectly.\n\n"
            "Topics:\n"
            "- Weather (it's always raining; vary the kind of rain)\n"
            "- Traffic / bridge / canal closures / cult-related avoidance\n"
            "- Lost-and-found in Jennifer's voice (kraken-handled umbrellas, "
            "  glow-in-the-dark fish, etc.)\n"
            "- Community reminders (bodega hours, when the mega-arcade closes)\n"
            "- Brief news in-universe (a new noodle place opened, the canal "
            "  whales were spotted nearer than usual)\n\n"
            "Each utility spot:\n"
            "- Clearly Jennifer doing her show, not breaking format\n"
            "- 20-40 words\n"
            "- Light, observational, occasionally dry\n"
        ),
    ),
    "E": CategorySpec(
        key="E",
        name="rival",
        character="Vince Vance",
        voice_id="N2lVS1w4EtoT3dr4eOWO",  # Callum
        bed_moods=("smooth-jazz", "lounge", "cheesy", "soft-rock", "elevator"),
        direction=(
            "Generate {n} commercials for the FICTIONAL RIVAL STATION "
            "'88.3 The Slick', voiced by VINCE VANCE.\n\n"
            "Vince is a DJ at 88.3 The Slick. Lounge-lizard / "
            "used-car-salesman energy, never aware of how cheesy he sounds. "
            "Plays it self-serious. These are ads voiced AS Vince — they "
            "sound like he made them himself, possibly poorly.\n\n"
            "Vince's signature moves:\n"
            "- Excessive emphasis on certain WORDS\n"
            "- Imagining 'real listeners' / 'real adults' as his audience\n"
            "- Smooth Jazz worship; refers to 99X with veiled condescension\n"
            "- Implies The Slick is the sophisticated choice\n"
            "- Self-promotes his own shows ('The Vince Vance Hour', "
            "  'Slick Saturdays with Vince')\n\n"
            "Each spot:\n"
            "- Opens or closes with the station tag '88.3 The Slick'\n"
            "- 20-50 words\n"
            "- Played straight — the cheese IS the joke, not the writing\n"
            "- Never directly attacks 99X by name; always 'other stations'\n"
        ),
    ),
}


SYSTEM_PROMPT_TEMPLATE = """\
You are writing radio commercials for 99X, a 24/7 internet radio station \
in the fictional rainy-city universe. Below is the full lore bible. Use it.

{lore}

# Critical rules

- NEVER reference real-world brands, locations, people, or events.
- NEVER break the fourth wall about LLMs, TTS, AI, or that this is generated.
- Stay strictly inside the rainy-city universe. Every place, business, \
person, event is fictional and part of that world.
- Each commercial is a self-contained 15-30 second spot. They don't \
reference each other across spots.
- Match the requested voice character's tone exactly.
- No real songs, real artist names, or real cultural references that \
break immersion.
- Tone reference always applies: earnest aesthetic + tongue-in-cheek \
pulp horror. No nihilism. No cruelty.

You will be asked to produce JSON. Respond with a single JSON object \
containing a 'commercials' array; nothing else.
"""


USER_PROMPT_TEMPLATE = """\
{direction}

Available bed_mood values (pick one per commercial that matches the \
commercial's tone):
{bed_moods}

Respond with a single JSON object:
{{
  "commercials": [
    {{
      "text": "<the spoken text, exactly as the voice should read it>",
      "bed_mood": "<one of the bed_mood values above>"
    }},
    ...
  ]
}}

No markdown, no surrounding prose, no comments — just the JSON object.
"""


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def build_system_prompt(lore_path: Path) -> str:
    lore = lore_path.read_text() if lore_path.exists() else "(lore file missing)"
    return SYSTEM_PROMPT_TEMPLATE.format(lore=lore)


def build_user_prompt(spec: CategorySpec, n_per_call: int) -> str:
    return USER_PROMPT_TEMPLATE.format(
        direction=spec.direction.format(n=n_per_call),
        bed_moods=", ".join(spec.bed_moods),
    )


def generate_batch(
    nim: NimClient, spec: CategorySpec, n_per_call: int, lore_path: Path,
) -> list[dict]:
    """One NIM call → list of {text, bed_mood} dicts (validated)."""
    system = build_system_prompt(lore_path)
    user = build_user_prompt(spec, n_per_call)
    # NIM chat_json appends its own JSON-only instruction; we expect
    # {"commercials": [...]}. max_tokens generous since we're returning N
    # items; ~50 words per item × N + JSON overhead.
    raw = nim.chat_json(
        system, user,
        max_tokens=200 + 80 * n_per_call,
        temperature=0.85,  # higher → more variety across batches
    )
    return _validate_batch(raw, spec)


def _validate_batch(raw: dict, spec: CategorySpec) -> list[dict]:
    items = raw.get("commercials")
    if not isinstance(items, list):
        raise NimError(f"expected 'commercials' array, got {type(items).__name__}: {raw!r}")
    out: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            log.warning("[%s] item %d not a dict: %r — skipping", spec.key, i, item)
            continue
        text = str(item.get("text", "")).strip()
        bed = str(item.get("bed_mood", "")).strip().lower()
        if not text:
            log.warning("[%s] item %d empty text — skipping", spec.key, i)
            continue
        if len(text) < 30:
            log.warning("[%s] item %d too short (%d chars) — skipping", spec.key, i, len(text))
            continue
        if len(text) > 600:
            log.warning("[%s] item %d very long (%d chars) — keeping anyway", spec.key, i, len(text))
        if bed not in spec.bed_moods:
            log.debug("[%s] item %d bed_mood %r not in vocab — falling back to %r",
                      spec.key, i, bed, spec.bed_moods[0])
            bed = spec.bed_moods[0]
        out.append({"text": text, "bed_mood": bed})
    return out


# ---------------------------------------------------------------------------
# Output emission
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    out = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    return out or "x"


def emit_python_module(
    output_path: Path, by_category: dict[CategoryKey, list[dict]],
) -> int:
    """Write the generated commercials to a Python module."""
    lines: list[str] = []
    lines.append('"""Generated commercial texts — DRAFT. Hand-curate before baking.\n')
    lines.append("Generated by `rcr.tools.generate_commercial_texts`. Each run")
    lines.append("OVERWRITES this file. Move curated keepers to a separate")
    lines.append("hand-edited module (likely src/rcr/jennifer/commercials.py).")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from dataclasses import dataclass")
    lines.append("from typing import Literal")
    lines.append("")
    lines.append('CommercialCategory = Literal["business", "psa", "meta", "utility", "rival"]')
    lines.append("")
    lines.append("@dataclass(frozen=True)")
    lines.append("class Commercial:")
    lines.append("    id: str")
    lines.append("    category: CommercialCategory")
    lines.append("    character: str")
    lines.append("    voice_id: str")
    lines.append("    bed_mood: str")
    lines.append("    text: str")
    lines.append("")
    lines.append("COMMERCIALS: tuple[Commercial, ...] = (")
    total = 0
    for key, items in by_category.items():
        spec = CATEGORIES[key]
        lines.append(f"    # --- {spec.name} ({spec.character}, voice={spec.voice_id}) ---")
        for i, item in enumerate(items, 1):
            total += 1
            cid = f"{spec.name}_{i:03d}"
            lines.append("    Commercial(")
            lines.append(f"        id={cid!r},")
            lines.append(f"        category={spec.name!r},")
            lines.append(f"        character={spec.character!r},")
            lines.append(f"        voice_id={spec.voice_id!r},")
            lines.append(f"        bed_mood={item['bed_mood']!r},")
            # repr() is bulletproof against quotes anywhere in the text —
            # NIM happily emits commercials ending in `."` which the
            # earlier triple-quoted wrapping couldn't survive. Less pretty
            # than triple-quotes but unambiguously correct.
            lines.append(f"        text={item['text']!r},")
            lines.append("    ),")
    lines.append(")")
    lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--only", choices=list(CATEGORIES.keys()) + ["all"],
                   default="all", help="One category to generate, or 'all'.")
    p.add_argument("--per-call", type=int, default=DEFAULT_PER_CALL,
                   help="Commercials per NIM call (default 10).")
    p.add_argument("--batches", type=int, default=DEFAULT_BATCHES,
                   help="NIM calls per category (default 5; 5×10=50 per category).")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help="Output Python module (overwritten each run).")
    p.add_argument("--lore", type=Path, default=DEFAULT_LORE)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not args.lore.exists():
        log.warning("lore file %s missing — NIM will get a fallback message", args.lore)

    try:
        nim = NimClient.from_env()
    except NimError as e:
        log.error("%s", e)
        return 2

    target_keys: list[CategoryKey] = (
        list(CATEGORIES.keys()) if args.only == "all" else [args.only]  # type: ignore[list-item]
    )

    by_category: dict[CategoryKey, list[dict]] = {}
    for key in target_keys:
        spec = CATEGORIES[key]
        log.info("=== category %s (%s, voice=%s) ===",
                 spec.key, spec.character, spec.voice_id)
        accumulated: list[dict] = []
        for batch_i in range(args.batches):
            log.info("  batch %d/%d: requesting %d commercials",
                     batch_i + 1, args.batches, args.per_call)
            try:
                items = generate_batch(nim, spec, args.per_call, args.lore)
            except NimError as e:
                log.error("  batch %d failed: %s", batch_i + 1, e)
                continue
            log.info("  batch %d returned %d valid commercials", batch_i + 1, len(items))
            accumulated.extend(items)
        log.info("[%s] total %d commercials accumulated", spec.key, len(accumulated))
        by_category[key] = accumulated

    if not by_category or all(not v for v in by_category.values()):
        log.error("no commercials generated; not writing output")
        return 1

    total = emit_python_module(args.output, by_category)
    log.info("wrote %d commercials to %s", total, args.output)
    log.info("REVIEW THIS FILE BY HAND before merging curated entries into "
             "src/rcr/jennifer/commercials.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
