#!/usr/bin/env python3

import argparse
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from ..core.java import add_throws_exception_to_tests
except ImportError:
    # Support direct script execution (python src/steps/fix.py ...).
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from src.core.java import add_throws_exception_to_tests
try:
    from .base import Step
except ImportError:
    from src.steps.base import Step
try:
    from ..core.layout import PIPELINE_OUTPUT_ROOT
except ImportError:
    from src.core.layout import PIPELINE_OUTPUT_ROOT

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


def iter_target_files(root):
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".java"):
                yield os.path.join(dirpath, name)


def strip_import(path, pattern, dry_run):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        print(f"error: failed to read {path}: {exc}", file=sys.stderr)
        return False

    new_lines = [line for line in lines if not pattern.match(line)]
    if new_lines == lines:
        return False

    if dry_run:
        return True

    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(new_lines)
    except OSError as exc:
        print(f"error: failed to write {path}: {exc}", file=sys.stderr)
        return False

    return True


def replace_extends(path, pattern, dry_run):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        print(f"error: failed to read {path}: {exc}", file=sys.stderr)
        return False

    changed = False
    new_lines = []
    for line in lines:
        updated = pattern.sub("", line)
        if updated != line:
            changed = True
        new_lines.append(updated)

    if not changed:
        return False

    if dry_run:
        return True

    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(new_lines)
    except OSError as exc:
        print(f"error: failed to write {path}: {exc}", file=sys.stderr)
        return False

    return True

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Remove import lines that include _scaffolding and extends *_scaffolding { "
            "from Java files."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=f"{PIPELINE_OUTPUT_ROOT}/llm-out",
        help="Root directory to scan (default: results/llm-out).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report files that would change without editing.",
    )
    args = parser.parse_args()

    import_pattern = re.compile(r"^\s*import\s+.*_scaffolding.*;\s*$")
    extends_pattern = re.compile(r"\s+extends\s+\S*_scaffolding\b")

    if not os.path.isdir(args.path):
        print(f"error: directory not found: {args.path}", file=sys.stderr)
        return 2

    changed = 0
    for path in iter_target_files(args.path):
        modified = strip_import(path, import_pattern, args.dry_run)
        modified = replace_extends(path, extends_pattern, args.dry_run) or modified
        modified = add_throws_exception_to_tests(path, args.dry_run) or modified
        if modified:
            changed += 1
            print(path)

    print(f"files updated: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


class AdoptedFixStep(Step):
    step_names = ("adopted-fix",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        if not self.pipeline.adopted_root.exists():
            print(f'[agt] adopted-fix: Skip (missing adopted dir): {self.pipeline.adopted_root}')
            return True
        if not self.pipeline.adopted_fix_script.exists():
            print(f'[agt] adopted-fix: Skip (missing script): {self.pipeline.adopted_fix_script}')
            return True

        fix_log = self.pipeline.logs_dir / f"{ctx.target_id}.adopted.fix.log"
        target_root = self.pipeline.adopted_root / ctx.target_id
        if not target_root.exists():
            print(f'[agt] adopted-fix: Skip (missing target dir): {target_root}')
            return True
        cmd = [sys.executable, str(self.pipeline.adopted_fix_script), str(target_root)]
        print(f'[agt] adopted-fix: {ctx.target_id}')
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        fix_log.write_text(proc.stdout or "", encoding="utf-8", errors="ignore")
        if proc.returncode != 0:
            print(f'[agt] adopted-fix: FAIL (see {fix_log}) repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
        return True
