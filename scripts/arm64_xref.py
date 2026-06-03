#!/usr/bin/env python3
"""Small AArch64 disassembly/xref helper for IL2CPP ELF files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs
from capstone.arm64 import ARM64_OP_IMM
from elftools.elf.elffile import ELFFile


def parse_int(value: str) -> int:
    return int(value, 0)


def load_segments(path: Path):
    with path.open("rb") as file:
        elf = ELFFile(file)
        for segment in elf.iter_segments():
            if segment["p_type"] != "PT_LOAD":
                continue
            vaddr = int(segment["p_vaddr"])
            filesz = int(segment["p_filesz"])
            memsz = int(segment["p_memsz"])
            offset = int(segment["p_offset"])
            yield vaddr, vaddr + memsz, offset, filesz


def va_to_offset(path: Path, va: int) -> int:
    for start, end, offset, filesz in load_segments(path):
        if start <= va < end:
            delta = va - start
            if delta >= filesz:
                raise ValueError(f"VA 0x{va:x} maps outside file-backed segment")
            return offset + delta
    raise ValueError(f"VA 0x{va:x} not in any PT_LOAD segment")


def read_va(path: Path, va: int, size: int) -> bytes:
    offset = va_to_offset(path, va)
    with path.open("rb") as file:
        file.seek(offset)
        return file.read(size)


def method_map(script_json: Path) -> dict[int, str]:
    data = json.loads(script_json.read_text(encoding="utf-8"))
    return {int(item["Address"]): item["Name"] for item in data.get("ScriptMethod", [])}


def nearest_method(methods: dict[int, str], address: int) -> tuple[int, str] | None:
    candidates = [item for item in methods.items() if item[0] <= address]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])


def disasm(path: Path, start: int, size: int) -> None:
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    for insn in md.disasm(read_va(path, start, size), start):
        print(f"0x{insn.address:08x}: {insn.mnemonic:8} {insn.op_str}")


def find_xrefs(path: Path, target: int, methods: dict[int, str] | None) -> None:
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    with path.open("rb") as file:
        elf = ELFFile(file)
        for section in elf.iter_sections():
            flags = int(section["sh_flags"])
            if not flags & 0x4:
                continue
            start = int(section["sh_addr"])
            data = section.data()
            for insn in md.disasm(data, start):
                if insn.mnemonic not in ("b", "bl"):
                    continue
                if not insn.operands or insn.operands[0].type != ARM64_OP_IMM:
                    continue
                if int(insn.operands[0].imm) != target:
                    continue
                owner = nearest_method(methods, insn.address) if methods else None
                if owner:
                    print(f"0x{insn.address:08x}: {insn.mnemonic} 0x{target:x}    {owner[1]}+0x{insn.address-owner[0]:x}")
                else:
                    print(f"0x{insn.address:08x}: {insn.mnemonic} 0x{target:x}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("elf", type=Path)
    parser.add_argument("--script-json", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("disasm")
    d.add_argument("start", type=parse_int)
    d.add_argument("size", type=parse_int)

    x = sub.add_parser("xrefs")
    x.add_argument("target", type=parse_int)

    args = parser.parse_args()
    methods = method_map(args.script_json) if args.script_json else None
    if args.command == "disasm":
        disasm(args.elf, args.start, args.size)
    elif args.command == "xrefs":
        find_xrefs(args.elf, args.target, methods)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
