from __future__ import annotations
import os
import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import BuildConfig
from .csvio import CsvIO
from .models import CutRecord, CutRow, ModuleKey, RepoRoot
from .build_root import BuildRootDetector
from .maven_fatjar import MavenFatJarBuilder
from .gradle_fatjar import GradleFatJarBuilder
from .exceptions import SkipBuild
from .logging_ctx import LoggingContext
from .utils import safe_name, which_or_fail


class BuildOrchestrator:
    def __init__(
        self,
        cfg: BuildConfig,
        io: CsvIO,
        detector: BuildRootDetector,
        maven: MavenFatJarBuilder,
        gradle: GradleFatJarBuilder,
        lc: LoggingContext,
    ):
        self.cfg = cfg
        self.io = io
        self.detector = detector
        self.maven = maven
        self.gradle = gradle
        self.lc = lc

    def run(self) -> None:
        self.cfg.repos_dir().mkdir(parents=True, exist_ok=True)
        self.cfg.out_dir().mkdir(parents=True, exist_ok=True)
        self.cfg.cache_dir().mkdir(parents=True, exist_ok=True)

        if self.cfg.mode == "local":
            which_or_fail("java")

        if self.cfg.java_home:
            os.environ["JAVA_HOME"] = self.cfg.java_home

        cut_rows = self.io.read_cut_rows(self.cfg.cut_csv)
        if self.cfg.retry_only:
            failed_in_agt = set()
            if self.cfg.failures_csv:
                failed_in_agt = self.io.read_failures(self.cfg.failures_csv)
            
            failed_in_build = set()
            if self.cfg.out_map_csv:
                failed_in_build = self.io.read_fatjar_map_failures(self.cfg.resolved_out_map_csv())
            
            all_failures = failed_in_agt | failed_in_build
            if all_failures:
                new_rows = [r for r in cut_rows if (r.repo, r.class_path) in all_failures]
                print(f"[RETRY] Filtering to {len(new_rows)} failures (from {len(cut_rows)} total rows)")
                if self.lc.logger:
                    self.lc.logger.info("[RETRY] Filtering to %d failures (from %d total rows)", len(new_rows), len(cut_rows))
                cut_rows = new_rows
            else:
                print("[RETRY] No failures found in AGT or previous build map. Running all.")
                if self.lc.logger:
                    self.lc.logger.info("[RETRY] No failures found in AGT or previous build map. Running all.")

        if not cut_rows:
            raise RuntimeError("No usable rows found.")

        repo_roots = self.io.read_repo_roots(self.cfg.resolved_repos_csv())

        module_dirs: Dict[ModuleKey, Path] = {}
        module_class_entries: Dict[ModuleKey, Set[str]] = {}
        cut_records: List[CutRecord] = []

        for row in cut_rows:
            rec, mk, mdir = self._resolve_row(row, repo_roots)
            cut_records.append(rec)
            if mk and mdir:
                module_dirs.setdefault(mk, mdir)
                if rec.fqcn:
                    module_class_entries.setdefault(mk, set()).add(rec.fqcn.replace(".", "/") + ".class")

        fatjar_by_module_key: Dict[str, str] = {}

        for mk, module_dir in module_dirs.items():
            root = repo_roots.get(mk.repo).repo_root if mk.repo in repo_roots else (self.cfg.repos_dir() / safe_name(mk.repo))
            self.lc.set_context(mk.repo, mk.module_rel)

            print(f"\n==== BUILD {mk.repo} [{mk.tool}] module={mk.module_rel} ====")
            if self.lc.logger:
                self.lc.logger.info("==== BUILD %s [%s] module=%s ====", mk.repo, mk.tool, mk.module_rel)

            k = f"{mk.repo}|{mk.tool}|{mk.module_rel}"

            try:
                if mk.tool == "maven":
                    jar = self.maven.build(mk.repo, root, module_dir, java21_home=self.cfg.java21_home)
                elif mk.tool == "gradle":
                    jar = self.gradle.build(self.cfg.cache_dir(), root, module_dir)
                else:
                    fatjar_by_module_key[k] = "FAIL"
                    continue

                missing_entries = self._missing_class_entries(jar, module_class_entries.get(mk, set()))
                if missing_entries:
                    raise SkipBuild(
                        f"Selected jar does not contain CUT classes for module {mk.module_rel} "
                        f"(example missing: {missing_entries[0]})"
                    )

                out_sub = self.cfg.out_dir() / safe_name(mk.repo) / safe_name(mk.module_rel or "root")
                out_sub.mkdir(parents=True, exist_ok=True)
                out_path = out_sub / jar.name
                shutil.copy2(jar, out_path)

                fatjar_by_module_key[k] = str(out_path)
                print(f"[OK] {out_path}")
                if self.lc.logger:
                    self.lc.logger.info("[OK] %s", str(out_path))

            except SkipBuild as e:
                fatjar_by_module_key[k] = f"SKIP: {e}"
                print(f"[SKIP] {mk.repo}:{mk.module_rel} -> {e}")
                if self.lc.logger:
                    self.lc.logger.info("[SKIP] %s:%s -> %s", mk.repo, mk.module_rel, str(e))

            except Exception as e:
                fatjar_by_module_key[k] = "FAIL"
                print(f"[FAIL] {mk.repo}:{mk.module_rel} -> {e}")
                if self.lc.logger:
                    self.lc.logger.error("[FAIL] %s:%s -> %s", mk.repo, mk.module_rel, str(e))

        out_csv = self.cfg.resolved_out_map_csv()
        self.io.write_map(out_csv, cut_records, fatjar_by_module_key)

        print("\n===== DONE (BUILD) =====")
        print(f"Fat jars in: {self.cfg.out_dir()}")
        print(f"Mapping CSV: {out_csv}")
        if self.lc.logger:
            self.lc.logger.info("===== DONE =====")
            self.lc.logger.info("Fat jars in: %s", str(self.cfg.out_dir()))
            self.lc.logger.info("Mapping CSV: %s", str(out_csv))
            self.lc.logger.info("Logs in: %s", str(self.cfg.resolved_log_dir()))

    def _resolve_row(self, row: CutRow, repo_roots: Dict[str, RepoRoot]) -> Tuple[CutRecord, Optional[ModuleKey], Optional[Path]]:
        if row.repo in self.cfg.skip_repos:
            return (
                CutRecord(
                    repo=row.repo,
                    class_path=row.class_path,
                    test_paths=row.test_paths,
                    build_tool="SKIP-REPO",
                    module_rel="",
                    fqcn=self.detector.infer_fqcn_from_path(row.class_path) or "",
                ),
                None,
                None,
            )

        root = repo_roots.get(row.repo).repo_root if row.repo in repo_roots else (self.cfg.repos_dir() / safe_name(row.repo))
        if not root.exists():
            return (
                CutRecord(
                    repo=row.repo,
                    class_path=row.class_path,
                    test_paths=row.test_paths,
                    build_tool="SKIP",
                    module_rel="",
                    fqcn=self.detector.infer_fqcn_from_path(row.class_path) or "",
                ),
                None,
                None,
            )

        class_abs = root / row.class_path
        start_dir = class_abs.parent if class_abs.exists() else root

        try:
            tool, module_dir = self.detector.find_build_root(start_dir, root)
        except FileNotFoundError:
            return (
                CutRecord(
                    repo=row.repo,
                    class_path=row.class_path,
                    test_paths=row.test_paths,
                    build_tool="SKIP",
                    module_rel="",
                    fqcn=self.detector.infer_fqcn_from_path(row.class_path) or "",
                ),
                None,
                None,
            )

        source_module_dir = self.detector.infer_source_module_dir(root, row.class_path)
        if tool == "gradle" and source_module_dir and source_module_dir.exists():
            module_dir = source_module_dir

        module_rel = self.detector.relpath(module_dir, root)
        mk = ModuleKey(repo=row.repo, tool=tool, module_rel=module_rel)

        return (
            CutRecord(
                repo=row.repo,
                class_path=row.class_path,
                test_paths=row.test_paths,
                build_tool=tool,
                module_rel=module_rel,
                fqcn=self.detector.infer_fqcn_from_path(row.class_path) or "",
            ),
            mk,
            module_dir,
        )

    @staticmethod
    def _missing_class_entries(jar: Path, expected_entries: Set[str]) -> List[str]:
        if not expected_entries:
            return []
        try:
            with zipfile.ZipFile(jar) as zf:
                names = set(zf.namelist())
        except Exception:
            return sorted(expected_entries)
        return sorted(entry for entry in expected_entries if entry not in names)
