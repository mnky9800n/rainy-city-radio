"""Curated commercial pool — pre-bake catalog for M4.5 talk-breaks.

Derived from a NIM bulk-generation run (see commercials_generated.py
for the larger raw pool). Rule violations dropped, near-duplicate
openings collapsed, ~8 per category retained. Hand-edit freely.
Re-run rcr.tools.generate_commercial_texts to get fresh raw material;
do NOT regenerate this file mechanically.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CommercialCategory = Literal["business", "psa", "meta", "utility", "rival"]

@dataclass(frozen=True)
class Commercial:
    id: str
    category: CommercialCategory
    character: str
    voice_id: str
    bed_mood: str
    text: str

COMMERCIALS: tuple[Commercial, ...] = (
    # --- business (Marlowe, voice=JBFqnCBsd6RMkjVDRZzb) ---
    Commercial(
        id='business_001',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='warm',
        text="Welcome to Jimmy's Kung-Fu Laundromat on 5th and Wavefront. Come for the clean clothes, stay for the roundhouse kicks. We'll even throw in a free scrub with your wash.",
    ),
    Commercial(
        id='business_002',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='warm',
        text="I'm Marlowe, proprietor of Mrs. Wong's All-Night Dim Sum on Canal Drive. Our secret ingredient? A pinch of love and a dash of canal water. Try the cha siu bao, it's a favorite.",
    ),
    Commercial(
        id='business_003',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='jazzy',
        text='Rainy City Ramen Co. is where the streets meet the steaming bowl. Our chef, Takashi, imports the finest noodles from the islands and serves them up with a side of rainy city soul.',
    ),
    Commercial(
        id='business_004',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='noir',
        text="Vinnie's Vinyl Vault on 6th and Drizzle is your one-stop shop for all things vinyl. We're talking records, tapes, and the occasional lost mixtape. Come for the music, stay for the nostalgia.",
    ),
    Commercial(
        id='business_005',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='lo-fi',
        text="Moonlight Tarot on 7th and Raindrop offers more than just a read. It's a journey through the cosmos, a whispered secret, and a dash of starlight. Come for the answers, stay for the mystery.",
    ),
    Commercial(
        id='business_006',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='warm',
        text="Golden Gear Hardware on 3rd and Tidal is the place to fix, to find, and to build. Our shelves are stocked with the finest hardware and the knowledge of the city's finest craftsmen.",
    ),
    Commercial(
        id='business_007',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='chill',
        text="The Drizzle Bookshop on 2nd and Rainfall is where the stories come alive. Our shelves are stacked with the city's best writers, the rarest finds, and the occasional lost manuscript.",
    ),
    Commercial(
        id='business_008',
        category='business',
        character='Marlowe',
        voice_id='JBFqnCBsd6RMkjVDRZzb',
        bed_mood='warm',
        text="Finley's Fish Market on Industrial and Whalewatch is where the catch of the day meets the city's finest chefs. Our fish is fresh, our people are friendly, and the views are simply whale-tastic.",
    ),
    # --- psa (Rainy-City Public Safety Bureau, voice=EXAVITQu4vr4xnSDxMaL) ---
    Commercial(
        id='psa_001',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='serious',
        text='This is a message from the Rainy-City Public Safety Bureau. Reports have been made of black-hood sightings near the 7th street caverns. If you see anyone in a black hood, do not approach them. Call the canal watch desk to report the location and description. Your safety is our top priority.',
    ),
    Commercial(
        id='psa_002',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='calm',
        text="Pits of hell have been reported opening on several blocks in the city. If you witness one, please remain calm and contact the emergency services number displayed on your phone's lock screen.",
    ),
    Commercial(
        id='psa_003',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='sparse',
        text="We're reminding residents to be cautious near the canal mouth. If you notice any suspicious activity, please report it to the nearest community center.",
    ),
    Commercial(
        id='psa_004',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='neutral',
        text="This is a test of the city's civil defense system. In the event of an emergency, please follow the evacuation routes displayed on your phone's map app.",
    ),
    Commercial(
        id='psa_005',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='serious',
        text="If you've lost a personal item in the city, please contact the Rainy-City Lost and Found at the public library.",
    ),
    Commercial(
        id='psa_006',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='calm',
        text="As a reminder, neighborhood watch groups are a vital part of our city's safety net. If you're interested in joining or starting a group in your area, please contact the community center nearest you.",
    ),
    Commercial(
        id='psa_007',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='serious',
        text="We've received reports of cult symbols being displayed on public buildings. If you see one, please take a photo and report it to the city's graffiti hotline.",
    ),
    Commercial(
        id='psa_008',
        category='psa',
        character='Rainy-City Public Safety Bureau',
        voice_id='EXAVITQu4vr4xnSDxMaL',
        bed_mood='ominous',
        text="In the event of a pit of hell opening, please prioritize your safety and the safety of those around you. If you're in a safe location, please stay there and follow any instructions from emergency services.",
    ),
    # --- meta (Jennifer, voice=cgSgspJ2msm6clMCkdW9) ---
    Commercial(
        id='meta_001',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='playful',
        text="Hi listeners, it's your girl Jennifer. Get ready to kick some cult butt with 'Streets of Rainy-City', the new arcade game where I'm the hero, and the city's the real star. Play it now and help me save my girlfriend!",
    ),
    Commercial(
        id='meta_002',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='99x',
        text="99X merch alert: our limited-edition 'Kung-Fu Queen' t-shirts are flying off the shelves. Get yours before they're gone, and show the city you're on my side!",
    ),
    Commercial(
        id='meta_003',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='atmospheric',
        text="It's your favorite DJ Jennifer, and I'm here to remind you: the city never sleeps. Neither do the Followers of Baal. Stay vigilant, and keep listening to 99X for the latest updates.",
    ),
    Commercial(
        id='meta_004',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='warm',
        text="I've got a shoutout to all my favorite bodegas: Marlowe's on 4th, Whale Tail Coffee on Industrial... you guys are the backbone of this city. Keep shining!",
    ),
    Commercial(
        id='meta_005',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='uplifting',
        text="Okay, listeners, I know it's been a long night, but we're not out of this yet. The Final Sacrifice is still looming, but we've got this. Keep dancing to the beat of 99X, and we'll get through it together!",
    ),
    Commercial(
        id='meta_006',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='warm',
        text="Hi guys, this is DJ Jennifer about Whale Tail Coffee. Their new coffee blend, 'Rainy-Day Roast', is the perfect pick-me-up for a long night of cult-busting. Try it out, and support local business!",
    ),
    Commercial(
        id='meta_007',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='atmospheric',
        text="The clock is ticking, and my pizza is getting cold... but I know we can stop the Followers of Baal before it's too late. Stay tuned to 99X for the latest updates, and let's do this!",
    ),
    Commercial(
        id='meta_008',
        category='meta',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='99x',
        text="99X is proud to partner with the Rainy-City Public Library to bring you 'Storytime in the Canals'. Join us next Saturday for a night of spooky tales and city history. Don't miss it!",
    ),
    # --- utility (Jennifer, voice=cgSgspJ2msm6clMCkdW9) ---
    Commercial(
        id='utility_001',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='rainy',
        text="Current weather: Light drizzle, steady at 2 inches per hour. So it's basically Saturday.",
    ),
    Commercial(
        id='utility_002',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='light',
        text="Be careful driving on 5th Street, folks. It's still flooded from last night's storm. You know, the one where the skies cried and the canals swelled.",
    ),
    Commercial(
        id='utility_003',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='neutral',
        text="Lost and found: a kraken-handled umbrella left at the 99X studio. If you're the owner, please swing by and pick it up. Your neighbors will thank you.",
    ),
    Commercial(
        id='utility_004',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='subtle',
        text="Reminder: the Mega-Arcade on 7th Street closes at 2 AM sharp. Don't say I didn't warn you.",
    ),
    Commercial(
        id='utility_005',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='atmospheric',
        text="Brief news: the canal whales were spotted near the 4th Street bridge today. Just a friendly reminder to not feed them. We know you want to, but please don't.",
    ),
    Commercial(
        id='utility_006',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='light',
        text="Traffic alert: the canal bridge on 3rd Street is closed due to... well, let's just say 'the situation' in the 7th Street caverns. Take a detour, folks.",
    ),
    Commercial(
        id='utility_007',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='neutral',
        text="New addition to the food scene: Noodle Nirvana on 2nd Street. Try their signature 'Rainy-Day Ramen' and let me know what you think.",
    ),
    Commercial(
        id='utility_008',
        category='utility',
        character='Jennifer',
        voice_id='cgSgspJ2msm6clMCkdW9',
        bed_mood='subtle',
        text="Lost and found: a glow-in-the-dark fish left in the studio's aquarium. If you're the owner, please come pick it up. We're not sure what kind of mischief it's getting up to at night.",
    ),
    # --- rival (Vince Vance, voice=N2lVS1w4EtoT3dr4eOWO) ---
    Commercial(
        id='rival_001',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='lounge',
        text='Real adults, you deserve the best. Tune in to 88.3 The Slick, where sophistication meets style. Your taste is elevated on The Vince Vance Hour, every Thursday at 9 PM.',
    ),
    Commercial(
        id='rival_002',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='smooth-jazz',
        text="Smooth jazz, it's the soundtrack of success. 88.3 The Slick brings you the classics, minus the pretentiousness. Join me on Slick Saturdays with Vince, for the ultimate listening experience.",
    ),
    Commercial(
        id='rival_003',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='cheesy',
        text='Are you tired of being treated like a child by other stations? 88.3 The Slick is the grown-up in the room, serving up the finest in adult contemporary music.',
    ),
    Commercial(
        id='rival_004',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='soft-rock',
        text='Take a break from the mundane and indulge in the finer things. 88.3 The Slick is your haven for soft rock, where the good times roll.',
    ),
    Commercial(
        id='rival_005',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='lounge',
        text='Real listeners know that when it comes to a sophisticated evening, 88.3 The Slick is the only choice. Tune in to The Vince Vance Hour, where the conversation is as smooth as the music.',
    ),
    Commercial(
        id='rival_006',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='elevator',
        text="Sick of being lectured on what to listen to? 88.3 The Slick is the one station that gets it, with expertly curated playlists that you'll actually enjoy.",
    ),
    Commercial(
        id='rival_007',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='smooth-jazz',
        text='Elevate your evening with the best in smooth jazz. 88.3 The Slick is your ticket to a world of refined taste, with Slick Saturdays with Vince.',
    ),
    Commercial(
        id='rival_008',
        category='rival',
        character='Vince Vance',
        voice_id='N2lVS1w4EtoT3dr4eOWO',
        bed_mood='cheesy',
        text="Real adults don't settle for bland, formulaic playlists. They demand more. 88.3 The Slick delivers, with The Vince Vance Hour, every Thursday at 9 PM.",
    ),
)
