#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.common import parse_package_and_class, repo_to_dir
from src.core.layout import PIPELINE_OUTPUT_ROOT, WORKSPACE_ROOT
from src.pipeline.helpers import test_method_names


REPOS_ROOT = ROOT / WORKSPACE_ROOT / "repos"
PR_DIR = ROOT / PIPELINE_OUTPUT_ROOT / "pull-requests" / "drafts"
REDUCE_SUMMARY = ROOT / PIPELINE_OUTPUT_ROOT / "reduced" / "pr-tests" / "reduce_summary.csv"
BACKUP_ROOT = ROOT / PIPELINE_OUTPUT_ROOT / "pull-requests" / "backups"


@dataclass(frozen=True)
class PrDraft:
    repo_dir: str
    repo: str
    fqcn: str
    target_id: str
    class_label: str
    methods: tuple[str, ...]
    path: Path


@dataclass(frozen=True)
class PrItem:
    repo: str
    repo_dir: str
    fqcn: str
    class_label: str
    methods: tuple[str, ...]
    repo_root: Path
    target_file: Path
    reduced_source: Path
    pr_draft: Path
    git_status: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _target_id(repo: str, fqcn: str) -> str:
    return f"{repo_to_dir(repo)}_{fqcn.replace('.', '_')}"


def _parse_draft(
    path: Path,
    target_by_id: dict[str, dict[str, str]],
    repo_by_dir: dict[str, str],
) -> PrDraft:
    text = path.read_text(encoding="utf-8", errors="ignore")
    stem = path.name.removesuffix(".pr-tests.md")
    row = target_by_id.get(stem)
    if row:
        repo = row["repo"]
        fqcn = row["fqcn"]
        repo_dir = repo_to_dir(repo)
        target_id = stem
    else:
        repo_dir = stem
        repo = repo_by_dir.get(repo_dir, repo_dir.replace("_", "/"))
        fqcn = ""
        target_id = ""
    intro = re.search(r"new test cases for `([^`]+)`", text)
    class_label = intro.group(1) if intro else (fqcn.rsplit(".", 1)[-1] if fqcn else "")
    selected_section = text.split("## Tests Added", 1)[-1].split("\n## ", 1)[0]
    methods = tuple(re.findall(r"^- `([^`]+)`\s*$", selected_section, re.MULTILINE))
    return PrDraft(
        repo_dir=repo_dir,
        repo=repo,
        fqcn=fqcn,
        target_id=target_id,
        class_label=class_label,
        methods=methods,
        path=path,
    )


def _method_exists(source: Path, method: str) -> bool:
    text = source.read_text(encoding="utf-8", errors="ignore")
    return re.search(rf"\b{re.escape(method)}\s*\(", text) is not None


def _repo_git_status(repo_root: Path, target_file: Path) -> str:
    try:
        rel = target_file.relative_to(repo_root)
    except ValueError:
        rel = target_file
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--short", "--", str(rel)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return (proc.stdout or "").strip()


def _find_repo_test_file(repo_root: Path, reduced_source: Path) -> Path | None:
    pkg, cls = parse_package_and_class(reduced_source)
    if not cls:
        return None
    target_cls = re.sub(r"_Top\d+$", "", cls)
    filename = f"{target_cls}.java"
    matches: list[Path] = []
    for candidate in repo_root.rglob(filename):
        cand_pkg, cand_cls = parse_package_and_class(candidate)
        if cand_pkg == pkg and cand_cls == target_cls:
            matches.append(candidate)
    if not matches:
        return None

    def score(path: Path) -> tuple[int, int, str]:
        parts = set(path.parts)
        generated_penalty = 1 if parts & {"build", "target", ".gradle", "generated"} else 0
        test_source_penalty = 0 if "src" in parts and "test" in parts else 1
        return generated_penalty, test_source_penalty, str(path)

    return sorted(matches, key=score)[0]


