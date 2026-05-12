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

## Secrets and per-host state

**Secrets live in `.env` at the repo root, per-host, never committed.** `.gitignore` excludes `.env` along with all generated per-host artifacts (baked voice mp3s, the music library, the rendered bg). Develop on one machine, deploy/run on another (`homebase`); the keys + caches do not travel via git.

Expected env vars (only the ones needed for what you're running):

| Var | Read by | When you need it |
|---|---|---|
| `YOUTUBE_STREAM_KEY` | `rcr.streamer.youtube_target_from_env` | Live (non-dry-run) streaming |
| `NIM_API_KEY` | `rcr.nim.NimClient.from_env` | Offline ingest tagging; later, M4 live scripting |
| `ELEVENLABS_API_KEY` | `rcr.jennifer.voicer.Voicer.from_env` | Baking static spots offline |
| `ELEVENLABS_VOICE_ID` | `rcr.jennifer.voicer.Voicer.from_env` | Baking static spots offline |

Loading them into the shell before running anything that needs them:

```
set -a; source .env; set +a
python -m rcr.main
```

**Per-host state that isn't in git** (any `.gitignore`-excluded directories may be empty after a fresh clone):

- `music/*.mp3` + `music/*.json` — the playable library and its sidecars
- `jennifer/spots/` — pre-baked ElevenLabs mp3s for the static spot pool
- `jennifer/voices/` — disk cache of generated voice lines, sha256-keyed
- `assets/stream_bg.png` — rendered from `static.jpg`

After a fresh clone or pull on a new host, before the streamer will play anything meaningful:

1. Populate `.env` with the keys you need.
2. `python -m rcr.tools.render_bg` — produces `assets/stream_bg.png`.
3. Drop mp3s into `music/` (the watcher in `rcr.tools.ingest_watch` tags them, or run `rcr.tools.ingest_track` per-file).
4. `python -m rcr.tools.generate_spots` — bakes the static spot pool into `jennifer/spots/`. Idempotent (sha256-keyed cache). Pass `--dry-run` to preview without calling ElevenLabs.
5. `python -m rcr.main` — stream.

## Common commands

- `python -m rcr.main` — run the streamer (live to YouTube via `YOUTUBE_STREAM_KEY`).
- `python -m rcr.main --dry-run --duration 30` — write to `out/live_test.flv` instead. Add `--voice-test-tone` to inject a periodic sine into `voice.fifo` so you can hear the sidechain ducking work without real Jennifer audio. Add `--no-jennifer` to disable the spot scheduler (music-only).
- `python -m rcr.tools.render_bg` — regenerate `assets/stream_bg.png` from `static.jpg`.
- `python -m rcr.tools.ingest_track music/foo.mp3` — tag a new track and write its sidecar.
- `python -m rcr.tools.ingest_watch` — watch `music/` and ingest dropped mp3s automatically.
- `python -m rcr.tools.generate_spots` — bake the static Jennifer spot pool into `jennifer/spots/`. `--dry-run` previews without calling ElevenLabs.
- `pytest` — tests.

## See also

- `docs/architecture.md` — human-readable architecture writeup with milestones
- `docs/lore.md` — Jennifer's character/universe bible (system prompt for NIM)
- Sister projects:
  - `github.com/mnky9800n/rainy-city` — rainy-city.com source. Has reusable ambient assets (`rain.mp3`, `thunder.mp3`, `city.mp3`) under `public/`.
  - `github.com/mnky9800n/streets-of-rainy-city` — Jennifer's game. Source of truth for character/lore.
