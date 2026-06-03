#!/usr/bin/env python3
"""Probe candidate ffmpeg HCA keys with a simple spectral flatness metric."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

import numpy as np


INPUT = Path("extract-min/assets/Audio/AS_1XE.entry0.hcaish")
SR = 48000
SECONDS = 12


def decode(name: str, key: int | None) -> np.ndarray:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if key is not None:
        cmd += [
            "-hca_highkey",
            str((key >> 32) & 0xFFFFFFFF),
            "-hca_lowkey",
            str(key & 0xFFFFFFFF),
        ]
    cmd += [
        "-f",
        "hca",
        "-i",
        str(INPUT),
        "-t",
        str(SECONDS),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-",
    ]
    raw = subprocess.check_output(cmd)
    audio = np.frombuffer(raw, dtype=np.float32)
    if audio.size % 2:
        audio = audio[:-1]
    return audio.reshape(-1, 2).mean(axis=1)


def spectral_flatness(audio: np.ndarray) -> float:
    n = 2048
    hop = 1024
    window = np.hanning(n)
    values = []
    eps = 1e-12
    for start in range(0, len(audio) - n, hop):
        frame = audio[start:start + n] * window
        mag = np.abs(np.fft.rfft(frame))[1:] + eps
        values.append(math.exp(np.mean(np.log(mag))) / np.mean(mag))
    return float(np.mean(values))


def zero_cross_rate(audio: np.ndarray) -> float:
    return float(np.mean(np.signbit(audio[1:]) != np.signbit(audio[:-1])))


def main() -> int:
    raw = int("10029784319315621076")
    mask = 0x00D47EB533AEF7E5
    candidates = {
        "none": None,
        "raw": raw,
        "raw56": raw & 0x00FFFFFFFFFFFFFF,
        "xor": raw ^ mask,
        "xor56": (raw ^ mask) & 0x00FFFFFFFFFFFFFF,
        "mask": mask,
        "mask56": mask & 0x00FFFFFFFFFFFFFF,
    }

    for name, key in candidates.items():
        audio = decode(name, key)
        rms = float(np.sqrt(np.mean(audio * audio)))
        peak = float(np.max(np.abs(audio)))
        flat = spectral_flatness(audio)
        zcr = zero_cross_rate(audio)
        key_text = "none" if key is None else f"0x{key:016x}"
        print(f"{name:7} key={key_text} flatness={flat:.4f} zcr={zcr:.4f} rms={rms:.4f} peak={peak:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
