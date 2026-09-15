from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
import csv
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


from ..core.common import looks_like_scaffolding, parse_package_and_class, repo_to_dir, split_list_field, ensure_dir
from .config import (
    PipelineArgs,
    PipelineConfig,
    build_pipeline_config,
    covfilter_variant_root,
    covfilter_variant_summary_csv,
    llm_adopted_summary_csv_path,
    llm_agentic_summary_csv_path,
    llm_improve_summary_csv_path,
    reduce_variant_summary_csv,
    selected_adopted_variants,
)
from .sanitize import (
    clear_pair_root,
    materialize_sanitized_pair,
    sanitize_compare_summary_csv,
    sanitize_summary_csv,
)
from .helpers import (
    expand_manual_sources,
    find_tests_in_bucket,
    first_test_fqcn_from_sources,
    first_test_source_for_fqcn,
    generated_source_matches_target,
    looks_like_test_source,
    pr_test_path,
)
from ..steps import (
    AdoptedCommentStep,
    AdoptedFilterStep,
    AdoptedFixStep,
    AdoptedReduceStep,
    AdoptedRunStep,
    AgentStep,
    CompareStep,
    CompileStep,
    CoverageComparisonReducedStep,
    CoverageComparisonStep,
    CoverageIncrementalComparisonStep,
    Rq4SnapshotCoverageStep,
    CovfilterStep,
    PullRequestMakerStep,
    ReducedAnnotationStep,
    ReduceStep,
    RunStep,
    SanitizeEvoSuiteStep,
    SendStep,
    Step,
)
from ..steps.clone import CloneConfig, run_clone as run_clone_step
from ..steps.fatjar import FatjarConfig, run_fatjar as run_fatjar_step


@dataclass
class TargetContext:
    repo: str
    fqcn: str
    target_id: str
    sut_jar: Path
    target_build: Path
    sources: List[Path]
    manual_sources: List[Path]
    final_sources: List[Path]
    repo_root_for_deps: Path
    module_rel: str = ""
    build_tool: str = ""
    class_path: str = ""
    manual_test_fqcn: Optional[str] = None
    generated_test_fqcn: Optional[str] = None


