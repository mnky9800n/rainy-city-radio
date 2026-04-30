"""M0 smoke test: looped PNG + audio source → local .flv file.

Verifies the streaming math (looped still image, AAC audio, FLV container)
without touching YouTube. Pass an mp3 path to use real music; otherwise a sine
wave is synthesized via lavfi for a quick check that ffmpeg + the rendered
background work together.

Usage:
    python -m rcr.tools.smoke_stream                       # 30s sine wave
    python -m rcr.tools.smoke_stream --audio music/foo.mp3 # use a real mp3
    python -m rcr.tools.smoke_stream --duration 10         # shorter clip
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def build_cmd(bg: Path, audio: Path | None, duration: int, out: Path) -> list[str]:
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-framerate", "2",
        "-i", str(bg),
    ]

    if audio is None:
        # Synthesize a 440Hz sine wave so M0 works before any music is dropped in.
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}"]
    else:
        cmd += ["-i", str(audio)]

    cmd += [
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-r", "2",
        "-g", "4",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-shortest",
        "-f", "flv",
        str(out),
    ]
    return cmd


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bg", default="assets/stream_bg.png", type=Path)
    p.add_argument("--audio", type=Path, default=None,
                   help="Path to an mp3. Omit to synthesize a 440Hz sine wave.")
    p.add_argument("--duration", type=int, default=30, help="Output length in seconds.")
    p.add_argument("--out", default="out/smoke.flv", type=Path)
    args = p.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found on PATH")
    if not args.bg.exists():
        sys.exit(f"{args.bg} not found — run `python -m rcr.tools.render_bg` first")
    if args.audio is not None and not args.audio.exists():
        sys.exit(f"{args.audio} not found")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_cmd(args.bg, args.audio, args.duration, args.out)

    print("$", " ".join(cmd))
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        sys.exit(rc)
    print(f"\nwrote {args.out} — play it back to confirm image + audio are aligned")


if __name__ == "__main__":
    main()
