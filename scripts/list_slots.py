#!/usr/bin/env python3
"""List replacement slot IDs and metadata from a supported APK."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from pathlib import Path
from typing import Optional

import UnityPy


GROUP_BUNDLE = "assets/aa/Android/chartdata-chartgroups_assets_all_5a6f189f2d5a4b412aaccd7d9ef79f9e.bundle"
HEADER_BUNDLE = "assets/aa/Android/chartdata-chartheaders_assets_all_4ead93ce53e5f795d0fc218f71549e5b.bundle"
DIFFICULTIES = ("BASIC", "ADVANCED", "HARD", "EXPERT")


def load_textassets(bundle_bytes: bytes) -> dict[str, str]:
    env = UnityPy.load(bundle_bytes)
    assets: dict[str, str] = {}
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        tree = obj.read_typetree()
        name = tree.get("m_Name")
        if isinstance(name, str):
            assets[name] = tree.get("m_Script", "")
    return assets


def audio_slot_from_group(group: dict) -> Optional[str]:
    audio_id = group.get("RepresentingAudioSourceID")
    if isinstance(audio_id, str) and audio_id.startswith("AS_"):
        return audio_id[3:]
    group_id = group.get("ID")
    if isinstance(group_id, str) and group_id.startswith("TG_"):
        return group_id[3:]
    return None


def build_rows(apk: Path) -> list[dict[str, object]]:
    with zipfile.ZipFile(apk) as zf:
        names = set(zf.namelist())
        groups = load_textassets(zf.read(GROUP_BUNDLE))
        headers = load_textassets(zf.read(HEADER_BUNDLE))

    rows: list[dict[str, object]] = []
    for name, text in sorted(groups.items()):
        if not name.startswith("TG_"):
            continue
        try:
            group = json.loads(text)
        except json.JSONDecodeError:
            continue
        slot = audio_slot_from_group(group)
        if not slot:
            continue

        levels: list[str] = []
        bpms: list[str] = []
        chart_ids = [cid for cid in group.get("ChildTuneIds", []) if isinstance(cid, str)]
        for difficulty in DIFFICULTIES:
            chart_id = f"CH_{slot}_{difficulty}"
            header_text = headers.get(chart_id)
            if not header_text:
                continue
            try:
                header = json.loads(header_text)
            except json.JSONDecodeError:
                continue
            level = header.get("NewDifficultyLevel")
            if level is not None:
                levels.append(f"{difficulty}:{level}")
            bpm = header.get("BPMDisplay")
            if bpm is not None:
                bpms.append(str(bpm))

        acb = f"assets/Audio/AS_{slot}.acb"
        awb = f"assets/Audio/AS_{slot}.awb"
        rows.append(
            {
                "slot": slot,
                "group_id": name,
                "title": group.get("Title") or "",
                "artist": group.get("Artist") or "",
                "genre": group.get("Genre") or "",
                "bpm": "/".join(sorted(set(bpms), key=bpms.index)),
                "levels": ",".join(levels),
                "charts": ",".join(chart_ids),
                "has_acb": acb in names,
                "has_awb": awb in names,
            }
        )
    return rows


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List slot IDs available in an APK.")
    parser.add_argument("--apk", type=Path, required=True, help="Input APK path")
    parser.add_argument("--format", choices=("table", "csv", "json"), default="table")
    parser.add_argument("--filter", help="Case-insensitive filter for slot/title/artist")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_argparser().parse_args(argv)
    rows = build_rows(args.apk)
    if args.filter:
        needle = args.filter.casefold()
        rows = [
            row
            for row in rows
            if needle in str(row["slot"]).casefold()
            or needle in str(row["title"]).casefold()
            or needle in str(row["artist"]).casefold()
        ]

    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    else:
        print(f"{'slot':32} {'title':28} {'artist':24} {'bpm':8} {'levels'}")
        for row in rows:
            print(
                f"{str(row['slot'])[:32]:32} "
                f"{str(row['title'])[:28]:28} "
                f"{str(row['artist'])[:24]:24} "
                f"{str(row['bpm'])[:8]:8} "
                f"{row['levels']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
