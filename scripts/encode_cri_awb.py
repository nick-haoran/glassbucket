#!/usr/bin/env python3
"""Encode a 16-bit WAV into encrypted HCA and package it as an AWB.

The generated AWB is meant for replacement-slot workflows: inherit container
parameters from an existing AWB and pair it with an existing ACB template.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from PyCriCodecsEx.awb import AWB, AWBBuilder
from PyCriCodecsEx.chunk import CriHcaQuality
from PyCriCodecsEx.hca import HCACodec


DEFAULT_HCA_KEY = 10029784319315621076

QUALITY_MAP = {
    "highest": CriHcaQuality.Highest,
    "high": CriHcaQuality.High,
    "middle": CriHcaQuality.Middle,
    "low": CriHcaQuality.Low,
    "lowest": CriHcaQuality.Lowest,
}


def write_hcakey(path: Path, key: int) -> None:
    path.write_text(str(key), encoding="ascii", newline="")


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Encode WAV to encrypted HCA/AWB using an existing AWB/ACB as templates."
    )
    parser.add_argument("--wav", type=Path, required=True, help="Input 16-bit PCM WAV")
    parser.add_argument("--template-awb", type=Path, required=True, help="AWB whose container parameters are reused")
    parser.add_argument("--template-acb", type=Path, required=True, help="ACB copied to the output path")
    parser.add_argument("--out-awb", type=Path, required=True, help="Output AWB path")
    parser.add_argument("--out-acb", type=Path, required=True, help="Output ACB path")
    parser.add_argument("--out-hca", type=Path, help="Optional output HCA path")
    parser.add_argument(
        "--key",
        type=int,
        default=DEFAULT_HCA_KEY,
        help="CRI HCA keycode as a 64-bit integer",
    )
    parser.add_argument("--quality", choices=sorted(QUALITY_MAP), default="high")
    parser.add_argument("--write-hcakey", action="store_true", help="Write .hcakey files next to the output AWB for vgmstream verification")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = build_argparser().parse_args(argv)
    for path_arg in ("wav", "template_awb", "template_acb"):
        path = getattr(args, path_arg)
        if not path.is_file():
            raise SystemExit(f"Missing file: {path}")

    args.out_awb.parent.mkdir(parents=True, exist_ok=True)
    args.out_acb.parent.mkdir(parents=True, exist_ok=True)
    if args.out_hca:
        args.out_hca.parent.mkdir(parents=True, exist_ok=True)

    template = AWB(str(args.template_awb))
    codec = HCACodec(
        str(args.wav),
        filename=args.out_awb.with_suffix(".hca").name,
        quality=QUALITY_MAP[args.quality],
        key=args.key,
        subkey=template.subkey,
    )
    hca_bytes = codec.get_encoded()
    awb_bytes = AWBBuilder(
        [hca_bytes],
        subkey=template.subkey,
        version=template.version,
        id_intsize=template.id_intsize,
        align=template.align,
    ).build()

    args.out_awb.write_bytes(awb_bytes)
    args.out_acb.write_bytes(args.template_acb.read_bytes())
    if args.out_hca:
        args.out_hca.write_bytes(hca_bytes)

    if args.write_hcakey:
        write_hcakey(args.out_awb.parent / ".hcakey", args.key)
        write_hcakey(args.out_awb.with_suffix(".hcakey"), args.key)
        write_hcakey(args.out_awb.with_suffix(args.out_awb.suffix + ".hcakey"), args.key)

    print(f"wrote {args.out_awb} ({len(awb_bytes)} bytes)")
    print(f"wrote {args.out_acb} ({args.out_acb.stat().st_size} bytes)")
    print(f"channels={codec.chnls} sample_rate={codec.sampling_rate} hca_bytes={len(hca_bytes)}")
    print(
        f"template_awb version={template.version} align={template.align} "
        f"id_intsize={template.id_intsize} subkey={template.subkey}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
