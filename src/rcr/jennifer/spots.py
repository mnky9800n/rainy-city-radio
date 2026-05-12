"""Static spot pool — the lines Jennifer always has on tap.

These are baked once into jennifer/spots/<id>.mp3 (see tools/generate_spots.py)
and replayed forever; the scheduler picks one from the pool by category.

Categories map onto how a working radio station structures filler:
    - station_id: short, frequent, recognizable. "You're listening to 99X."
    - patter:     generic warm-up that works any time of day.
    - lore_*:     time-of-day-coloured vignettes, with the rainy-city universe
                  bleeding in around the edges (the cult, the pizza, the rain).

Tone is locked by docs/lore.md: earnest + tongue-in-cheek pulp horror, never
nihilistic, never breaking the fourth wall.

If you add a spot, run `python -m rcr.tools.generate_spots` to bake it. The
voicer cache is content-addressed, so existing spots aren't re-billed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal[
    "station_id",
    "patter",
    "lore_late_night",
    "lore_dawn",
    "lore_day",
    "lore_dusk",
]


@dataclass(frozen=True)
class Spot:
    id: str
    category: Category
    text: str


SPOTS: tuple[Spot, ...] = (
    # --- Station IDs: short, frequent, recognizable -------------------------
    Spot("station_01", "station_id",
         "You're listening to 99X, 99.7 FM — rainy-city radio. "
         "The city, the rain, and tunes for both."),
    Spot("station_02", "station_id",
         "99X, rainy-city. The only station broadcasting from inside the storm."),
    Spot("station_03", "station_id",
         "This is 99X. I'm Jennifer. "
         "The pizza's getting cold, but the music's just right."),
    Spot("station_04", "station_id",
         "99 point seven, rainy-city radio. You're not alone out there."),
    Spot("station_05", "station_id",
         "99X. We play what we like, and we like what's on."),
    Spot("station_06", "station_id",
         "Rainy-city radio, 99X. Streaming wet, broadcasting warm."),

    # --- Patter: generic, any time of day -----------------------------------
    Spot("patter_01", "patter",
         "Hey — hope the umbrellas are holding up out there. "
         "I'm Jennifer, you're on 99X, "
         "and we're gonna keep the music going as long as the rain does. "
         "Which, you know, is forever."),
    Spot("patter_02", "patter",
         "I love this stretch of the night. "
         "The city gets quiet, the rain gets loud, "
         "and the music gets to be exactly the right size. That's 99X."),
    Spot("patter_03", "patter",
         "Couple of you wrote in about the static last night — yeah, that wasn't us. "
         "Something weird happening near the canals again. "
         "Anyway. Here's more music."),
    Spot("patter_04", "patter",
         "If you've been listening for a while, thanks. "
         "If you just tuned in, welcome. I'm Jennifer, "
         "and this is the part of the night I like best. "
         "99X, rainy-city radio."),
    Spot("patter_05", "patter",
         "Reminder that the bodega on 4th is open all night, "
         "and they will sell you a cup of coffee for less than the price of a song. "
         "Now back to the music."),
    Spot("patter_06", "patter",
         "You're listening to 99X. Take care of each other out there. "
         "The rain is doing its part."),

    # --- Late night: 22:00 - 04:00 ------------------------------------------
    Spot("lore_late_01", "lore_late_night",
         "It's late. The 7th street caverns are quiet tonight, "
         "which is honestly the worst version. "
         "Stay out of there. Put on something warm. I'll keep you company."),
    Spot("lore_late_02", "lore_late_night",
         "Couple of black-hood sightings near the docks again. "
         "If you see one — don't be a hero, just walk the other way. "
         "There's a whole game about being a hero, and I'm in it. "
         "You're in the radio. Stay in the radio."),
    Spot("lore_late_03", "lore_late_night",
         "Three a.m. is my favorite hour. "
         "The cult sleeps, the city breathes, "
         "and the playlist gets a little weirder. 99X."),

    # --- Dawn: 04:00 - 08:00 ------------------------------------------------
    Spot("lore_dawn_01", "lore_dawn",
         "Sun's coming up somewhere behind all this rain. "
         "I always think dawn looks like the city forgiving itself for the night. "
         "Here's something gentle."),
    Spot("lore_dawn_02", "lore_dawn",
         "Made it to morning. Pizza's cold by now, but we're still here. "
         "99X, rainy-city, and the day is yours."),

    # --- Day: 08:00 - 17:00 -------------------------------------------------
    Spot("lore_day_01", "lore_day",
         "Afternoon, rainy-city. Hope the meetings are going okay. "
         "The cult's still around, but so is everybody who's not in it. "
         "Stay on the second list."),
    Spot("lore_day_02", "lore_day",
         "Lunch rush playlist coming up. "
         "If you're in the mega-arcade I'm sorry about the noise — "
         "but I'm also not sorry, because the noise is me."),

    # --- Dusk: 17:00 - 22:00 ------------------------------------------------
    Spot("lore_dusk_01", "lore_dusk",
         "Dusk. The neon's coming on. "
         "Whatever you've been carrying around today, "
         "you can put a little of it down now. That's what I'm here for."),
    Spot("lore_dusk_02", "lore_dusk",
         "Couple hours until the late shift starts. "
         "I'm gonna keep things mid-tempo for a bit — "
         "let the city catch its breath."),
)


def by_id(spot_id: str) -> Spot:
    for s in SPOTS:
        if s.id == spot_id:
            return s
    raise KeyError(spot_id)


def by_category(category: Category) -> tuple[Spot, ...]:
    return tuple(s for s in SPOTS if s.category == category)


def category_for_hour(hour: int) -> Category:
    """Pick the time-of-day lore category for a 24h hour. Anything outside the
    lore-bracketed bands falls back to plain patter via the scheduler's
    weighted pick; this only returns the *lore* category for the hour."""
    if 4 <= hour < 8:
        return "lore_dawn"
    if 8 <= hour < 17:
        return "lore_day"
    if 17 <= hour < 22:
        return "lore_dusk"
    return "lore_late_night"
