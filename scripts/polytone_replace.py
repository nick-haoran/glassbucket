#!/usr/bin/env python3
"""Replace an existing song slot inside an APK.

This is intentionally a replacement-slot tool. It keeps existing slot IDs
such as TG_TARGET_SLOT, CH_TARGET_SLOT_BASIC, and AS_TARGET_SLOT so the
Addressables catalog does not need new entries.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import UnityPy


DIFFICULTIES = ("BASIC", "ADVANCED", "HARD", "EXPERT")

GROUP_BUNDLE = "assets/aa/Android/chartdata-chartgroups_assets_all_5a6f189f2d5a4b412aaccd7d9ef79f9e.bundle"
HEADER_BUNDLE = "assets/aa/Android/chartdata-chartheaders_assets_all_4ead93ce53e5f795d0fc218f71549e5b.bundle"
CHART_BUNDLE = "assets/aa/Android/chartdata-charts_assets_all_f4607ccf7ee5a496b006b55051836f83.bundle"
CATALOG_BIN = "assets/aa/catalog.bin"
DEFAULT_UBER_SIGNER = Path("tools/uber-apk-signer/uber-apk-signer-1.3.0.jar")

ADDRESSABLE_BUNDLES = (GROUP_BUNDLE, HEADER_BUNDLE, CHART_BUNDLE)
CATALOG_BUNDLE_CRC_REL = 68
CATALOG_BUNDLE_SIZE_REL = 72

SIGNATURE_PREFIX = "META-INF/"
SIGNATURE_SUFFIXES = (".RSA", ".DSA", ".EC", ".SF", ".MF")


@dataclass(frozen=True)
class SlotIds:
    slot: str

    @property
    def audio_id(self) -> str:
        return f"AS_{self.slot}"

    @property
    def group_id(self) -> str:
        return f"TG_{self.slot}"

    def chart_id(self, difficulty: str) -> str:
        return f"CH_{self.slot}_{difficulty.upper()}"

    @property
    def acb_zip_path(self) -> str:
        return f"assets/Audio/{self.audio_id}.acb"

    @property
    def awb_zip_path(self) -> str:
        return f"assets/Audio/{self.audio_id}.awb"


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def parse_key_value_path(raw: str, label: str) -> Tuple[str, Path]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"{label} must be DIFFICULTY=path")
    key, value = raw.split("=", 1)
    key = key.strip().upper()
    if key not in DIFFICULTIES:
        raise argparse.ArgumentTypeError(
            f"{label} difficulty must be one of {', '.join(DIFFICULTIES)}"
        )
    path = Path(value.strip().strip('"'))
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"{label} file does not exist: {path}")
    return key, path


def parse_chart_arg(raw: str) -> Tuple[str, Path]:
    return parse_key_value_path(raw, "--chart")


def parse_header_arg(raw: str) -> Tuple[str, Path]:
    return parse_key_value_path(raw, "--header")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def read_json_text(path: Path) -> str:
    data = json.loads(read_text(path))
    return json.dumps(data, ensure_ascii=False, indent=4)


def load_textassets(bundle_bytes: bytes) -> Dict[str, str]:
    env = UnityPy.load(bundle_bytes)
    result: Dict[str, str] = {}
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        tree = obj.read_typetree()
        name = tree.get("m_Name")
        if isinstance(name, str):
            result[name] = tree.get("m_Script", "")
    return result


def patch_textasset_bundle(bundle_bytes: bytes, replacements: Mapping[str, str]) -> bytes:
    env = UnityPy.load(bundle_bytes)
    remaining = set(replacements)
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        tree = obj.read_typetree()
        name = tree.get("m_Name")
        if name in replacements:
            tree["m_Script"] = replacements[name]
            obj.save_typetree(tree)
            remaining.discard(name)
    if remaining:
        missing = ", ".join(sorted(remaining))
        raise RuntimeError(f"TextAsset(s) not found in bundle: {missing}")
    if env.file is None:
        raise RuntimeError("UnityPy did not expose a writable bundle file")
    return env.file.save()


def patch_catalog_bundle_metadata(
    catalog_bytes: bytes,
    bundle_sizes: Mapping[str, int],
    *,
    disable_crc: bool = True,
) -> bytes:
    """Patch Addressables catalog bundle metadata after replacing local bundles.

    The target catalog stores AssetBundleRequestOptions near each bundle filename.
    For the chartdata bundles verified in this APK, the 32-bit CRC sits 68 bytes
    after the filename and BundleSize sits 72 bytes after the filename. Setting
    CRC to 0 disables Unity's CRC check; BundleSize must match the new bundle.
    """
    catalog = bytearray(catalog_bytes)
    for bundle_path, new_size in bundle_sizes.items():
        bundle_name = PurePosixPath(bundle_path).name.encode("utf-8")
        name_offset = catalog.find(bundle_name)
        if name_offset < 0:
            raise RuntimeError(f"Bundle name not found in catalog: {bundle_name.decode()}")

        base = name_offset + len(bundle_name)
        crc_offset = base + CATALOG_BUNDLE_CRC_REL
        size_offset = base + CATALOG_BUNDLE_SIZE_REL
        if size_offset + 4 > len(catalog):
            raise RuntimeError(f"Catalog record is too short for bundle: {bundle_name.decode()}")

        old_size = struct.unpack_from("<I", catalog, size_offset)[0]
        if old_size <= 0:
            raise RuntimeError(
                f"Unexpected catalog BundleSize for {bundle_name.decode()}: {old_size}"
            )

        if disable_crc:
            struct.pack_into("<I", catalog, crc_offset, 0)
        struct.pack_into("<I", catalog, size_offset, new_size)
        eprint(
            "[catalog]",
            bundle_name.decode(),
            f"BundleSize {old_size}->{new_size}",
            "Crc->0" if disable_crc else "Crc unchanged",
        )
    return bytes(catalog)


def require_zip_entry(zf: zipfile.ZipFile, name: str) -> zipfile.ZipInfo:
    try:
        return zf.getinfo(name)
    except KeyError as exc:
        raise RuntimeError(f"APK entry not found: {name}") from exc


def patch_header_json(
    original_text: str,
    ids: SlotIds,
    difficulty: str,
    title: Optional[str],
    artist: Optional[str],
    bpm: Optional[str],
    level: Optional[int],
    visual_source_id: Optional[str],
    cover_id: Optional[str],
) -> str:
    data = json.loads(original_text)
    data["ID"] = ids.chart_id(difficulty)
    data["AudioSourceID"] = ids.audio_id
    data["AffiliatedTuneGroupID"] = ids.group_id
    data["DifficultyName"] = difficulty.lower()
    if title is not None:
        data["Title"] = title
    if artist is not None:
        data["Artist"] = artist
    if bpm is not None:
        data["BPMDisplay"] = bpm
    if level is not None:
        data["NewDifficultyLevel"] = level
    if visual_source_id is not None:
        data["VisualSourceID"] = visual_source_id
    if cover_id is not None:
        data["CoverID"] = cover_id
    return json.dumps(data, ensure_ascii=False, indent=4)


def patch_group_json(
    original_text: str,
    ids: SlotIds,
    title: Optional[str],
    artist: Optional[str],
    genre: Optional[str],
    cover_id: Optional[str],
    child_chart_ids: Iterable[str],
) -> str:
    data = json.loads(original_text)
    data["ID"] = ids.group_id
    data["RepresentingAudioSourceID"] = ids.audio_id
    data["ChildTuneIds"] = list(child_chart_ids)
    if title is not None:
        data["Title"] = title
    if artist is not None:
        data["Artist"] = artist
    if genre is not None:
        data["Genre"] = genre
    if cover_id is not None:
        data["CoverID"] = cover_id
    return json.dumps(data, ensure_ascii=False, indent=4)


def is_apk_signature_file(name: str) -> bool:
    upper = name.upper()
    return upper.startswith(SIGNATURE_PREFIX) and upper.endswith(SIGNATURE_SUFFIXES)


def clone_zipinfo(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    cloned = zipfile.ZipInfo(info.filename, info.date_time)
    cloned.compress_type = info.compress_type
    cloned.comment = info.comment
    cloned.extra = info.extra
    cloned.internal_attr = info.internal_attr
    cloned.external_attr = info.external_attr
    cloned.create_system = info.create_system
    return cloned


def rewrite_apk(
    input_apk: Path,
    output_apk: Path,
    replacements: Mapping[str, bytes],
    strip_signatures: bool,
) -> Tuple[int, int]:
    output_apk.parent.mkdir(parents=True, exist_ok=True)
    replaced = 0
    stripped = 0
    seen: set[str] = set()

    with zipfile.ZipFile(input_apk, "r") as zin, zipfile.ZipFile(
        output_apk, "w", allowZip64=True
    ) as zout:
        for info in zin.infolist():
            name = info.filename
            if strip_signatures and is_apk_signature_file(name):
                stripped += 1
                continue
            out_info = clone_zipinfo(info)
            if name in replacements:
                data = replacements[name]
                replaced += 1
                seen.add(name)
            else:
                data = zin.read(name)
            zout.writestr(out_info, data)

    missing = sorted(set(replacements) - seen)
    if missing:
        raise RuntimeError("Replacement APK entries were not present: " + ", ".join(missing))
    return replaced, stripped


def run_uber_apk_signer(
    apk: Path,
    signer_jar: Path,
    out_dir: Path,
    extra_args: Optional[List[str]] = None,
) -> List[Path]:
    if not signer_jar.is_file():
        raise RuntimeError(f"uber-apk-signer jar not found: {signer_jar}")

    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    command = [
        "java",
        "-jar",
        str(signer_jar),
        "-a",
        str(apk),
        "-o",
        str(out_dir),
        "--allowResign",
    ]
    if extra_args:
        command.extend(extra_args)

    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.stdout:
        eprint(completed.stdout.rstrip())
    if completed.stderr:
        eprint(completed.stderr.rstrip())
    if completed.returncode != 0:
        raise RuntimeError(f"uber-apk-signer failed with exit code {completed.returncode}")

    signed = [
        path
        for path in out_dir.glob("*.apk")
        if path.stat().st_mtime >= started_at - 1
    ]
    signed.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return signed


def build_replacements(args: argparse.Namespace) -> Dict[str, bytes]:
    ids = SlotIds(args.slot.upper())
    charts: Dict[str, Path] = dict(args.chart or [])
    header_files: Dict[str, Path] = dict(args.header or [])
    levels: Dict[str, int] = {}

    for raw in args.level or []:
        if "=" not in raw:
            raise RuntimeError("--level must be DIFFICULTY=number")
        difficulty, value = raw.split("=", 1)
        difficulty = difficulty.strip().upper()
        if difficulty not in DIFFICULTIES:
            raise RuntimeError(f"Unknown difficulty in --level: {difficulty}")
        levels[difficulty] = int(value.strip())

    replacements: Dict[str, bytes] = {}

    if args.wav:
        raise RuntimeError(
            "WAV -> ACB/AWB encoding is not implemented in this tool yet. "
            "Pass --audio-acb and --audio-awb generated by a CRI-compatible encoder."
        )
    if bool(args.audio_acb) != bool(args.audio_awb):
        raise RuntimeError("--audio-acb and --audio-awb must be provided together")
    if args.audio_acb and args.audio_awb:
        replacements[ids.acb_zip_path] = args.audio_acb.read_bytes()
        replacements[ids.awb_zip_path] = args.audio_awb.read_bytes()

    with zipfile.ZipFile(args.apk, "r") as zf:
        group_info = require_zip_entry(zf, GROUP_BUNDLE)
        header_info = require_zip_entry(zf, HEADER_BUNDLE)
        chart_info = require_zip_entry(zf, CHART_BUNDLE)
        del group_info, header_info, chart_info

        group_bytes = zf.read(GROUP_BUNDLE)
        header_bytes = zf.read(HEADER_BUNDLE)
        chart_bytes = zf.read(CHART_BUNDLE)
        catalog_bytes = zf.read(CATALOG_BIN)

    group_assets = load_textassets(group_bytes)
    header_assets = load_textassets(header_bytes)

    group_name = ids.group_id
    if group_name not in group_assets:
        raise RuntimeError(f"Group TextAsset not found: {group_name}")

    header_replacements: Dict[str, str] = {}
    chart_replacements: Dict[str, str] = {}
    active_difficulties: List[str] = []

    for difficulty in DIFFICULTIES:
        chart_id = ids.chart_id(difficulty)
        if difficulty in charts or difficulty in header_files:
            active_difficulties.append(difficulty)

        if difficulty in charts:
            chart_replacements[chart_id] = read_text(charts[difficulty])

        if difficulty in header_files:
            header_replacements[chart_id] = read_json_text(header_files[difficulty])
        elif difficulty in charts or args.patch_all_headers:
            original = header_assets.get(chart_id)
            if original is None:
                raise RuntimeError(f"Header TextAsset not found: {chart_id}")
            header_replacements[chart_id] = patch_header_json(
                original,
                ids=ids,
                difficulty=difficulty,
                title=args.title,
                artist=args.artist,
                bpm=args.bpm,
                level=levels.get(difficulty),
                visual_source_id=args.visual_source_id,
                cover_id=args.cover_id,
            )

    if not active_difficulties and args.patch_all_headers:
        active_difficulties = list(DIFFICULTIES)
    if not active_difficulties:
        active_difficulties = list(DIFFICULTIES)

    group_text = patch_group_json(
        group_assets[group_name],
        ids=ids,
        title=args.title,
        artist=args.artist,
        genre=args.genre,
        cover_id=args.cover_id,
        child_chart_ids=[ids.chart_id(d) for d in active_difficulties],
    )

    group_bundle_new = patch_textasset_bundle(group_bytes, {group_name: group_text})
    replacements[GROUP_BUNDLE] = group_bundle_new

    if header_replacements:
        replacements[HEADER_BUNDLE] = patch_textasset_bundle(header_bytes, header_replacements)
    if chart_replacements:
        replacements[CHART_BUNDLE] = patch_textasset_bundle(chart_bytes, chart_replacements)

    if not args.no_catalog_patch:
        bundle_sizes = {
            bundle: len(replacements[bundle])
            for bundle in ADDRESSABLE_BUNDLES
            if bundle in replacements
        }
        if bundle_sizes:
            replacements[CATALOG_BIN] = patch_catalog_bundle_metadata(catalog_bytes, bundle_sizes)

    return replacements


def resolve_output(input_apk: Path, out: Optional[Path], slot: str) -> Path:
    if out is not None:
        return out
    return input_apk.with_name(f"{input_apk.stem}-{slot.lower()}-replaced-unsigned.apk")


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replace an existing song slot without adding catalog entries."
    )
    parser.add_argument("--apk", type=Path, required=True, help="Input APK path")
    parser.add_argument("--out", type=Path, help="Output APK path")
    parser.add_argument("--slot", default="ALCHEMY", help="Existing slot name without AS_/TG_/CH_")

    parser.add_argument("--audio-acb", type=Path, help="Replacement ACB file")
    parser.add_argument("--audio-awb", type=Path, help="Replacement AWB file")
    parser.add_argument("--wav", type=Path, help="Reserved for future CRI encoding flow")

    parser.add_argument(
        "--chart",
        action="append",
        type=parse_chart_arg,
        help="Replace chart text: DIFFICULTY=path",
    )
    parser.add_argument(
        "--header",
        action="append",
        type=parse_header_arg,
        help="Replace full header JSON: DIFFICULTY=path",
    )
    parser.add_argument("--patch-all-headers", action="store_true", help="Patch metadata on all slot headers")
    parser.add_argument("--level", action="append", help="Set NewDifficultyLevel: DIFFICULTY=number")

    parser.add_argument("--title", help="Display title")
    parser.add_argument("--artist", help="Display artist")
    parser.add_argument("--genre", help="Group genre")
    parser.add_argument("--bpm", help="Header BPMDisplay")
    parser.add_argument("--visual-source-id", help="Header VisualSourceID")
    parser.add_argument("--cover-id", help="Header/group CoverID")
    parser.add_argument(
        "--no-catalog-patch",
        action="store_true",
        help="Do not patch Addressables catalog Crc/BundleSize for replaced chartdata bundles.",
    )

    parser.add_argument(
        "--keep-signature",
        action="store_true",
        help="Keep old META-INF signature files. Usually wrong for modified APKs.",
    )
    parser.add_argument("--sign", action="store_true", help="Sign output APK with uber-apk-signer")
    parser.add_argument(
        "--uber-apk-signer",
        type=Path,
        default=DEFAULT_UBER_SIGNER,
        help="Path to uber-apk-signer jar",
    )
    parser.add_argument(
        "--signed-out-dir",
        type=Path,
        default=Path("out/signed"),
        help="Folder for signed APK output",
    )
    parser.add_argument(
        "--uber-arg",
        action="append",
        help="Extra raw argument forwarded to uber-apk-signer. Repeat for multiple args.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = build_argparser()
    args = parser.parse_args(argv)
    args.apk = args.apk.resolve()
    if not args.apk.is_file():
        parser.error(f"APK does not exist: {args.apk}")
    if args.audio_acb:
        args.audio_acb = args.audio_acb.resolve()
    if args.audio_awb:
        args.audio_awb = args.audio_awb.resolve()
    if args.wav:
        args.wav = args.wav.resolve()

    output_apk = resolve_output(args.apk, args.out.resolve() if args.out else None, args.slot)
    args.uber_apk_signer = args.uber_apk_signer.resolve()
    args.signed_out_dir = args.signed_out_dir.resolve()

    eprint(f"[1/3] Building replacements for slot {args.slot.upper()}")
    replacements = build_replacements(args)
    eprint(f"[2/3] Rewriting APK: {output_apk}")
    replaced, stripped = rewrite_apk(
        input_apk=args.apk,
        output_apk=output_apk,
        replacements=replacements,
        strip_signatures=not args.keep_signature,
    )
    eprint(f"[3/3] Done. Replaced {replaced} entries, stripped {stripped} signature entries.")
    if args.sign:
        eprint("[sign] Signing with uber-apk-signer")
        signed = run_uber_apk_signer(
            output_apk,
            signer_jar=args.uber_apk_signer,
            out_dir=args.signed_out_dir,
            extra_args=args.uber_arg,
        )
        if signed:
            for path in signed:
                eprint(f"[sign] Signed APK: {path}")
        else:
            eprint("[sign] Signing finished, but no new APK was detected in the output folder.")
    else:
        eprint("Output is unsigned. Use --sign to sign with uber-apk-signer before installing.")
    print(str(output_apk))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
