# rainy-city-radio architecture

A 24/7 internet radio station broadcasting DJ Jennifer to YouTube Live and embedded on rainy-city.com as "99X (99.7FM)". This document is the human-readable design — see `CLAUDE.md` for the version aimed at future Claude Code sessions.

## Goals

1. Jennifer feels like a real working DJ — not a TTS jukebox. Continuous music with announcer breaks, live chat replies, and lore beats from the rainy-city universe.
2. Runs cheaply on a single 1-vCPU DigitalOcean droplet (`homebase`).
3. Stays inside free tiers: NVIDIA NIM for LLM scripting, ElevenLabs for voice (heavy caching to keep usage low).
4. Survives provider outages — when an API hiccups, music keeps playing and Jennifer falls back to pre-recorded filler.

## Non-goals (v1)

- Multiple DJs / talk shows / scheduled programming blocks
- A web dashboard for managing the station
- Listener call-ins, voice chat, or two-way interaction beyond YouTube chat
- Hardware-encoded video, animation, or visualizations beyond the static frame
- Per-track key detection / Camelot harmonic mixing (deferred — Level 1 BPM/energy continuity covers most of the value)

## Constraints

| Resource | Available | Implication |
|---|---|---|
| CPU | 1 vCPU | No live video re-encoding. Audio mixing must happen in ffmpeg, not Python. |
| RAM | 1.9 GB + 2 GB swap | Stream files through ffmpeg, don't buffer large mp3s in Python. |
| Disk | ~13 GB free | Plenty for music + ElevenLabs cache + sister-project ambient assets. |
| Network | DO droplet upstream | Plenty for one ~3 Mbps RTMP stream. |
| LLM | NVIDIA NIM free tier | Stateless ≤500-token calls; cache results; have filler fallback. |
| TTS | ElevenLabs | Pick one voice ID forever; cache every line on disk. |

## High-level design

A single Python `asyncio` process orchestrates four concurrent activities:

1. **Music feeder** — selects the next track per the music programming algorithm, streams its audio bytes into `/tmp/rcr/music.fifo`.
2. **Jennifer scheduler** — decides *when* she should talk (between tracks, periodic spots, on chat events, time-of-day beats), generates her line via NIM (or fetches a static/templated one), gets audio from ElevenLabs (or the cache), and feeds it into `/tmp/rcr/voice.fifo`. Writes silence frames to the same FIFO when she isn't talking.
3. **Chat source** — `pytchat` reads the YouTube live chat for the current broadcast and emits `ChatMessage` events. The Jennifer scheduler subscribes and decides which (if any) deserve a reply.
4. **ffmpeg supervisor** — owns the long-running ffmpeg subprocess that mixes both FIFOs plus the ambient bed and pushes the result to YouTube RTMP. Restarts ffmpeg if it dies.

A separate one-off **ingest tool** runs offline whenever new music is added: BPM detection (`librosa`), energy/mood tagging (NIM), and writes the sidecar `tracks.json` entry.

Another one-off **render tool** generates the 1920×1080 static frame from `static.jpg` (centered art + blurred-stretched background fill).

## Component details

### Streaming pipeline (ffmpeg)

Single long-running process. Approximate command shape:

```
ffmpeg \
  -loop 1 -framerate 2 -i assets/stream_bg.png \
  -f s16le -ar 44100 -ac 2 -i /tmp/rcr/music.fifo \
  -f s16le -ar 44100 -ac 2 -i /tmp/rcr/voice.fifo \
  -stream_loop -1 -i assets/ambient_rain.wav \
  -filter_complex "
    [2:a]asplit=2[voice_main][voice_sc];
    [1:a][voice_sc]sidechaincompress=threshold=0.03:ratio=8:attack=50:release=500[ducked_music];
    [3:a]volume=-25dB[bed];
    [ducked_music][voice_main][bed]amix=inputs=3:duration=first:dropout_transition=0[mix]
  " \
  -map 0:v -map "[mix]" \
  -c:v libx264 -preset ultrafast -tune stillimage -pix_fmt yuv420p -r 2 -g 4 \
  -c:a aac -b:a 128k -ar 44100 -ac 2 \
  -f flv "rtmp://a.rtmp.youtube.com/live2/${YOUTUBE_STREAM_KEY}"
```

(Final filter graph TBD during implementation — sidechain trigger may need to be the un-amixed voice channel.)

### Music programming

Pure function: `select(library, ring_buffer, last_track, arc_state) -> Track`.

