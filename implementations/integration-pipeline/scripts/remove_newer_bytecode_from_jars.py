#!/usr/bin/env python3
"""
remove_newer_bytecode_from_jars.py

Removes .class entries compiled with a bytecode major version > max_major
(default 65 = Java 21) from all jars under a directory (default: ./out).

Behavior:
- For each jar, create a cleaned jar.
- Rename original jar to .old (or .old.N).
- Move cleaned jar to the original name.
- Non-class entries are preserved unchanged.
- Multi-release jars: also checks META-INF/versions/*/*.class entries.

Usage:
  python3 remove_newer_bytecode_from_jars.py --root ./out --max-major 65
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import zipfile


JAVA_MAJOR_TO_VERSION = {
    45: "1.1",
    46: "1.2",
    47: "1.3",
    48: "1.4",
    49: "5",
    50: "6",
    51: "7",
    52: "8",
    53: "9",
    54: "10",
    55: "11",
    56: "12",
    57: "13",
    58: "14",
    59: "15",
    60: "16",
    61: "17",
    62: "18",
    63: "19",
    64: "20",
    65: "21",
    66: "22",
    67: "23",
    68: "24",
    69: "25",
}


@dataclass
class RemovedClass:
    entry_name: str
    major: int
    minor: int


@dataclass
class JarResult:
    jar_path: Path
    total_entries: int
    removed: List[RemovedClass]
    kept_class_count: int
    kept_other_count: int
    output_tmp: Optional[Path] = None
    old_path: Optional[Path] = None


def is_class_entry(name: str) -> bool:
    return name.endswith(".class") and not name.endswith("/")


def class_major_minor(class_bytes: bytes) -> Optional[Tuple[int, int]]:
    """
    Java class file header:
      u4 magic = 0xCAFEBABE
      u2 minor_version
      u2 major_version
    """
    if len(class_bytes) < 8:
        return None
    magic = class_bytes[:4]
    if magic != b"\xCA\xFE\xBA\xBE":
        return None
    minor = struct.unpack(">H", class_bytes[4:6])[0]
    major = struct.unpack(">H", class_bytes[6:8])[0]
    return major, minor


def java_version_name(major: int) -> str:
    return JAVA_MAJOR_TO_VERSION.get(major, f"? (major={major})")


def next_old_name(p: Path) -> Path:
    """
    If foo.jar.old exists, use foo.jar.old.1, .2, ...
    """
    candidate = p.with_name(p.name + ".old")
    if not candidate.exists():
        return candidate
    i = 1
    while True:
        c = p.with_name(p.name + f".old.{i}")
        if not c.exists():
            return c
        i += 1


def safe_replace(original: Path, new_file: Path) -> Tuple[Path, Path]:
    """
    Rename original -> .old(.N), then move new_file -> original name.
    Returns (old_path, final_path)
    """
    old_path = next_old_name(original)
    original.rename(old_path)
    new_file.rename(original)
    return old_path, original


def clean_jar(jar_path: Path, max_major: int, dry_run: bool = False) -> JarResult:
    removed: List[RemovedClass] = []
    kept_class_count = 0
    kept_other_count = 0
    total_entries = 0

    # We write a temp jar in the same directory to keep moves atomic
    tmp_name = jar_path.name + f".tmp_clean_{int(time.time()*1000)}"
    tmp_path = jar_path.with_name(tmp_name)

    with zipfile.ZipFile(jar_path, "r") as zin:
        infolist = zin.infolist()
        total_entries = len(infolist)

        if dry_run:
            # Just scan
            for info in infolist:
                name = info.filename
                if is_class_entry(name):
                    data = zin.read(name)
                    mm = class_major_minor(data)
                    if mm:
                        major, minor = mm
                        if major > max_major:
                            removed.append(RemovedClass(name, major, minor))
                        else:
                            kept_class_count += 1
                    else:
                        # Not a valid class header; keep it
                        kept_class_count += 1
                else:
                    kept_other_count += 1
            return JarResult(
                jar_path=jar_path,
                total_entries=total_entries,
                removed=removed,
                kept_class_count=kept_class_count,
                kept_other_count=kept_other_count,
                output_tmp=None,
                old_path=None,
            )

        # Write cleaned jar
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            # Preserve manifest ordering if present (nice-to-have)
            # Write MANIFEST.MF first if it exists
            manifest_name = "META-INF/MANIFEST.MF"
            names_set = {i.filename for i in infolist}
            if manifest_name in names_set:
                info = zin.getinfo(manifest_name)
                data = zin.read(manifest_name)
                zout.writestr(info, data)

            for info in infolist:
                name = info.filename
                if name == manifest_name:
                    continue  # already written

                if is_class_entry(name):
                    data = zin.read(name)
                    mm = class_major_minor(data)
                    if mm:
                        major, minor = mm
                        if major > max_major:
                            removed.append(RemovedClass(name, major, minor))
                            continue
                        kept_class_count += 1
                    else:
                        # Invalid class header -> keep
                        kept_class_count += 1

                    # Preserve metadata as much as zipfile allows by reusing ZipInfo
                    zout.writestr(info, data)
                else:
                    kept_other_count += 1
                    data = zin.read(name)
                    zout.writestr(info, data)

    return JarResult(
        jar_path=jar_path,
        total_entries=total_entries,
        removed=removed,
        kept_class_count=kept_class_count,
        kept_other_count=kept_other_count,
        output_tmp=tmp_path,
        old_path=None,
    )


def iter_jars(root: Path) -> List[Path]:
    return sorted([p for p in root.rglob("*.jar") if p.is_file()])


def print_result(res: JarResult, max_major: int):
    if not res.removed:
        print(f"[OK] {res.jar_path} (no classes > {max_major}) entries={res.total_entries}")
        return

    worst = max((r.major for r in res.removed), default=0)
    worst_java = java_version_name(worst)
    print(f"[CLEAN] {res.jar_path}")
    print(f"  entries: {res.total_entries} | kept classes: {res.kept_class_count} | kept others: {res.kept_other_count}")
    print(f"  removed classes: {len(res.removed)} | worst major: {worst} (Java {worst_java})")

    # Show up to first 20 removed entries
    show = res.removed[:20]
    for r in show:
        print(f"    - {r.entry_name}  major={r.major} (Java {java_version_name(r.major)})")
    if len(res.removed) > 20:
        print(f"    ... (+{len(res.removed) - 20} more)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="./out", help="Directory containing jars (default: ./out)")
    ap.add_argument("--max-major", type=int, default=65, help="Max allowed class major version (default 65 = Java 21)")
    ap.add_argument("--dry-run", action="store_true", help="Scan and report, do not modify jars")
    ap.add_argument("--only-changed", action="store_true", help="Only print jars that would be modified")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"[ERROR] root does not exist: {root}", file=sys.stderr)
        sys.exit(2)

    jars = iter_jars(root)
    if not jars:
        print(f"[WARN] No jars found under: {root}")
        return

    changed = 0
    skipped = 0
    failed = 0

    for jar in jars:
        try:
            res = clean_jar(jar, max_major=args.max_major, dry_run=args.dry_run)
            if args.only_changed and not res.removed:
                skipped += 1
                continue

            print_result(res, args.max_major)

            if args.dry_run:
                if res.removed:
                    changed += 1
                continue

            # If nothing removed, delete tmp if created (shouldn't exist, but be safe)
            if not res.removed:
                if res.output_tmp and res.output_tmp.exists():
                    res.output_tmp.unlink()
                skipped += 1
                continue

            # Replace jar with cleaned version
            assert res.output_tmp is not None and res.output_tmp.exists()
            old_path, final_path = safe_replace(jar, res.output_tmp)
            res.old_path = old_path
            print(f"  -> renamed original to: {old_path.name}")
            print(f"  -> wrote cleaned jar as: {final_path.name}")
            changed += 1

        except Exception as e:
            failed += 1
            print(f"[FAIL] {jar} -> {e}", file=sys.stderr)
            # If temp file exists, try to remove it
            # (best effort; do not crash)
            try:
                for tmp in jar.parent.glob(jar.name + ".tmp_clean_*"):
                    tmp.unlink(missing_ok=True)
            except Exception:
                pass

    print("\n===== SUMMARY =====")
    print(f"root: {root}")
    print(f"max_major: {args.max_major} (Java {java_version_name(args.max_major)})")
    print(f"dry_run: {args.dry_run}")
    print(f"modified jars: {changed}")
    print(f"unchanged jars: {skipped}")
    print(f"failed jars: {failed}")


if __name__ == "__main__":
    main()