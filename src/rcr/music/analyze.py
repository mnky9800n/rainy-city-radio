"""Offline audio analysis via librosa.

Single function: `analyze(mp3) -> AnalysisResult`. Loads the file, computes BPM
+ duration + a single intensity proxy (mean onset strength), returns plain
floats. This is CPU-heavy (a few seconds per minute of audio); keep it strictly
offline at ingest time and never call it on the streaming path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np


@dataclass(frozen=True)
class AnalysisResult:
    bpm: float
    duration: float
    onset_strength: float


def analyze(mp3: Path, sr: int = 22050) -> AnalysisResult:
    # 22050 Hz is plenty for BPM/onset analysis; halving the rate halves the
    # work vs the source 48k. We're not preserving fidelity here, just
    # extracting tempo + intensity features.
    y, sr_actual = librosa.load(str(mp3), sr=sr, mono=True)

    duration = float(librosa.get_duration(y=y, sr=sr_actual))

    tempo, _beats = librosa.beat.beat_track(y=y, sr=sr_actual)
    bpm = float(np.atleast_1d(tempo)[0])

    onset_env = librosa.onset.onset_strength(y=y, sr=sr_actual)
    onset_strength = float(np.mean(onset_env))

    return AnalysisResult(bpm=bpm, duration=duration, onset_strength=onset_strength)
