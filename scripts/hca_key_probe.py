#!/usr/bin/env python3
"""Probe candidate ffmpeg HCA keys with a simple spectral flatness metric."""

from __future__ import annotations

import math
import os
import subprocess
from argparse import ArgumentParser
from pathlib import Path

import numpy as np


DEFAULT_HCA_KEYS = [10029784319315621076]
SR = 48000
SECONDS = 12


def parse_key(raw: str) -> int:
    raw = raw.strip()
    return int(raw, 16) if raw.lower().startswith("0x") else int(raw)


def decode(input_path: Path, key: int | None) -> np.ndarray:
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
        str(input_path),
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


def build_argparser() -> ArgumentParser:
    parser = ArgumentParser(description="Probe candidate HCA keys with ffmpeg.")
    parser.add_argument("--input", type=Path, required=True, help="Input HCA file")
    parser.add_argument(
        "--keys",
        help="Comma-separated decimal or 0x-prefixed candidate keys. Can also use HCA_KEYS.",
    )
    parser.add_argument(
        "--mask",
        type=parse_key,
        help="Optional mask used to add xor/xor56 candidate variants for each key.",
    )
    return parser


def main() -> int:
    args = build_argparser().parse_args()
    raw_keys = args.keys or os.environ.get("HCA_KEYS", "")
    keys = [parse_key(item) for item in raw_keys.split(",") if item.strip()]
    if not keys:
        keys = DEFAULT_HCA_KEYS

    candidates: dict[str, int | None] = {"none": None}
    for index, key in enumerate(keys, start=1):
        prefix = f"key{index}"
        candidates[prefix] = key
        candidates[f"{prefix}_56"] = key & 0x00FFFFFFFFFFFFFF
        if args.mask is not None:
            candidates[f"{prefix}_xor"] = key ^ args.mask
            candidates[f"{prefix}_xor56"] = (key ^ args.mask) & 0x00FFFFFFFFFFFFFF
    if args.mask is not None:
        candidates["mask"] = args.mask
        candidates["mask56"] = args.mask & 0x00FFFFFFFFFFFFFF

    for name, key in candidates.items():
        audio = decode(args.input, key)
        rms = float(np.sqrt(np.mean(audio * audio)))
        peak = float(np.max(np.abs(audio)))
        flat = spectral_flatness(audio)
        zcr = zero_cross_rate(audio)
        key_text = "none" if key is None else f"0x{key:016x}"
        print(f"{name:7} key={key_text} flatness={flat:.4f} zcr={zcr:.4f} rms={rms:.4f} peak={peak:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
