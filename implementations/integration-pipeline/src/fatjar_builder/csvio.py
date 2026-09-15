from __future__ import annotations
import csv
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .models import CutRow, CutRecord, RepoRoot


class CsvIO:
    def read_cut_rows(self, cut_csv: Path) -> List[CutRow]:
        out: List[CutRow] = []
        if not cut_csv.exists():
            return out
        with cut_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                repo = (r.get("repo") or "").strip()
                class_path = (r.get("class_path") or "").strip()
                test_paths = (r.get("test_paths") or "").strip()
                if repo and class_path:
                    out.append(CutRow(repo=repo, class_path=class_path, test_paths=test_paths))
        return out

    def read_failures(self, failures_csv: Path) -> Set[Tuple[str, str]]:
        """Returns set of (repo, class_path) from generate-auto.failures.csv."""
        out: Set[Tuple[str, str]] = set()
        if not failures_csv.exists():
            return out
        with failures_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                repo = (r.get("repo") or "").strip()
                cp = (r.get("class_path") or "").strip()
                if repo and cp:
                    out.add((repo, cp))
        return out

    def read_fatjar_map_failures(self, out_map_csv: Path) -> Set[Tuple[str, str]]:
        """Returns set of (repo, class_path) from cut_to_fatjar_map.csv that failed build."""
        out: Set[Tuple[str, str]] = set()
        if not out_map_csv.exists():
            return out
        with out_map_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                repo = (r.get("repo") or "").strip()
                cp = (r.get("class_path") or "").strip()
                fj = (r.get("fatjar_path") or "").strip()
                if repo and cp and (not fj or fj == "FAIL" or fj.startswith("SKIP")):
                    out.add((repo, cp))
        return out

    def read_repo_roots(self, repos_csv: Path) -> Dict[str, RepoRoot]:
        out: Dict[str, RepoRoot] = {}
        with repos_csv.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                repo = (r.get("repo") or "").strip()
                root = (r.get("repo_root") or "").strip()
                if not repo:
                    continue
                if root in ("FAIL", "SKIP-REPO", ""):
                    continue
                out[repo] = RepoRoot(repo=repo, repo_root=Path(root))
        return out

    def write_map(self, out_csv: Path, records: List[CutRecord], fatjar_by_module_key: Dict[str, str]) -> None:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            fieldnames = ["repo", "build_tool", "module_rel", "class_path", "fqcn", "test_paths", "fatjar_path"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()

            for r in records:
                if r.build_tool in ("SKIP", "SKIP-REPO"):
                    fatjar_path = r.build_tool
                else:
                    mk = f"{r.repo}|{r.build_tool}|{r.module_rel}"
                    fatjar_path = fatjar_by_module_key.get(mk, "FAIL")

                w.writerow({
                    "repo": r.repo,
                    "build_tool": r.build_tool,
                    "module_rel": r.module_rel,
                    "class_path": r.class_path,
                    "fqcn": r.fqcn,
                    "test_paths": r.test_paths,
                    "fatjar_path": fatjar_path,
                })