def _fatjar_value_is_missing(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return True

    upper = normalized.upper()
    return upper == "FAIL" or upper.startswith("FAIL:") or upper.startswith("SKIP")


class Pipeline:
    def __init__(self, args: PipelineArgs) -> None:
        self.args = args
        self.config: Optional[PipelineConfig] = None
        self.ran = 0
        self.skipped = 0
        self.covfilter_allow: Optional[Set[Tuple[str, str]]] = None
        self.steps: List[Step] = []
        self.generate_auto_output_root: Optional[Path] = None
        self.annotation_filtered_targets: Optional[List[Tuple[str, str]]] = None

    def run_configuration(self) -> None:
        self.config = build_pipeline_config(self.args)
        for f in fields(PipelineConfig):
            setattr(self, f.name, getattr(self.config, f.name))

    def run(self) -> int:
        step = (self.args.step or "all").strip()
        if step in ("clone", "all"):
            rc = self.run_clone()
            if rc != 0:
                return rc
        if step == "clone":
            self.ran = 1
            self.print_step_summary()
            return 0
        if step in ("fatjar", "all"):
            rc = self.run_fatjar()
            if rc != 0:
                return rc
        if step == "fatjar":
            self.ran = 1
            self.print_step_summary()
            return 0
        self.run_configuration()
        if step in ("generate-auto", "all"):
            rc = self.run_generate_auto()
            if rc != 0:
                return rc
        elif step == "sync":
            rc = self.run_sync()
            if rc != 0:
                return rc
        if step in ("generate-auto", "sync"):
            self.ran = 1
            self.print_step_summary()
            return 0
        if step == "all":
            self.run_configuration()
        self.steps = self.build_steps()
        self._prefilter_inventory_for_retry(step)
        self._prefilter_inventory_for_pr_tests(step)
        self._prefilter_inventory_for_positive_pr_tests(step)
        if not self._prefilter_inventory_for_annotation_dataset(step):
            return 1
        for r in self.inv_rows:
            ctx = self.build_target_context(r)
            if not ctx:
                continue
            ok = self.process_target(ctx)
            if not ok:
                continue
        self._write_annotation_filtered_summaries(step)

        self.print_step_summary()
        return 0

    def print_step_summary(self) -> None:
        step = (self.args.step or "all").strip()
        print(f"[agt] {step} done.")
        print(f"[agt] {step} ran:     {self.ran}")
        print(f"[agt] {step} skipped: {self.skipped}")

        artifact_lines = self._step_artifact_lines(step)
        for label, value in artifact_lines:
            print(f"[agt] {label}: {value}")

    def _step_artifact_lines(self, step: str) -> List[Tuple[str, Path]]:
        if step == "clone":
            base_dir = self._step_base_dir(self.args.clone_mode, self.args.clone_base_dir)
            out_dir = base_dir / "out"
            return [
                ("Repos dir", base_dir / "repos"),
                ("Repo roots CSV", out_dir / "repo_roots.csv"),
                ("Clone logs", out_dir / "logs-clone"),
                ("Pipeline logs", self._pipeline_logs_dir()),
            ]
        if step == "fatjar":
            base_dir = self._step_base_dir(self.args.fatjar_mode, self.args.fatjar_base_dir)
            out_dir = base_dir / "out"
            return [
                ("Fatjar map", Path(self.args.cut_to_fatjar_map_csv).resolve()),
                ("Fatjar output", out_dir),
                ("Fatjar logs", out_dir / "logs-build"),
                ("Pipeline logs", self._pipeline_logs_dir()),
            ]
        if step == "generate-auto":
            return [
                ("Generated tests", self.generated_dir),
                ("Manual tests", self.manual_dir),
                ("Tests inventory", self.inventory_csv),
                (
                    "Failure summary",
                    (self.generate_auto_output_root or Path(self.args.generate_auto_output_dir)) / "log" / "generate-auto.failures.csv",
                ),
                (
                    "run-agt output",
                    self.generate_auto_output_root or Path(self.args.generate_auto_output_dir),
                ),
                ("Logs", self.logs_dir),
            ]
        if step == "sync":
            return [
                ("Generated tests", self.generated_dir),
                ("Manual tests", self.manual_dir),
                ("Tests inventory", self.inventory_csv),
                (
                    "run-agt output",
                    self.generate_auto_output_root or Path(self.args.generate_auto_output_dir),
                ),
                ("Logs", self.logs_dir),
            ]
        if step == "compile":
            return [
                ("Compile summary", self.compile_summary_csv),
                ("Build dir", self.build_dir),
                ("Logs", self.logs_dir),
            ]
        if step == "run":
            return [
                ("Coverage summary", self.summary_csv),
                ("Coverage errors", self.coverage_errors_csv),
                ("Coverage zero hit", self.coverage_zero_hit_csv),
                ("Coverage report issues", self.coverage_report_issues_csv),
                ("Logs", self.logs_dir),
            ]
        if step == "adopted-run":
            return [
                ("Adopted coverage summary", self.adopted_summary_csv),
                ("Coverage errors", self.coverage_errors_csv),
                ("Logs", self.logs_dir),
            ]
        if step == "coverage-comparison":
            return [
                ("Coverage compare", self.coverage_compare_csv),
                ("Logs", self.logs_dir),
            ]
        if step == "coverage-comparison-reduced":
            return [
                ("Coverage compare reduced", self.coverage_compare_reduced_csv),
                ("Logs", self.logs_dir),
            ]
        if step == "coverage-incremental":
            return [
                ("Coverage incremental", self.coverage_incremental_csv),
                ("Logs", self.logs_dir),
            ]
        if step == "rq4-snapshot-coverage":
            return [
                ("RQ4 snapshot coverage", Path(self.args.rq4_coverage_output)),
                ("Logs", self.logs_dir),
            ]
        if step == "filter":
            auto_variant = self.args.auto_variant
            lines = [
                (
                    "Covfilter summary",
                    covfilter_variant_summary_csv(
                        self.covfilter_out_root,
                        self.adopted_covfilter_out_root,
                        auto_variant,
                        self.args.includes,
                        self.agentic_covfilter_out_root,
                    ),
                ),
                (
                    "Covfilter out",
                    covfilter_variant_root(
                        self.covfilter_out_root,
                        self.adopted_covfilter_out_root,
                        auto_variant,
                        self.agentic_covfilter_out_root,
                    ),
                ),
            ]
            if auto_variant == "auto" and self.args.sanitize_compare:
                lines.extend(
                    [
                        (
                            "Sanitize compare",
                            sanitize_compare_summary_csv(Path(self.args.sanitize_compare_out), self.args.includes),
                        ),
                        ("Sanitized ES", Path(self.args.sanitized_es_dir)),
                    ]
                )
            lines.append(("Logs", self.logs_dir))
            return lines
        if step == "sanitize-es":
            return [
                ("Sanitize summary", sanitize_summary_csv(Path(self.args.sanitized_es_dir), self.args.includes)),
                ("Sanitized ES", Path(self.args.sanitized_es_dir)),
                ("Logs", self.logs_dir),
            ]
        if step == "adopted-filter":
            lines: List[Tuple[str, Path]] = []
            for variant in selected_adopted_variants(self.args.adopted_filter_variants):
                label_prefix = {"adopted": "Adopted", "agentic": "Agentic", "pr-tests": "PR tests"}[variant]
                lines.append(
                    (
                        f"{label_prefix} covfilter summary",
                        covfilter_variant_summary_csv(
                            self.covfilter_out_root,
                            self.adopted_covfilter_out_root,
                            variant,
                            self.args.includes,
                            self.agentic_covfilter_out_root,
                        ),
                    )
                )
                lines.append(
                    (
                        f"{label_prefix} covfilter out",
                        covfilter_variant_root(
                            self.covfilter_out_root,
                            self.adopted_covfilter_out_root,
                            variant,
                            self.agentic_covfilter_out_root,
                        ),
                    )
                )
            lines.append(("Logs", self.logs_dir))
            return lines
        if step == "reduce":
            auto_variant = self.args.auto_variant
            return [
                ("Reduce summary", reduce_variant_summary_csv(Path(self.args.reduced_out), auto_variant, self.args.includes)),
                ("Reduced out", Path(self.args.reduced_out) / auto_variant),
                ("Logs", self.logs_dir),
            ]
        if step == "adopted-reduce":
            lines = [
                (
                    f"{variant} reduce summary",
                    reduce_variant_summary_csv(self.adopted_reduced_out_root, variant, self.args.includes),
                )
                for variant in selected_adopted_variants(self.args.adopted_filter_variants)
            ]
            return [*lines, ("Reduced out", self.adopted_reduced_out_root), ("Logs", self.logs_dir)]
        if step in ("llm-all", "llm-integration", "llm-integration-step-by-step"):
            return [
                ("LLM adopted summary", llm_adopted_summary_csv_path(self.adopted_root, self.args.includes)),
                ("LLM improve summary", llm_improve_summary_csv_path(self.adopted_root, self.args.includes)),
                ("LLM agentic summary", llm_agentic_summary_csv_path(self.adopted_root, self.args.includes)),
                ("LLM out", self.adopted_root),
                ("Logs", self.logs_dir),
            ]
        if step == "llm-agt-improvement":
            return [
                ("LLM improve summary", llm_improve_summary_csv_path(self.adopted_root, self.args.includes)),
                ("LLM out", self.adopted_root),
                ("Logs", self.logs_dir),
            ]
        if step == "llm-agent":
            return [
                ("LLM agentic summary", llm_agentic_summary_csv_path(self.adopted_root, self.args.includes)),
                ("LLM out", self.adopted_root),
                ("Logs", self.logs_dir),
            ]
        if step == "annotation":
            lines = [
                ("Annotation summary", self.annotation_summary_csv),
                ("Annotation out", Path(self.args.annotation_out)),
                ("Logs", self.logs_dir),
            ]
            if (getattr(self.args, "filtered_dataset", "") or "").strip():
                annotation_root = Path(self.args.annotation_out)
                lines.extend(
                    [
                        ("Filtered summary", annotation_root / "summary_filtered.csv"),
                        ("Filtered auto summary", annotation_root / "summary_filtered.auto.csv"),
                        ("Filtered adopted summary", annotation_root / "summary_filtered.adopted.csv"),
                        ("Filtered agentic summary", annotation_root / "summary_filtered.agentic.csv"),
                        ("Filtered PR tests summary", annotation_root / "summary_filtered.pr-tests.csv"),
                        ("Filtered dataset", annotation_root / "dataset_filtered.csv"),
                    ]
                )
            return lines
        return [
            ("Out dir", self.out_dir),
            ("Logs", self.logs_dir),
        ]

    @staticmethod
    def _step_base_dir(mode: str, base_dir: str) -> Path:
        if (mode or "").strip() == "docker":
            return Path("/work")
        return Path(base_dir).resolve()

    def _pipeline_logs_dir(self) -> Path:
        logs_dir = Path(self.args.out_dir) / "logs"
        ensure_dir(logs_dir)
        return logs_dir.resolve()

    def run_clone(self) -> int:
        return run_clone_step(
            CloneConfig(
                cut_csv=Path(self.args.selected_cut_csv),
                mode=self.args.clone_mode,
                base_dir=self._step_base_dir(self.args.clone_mode, self.args.clone_base_dir),
                update_existing=self.args.clone_update_existing,
                log_dir=Path(self.args.clone_log_dir).resolve() if self.args.clone_log_dir else None,
                out_repos_csv=(
                    Path(self.args.clone_out_repos_csv).resolve() if self.args.clone_out_repos_csv else None
                ),
            )
        )

    def run_fatjar(self) -> int:
        out_map_csv = Path(self.args.cut_to_fatjar_map_csv)
        ensure_dir(out_map_csv.resolve().parent)
        return run_fatjar_step(
            FatjarConfig(
                cut_csv=Path(self.args.selected_cut_csv),
                mode=self.args.fatjar_mode,
                base_dir=self._step_base_dir(self.args.fatjar_mode, self.args.fatjar_base_dir),
                java_home=self.args.fatjar_java_home,
                java21_home=self.args.fatjar_java21_home,
                out_map_csv=out_map_csv,
                retry_only=self.args.fatjar_retry_only,
                log_dir=Path(self.args.fatjar_log_dir).resolve() if self.args.fatjar_log_dir else None,
                repos_csv=Path(self.args.fatjar_repos_csv).resolve() if self.args.fatjar_repos_csv else None,
                failures_csv=(
                    Path(self.args.fatjar_failures_csv).resolve() if self.args.fatjar_failures_csv else None
                ),
            )
        )

    def run_generate_auto(self) -> int:
        step = (self.args.step or "all").strip()
        if step not in ("generate-auto", "all"):
            return 0

        run_agt_script = Path(self.args.generate_auto_script)
        collect_tests_script = Path(self.args.collect_tests_script)
        if not run_agt_script.exists():
            print(f"[agt] generate-auto: FAIL (missing run-agt script): {run_agt_script}")
            return 1
        if not collect_tests_script.exists():
            print(f"[agt] generate-auto: FAIL (missing collect_tests script): {collect_tests_script}")
            return 1

        env = os.environ.copy()
        repos_dir = Path(env.get("REPOS_DIR", str(self.repos_dir))).resolve()
        output_root = Path(env.get("OUTPUT_DIR", self.args.generate_auto_output_dir)).resolve()
        tests_inventory_csv = Path(env.get("TESTS_INVENTORY_CSV", str(self.inventory_csv))).resolve()
        map_csv = self.map_csv.resolve()
        generated_tests_root = output_root / "generated-tests"
        java_home = self._resolve_generate_auto_java_home(env)

        env["REPOS_DIR"] = str(repos_dir)
        env["OUTPUT_DIR"] = str(output_root)
        env["TESTS_INVENTORY_CSV"] = str(tests_inventory_csv)
        env["ONLY_MISSING_GENERATED"] = "1" if self.args.generate_auto_skip_existing else "0"
        if java_home is not None:
            env["JAVA_HOME"] = str(java_home)
            env["PATH"] = f"{java_home / 'bin'}:{env.get('PATH', '')}"
        self.generate_auto_output_root = output_root

        run_log = self.logs_dir / "generate-auto.run-agt.log"
        existing_attempt_dirs = self._generated_attempt_dirs(generated_tests_root)
        run_cmd = [str(run_agt_script.resolve()), str(map_csv)]
        print(f"[agt] generate-auto: running {run_agt_script} with map {map_csv}")
        if java_home is not None:
            print(f"[agt] generate-auto: using JAVA_HOME={java_home}")
        run_proc = subprocess.run(
            run_cmd,
            cwd=str(run_agt_script.resolve().parent),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        run_log.write_text(run_proc.stdout or "", encoding="utf-8", errors="ignore")
        if run_proc.returncode != 0:
            print(f"[agt] generate-auto: FAIL (run-agt) (see {run_log})")
            return run_proc.returncode
        new_attempt_dirs = self._generated_attempt_dirs(generated_tests_root) - existing_attempt_dirs
        selected_targets = self._selected_generate_auto_targets(run_proc.stdout or "")
        if selected_targets > 0 and not new_attempt_dirs:
            print(
                f"[agt] generate-auto: FAIL (run-agt produced no new generated-tests output for {selected_targets} selected targets) (see {run_log})"
            )
            return 1

        return self.run_sync(force=True)

    @staticmethod
    def _generated_attempt_dirs(root: Path) -> Set[Path]:
        if not root.exists():
            return set()
        return {path.resolve() for path in root.iterdir() if path.is_dir()}

    @staticmethod
    def _selected_generate_auto_targets(output: str) -> int:
        match = re.search(r"selected=(\d+)", output)
        if not match:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0

    def _resolve_generate_auto_java_home(self, env: dict[str, str]) -> Optional[Path]:
        configured = (self.args.generate_auto_java_home or "").strip()
        if configured:
            candidate = Path(configured).expanduser().resolve()
            if candidate.exists():
                return candidate

        java21_home = (env.get("JAVA21_HOME") or "").strip()
        if java21_home:
            candidate = Path(java21_home).expanduser().resolve()
            if candidate.exists():
                return candidate

        sdkman_candidates = (env.get("SDKMAN_CANDIDATES_DIR") or "").strip()
        if sdkman_candidates:
            java_root = Path(sdkman_candidates).expanduser().resolve() / "java"
            if java_root.exists():
                matches = sorted(
                    (path for path in java_root.iterdir() if path.is_dir() and path.name.startswith("21.")),
                    key=lambda path: path.name,
                )
                if matches:
                    return matches[-1]
        return None

    def run_sync(self, force: Optional[bool] = None) -> int:
        collect_tests_script = Path(self.args.collect_tests_script)
        if not collect_tests_script.exists():
            print(f"[agt] sync: FAIL (missing collect_tests script): {collect_tests_script}")
            return 1

        force_merge = self.args.sync_force if force is None else force
        output_root = self.generate_auto_output_root or Path(self.args.generate_auto_output_dir).resolve()
        repos_dir = Path(self.repos_dir).resolve()
        collected_tests_root = self.generated_dir.parent.resolve()
        staging_root = Path(tempfile.mkdtemp(prefix="agt-sync-", dir=str(self.out_dir.resolve())))
        map_csv = self.map_csv.resolve()
        collect_cmd = [
            str(collect_tests_script.resolve()),
            "--map",
            str(map_csv),
            "--repos",
            str(repos_dir),
            "--evosuite-root",
            str(output_root),
            "--out",
            str(staging_root),
        ]
        print(f"[agt] sync: collecting generated tests into staging dir {staging_root}")
        try:
            collect_proc = subprocess.run(
                collect_cmd,
                cwd=str(collect_tests_script.resolve().parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            collect_log = self.logs_dir / "sync.collect-tests.log"
            collect_log.write_text(collect_proc.stdout or "", encoding="utf-8", errors="ignore")
            if collect_proc.returncode != 0:
                print(f"[agt] sync: FAIL (collect-tests) (see {collect_log})")
                return collect_proc.returncode

            staged_generated = staging_root / "generated"
            staged_logs = staging_root / "_logs"
            target_generated = collected_tests_root / "generated"
            target_logs = collected_tests_root / "_logs"
            target_manual = collected_tests_root / "manual"
            target_generated_exists = target_generated.exists()

            if target_generated_exists and not force_merge:
                print(f"[agt] sync: FAIL (target already exists: {target_generated}; rerun with --force)")
                return 1
            shutil.copytree(staged_generated, target_generated, dirs_exist_ok=force_merge)

            target_logs.mkdir(parents=True, exist_ok=True)
            for log_name in ("tests_inventory.csv", "warnings.log"):
                staged_log = staged_logs / log_name
                if staged_log.exists():
                    shutil.copy2(staged_log, target_logs / log_name)

            if force_merge and target_generated_exists:
                print(f"[agt] sync: force-merged generated tests under {target_generated}")
            else:
                print(f"[agt] sync: updated generated tests under {target_generated}")
            print(f"[agt] sync: preserved manual tests under {target_manual}")
            return 0
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    @staticmethod
    def _read_problem_category_targets(summary_csv: Path, *, problem_category: str) -> Set[Tuple[str, str]]:
        if not summary_csv.exists():
            return set()
        targets: Set[Tuple[str, str]] = set()
        try:
            with summary_csv.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    if (row.get("problem_category", "") or "").strip() != problem_category:
                        continue
                    repo = (row.get("repo", "") or "").strip()
                    fqcn = (row.get("fqcn", "") or "").strip()
                    if repo and fqcn:
                        targets.add((repo, fqcn))
        except OSError:
            return set()
        return targets

    @classmethod
    def _read_zero_kept_targets(cls, summary_csv: Path) -> Set[Tuple[str, str]]:
        return cls._read_problem_category_targets(summary_csv, problem_category="zero_kept_tests")

    @classmethod
    def _read_missing_test_deltas_targets(cls, summary_csv: Path) -> Set[Tuple[str, str]]:
        return cls._read_problem_category_targets(summary_csv, problem_category="missing_test_deltas_csv")

    def _reduce_summary_csv_for_variant(self, variant: str) -> Path:
        if variant == "adopted":
            return self.adopted_reduce_summary_csv
        if variant == "agentic":
            return self.agentic_reduce_summary_csv
        if variant == "pr-tests":
            return reduce_variant_summary_csv(self.adopted_reduced_out_root, variant, self.args.includes)
        return reduce_variant_summary_csv(Path(self.args.reduced_out), variant, self.args.includes)

    def _target_selected_for_zero_kept_retry(self, repo: str, fqcn: str, *, variants: Tuple[str, ...]) -> bool:
        if not getattr(self.args, "retry_zero_kept_tests", False):
            return True

        cache = getattr(self, "_retry_zero_kept_targets_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_retry_zero_kept_targets_cache", cache)

        typed_cache: Dict[str, Set[Tuple[str, str]]] = cache
        for variant in variants:
            targets = typed_cache.get(variant)
            if targets is None:
                summary_csv = self._reduce_summary_csv_for_variant(variant)
                targets = self._read_zero_kept_targets(summary_csv)
                typed_cache[variant] = targets
            if (repo, fqcn) in targets:
                return True
        return False

    def _target_selected_for_missing_test_deltas_retry(self, repo: str, fqcn: str, *, variants: Tuple[str, ...]) -> bool:
        if not getattr(self.args, "retry_missing_test_deltas_csv", False):
            return True

        cache = getattr(self, "_retry_missing_test_deltas_targets_cache", None)
        if cache is None:
            cache = {}
            setattr(self, "_retry_missing_test_deltas_targets_cache", cache)

        typed_cache: Dict[str, Set[Tuple[str, str]]] = cache
        for variant in variants:
            targets = typed_cache.get(variant)
            if targets is None:
                summary_csv = self._reduce_summary_csv_for_variant(variant)
                targets = self._read_missing_test_deltas_targets(summary_csv)
                typed_cache[variant] = targets
            if (repo, fqcn) in targets:
                return True
        return False

    @staticmethod
    def _retry_variants_for_step(step: str) -> Tuple[str, ...]:
        if step in ("llm-agent", "llm-agt-improvement"):
            return ("agentic",)
        if step in ("llm-integration", "llm-integration-step-by-step"):
            return ("adopted",)
        return ()

    def _active_retry_problem_categories(self, step: str) -> Tuple[str, ...]:
        categories: List[str] = []
        if step in ("adopted-filter", "llm-agent", "llm-agt-improvement", "llm-integration", "llm-integration-step-by-step") and getattr(
            self.args, "retry_zero_kept_tests", False
        ):
            categories.append("zero_kept_tests")
        if step in ("filter", "adopted-filter") and getattr(self.args, "retry_missing_test_deltas_csv", False):
            categories.append("missing_test_deltas_csv")
        return tuple(categories)

    def _target_selected_for_retry(self, repo: str, fqcn: str, *, variants: Tuple[str, ...], step: str) -> bool:
        if not self._active_retry_problem_categories(step):
            return True
        matched = False
        if step in ("adopted-filter", "llm-agent", "llm-agt-improvement", "llm-integration", "llm-integration-step-by-step") and getattr(
            self.args, "retry_zero_kept_tests", False
        ):
            matched = self._target_selected_for_zero_kept_retry(repo, fqcn, variants=variants) or matched
        if step in ("filter", "adopted-filter") and getattr(self.args, "retry_missing_test_deltas_csv", False):
            matched = self._target_selected_for_missing_test_deltas_retry(repo, fqcn, variants=variants) or matched
        return matched

    def _prefilter_inventory_for_retry(self, step: str) -> None:
        retry_categories = self._active_retry_problem_categories(step)
        if not retry_categories:
            return

        if step == "filter":
            variants = (self.args.auto_variant,)
        elif step == "adopted-filter":
            variants = selected_adopted_variants(self.args.adopted_filter_variants)
        else:
            if not getattr(self.args, "retry_zero_kept_tests", False):
                return
            variants = self._retry_variants_for_step(step)
        if not variants:
            return

        before = len(self.inv_rows)
        if before == 0:
            return

        filtered: List[Dict[str, str]] = []
        skipped = 0
        for row in self.inv_rows:
            repo = (row.get("repo", "") or "").strip().strip('"')
            fqcn = (row.get("fqcn", "") or "").strip().strip('"')
            if repo and fqcn and self._target_selected_for_retry(repo, fqcn, variants=variants, step=step):
                filtered.append(row)
            else:
                skipped += 1

        self.inv_rows = filtered
        if skipped:
            self.skipped += skipped
            print(
                f'[agt] {step}: Retry prefilter kept {len(filtered)}/{before} targets '
                f"(skipped {skipped} not marked {' or '.join(retry_categories)})."
            )

    def _pr_tests_only_request(self, step: str) -> bool:
        adopted_like_steps = {
            "adopted-filter",
            "adopted-reduce",
            "adopted-run",
            "compare",
            "coverage-comparison",
            "coverage-comparison-reduced",
        }
        if step in adopted_like_steps:
            return set(selected_adopted_variants(self.args.adopted_filter_variants)) == {"pr-tests"}
        if step == "coverage-incremental":
            selected = {
                variant.strip().lower()
                for variant in (self.args.coverage_incremental_variants or "").split(",")
                if variant.strip()
            }
            return selected == {"pr-tests"}
        if step == "annotation":
            selected = {
                variant.strip().lower()
                for variant in (self.args.annotation_variants or "").split(",")
                if variant.strip()
            }
            return selected == {"pr-tests"}
        return step == "pull-request-maker"

    def _pr_tests_only_needs_generated_sources(self, step: str) -> bool:
        if not self._pr_tests_only_request(step):
            return True
        return step in {"compare", "coverage-comparison", "coverage-comparison-reduced"}

    def _prefilter_inventory_for_pr_tests(self, step: str) -> None:
        if not self._pr_tests_only_request(step):
            return

        before = len(self.inv_rows)
        filtered: List[Dict[str, str]] = []
        for row in self.inv_rows:
            repo = (row.get("repo", "") or "").strip().strip('"')
            fqcn = (row.get("fqcn", "") or "").strip().strip('"')
            if not repo or not fqcn:
                continue
            target_id = f"{repo_to_dir(repo)}_{fqcn.replace('.', '_')}"
            if pr_test_path(self.pr_tests_root, target_id, fqcn):
                filtered.append(row)

        self.inv_rows = filtered
        skipped = before - len(filtered)
        if skipped:
            self.skipped += skipped
        print(
            f"[agt] {step}: PR-tests prefilter kept {len(filtered)}/{before} targets "
            f"(skipped {skipped} targets without PR tests)."
        )

    def _prefilter_inventory_for_positive_pr_tests(self, step: str) -> None:
        downstream_steps = {
            "adopted-run",
            "compare",
            "coverage-comparison",
            "coverage-comparison-reduced",
            "coverage-incremental",
        }
        if step not in downstream_steps or not self._pr_tests_only_request(step):
            return

        def has_positive_delta(row: Dict[str, str]) -> bool:
            repo = (row.get("repo", "") or "").strip().strip('"')
            fqcn = (row.get("fqcn", "") or "").strip().strip('"')
            target_id = f"{repo_to_dir(repo)}_{fqcn.replace('.', '_')}"
            covfilter_dir = self.adopted_covfilter_out_root / "pr-tests" / target_id
            deltas_csv = next(
                (
                    candidate
                    for candidate in (
                        covfilter_dir / "test_deltas_selected.csv",
                        covfilter_dir / "test_deltas_kept.csv",
                        covfilter_dir / "test_deltas_all.csv",
                    )
                    if candidate.exists()
                ),
                None,
            )
            if deltas_csv is None:
                return False
            try:
                with deltas_csv.open("r", encoding="utf-8", newline="") as handle:
                    for delta in csv.DictReader(handle):
                        for field in ("added_lines", "added_methods", "added_branches", "added_instructions"):
                            try:
                                if int((delta.get(field, "") or "0").strip()) > 0:
                                    return True
                            except ValueError:
                                continue
            except OSError:
                return False
            return False

        before = len(self.inv_rows)
        self.inv_rows = [row for row in self.inv_rows if has_positive_delta(row)]
        skipped = before - len(self.inv_rows)
        if skipped:
            self.skipped += skipped
        print(
            f"[agt] {step}: Positive PR-coverage prefilter kept {len(self.inv_rows)}/{before} targets "
            f"(skipped {skipped} targets with no added coverage)."
        )

    def _prefilter_inventory_for_annotation_dataset(self, step: str) -> bool:
        filtered_dataset = (getattr(self.args, "filtered_dataset", "") or "").strip()
        if not filtered_dataset or step not in ("annotation", "coverage-incremental", "all"):
            return True

        dataset_csv = Path(filtered_dataset)
        if not dataset_csv.exists():
            print(f"[agt] {step}: FAIL (filtered dataset not found): {dataset_csv}")
            return False

        targets: List[Tuple[str, str]] = []
        seen: Set[Tuple[str, str]] = set()
        try:
            with dataset_csv.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    repo = (row.get("repo", "") or "").strip()
                    fqcn = (row.get("fqcn", "") or "").strip()
                    if not repo or not fqcn:
                        continue
                    key = (repo, fqcn)
                    if key in seen:
                        continue
                    seen.add(key)
                    targets.append(key)
        except OSError:
            print(f"[agt] {step}: FAIL (cannot read filtered dataset): {dataset_csv}")
            return False

        if not targets:
            print(f"[agt] {step}: FAIL (filtered dataset has no repo/fqcn rows): {dataset_csv}")
            return False

        self.annotation_filtered_targets = targets
        allowed = set(targets)
        before = len(self.inv_rows)
        if before == 0:
            print(f"[agt] {step}: filtered dataset loaded {len(targets)} targets.")
            return True

        kept: List[Dict[str, str]] = []
        skipped = 0
        for row in self.inv_rows:
            repo = (row.get("repo", "") or "").strip().strip('"')
            fqcn = (row.get("fqcn", "") or "").strip().strip('"')
            if (repo, fqcn) in allowed:
                kept.append(row)
            else:
                skipped += 1

        self.inv_rows = kept
        if skipped:
            self.skipped += skipped
        print(
            f'[agt] {step}: Filtered-dataset prefilter kept {len(kept)}/{before} targets '
            f'(dataset size {len(targets)}, skipped {skipped}).'
        )
        return True

    def _write_annotation_filtered_summaries(self, step: str) -> None:
        if step not in ("annotation", "all"):
            return
        if not self.annotation_filtered_targets:
            return
        if not self.annotation_summary_csv.exists():
            print(f"[agt] annotation: Skip filtered summaries (missing annotation summary): {self.annotation_summary_csv}")
            return

        with self.annotation_summary_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]

        if not fieldnames:
            print(f"[agt] annotation: Skip filtered summaries (empty header): {self.annotation_summary_csv}")
            return

        ordered_targets = list(self.annotation_filtered_targets)
        allowed = set(ordered_targets)
        filtered_rows = [
            row
            for row in rows
            if (
                ((row.get("repo", "") or "").strip(), (row.get("fqcn", "") or "").strip())
                in allowed
            )
        ]

        annotation_root = Path(self.args.annotation_out)
        summary_filtered_csv = annotation_root / "summary_filtered.csv"
        variant_summary_paths = {
            "auto": annotation_root / "summary_filtered.auto.csv",
            "adopted": annotation_root / "summary_filtered.adopted.csv",
            "agentic": annotation_root / "summary_filtered.agentic.csv",
            "pr-tests": annotation_root / "summary_filtered.pr-tests.csv",
        }
        dataset_filtered_csv = annotation_root / "dataset_filtered.csv"

        def _write_summary(path: Path, out_rows: List[Dict[str, str]]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                if out_rows:
                    writer.writerows(out_rows)

        _write_summary(summary_filtered_csv, filtered_rows)
        for variant, path in variant_summary_paths.items():
            variant_rows = [
                row for row in filtered_rows if (row.get("variant", "") or "").strip().lower() == variant
            ]
            _write_summary(path, variant_rows)

        rank = {"auto": 0, "adopted": 1, "agentic": 2, "pr-tests": 3}
        representative_rows: Dict[Tuple[str, str], Dict[str, str]] = {}
        for row in filtered_rows:
            repo = (row.get("repo", "") or "").strip()
            fqcn = (row.get("fqcn", "") or "").strip()
            if not repo or not fqcn:
                continue
            key = (repo, fqcn)
            candidate_rank = rank.get((row.get("variant", "") or "").strip().lower(), 9)
            current = representative_rows.get(key)
            current_rank = rank.get((current.get("variant", "") or "").strip().lower(), 9) if current else 99
            if current is None or candidate_rank < current_rank:
                representative_rows[key] = row

        dataset_fields = ["repo", "fqcn", "manual_test_fqcn", "input_test_source", "line_total"]
        dataset_rows: List[Dict[str, str]] = []
        for repo, fqcn in ordered_targets:
            representative = representative_rows.get((repo, fqcn), {})
            dataset_rows.append(
                {
                    "repo": repo,
                    "fqcn": fqcn,
                    "manual_test_fqcn": (representative.get("manual_test_fqcn", "") or "").strip(),
                    "input_test_source": (representative.get("input_test_source", "") or "").strip(),
                    "line_total": (representative.get("line_total", "") or "").strip(),
                }
            )

        dataset_filtered_csv.parent.mkdir(parents=True, exist_ok=True)
        with dataset_filtered_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=dataset_fields)
            writer.writeheader()
            if dataset_rows:
                writer.writerows(dataset_rows)

        print(f"[agt] annotation: wrote filtered summary -> {summary_filtered_csv}")
        print(f"[agt] annotation: wrote filtered dataset -> {dataset_filtered_csv}")

    def build_target_context(self, r: Dict[str, str]) -> Optional[TargetContext]:
        repo = (r.get("repo", "") or "").strip().strip('"')
        fqcn = (r.get("fqcn", "") or "").strip().strip('"')
        if not repo or not fqcn:
            return None

        step = (self.args.step or "").strip()
        if step in ("llm-agent", "llm-agt-improvement", "llm-integration", "llm-integration-step-by-step", "adopted-filter"):
            if step in ("llm-agent", "llm-agt-improvement"):
                variants = ("agentic",)
            elif step in ("llm-integration", "llm-integration-step-by-step"):
                variants = ("adopted",)
            else:
                variants = selected_adopted_variants(self.args.adopted_filter_variants)
            if not self._target_selected_for_retry(repo, fqcn, variants=variants, step=step):
                self.skipped += 1
                return None

        # Optional wildcard filtering
        if self.args.includes and self.args.includes != "*":
            if not (fnmatch.fnmatch(repo, self.args.includes) or fnmatch.fnmatch(fqcn, self.args.includes)):
                return None

        gen_files = split_list_field(r.get("generated_files", ""))
        man_files = split_list_field(r.get("manual_files", ""))

        if not gen_files and not man_files:
            self.skipped += 1
            return None

        m = self.mapping.get((repo, fqcn))
        fatjar_path = (m.fatjar_path or "").strip() if m else ""
        fallback_sut_dir = (self.out_dir / "__unused_sut_classes__").resolve()
        ensure_dir(fallback_sut_dir)
        if fatjar_path and not _fatjar_value_is_missing(fatjar_path):
            candidate_sut = Path(fatjar_path).resolve()
            if candidate_sut.exists():
                sut_jar = candidate_sut
            else:
                print(
                    f'[agt] Missing sut fatjar; using fallback classes dir: '
                    f'repo="{repo}" fqcn="{fqcn}" fatjar="{candidate_sut}"'
                )
                sut_jar = fallback_sut_dir
        else:
            sut_jar = fallback_sut_dir

        target_id = f"{repo_to_dir(repo)}_{fqcn.replace('.', '_')}"
        target_build = self.build_dir / "test-classes" / target_id
        ensure_dir(target_build)

        sources: List[Path] = []
        manual_sources: List[Path] = []
        manual_primary_sources: List[Path] = []
        generated_primary_sources: List[Path] = []
        repo_bucket_gen = self.generated_dir / repo_to_dir(repo)
        repo_bucket_man = self.manual_dir / repo_to_dir(repo)
        repo_root_for_deps = self.repos_dir / repo_to_dir(repo)

        def _fallback_bucket_sources(bucket_root: Path) -> List[Path]:
            target_bucket = bucket_root / fqcn
            if not target_bucket.exists():
                return []
            candidates = sorted(
                p for p in target_bucket.glob("*.java")
                if looks_like_test_source(p)
            )
            return candidates

        def _bucket_sources_for_target(bucket_root: Path, filenames: List[str]) -> List[Path]:
            target_bucket = bucket_root / fqcn
            ordered: List[Path] = []
            seen_paths: Set[Path] = set()
            unresolved: List[str] = []

            for name in filenames:
                raw_name = (name or "").strip()
                if not raw_name or raw_name.lower() == "null":
                    continue
                candidate = target_bucket / raw_name
                if candidate.exists():
                    if candidate not in seen_paths:
                        seen_paths.add(candidate)
                        ordered.append(candidate)
                else:
                    unresolved.append(raw_name)

            for candidate in find_tests_in_bucket(bucket_root, unresolved):
                if candidate not in seen_paths:
                    seen_paths.add(candidate)
                    ordered.append(candidate)

            return ordered

        # ---------- GENERATED ----------
        if gen_files and self._pr_tests_only_needs_generated_sources(step):
            expanded: List[str] = []
            seen_names: Set[str] = set()

            for f in gen_files:
                if not f or f.lower() == "null":
                    continue
                if f not in seen_names:
                    seen_names.add(f)
                    expanded.append(f)

                if f.endswith("_ESTest.java"):
                    scaf = f.replace("_ESTest.java", "_ESTest_scaffolding.java")
                    if scaf not in seen_names:
                        seen_names.add(scaf)
                        expanded.append(scaf)

            gen_scaf = [f for f in expanded if looks_like_scaffolding(f)]
            gen_non_scaf = [f for f in expanded if not looks_like_scaffolding(f)]
            generated_candidates = _bucket_sources_for_target(repo_bucket_gen, gen_scaf)
            generated_candidates.extend(_bucket_sources_for_target(repo_bucket_gen, gen_non_scaf))
            if not generated_candidates:
                generated_candidates = _fallback_bucket_sources(repo_bucket_gen)
            for candidate in generated_candidates:
                if generated_source_matches_target(candidate, fqcn):
                    sources.append(candidate)
                    if not looks_like_scaffolding(candidate.name):
                        generated_primary_sources.append(candidate)
                else:
                    print(
                        f'[agt] Ignore generated foreign source: repo="{repo}" fqcn="{fqcn}" file="{candidate.name}"'
                    )

        # ---------- MANUAL ----------
        if man_files:
            manual_primary = _bucket_sources_for_target(repo_bucket_man, man_files)
            if not manual_primary:
                manual_primary = _fallback_bucket_sources(repo_bucket_man)
            filtered_manual_primary: List[Path] = []
            ignored_manual_primary: List[Path] = []
            for candidate in manual_primary:
                if looks_like_test_source(candidate):
                    filtered_manual_primary.append(candidate)
                else:
                    ignored_manual_primary.append(candidate)
            for candidate in ignored_manual_primary:
                print(
                    f'[agt] Ignore manual non-test source: repo="{repo}" fqcn="{fqcn}" file="{candidate.name}"'
                )
            if not filtered_manual_primary:
                fallback_manual = _fallback_bucket_sources(repo_bucket_man)
                for candidate in fallback_manual:
                    if candidate not in filtered_manual_primary:
                        filtered_manual_primary.append(candidate)
            manual_primary = list(filtered_manual_primary)
            manual_primary_sources = list(manual_primary)
            manual_all = expand_manual_sources(manual_primary)
            manual_sources = list(manual_all)
            sources.extend(manual_all)

        # de-dupe
        uniq: List[Path] = []
        seen = set()
        for s in sources:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        sources = uniq

        if not sources:
            print(f'[agt] Skip (no existing .java files): repo="{repo}" fqcn="{fqcn}"')
            self.skipped += 1
            return None

        generated_test_fqcn = first_test_fqcn_from_sources(generated_primary_sources, prefer_estest=True)
        if (
            self.args.auto_variant != "auto-original"
            and generated_test_fqcn
            and not self._pr_tests_only_request(step)
        ):
            sanitized_root = Path(self.args.sanitized_es_dir)
            clear_pair_root(sanitized_root, repo, fqcn)
            sanitized_pair = materialize_sanitized_pair(
                source_root=self.generated_dir,
                sanitized_root=sanitized_root,
                repo=repo,
                fqcn=fqcn,
                test_fqcn=generated_test_fqcn,
            )
            if sanitized_pair is not None:
                sources = list(manual_sources)
                sources.append(sanitized_pair.test_src)
                if sanitized_pair.scaffolding_src is not None and sanitized_pair.scaffolding_src.exists():
                    sources.append(sanitized_pair.scaffolding_src)
                generated_test_fqcn = sanitized_pair.test_fqcn

        # repo root for dependency search (ONLY used when javac asks)
        return TargetContext(
            repo=repo,
            fqcn=fqcn,
            target_id=target_id,
            sut_jar=sut_jar,
            target_build=target_build,
            sources=sources,
            manual_sources=manual_sources,
            final_sources=list(sources),
            repo_root_for_deps=repo_root_for_deps,
            module_rel=(m.module_rel or "").strip() if m else "",
            build_tool=(m.build_tool or "").strip() if m else "",
            class_path=(m.class_path or "").strip() if m else "",
            manual_test_fqcn=first_test_fqcn_from_sources(manual_primary_sources, prefer_estest=False),
            generated_test_fqcn=generated_test_fqcn,
        )

    def process_target(self, ctx: TargetContext) -> bool:
        for step in self.steps:
            if isinstance(step, CompileStep):
                if not step.run(ctx):
                    self.skipped += 1
                    return False

                manual_identities = {
                    parse_package_and_class(source)
                    for source in ctx.manual_sources
                    if parse_package_and_class(source)[1]
                }
                compiled_manual_sources = [
                    source
                    for source in ctx.final_sources
                    if parse_package_and_class(source) in manual_identities
                ]
                compiled_generated_sources = [
                    source
                    for source in ctx.final_sources
                    if parse_package_and_class(source) not in manual_identities
                ]

                if not ctx.manual_test_fqcn or first_test_source_for_fqcn(compiled_manual_sources, ctx.manual_test_fqcn) is None:
                    ctx.manual_test_fqcn = first_test_fqcn_from_sources(
                        compiled_manual_sources,
                        prefer_estest=False,
                    )
                if not ctx.generated_test_fqcn or first_test_source_for_fqcn(compiled_generated_sources, ctx.generated_test_fqcn) is None:
                    ctx.generated_test_fqcn = first_test_fqcn_from_sources(
                        compiled_generated_sources,
                        prefer_estest=True,
                    )
                continue

            if not step.run(ctx):
                self.skipped += 1
                return False
        return True

    def build_steps(self) -> List[Step]:
        return [
            CompileStep(self),
            SanitizeEvoSuiteStep(self),
            AdoptedFixStep(self),
            AdoptedCommentStep(self),
            CovfilterStep(self),
            AdoptedFilterStep(self),
            ReduceStep(self),
            AdoptedReduceStep(self),
            ReducedAnnotationStep(self),
            SendStep(self),
            AgentStep(self),
            CompareStep(self),
            PullRequestMakerStep(self),
            CoverageComparisonStep(self),
            CoverageComparisonReducedStep(self),
            CoverageIncrementalComparisonStep(self),
            Rq4SnapshotCoverageStep(self),
            RunStep(self),
            AdoptedRunStep(self),
        ]


def run_pipeline(args: PipelineArgs) -> int:
    pipeline = Pipeline(args)
    return pipeline.run()