def discover_items() -> list[PrItem]:
    reduce_rows = [
        row
        for row in _read_csv(REDUCE_SUMMARY)
        if row.get("variant") == "pr-tests" and row.get("status") == "passed"
    ]
    repo_by_dir = {repo_to_dir(row["repo"]): row["repo"] for row in reduce_rows}
    target_by_id = {_target_id(row["repo"], row["fqcn"]): row for row in reduce_rows}
    drafts = [_parse_draft(path, target_by_id, repo_by_dir) for path in sorted(PR_DIR.glob("*.pr-tests.md"))]
    rows_by_repo: dict[str, list[dict[str, str]]] = {}
    for row in reduce_rows:
        rows_by_repo.setdefault(row["repo"], []).append(row)

    items: list[PrItem] = []
    for draft in drafts:
        repo_root = REPOS_ROOT / draft.repo_dir
        candidate_rows = [target_by_id[draft.target_id]] if draft.target_id in target_by_id else rows_by_repo.get(draft.repo, [])
        remaining = set(draft.methods) if draft.methods else None
        for row in candidate_rows:
            reduced = ROOT / row["reduced_test_path"]
            if not reduced.exists():
                continue
            candidate_methods = draft.methods or tuple(test_method_names(reduced))
            matched = tuple(
                method
                for method in candidate_methods
                if (remaining is None or method in remaining) and _method_exists(reduced, method)
            )
            if not matched:
                continue
            target_file = _find_repo_test_file(repo_root, reduced)
            if target_file is None:
                continue
            items.append(
                PrItem(
                    repo=draft.repo,
                    repo_dir=draft.repo_dir,
                    fqcn=row["fqcn"],
                    class_label=draft.class_label,
                    methods=matched,
                    repo_root=repo_root,
                    target_file=target_file,
                    reduced_source=reduced,
                    pr_draft=draft.path,
                    git_status=_repo_git_status(repo_root, target_file),
                )
            )
            if remaining is not None:
                remaining.difference_update(matched)
        if remaining:
            missing = ", ".join(sorted(remaining))
            raise SystemExit(f"Could not map selected methods for {draft.repo}: {missing}")
    return items


def _filter_items(items: Iterable[PrItem], repo: str | None) -> list[PrItem]:
    selected = list(items)
    if repo:
        selected = [
            item
            for item in selected
            if item.repo == repo or item.repo_dir == repo or repo_to_dir(item.repo) == repo
        ]
    return selected


def print_items(items: list[PrItem]) -> None:
    for index, item in enumerate(items, 1):
        rel = item.target_file.relative_to(item.repo_root)
        dirty = item.git_status if item.git_status else "clean"
        print(f"{index:02d}. {item.repo} [{dirty}]")
        print(f"    class:   {item.class_label}")
        print(f"    file:    {rel}")
        print(f"    methods: {', '.join(item.methods)}")
        print(f"    draft:   {item.pr_draft.relative_to(ROOT)}")
        print(f"    source:  {item.reduced_source.relative_to(ROOT)}")


def backup_items(items: list[PrItem], label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"{timestamp}-{label}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = backup_dir / "manifest.csv"
    fields = [
        "repo",
        "fqcn",
        "methods",
        "target_file",
        "backup_file",
        "reduced_source",
        "pr_draft",
        "git_status",
    ]
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in items:
            rel = item.target_file.relative_to(item.repo_root)
            backup_file = backup_dir / item.repo_dir / rel
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.target_file, backup_file)
            writer.writerow(
                {
                    "repo": item.repo,
                    "fqcn": item.fqcn,
                    "methods": ";".join(item.methods),
                    "target_file": str(item.target_file),
                    "backup_file": str(backup_file),
                    "reduced_source": str(item.reduced_source),
                    "pr_draft": str(item.pr_draft),
                    "git_status": item.git_status,
                }
            )
    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare supervised real PR submissions for selected PR tests.")
    parser.add_argument("command", choices=("list", "backup"))
    parser.add_argument("--repo", help="Limit to one repo, e.g. alibaba/druid or alibaba_druid.")
    parser.add_argument("--label", default="all", help="Backup label suffix.")
    args = parser.parse_args()

    items = _filter_items(discover_items(), args.repo)
    if not items:
        raise SystemExit("No PR items matched.")

    print_items(items)
    if args.command == "backup":
        backup_dir = backup_items(items, args.label)
        print(f"\nBackup written to: {backup_dir}")
        print(f"Manifest: {backup_dir / 'manifest.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
