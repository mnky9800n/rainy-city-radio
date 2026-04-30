# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A 24/7 internet radio station broadcasting to YouTube Live and embedded on rainy-city.com as "99X (99.7FM)". The DJ is **Jennifer**, a character voiced by ElevenLabs and scripted by an LLM. Music is mp3s on disk; visuals are a single static image. ffmpeg is the streaming engine.

Jennifer is also the kung-fu hero of the sister project `streets-of-rainy-city` (her girlfriend has been kidnapped by the Followers of Baal). Her DJ persona lives in that universe — see `docs/lore.md` for the bible.

## Production environment

This repo deploys to one host: `homebase`, a DigitalOcean droplet.

- 1 vCPU (no real GPU — virtio only)
- 1.9 GB RAM + 2 GB swap (treat swap as safety net, not headroom)
- ~13 GB free disk after swap
- Already runs other services (rainy-city.com is on the same box) — be a good tenant

The 1 vCPU is the real constraint. **Architecture must avoid live video re-encoding**: the YouTube stream uses a pre-rendered 1920×1080 PNG looped statically (`-loop 1 -tune stillimage`, ~2fps), so x264 cost is near-zero and the CPU goes to audio mixing. Don't propose anything that re-encodes high-res video, runs an in-process ML model, or spins up parallel ffmpegs.

## Architecture

```
        ┌──────────────────────────────────────────────────┐
        │  Python service (asyncio)                        │
        │                                                  │
        │  ┌────────────┐   ┌──────────────┐  ┌─────────┐ │
        │  │ Music      │   │ Jennifer     │  │ Chat    │ │
        │  │ scheduler  │   │ scheduler    │  │ source  │ │
        │  │ (BPM/      │   │ (NIM script  │  │ (pytchat│ │
        │  │  energy/   │   │  → ElevenLabs│  │  → ABC) │ │
        │  │  mood arc) │   │  → cache)    │  │         │ │
        │  └─────┬──────┘   └──────┬───────┘  └────┬────┘ │
        │        │                 │                │     │
        │        ▼                 ▼                │     │
        │   music.fifo         voice.fifo           │     │
        └────────┼─────────────────┼────────────────┼─────┘
                 │                 │                │
                 ▼                 ▼                │
            ┌─────────────────────────────┐         │
            │  ffmpeg                     │◄────────┘
            │  - amix                     │  (chat msgs feed Jennifer)
            │  - sidechaincompress        │
            │  - ambient_rain.wav loop    │
            │  - static.png loop (-c:v)   │
            └─────────────┬───────────────┘
                          │
                          ▼
                  YouTube RTMP ingest
```

**Key invariants:**

1. **One ffmpeg process** does ALL the streaming. Python feeds it via FIFOs and never mixes audio itself.
2. **Two named pipes**, `/tmp/rcr/music.fifo` and `/tmp/rcr/voice.fifo`, are the audio interface. Each producer writes to its own pipe.
3. **Voice silence is explicit.** A silence-generator task writes silence frames to `voice.fifo` whenever Jennifer isn't speaking — ffmpeg blocks otherwise.
4. **Ducking is done by ffmpeg's `sidechaincompress`** (~12dB attenuation, ~50ms attack, ~500ms release), with voice as the sidechain trigger.
5. **Continuous ambient rain bed** at -25dB is mixed in under everything for "rainy city" identity.

## Repo layout (target — does not all exist yet)

