#!/usr/bin/env python3
"""Extract HCA streams from CRIWARE AWB/AFS2 files and optionally transcode them.

This targets AWB files found in the supported Android APK. The audio payload is
stored as HCA inside an AFS2 container; ffmpeg can decode the extracted HCA stream
but does not understand this AWB container directly.

Important: encrypted AWB files also need the internal AWB/HCA subkey.
This script's ffmpeg transcode path does not recover that subkey, so encrypted
outputs may sound wrong even when --hca-key is provided. Use vgmstream with a
.hcakey companion file for final decrypted audio.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path


def read_int_le(data: bytes, offset: int, size: int) -> int:
    if size == 1:
        return data[offset]
    if size == 2:
        return struct.unpack_from("<H", data, offset)[0]
    if size == 4:
        return struct.unpack_from("<I", data, offset)[0]
    if size == 8:
        return struct.unpack_from("<Q", data, offset)[0]
    raise ValueError(f"unsupported integer size: {size}")


def align_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    return (value + alignment - 1) // alignment * alignment


def parse_afs2(data: bytes) -> list[tuple[int, int, int]]:
    if data[:4] != b"AFS2":
        raise ValueError("not an AFS2/AWB file")

    offset_size = data[5]
    id_size = data[6]
    file_count = struct.unpack_from("<I", data, 8)[0]
    alignment = struct.unpack_from("<I", data, 12)[0]

    if file_count <= 0:
        raise ValueError("AFS2 has no entries")
    if offset_size not in (1, 2, 4, 8):
        raise ValueError(f"unsupported AFS2 offset size: {offset_size}")
    if id_size not in (0, 1, 2, 4, 8):
        raise ValueError(f"unsupported AFS2 id size: {id_size}")

    table = 16
    ids: list[int] = []
    for index in range(file_count):
        if id_size:
            ids.append(read_int_le(data, table + index * id_size, id_size))
        else:
            ids.append(index)

    offsets_start = table + file_count * id_size
    offsets = [
        read_int_le(data, offsets_start + index * offset_size, offset_size)
        for index in range(file_count + 1)
    ]

    entries: list[tuple[int, int, int]] = []
    for index in range(file_count):
        start = align_up(offsets[index], alignment)
        end = offsets[index + 1]
        if end == 0:
            end = len(data)
        if start < 0 or end > len(data) or start >= end:
            raise ValueError(
                f"invalid AFS2 entry {index}: start=0x{start:x}, end=0x{end:x}, size=0x{len(data):x}"
            )
        entries.append((ids[index], start, end))

    return entries


def parse_key(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value, 0)


def transcode(ffmpeg: str, hca_path: Path, output_path: Path, fmt: str, overwrite: bool, hca_key: int | None) -> None:
    if output_path.exists() and not overwrite:
        return

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if overwrite:
        cmd.append("-y")
    else:
        cmd.append("-n")
    if hca_key is not None:
        cmd += [
            "-hca_highkey",
            str((hca_key >> 32) & 0xFFFFFFFF),
            "-hca_lowkey",
            str(hca_key & 0xFFFFFFFF),
        ]
    cmd += ["-f", "hca", "-i", str(hca_path)]

    if fmt == "mp3":
        cmd += ["-codec:a", "libmp3lame", "-q:a", "2"]
    elif fmt == "wav":
        cmd += ["-codec:a", "pcm_s16le"]
    else:
        raise ValueError(f"unsupported output format: {fmt}")

    cmd.append(str(output_path))
    subprocess.run(cmd, check=True)


def convert_bytes(
    input_name: str,
    data: bytes,
    output_dir: Path,
    fmt: str,
    ffmpeg: str,
    keep_hca: bool,
    overwrite: bool,
    hca_key: int | None,
) -> None:
    entries = parse_afs2(data)
    stem = Path(input_name).stem
    song_dir = output_dir / stem
    song_dir.mkdir(parents=True, exist_ok=True)

    print(f"{input_name}: {len(entries)} stream(s)")
    for index, (entry_id, start, end) in enumerate(entries):
        suffix = f"{index:03d}_id{entry_id}"
        hca_path = song_dir / f"{stem}_{suffix}.hca"
        if overwrite or not hca_path.exists():
            hca_path.write_bytes(data[start:end])

        if fmt != "hca":
            output_path = song_dir / f"{stem}_{suffix}.{fmt}"
            transcode(ffmpeg, hca_path, output_path, fmt, overwrite, hca_key)
            print(f"  entry {index}: 0x{start:x}-0x{end:x} -> {output_path}")
        else:
            print(f"  entry {index}: 0x{start:x}-0x{end:x} -> {hca_path}")

        if fmt != "hca" and not keep_hca:
            hca_path.unlink(missing_ok=True)


def convert_one(
    input_path: Path,
    output_dir: Path,
    fmt: str,
    ffmpeg: str,
    keep_hca: bool,
    overwrite: bool,
    hca_key: int | None,
) -> None:
    convert_bytes(str(input_path), input_path.read_bytes(), output_dir, fmt, ffmpeg, keep_hca, overwrite, hca_key)


def matches_pattern(name: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return fnmatch.fnmatch(Path(name).name.lower(), pattern.lower())


def expand_inputs(paths: list[Path], pattern: str | None) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if path.is_dir():
            expanded.extend(item for item in sorted(path.glob("*.awb")) if matches_pattern(item.name, pattern))
        else:
            expanded.append(path)
    return expanded


def iter_awb_from_archive(path: Path, pattern: str | None):
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            name for name in archive.namelist()
            if name.lower().startswith("assets/audio/") and name.lower().endswith(".awb")
            and matches_pattern(name, pattern)
        )
        if not names:
            raise ValueError("archive contains no assets/Audio/*.awb files")
        for name in names:
            yield name, archive.read(name)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract/decode HCA streams from CRIWARE AWB files. For encrypted AWB audio, prefer vgmstream."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="AWB file(s), directories containing .awb files, or APK/ZIP archives",
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("converted-audio"))
    parser.add_argument("--format", choices=("hca", "wav", "mp3"), default="wav")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--keep-hca", action="store_true", help="keep extracted .hca when transcoding")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--hca-key", help="CRI HCA key as decimal or 0x-prefixed hex")
    parser.add_argument("--pattern", help="glob filter for AWB file names, for example AS_1XE.awb or AS_*.awb")
    parser.add_argument("--limit", type=int, help="maximum number of AWB files to convert")
    args = parser.parse_args()

    inputs = expand_inputs(args.inputs, args.pattern)
    if not inputs:
        print("no AWB files found", file=sys.stderr)
        return 2

    if args.format != "hca" and not shutil.which(args.ffmpeg) and not Path(args.ffmpeg).exists():
        print(f"ffmpeg not found: {args.ffmpeg}", file=sys.stderr)
        return 2

    hca_key = parse_key(args.hca_key)
    failures = 0
    converted_files = 0
    for input_path in inputs:
        try:
            if input_path.suffix.lower() in (".apk", ".zip"):
                for member_name, member_data in iter_awb_from_archive(input_path, args.pattern):
                    convert_bytes(
                        member_name,
                        member_data,
                        args.output_dir,
                        args.format,
                        args.ffmpeg,
                        args.keep_hca,
                        args.overwrite,
                        hca_key,
                    )
                    converted_files += 1
                    if args.limit is not None and converted_files >= args.limit:
                        return 0 if failures == 0 else 1
            else:
                convert_one(input_path, args.output_dir, args.format, args.ffmpeg, args.keep_hca, args.overwrite, hca_key)
                converted_files += 1
                if args.limit is not None and converted_files >= args.limit:
                    return 0 if failures == 0 else 1
        except Exception as exc:
            failures += 1
            print(f"ERROR: {input_path}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
