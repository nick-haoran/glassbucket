#!/usr/bin/env python3
"""Compare broad spectral-band energy between two audio files."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np


def decode(path: Path, seconds: float | None = None) -> tuple[np.ndarray, int]:
    sr = 48000
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path)]
    if seconds is not None:
        cmd += ["-t", str(seconds)]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    raw = subprocess.check_output(cmd)
    return np.frombuffer(raw, dtype=np.float32), sr


def band_power(audio: np.ndarray, sr: int, bands: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    n = 4096
    hop = 2048
    window = np.hanning(n).astype(np.float32)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    totals = np.zeros(len(bands), dtype=np.float64)
    spectrum_acc = np.zeros_like(freqs, dtype=np.float64)
    frames = 0
    for start in range(0, max(0, len(audio) - n), hop):
        frame = audio[start:start + n] * window
        power = np.abs(np.fft.rfft(frame)) ** 2
        spectrum_acc += power
        for i, (lo, hi) in enumerate(bands):
            mask = (freqs >= lo) & (freqs < hi)
            totals[i] += float(power[mask].sum())
        frames += 1
    if frames:
        spectrum_acc /= frames
    return totals, spectrum_acc


def spectral_centroid(spec: np.ndarray, sr: int) -> float:
    freqs = np.fft.rfftfreq((len(spec) - 1) * 2, 1.0 / sr)
    denom = spec.sum()
    return float((freqs * spec).sum() / denom) if denom else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--seconds", type=float, default=None)
    args = parser.parse_args()

    bands = [(0, 200), (200, 2000), (2000, 8000), (8000, 20000)]
    names = ["0-200", "200-2k", "2k-8k", "8k-20k"]
    ref, sr = decode(args.reference, args.seconds)
    cand, _ = decode(args.candidate, args.seconds)
    length = min(len(ref), len(cand))
    ref = ref[:length]
    cand = cand[:length]

    ref_bands, ref_spec = band_power(ref, sr, bands)
    cand_bands, cand_spec = band_power(cand, sr, bands)
    ref_total = ref_bands.sum()
    cand_total = cand_bands.sum()
    print(f"duration_used={length / sr:.2f}s sample_rate={sr}")
    print("band        reference    candidate    delta_pp")
    for name, r, c in zip(names, ref_bands / ref_total, cand_bands / cand_total):
        print(f"{name:8} {r*100:10.3f}% {c*100:10.3f}% {(c-r)*100:9.3f}")

    eps = 1e-12
    log_ref = np.log(ref_spec + eps)
    log_cand = np.log(cand_spec + eps)
    corr = float(np.corrcoef(log_ref, log_cand)[0, 1])
    print(f"centroid_reference={spectral_centroid(ref_spec, sr):.1f}Hz")
    print(f"centroid_candidate={spectral_centroid(cand_spec, sr):.1f}Hz")
    print(f"log_spectrum_correlation={corr:.6f}")
    print(f"rms_reference={np.sqrt(np.mean(ref*ref)):.6f}")
    print(f"rms_candidate={np.sqrt(np.mean(cand*cand)):.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