```
rainy-city-radio/
├── CLAUDE.md
├── README.md
├── static.jpg                       # source art (714x1280, DJ Jennifer at the decks)
├── assets/
│   ├── stream_bg.png               # pre-rendered 1920x1080, generated from static.jpg
│   └── ambient_rain.wav            # ambient bed
├── music/
│   ├── *.mp3
│   └── tracks.json                 # sidecar metadata (bpm, energy, mood, fictional_artist, ...)
├── jennifer/
│   ├── voices/                     # ElevenLabs cache (sha256-keyed)
│   ├── spots/                      # pre-generated static spots
│   └── lore.md                     # the bible — fed to NIM as system prompt
├── src/rcr/
│   ├── main.py                     # entry point, asyncio orchestrator
│   ├── streamer.py                 # ffmpeg subprocess wrangler
│   ├── music/
│   │   ├── selector.py             # pure: (library, ring_buffer, last, arc) -> next
│   │   └── feeder.py               # writes mp3 bytes to music.fifo
│   ├── jennifer/
│   │   ├── scheduler.py            # decides when she talks
│   │   ├── scriptwriter.py         # NIM client
│   │   ├── voicer.py               # ElevenLabs client + cache
│   │   └── feeder.py               # writes voice bytes to voice.fifo (+ silence)
│   ├── chat/
│   │   ├── source.py               # ChatSource ABC
│   │   ├── pytchat_source.py
│   │   └── youtube_api_source.py   # fallback impl
│   └── tools/
│       ├── render_bg.py            # one-off: static.jpg -> assets/stream_bg.png
│       └── ingest_track.py         # one-off: mp3 -> bpm/energy/mood, writes tracks.json
└── pyproject.toml
```

## Decisions already made (don't re-litigate without reason)

- **Language:** Python (3.11+).
- **Streaming:** ffmpeg, RTMP to YouTube. Static-image video track, ducked audio.
- **Music selection:** weighted shuffle with recent-N filter + BPM/energy continuity (Level 1 harmonic mixing) + soft 40-min mood arc (`chill→mid→peak→mid→chill`). Sidecar `tracks.json` per track.
- **Track tagging:** offline at ingest. BPM via `librosa.beat.beat_track`; energy/mood tags via NIM. One-time CPU spike per track is fine — never during streaming.
- **Jennifer scripting:** NVIDIA NIM free tier (OpenAI-compatible API at `integrate.api.nvidia.com`), Llama 3.x. Stateless calls, ≤500-token prompts, ≤150-token completions. Filler-line fallback when API fails.
- **Jennifer voicing:** ElevenLabs. **One** voice ID, locked stability/similarity/style. Aggressive disk caching keyed on `sha256(text + voice_id + settings)`. Static spots pre-generated; templated lines templated; only genuinely novel lines hit the API live.
- **Chat:** `pytchat` behind a `ChatSource` ABC. Don't let pytchat types leak past the boundary.
- **Visuals:** pre-rendered 1920×1080 PNG (centered art + blurred-stretched bg). Generated once by `tools/render_bg.py`. **No live video work.**

## Constraints to honor

- **Free-tier hygiene.** NIM has a credit cap and rate limits; ElevenLabs charges per character. Cache aggressively, prefer templates over LLM calls, prefer cache hits over fresh generation.
- **Voice consistency.** Never mix voice IDs or change ElevenLabs settings — character drift breaks Jennifer.
- **No live re-encoding.** Visuals stay pre-rendered. Audio mixing is ffmpeg's job, not Python's.
- **Voicing/silence symmetry.** If you write voice audio without surrounding silence frames, ffmpeg will desync. The voice feeder must always be writing — silence or speech.
- **Lore consistency.** Jennifer references the rainy-city universe (Followers of Baal, kung-fu, kidnapped girlfriend, pizza-as-stakes humor). Earnest aesthetic + pulp horror humor. Not gritty, not mean. Don't default to heteronormative framing — Jennifer has a girlfriend, and the universe reflects that.

## Common commands (placeholder)

The Python service does not exist yet. When it does, expected commands:

- `python -m rcr.main` — run the streamer (reads YouTube stream key from env)
- `python -m rcr.tools.render_bg` — regenerate `assets/stream_bg.png` from `static.jpg`
- `python -m rcr.tools.ingest_track music/foo.mp3` — tag a new track and write its sidecar
- `pytest` — tests (selector logic, cache key derivation, ChatSource impls with fixtures)

Update this section as those entrypoints become real.

## See also

- `docs/architecture.md` — human-readable architecture writeup with milestones
- `docs/lore.md` — Jennifer's character/universe bible (system prompt for NIM)
- Sister projects:
  - `github.com/mnky9800n/rainy-city` — rainy-city.com source. Has reusable ambient assets (`rain.mp3`, `thunder.mp3`, `city.mp3`) under `public/`.
  - `github.com/mnky9800n/streets-of-rainy-city` — Jennifer's game. Source of truth for character/lore.