- Ring-buffer filter excludes recent N (`N = min(10, max(1, len(library)//3))`).
- BPM/energy continuity: `|bpm - last.bpm| ≤ 15` AND `|energy - last.energy| ≤ 1`. Relax to ±25 BPM if empty; any track if still empty.
- Mood arc: rotates `chill → mid → peak → mid → chill` over ~40-min blocks. Tracks whose `mood` tags match the current arc state get a 2× weight bonus.
- Final pick: weighted random over filtered candidates.

Easy to unit-test — fixture libraries, deterministic RNG seed.

### Jennifer's content classes

| Class | When | LLM call? | TTS call? |
|---|---|---|---|
| Static (station IDs, generic spots) | Periodic | No (pre-generated) | No (cached) |
| Templated (track intros/outros) | Around each track | Optional | Cached on hit, generated on first miss |
| Reactive (chat replies, time-of-day lore) | Live events | Yes | Generated then cached |

### Jennifer's scheduler heuristics (v1)

- **Track intros** every 2-4 tracks, not every track. Avoids over-talking.
- **Station IDs** every ~15 minutes, picking from the static pool.
- **Chat replies**: at most one reply per ~3 minutes. Pick the most "interesting" message (length heuristic + simple novelty filter — don't reply to the same person twice in a row).
- **Lore drops**: scheduled by time-of-day. Late-night = melancholic, dawn = sleepy hopeful, etc.

These are tunable; expect to iterate after listening to the stream for a while.

### Chat source

`ChatSource` ABC with `__aiter__` yielding `ChatMessage`. `PytchatSource` is the v1 impl. A `YouTubeApiSource` is sketched but not built — only built if `pytchat` ever breaks for good.

## Implementation milestones

Each milestone produces something runnable; no big-bang at the end.

### M0 — Skeleton (silent)
- `pyproject.toml`, package layout, `pytest` running.
- `tools/render_bg.py` — produces `assets/stream_bg.png` from `static.jpg`.
- Simplest possible ffmpeg loop streaming the static image + a fixed mp3 to a local file (not YouTube yet). Verifies the streaming math.

**Done when:** you can play the local output file and see the right image with audio.

### M1 — YouTube live, music only
- ffmpeg pushes to a real YouTube live RTMP endpoint.
- `music/feeder.py` writes a single mp3's bytes to `music.fifo`, ffmpeg consumes them.
- Hard-coded playlist (random shuffle, no algorithm yet) cycles through `music/`.
- Ambient rain bed mixed in.

**Done when:** you can watch a real YouTube live broadcast playing your music with ambient rain underneath.

### M2 — Music programming
- Sidecar `tracks.json` schema + loader.
- `tools/ingest_track.py` — librosa BPM + NIM tagging, writes sidecar.
- `music/selector.py` — the pure function described above. Unit tests with fixture libraries.
- Feeder uses the selector instead of random shuffle.

**Done when:** sequential tracks have flowing BPM/energy and the mood arc is observable across a 40-minute listen.

### M3 — Jennifer (static + templated)
- ElevenLabs client with disk cache.
- Generate ~20 static spots (station IDs, generic patter, a few lore drops) at setup.
- Templated track-intro generator ("That was X. Up next, Y.") with cache.
- Jennifer feeder writes voice or silence to `voice.fifo`.
- ffmpeg `sidechaincompress` ducks music when she talks.

**Done when:** you can listen to the live stream and hear Jennifer announce tracks naturally with ducking.

### M4 — Jennifer (reactive)
- NIM scriptwriter for live lines.
- `pytchat` chat source.
- Chat-reply scheduler with rate limit and novelty filter.
- Filler-line fallback when NIM/ElevenLabs fails.

**Done when:** posting in YouTube chat sometimes gets a Jennifer reply, and pulling the network on the API briefly does not break the stream.

### M5 — Hardening
- systemd service, auto-restart on crash.
- Log rotation; metrics (cache hit rate, NIM/ElevenLabs request counts, ffmpeg restarts).
- Health check (is the RTMP push actually reaching YouTube? heartbeat).
- Graceful behavior when YouTube goes offline / stream key rotated / etc.

**Done when:** you can leave the station running for a week without manual intervention.

## Open questions (intentionally deferred)

- **Music sourcing pipeline.** Suno is v1 source. If we eventually want a richer library, we can revisit (curated buys, contributor uploads, etc.). Not blocking.
- **Visual richness.** Static frame is sufficient for v1. Animated/dynamic visuals are deferred and would require an explicit CPU-budget conversation.
- **Multi-host scaling.** Not needed. One droplet, one stream, that's the product.
- **Listener metrics.** YouTube provides analytics out of the box; we don't need to instrument viewer-side ourselves.
