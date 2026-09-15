from __future__ import annotations
import os
from pathlib import Path
from typing import Optional, Tuple

from .repo_scan import RepoScanner


class BuildRootDetector:
    def __init__(self, scanner: RepoScanner):
        self.scanner = scanner

    @staticmethod
    def pom_has_modules(pom: Path) -> bool:
        try:
            txt = pom.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        return ("<modules>" in txt) and ("<module>" in txt)

    def pick_best_pom(self, repo_root: Path, scope_dir: Optional[Path] = None) -> Optional[Path]:
        candidates: list[Path] = []

        if scope_dir:
            cur = scope_dir.resolve()
            rr = repo_root.resolve()
            while True:
                pom = cur / "pom.xml"
                if pom.exists() and pom.is_file():
                    candidates.append(pom)
                if cur == rr or cur.parent == cur:
                    break
                cur = cur.parent

        if not candidates:
            candidates = self.scanner.find_all_files(repo_root, ["pom.xml"])
        if not candidates:
            return None

        def depth_score(p: Path) -> int:
            rel = p.relative_to(repo_root)
            depth = len(rel.parts)
            bonus = -1000 if self.pom_has_modules(p) else 0
            return depth + bonus

        candidates = sorted(set(candidates), key=depth_score)
        return candidates[0]

    def pick_module_pom(self, module_dir: Path, repo_root: Path) -> Optional[Path]:
        cur = module_dir.resolve()
        rr = repo_root.resolve()
        while True:
            pom = cur / "pom.xml"
            if pom.exists() and pom.is_file():
                return pom
            if cur == rr or cur.parent == cur:
                break
            cur = cur.parent
        return None

    def pick_gradle_root(self, repo_root: Path, scope_dir: Optional[Path] = None) -> Optional[Path]:
        def has_settings(d: Path) -> bool:
            return (d / "settings.gradle").exists() or (d / "settings.gradle.kts").exists()

        def has_build(d: Path) -> bool:
            return (d / "build.gradle").exists() or (d / "build.gradle.kts").exists()

        rr = repo_root.resolve()

        if scope_dir:
            cur = scope_dir.resolve()
            while True:
                if has_settings(cur):
                    return cur
                if cur == rr or cur.parent == cur:
                    break
                cur = cur.parent

        settings = self.scanner.find_all_files(repo_root, ["settings.gradle", "settings.gradle.kts"])
        if settings:
            ds = [p.parent for p in settings]
            ds.sort(key=lambda d: len(d.relative_to(repo_root).parts))
            return ds[0]

        if scope_dir:
            cur = scope_dir.resolve()
            while True:
                if has_build(cur):
                    return cur
                if cur == rr or cur.parent == cur:
                    break
                cur = cur.parent

        builds = self.scanner.find_all_files(repo_root, ["build.gradle", "build.gradle.kts"])
        if builds:
            ds = [p.parent for p in builds]
            ds.sort(key=lambda d: len(d.relative_to(repo_root).parts))
            return ds[0]

        return None

    def find_build_root(self, start_dir: Path, repo_root: Path) -> Tuple[str, Path]:
        cur = start_dir.resolve()
        rr = repo_root.resolve()

        while True:
            if (cur / "pom.xml").exists():
                return ("maven", cur)
            if (cur / "build.gradle").exists() or (cur / "build.gradle.kts").exists():
                return ("gradle", cur)
            if cur == rr or cur.parent == cur:
                break
            cur = cur.parent

        pom = self.pick_best_pom(repo_root)
        if pom:
            return ("maven", pom.parent)

        gradle_root = self.pick_gradle_root(repo_root)
        if gradle_root:
            return ("gradle", gradle_root)

        raise FileNotFoundError(f"No pom.xml or build.gradle(.kts) found in repo {repo_root}")

    @staticmethod
    def infer_fqcn_from_path(class_path: str) -> Optional[str]:
        p = class_path.replace("\\", "/")
        marker = "/src/main/java/"
        if marker not in p or not p.endswith(".java"):
            return None
        rel = p.split(marker, 1)[1][:-5]
        return rel.replace("/", ".")

    @staticmethod
    def infer_source_module_dir(repo_root: Path, class_path: str) -> Optional[Path]:
        p = class_path.replace("\\", "/")
        markers = [
            "/src/main/java/",
            "/src/main/kotlin/",
            "/src/main/groovy/",
            "/src/main/scala/",
        ]
        for marker in markers:
            if marker not in p:
                continue
            rel = p.split(marker, 1)[0]
            return repo_root if not rel else (repo_root / rel)
        return None

    @staticmethod
    def relpath(child: Path, parent: Path) -> str:
        return os.path.relpath(child, parent).replace("\\", "/")
