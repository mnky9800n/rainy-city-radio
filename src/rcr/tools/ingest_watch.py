"""Watch music/ for new mp3s and auto-ingest them.

Drop-folder ingestion: scping or moving an mp3 into `music/` is the entire
workflow for adding music. This daemon detects the new file, waits for it to
stop growing (so it doesn't try to read a half-uploaded file), then runs the
librosa + NIM pipeline and writes the sidecar.

Usage:
    set -a; source .env; set +a
    python -m rcr.tools.ingest_watch
    python -m rcr.tools.ingest_watch --music-dir other/music

By design, this also runs once at startup over every untagged mp3 already in
the directory. That makes a fresh checkout self-healing: drop in N mp3s and
launch the watcher; it tags them all and then waits for new arrivals.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from rcr.music.ingest import ingest
from rcr.music.tracks import load, untagged
from rcr.nim import NimClient, NimError

log = logging.getLogger("rcr.tools.ingest_watch")

# How long the file size must be stable before we consider the upload finished.
STABLE_SECONDS = 3.0
POLL_SECONDS = 1.0


class IngestHandler(FileSystemEventHandler):
    def __init__(self, music_dir: Path, nim: NimClient):
        self.music_dir = music_dir
        self.nim = nim
        self._lock = threading.Lock()
        self._inflight: set[Path] = set()

    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe_handle(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        # `mv foo.mp3 music/` lands here, not on_created, on Linux.
        self._maybe_handle(event, path=getattr(event, "dest_path", None))

    def _maybe_handle(self, event: FileSystemEvent, path: str | None = None) -> None:
        raw = path if path is not None else event.src_path
        if not raw:
            return
        p = Path(raw)
        if event.is_directory or p.suffix.lower() != ".mp3":
            return
        if p.parent.resolve() != self.music_dir.resolve():
            return
        if load(p) is not None:
            return
        with self._lock:
            if p in self._inflight:
                return
            self._inflight.add(p)
        threading.Thread(target=self._ingest_when_stable, args=(p,), daemon=True).start()

    def _ingest_when_stable(self, mp3: Path) -> None:
        try:
            if not _wait_for_stable(mp3):
                log.warning("%s vanished before it stabilised; skipping", mp3.name)
                return
            log.info("ingesting %s", mp3.name)
            try:
                ingest(mp3, nim=self.nim)
            except NimError as e:
                log.error("%s: NIM tagging failed: %s", mp3.name, e)
            except Exception as e:
                log.error("%s: ingest failed: %s", mp3.name, e)
        finally:
            with self._lock:
                self._inflight.discard(mp3)


def _wait_for_stable(mp3: Path) -> bool:
    last_size = -1
    stable_for = 0.0
    while stable_for < STABLE_SECONDS:
        if not mp3.exists():
            return False
        size = mp3.stat().st_size
        if size == last_size and size > 0:
            stable_for += POLL_SECONDS
        else:
            stable_for = 0.0
            last_size = size
        time.sleep(POLL_SECONDS)
    return True


def _catch_up(music_dir: Path, nim: NimClient) -> None:
    pending = untagged(music_dir)
    if not pending:
        return
    log.info("catching up: %d untagged mp3(s) already in %s", len(pending), music_dir)
    for mp3 in pending:
        try:
            ingest(mp3, nim=nim)
        except NimError as e:
            log.error("%s: NIM tagging failed: %s", mp3.name, e)
        except Exception as e:
            log.error("%s: ingest failed: %s", mp3.name, e)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--music-dir", type=Path, default=Path("music"))
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--no-catch-up", action="store_true",
                   help="Skip the startup pass over already-present untagged mp3s.")
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not args.music_dir.is_dir():
        raise SystemExit(f"--music-dir {args.music_dir} is not a directory")

    nim = NimClient.from_env()

    if not args.no_catch_up:
        _catch_up(args.music_dir, nim)

    handler = IngestHandler(args.music_dir, nim)
    observer = Observer()
    observer.schedule(handler, str(args.music_dir), recursive=False)
    observer.start()
    log.info("watching %s — drop mp3s in to auto-ingest. Ctrl-C to stop.",
             args.music_dir)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
