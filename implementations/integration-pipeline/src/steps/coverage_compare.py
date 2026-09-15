from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from ..core.coverage import write_coverage_row
from ..core.java import compile_test_set_smart
from ..core.layout import PIPELINE_OUTPUT_ROOT
from ..core.rq4_native_coverage import (
    NativeCoverageResult,
    measure_native_test_file,
    prepare_target_checkout,
    prepare_shared_checkout,
    remove_shared_checkout,
)
from ..pipeline.config import ADOPTED_LIKE_VARIANTS, covfilter_candidate_out_dirs, selected_adopted_variants
from .run import run_test_with_coverage
from .covfilter import _materialize_pr_covfilter_sources
from ..pipeline.helpers import (
    adopted_variants,
    find_scaffolding_source,
    fix_reduced_scaffolding_import,
    first_test_source_for_fqcn,
    is_empty_generated_test_source,
    reduced_test_path,
    reduced_variant_test_path,
)
from .base import Step


RQ4_FIELDS = (
    "target_id", "repo", "fqcn", "pr", "url", "state", "cutoff_date",
    "base_sha", "submitted_sha", "measurement_basis", "phase",
    "production_fqcn", "test_fqcn", "status",
    "line_percentage_coverage", "line_covered", "line_total",
    "branch_percentage_coverage", "branch_covered", "branch_total",
    "command", "detail", "snapshot_path", "log_file",
)

RQ4_REPO_ALIASES = {
    "camunda-cloud/zeebe": "camunda/camunda",
    "eclipse/jetty.project": "jetty/jetty.project",
    "real-logic/aeron": "aeron-io/aeron",
    "seata/seata": "apache/incubator-seata",
}


def _rq4_normalize_repo(repo: str) -> str:
    value = repo.strip().lower()
    return RQ4_REPO_ALIASES.get(value, value)


def _rq4_manifest_rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    mapped = {row["target_id"]: row for row in rows}
    if len(rows) != 32 or len(mapped) != 32:
        raise ValueError(f"RQ4 snapshot manifest must contain 32 unique targets: {path}")
    return mapped


def _append_rq4_row(path: Path, row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RQ4_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in RQ4_FIELDS})


class Rq4SnapshotCoverageStep(Step):
    """Measure full committed test files before and after each submitted PR.

    This deliberately does not inspect ``// Adopted Tests`` or infer added methods.
    Each phase runs the complete committed file in its matching repository
    revision. Coverage is extracted at CUT source-file level, including nested
    classes compiled from that source file.
    """

    step_names = ("rq4-snapshot-coverage",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        manifest_path = Path(self.pipeline.args.rq4_snapshot_manifest).resolve()
        try:
            manifest = _rq4_manifest_rows(manifest_path)
        except (OSError, ValueError) as error:
            print(f"[agt] rq4-snapshot-coverage: FAIL ({error})")
            return False
        row = next(
            (
                item for item in manifest.values()
                if _rq4_normalize_repo(item.get("repo_normalized", "")) == _rq4_normalize_repo(ctx.repo)
                and item.get("fqcn", "").rsplit(".", 1)[-1] == ctx.fqcn.rsplit(".", 1)[-1]
            ),
            None,
        )
        if row is None:
            return True
        short_id = row["target_id"]

        output = Path(self.pipeline.args.rq4_coverage_output).resolve()
        if output.exists():
            with output.open(encoding="utf-8", newline="") as handle:
                if any(item.get("target_id") == short_id for item in csv.DictReader(handle)):
                    print(f"[agt] rq4-snapshot-coverage: Skip existing {short_id}")
                    return True

        target_root = self.pipeline.build_dir / "rq4-snapshot-coverage" / short_id
        if target_root.exists():
            shutil.rmtree(target_root)
        target_root.mkdir(parents=True, exist_ok=True)

        snapshots_root = manifest_path.parents[3]
        jacoco_cli = (
            self.pipeline.jacoco_agent.parent / "org.jacoco.cli-run-0.8.14.jar"
        ).resolve()
        phase_rows: list[tuple[str, NativeCoverageResult, Path, Path]] = []
        for phase, sha_field, snapshot_field in (
            ("before", "base_sha", "base_snapshot"),
            ("after", "submitted_sha", "submitted_snapshot"),
        ):
            phase_root = target_root / phase
            checkout = phase_root / "repository"
            snapshot = (snapshots_root / row[snapshot_field]).resolve()
            log_file = phase_root / "build.log"
            ok, detail = prepare_shared_checkout(
                ctx.repo_root_for_deps, checkout, row[sha_field]
            )
            if not ok:
                measured = NativeCoverageResult(status="checkout_failed", detail=detail)
            else:
                prepare_target_checkout(short_id, ctx.repo_root_for_deps, checkout)
                original_test_path = row.get("original_target_test_path", "").strip()
                submitted_test_path = row["outcome_target_test_path"].strip()
                if phase == "before":
                    test_paths = [original_test_path or submitted_test_path]
                else:
                    test_paths = list(
                        dict.fromkeys(
                            path for path in (original_test_path, submitted_test_path)
                            if path
                        )
                    )
                measured = measure_native_test_file(
                    target_id=short_id,
                    repo=checkout,
                    target_class=row["target_class"],
                    test_paths=test_paths,
                    production_path=ctx.class_path,
                    build_tool=ctx.build_tool,
                    jacoco_agent=self.pipeline.jacoco_agent,
                    jacoco_cli=jacoco_cli,
                    output_dir=phase_root,
                    timeout_seconds=self.pipeline.args.rq4_build_timeout_seconds,
                )
                removed, cleanup_detail = remove_shared_checkout(
                    ctx.repo_root_for_deps, checkout
                )
                if not removed:
                    print(
                        f"[agt] rq4-snapshot-coverage: cleanup failed for "
                        f"{short_id} {phase}: {cleanup_detail}"
                    )
            phase_rows.append((phase, measured, snapshot, log_file))

        after = next(measured for phase, measured, _, _ in phase_rows if phase == "after")
        normalized_rows: list[tuple[str, NativeCoverageResult, Path, Path]] = []
        for phase, measured, snapshot, log_file in phase_rows:
            if measured.status == "test_file_absent" and phase == "before" and after.line_total:
                measured = NativeCoverageResult(
                    status="test_file_absent",
                    line_total=after.line_total,
                    branch_total=after.branch_total,
                    production_fqcn=after.production_fqcn,
                    test_fqcn=after.test_fqcn,
                    detail="the submitted PR adds a new test file",
                )
            normalized_rows.append((phase, measured, snapshot, log_file))

        for phase, measured, snapshot, log_file in normalized_rows:
            line_percentage = (
                100.0 * measured.line_covered / measured.line_total
                if measured.line_total else 0.0
            )
            branch_percentage = (
                100.0 * measured.branch_covered / measured.branch_total
                if measured.branch_total else 0.0
            )
            _append_rq4_row(
                output,
                {
                    "target_id": short_id,
                    "repo": ctx.repo,
                    "fqcn": ctx.fqcn,
                    "pr": row["pr"],
                    "url": row["url"],
                    "state": row["state"],
                    "cutoff_date": row["cutoff_date"],
                    "base_sha": row["base_sha"],
                    "submitted_sha": row["submitted_sha"],
                    "measurement_basis": "complete_test_files_at_merge_base_and_submitted_head_on_matching_revisions_source_file_cut",
                    "phase": phase,
                    "production_fqcn": measured.production_fqcn,
                    "test_fqcn": measured.test_fqcn,
                    "status": measured.status,
                    "line_percentage_coverage": f"{line_percentage:.6f}",
                    "line_covered": str(measured.line_covered),
                    "line_total": str(measured.line_total),
                    "branch_percentage_coverage": f"{branch_percentage:.6f}",
                    "branch_covered": str(measured.branch_covered),
                    "branch_total": str(measured.branch_total),
                    "command": measured.command,
                    "detail": measured.detail,
                    "snapshot_path": str(snapshot),
                    "log_file": str(log_file),
                },
            )
        self.pipeline.ran += 1
        return True

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


def _matching_coverage_rows(
    csv_path: Path,
    *,
    repo: str,
    fqcn: str,
    variants: set[str],
) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            return [
                dict(row)
                for row in csv.DictReader(handle)
                if row.get("repo") == repo and row.get("fqcn") == fqcn and row.get("variant") in variants
            ]
    except OSError:
        return []


def _append_existing_coverage_rows(
    csv_path: Path,
    rows: list[dict[str, str]],
    *,
    variant_override: Optional[str] = None,
) -> None:
    for row in rows:
        status = "passed"
        for field in ("failed", "timeout", "skipped"):
            if (row.get(field, "0") or "0").strip() not in {"", "0"}:
                status = field
                break
        write_coverage_row(
            csv_path=csv_path,
            repo=row.get("repo", ""),
            fqcn=row.get("fqcn", ""),
            variant=variant_override or row.get("variant", ""),
            line_covered=_read_int(row, "line_covered"),
            line_total=_read_int(row, "line_total"),
            branch_covered=_read_int(row, "branch_covered"),
            branch_total=_read_int(row, "branch_total"),
            status=status,
        )



def run_coverage_for_test(
    *,
    test_src: Path,
    variant: str,
    repo: str,
    fqcn: str,
    build_dir: Path,
    compiled_tests_dir: Optional[Path],
    log_file: Path,
    exec_file: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    jacoco_agent: Path,
    tool_jar: Path,
    timeout_ms: int,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
    build_tool: str,
    summary_csv: Path,
    max_rounds: int = 3,
    compile_sources: Optional[list[Path]] = None,
) -> None:
    if compile_sources is not None:
        ok_compile, _tail, _final_sources = compile_test_set_smart(
            java_files=compile_sources,
            build_dir=build_dir,
            libs_glob_cp=libs_glob_cp,
            sut_jar=sut_jar,
            log_file=log_file,
            repo_root_for_deps=repo_root_for_deps,
            module_rel=module_rel,
            build_tool=build_tool,
            max_rounds=max_rounds,
        )
        if not ok_compile:
            write_coverage_row(
                csv_path=summary_csv,
                repo=repo,
                fqcn=fqcn,
                variant=variant,
                line_covered=0,
                line_total=0,
                branch_covered=0,
                branch_total=0,
                status="failed",
            )
            return
        compiled_tests_dir = build_dir

    if compiled_tests_dir is None:
        write_coverage_row(
            csv_path=summary_csv,
            repo=repo,
            fqcn=fqcn,
            variant=variant,
            line_covered=0,
            line_total=0,
            branch_covered=0,
            branch_total=0,
            status="failed",
        )
        return
    jacoco_cli = jacoco_agent.parent / "org.jacoco.cli-run-0.8.14.jar"
    result, stats, _test_fqcn, _coverage_observation = run_test_with_coverage(
        test_src=test_src,
        compiled_tests_dir=compiled_tests_dir,
        exec_file=exec_file,
        run_log=log_file,
        sut_jar=sut_jar,
        libs_glob_cp=libs_glob_cp,
        jacoco_agent=jacoco_agent,
        tool_jar=tool_jar,
        timeout_ms=timeout_ms,
        jacoco_cli=jacoco_cli,
        target_fqcn=fqcn,
        coverage_tmp_dir=build_dir,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
        build_tool=build_tool,
    )

    write_coverage_row(
        csv_path=summary_csv,
        repo=repo,
        fqcn=fqcn,
        variant=variant,
        line_covered=stats[0],
        line_total=stats[1],
        branch_covered=stats[2],
        branch_total=stats[3],
        status=result.status,
    )


class CoverageComparisonStep(Step):
    step_names = ("coverage-comparison",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        auto_variant = self.pipeline.args.auto_variant
        if self.pipeline.args.skip_empty_tests:
            generated_test_src = first_test_source_for_fqcn(ctx.final_sources or ctx.sources, ctx.generated_test_fqcn)
            if is_empty_generated_test_source(generated_test_src):
                print(f'[agt] coverage-comparison: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                return True
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] coverage-comparison: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            return True

        manual_test_src = first_test_source_for_fqcn(ctx.manual_sources or ctx.final_sources, ctx.manual_test_fqcn)
        generated_test_src = first_test_source_for_fqcn(ctx.final_sources, ctx.generated_test_fqcn)
        selected = set(selected_adopted_variants(self.pipeline.args.adopted_filter_variants))
        if selected == {"pr-tests"}:
            base_rows = _matching_coverage_rows(
                self.pipeline.summary_csv,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variants={"manual", auto_variant},
            )
            pr_rows = _matching_coverage_rows(
                self.pipeline.adopted_summary_csv,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variants={"pr-tests"},
            )
            if {row.get("variant", "") for row in base_rows} == {"manual", auto_variant} and len(pr_rows) == 1:
                _append_existing_coverage_rows(self.pipeline.coverage_compare_csv, [*base_rows, *pr_rows])
                return True
        variants = [
            (variant, source)
            for variant, source in adopted_variants(
                self.pipeline.adopted_root,
                ctx.target_id,
                ctx.fqcn,
                self.pipeline.pr_tests_root,
                self.pipeline.out_dir / "pr-tests",
            )
            if variant in selected
        ]
        tool_jar = Path(self.pipeline.args.tool_jar)

        if manual_test_src and manual_test_src.exists():
            run_coverage_for_test(
                test_src=manual_test_src,
                variant="manual",
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                build_dir=self.pipeline.build_dir / "coverage-compare" / "manual" / ctx.target_id,
                compiled_tests_dir=ctx.target_build,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.coverage.manual.log",
                exec_file=self.pipeline.out_dir / f"{ctx.target_id}__manual.exec",
                libs_glob_cp=self.pipeline.args.libs_cp,
                sut_jar=ctx.sut_jar,
                jacoco_agent=self.pipeline.jacoco_agent,
                tool_jar=tool_jar,
                timeout_ms=self.pipeline.args.timeout_ms,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
                summary_csv=self.pipeline.coverage_compare_csv,
                compile_sources=None,
            )

        if generated_test_src and generated_test_src.exists():
            run_coverage_for_test(
                test_src=generated_test_src,
                variant=auto_variant,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                build_dir=self.pipeline.build_dir / "coverage-compare" / auto_variant / ctx.target_id,
                compiled_tests_dir=ctx.target_build,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.coverage.{auto_variant}.log",
                exec_file=self.pipeline.out_dir / f"{ctx.target_id}__{auto_variant}.exec",
                libs_glob_cp=self.pipeline.args.libs_cp,
                sut_jar=ctx.sut_jar,
                jacoco_agent=self.pipeline.jacoco_agent,
                tool_jar=tool_jar,
                timeout_ms=self.pipeline.args.timeout_ms,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
                summary_csv=self.pipeline.coverage_compare_csv,
                compile_sources=None,
            )

        for variant, adopted_src in variants:
            if not adopted_src.exists():
                continue
            if variant == "pr-tests":
                reduced_src = reduced_variant_test_path(
                    self.pipeline.adopted_reduced_out_root,
                    variant,
                    ctx.target_id,
                    self.pipeline.args.adopted_reduce_max_tests,
                    allow_any_top_n=True,
                )
                if reduced_src is not None:
                    adopted_src = reduced_src
                else:
                    staged_sources, _staged_fqcn, allowed_methods, _output_fqcn = _materialize_pr_covfilter_sources(
                        ctx=ctx,
                        pr_source=adopted_src,
                        stage_root=self.pipeline.out_dir / "pr-tests-coverage" / ctx.target_id,
                    )
                    if not staged_sources or not allowed_methods:
                        continue
                    adopted_src = staged_sources[-1]
            run_coverage_for_test(
                test_src=adopted_src,
                variant=variant,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                build_dir=self.pipeline.build_dir / "coverage-compare" / variant / ctx.target_id,
                compiled_tests_dir=None,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.coverage.{variant}.log",
                exec_file=self.pipeline.out_dir / f"{ctx.target_id}__{variant}.exec",
                libs_glob_cp=self.pipeline.args.libs_cp,
                sut_jar=ctx.sut_jar,
                jacoco_agent=self.pipeline.jacoco_agent,
                tool_jar=tool_jar,
                timeout_ms=self.pipeline.args.timeout_ms,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
                summary_csv=self.pipeline.coverage_compare_csv,
                compile_sources=[adopted_src],
            )
        return True


class CoverageComparisonReducedStep(Step):
    step_names = ("coverage-comparison-reduced",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        auto_variant = self.pipeline.args.auto_variant
        if self.pipeline.args.skip_empty_tests:
            generated_test_src = first_test_source_for_fqcn(ctx.final_sources or ctx.sources, ctx.generated_test_fqcn)
            if is_empty_generated_test_source(generated_test_src):
                print(
                    f'[agt] coverage-comparison-reduced: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"'
                )
                return True
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(
                f'[agt] coverage-comparison-reduced: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"'
            )
            return True

        selected = set(selected_adopted_variants(self.pipeline.args.adopted_filter_variants))
        if selected == {"pr-tests"}:
            pr_rows = _matching_coverage_rows(
                self.pipeline.adopted_summary_csv,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variants={"pr-tests"},
            )
            if len(pr_rows) == 1:
                _append_existing_coverage_rows(
                    self.pipeline.coverage_compare_reduced_csv,
                    pr_rows,
                    variant_override="pr-tests-reduced",
                )
                return True

        reduced_root = Path(self.pipeline.args.reduced_out)
        top_n = max(1, min(self.pipeline.args.coverage_compare_top_n, 100))
        generated_test_src = first_test_source_for_fqcn(ctx.final_sources, ctx.generated_test_fqcn)
        auto_reduced = (
            reduced_test_path(
                reduced_root,
                ctx.target_id,
                generated_test_src,
                top_n,
                preferred_variants=[auto_variant],
            )
            if generated_test_src
            else None
        )
        tool_jar = Path(self.pipeline.args.tool_jar)

        if auto_reduced and auto_reduced.exists():
            compile_sources = [auto_reduced]
            scaffolding = find_scaffolding_source(auto_reduced, ctx.final_sources)
            if scaffolding and scaffolding.exists():
                compile_sources.append(scaffolding)
            fix_reduced_scaffolding_import(auto_reduced, scaffolding)
            run_coverage_for_test(
                test_src=auto_reduced,
                variant=f"{auto_variant}-reduced",
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                build_dir=self.pipeline.build_dir / "coverage-compare-reduced" / auto_variant / ctx.target_id,
                compiled_tests_dir=None,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.coverage.{auto_variant}-reduced.log",
                exec_file=self.pipeline.out_dir / f"{ctx.target_id}__{auto_variant}_reduced.exec",
                libs_glob_cp=self.pipeline.args.libs_cp,
                sut_jar=ctx.sut_jar,
                jacoco_agent=self.pipeline.jacoco_agent,
                tool_jar=tool_jar,
                timeout_ms=self.pipeline.args.timeout_ms,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
                summary_csv=self.pipeline.coverage_compare_reduced_csv,
                compile_sources=compile_sources,
            )

        for variant in selected_adopted_variants(self.pipeline.args.adopted_filter_variants):
            reduced_src = reduced_variant_test_path(self.pipeline.adopted_reduced_out_root, variant, ctx.target_id, top_n)
            if not reduced_src or not reduced_src.exists():
                continue
            fix_reduced_scaffolding_import(reduced_src, find_scaffolding_source(reduced_src, ctx.final_sources))
            run_coverage_for_test(
                test_src=reduced_src,
                variant=f"{variant}-reduced",
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                build_dir=self.pipeline.build_dir / "coverage-compare-reduced" / variant / ctx.target_id,
                compiled_tests_dir=None,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.coverage.{variant}-reduced.log",
                exec_file=self.pipeline.out_dir / f"{ctx.target_id}__{variant}_reduced.exec",
                libs_glob_cp=self.pipeline.args.libs_cp,
                sut_jar=ctx.sut_jar,
                jacoco_agent=self.pipeline.jacoco_agent,
                tool_jar=tool_jar,
                timeout_ms=self.pipeline.args.timeout_ms,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
                summary_csv=self.pipeline.coverage_compare_reduced_csv,
                compile_sources=[reduced_src],
            )
        return True


_INCREMENTAL_VARIANTS = ("auto", "auto-original", *ADOPTED_LIKE_VARIANTS)


def _read_int(row: dict[str, str], key: str) -> int:
    raw = (row.get(key, "") or "").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _percentage(numerator: int, denominator: int) -> float:
    return (100.0 * numerator / denominator) if denominator > 0 else 0.0


def _selected_incremental_variants(variants_csv: str, auto_variant: str) -> tuple[str, ...]:
    requested = {variant.strip().lower() for variant in (variants_csv or "").split(",") if variant.strip()}
    if not requested or "all" in requested:
        selected = [auto_variant, *ADOPTED_LIKE_VARIANTS]
    else:
        selected = [variant for variant in _INCREMENTAL_VARIANTS if variant in requested]
    return tuple(dict.fromkeys(selected))


def _class_deltas_csv_for_variant(pipeline, variant: str, target_id: str) -> Optional[Path]:
    for candidate in covfilter_candidate_out_dirs(
        pipeline.covfilter_out_root,
        pipeline.adopted_covfilter_out_root,
        variant,
        target_id,
        pipeline.agentic_covfilter_out_root,
    ):
        class_deltas_csv = candidate / "class_deltas.csv"
        if class_deltas_csv.exists():
            return class_deltas_csv
    return None


def _incremental_target_totals(pipeline, repo: str, fqcn: str) -> tuple[int, int]:
    cache = getattr(pipeline, "_coverage_incremental_totals_cache", None)
    if cache is None:
        cache = {}
        csv_paths = (
            pipeline.summary_csv,
            pipeline.adopted_summary_csv,
            pipeline.coverage_compare_csv,
            pipeline.coverage_compare_reduced_csv,
            Path(f"{PIPELINE_OUTPUT_ROOT}/aggregate/coverage/coverage_summary.csv"),
            Path(f"{PIPELINE_OUTPUT_ROOT}/aggregate/coverage/adopted_coverage_summary.csv"),
            Path(f"{PIPELINE_OUTPUT_ROOT}/aggregate/coverage/coverage_compare.csv"),
            Path(f"{PIPELINE_OUTPUT_ROOT}/aggregate/coverage/coverage_compare_reduced.csv"),
        )
        for csv_path in dict.fromkeys(csv_paths):
            if not csv_path.exists():
                continue
            try:
                with csv_path.open("r", encoding="utf-8", newline="") as handle:
                    for row in csv.DictReader(handle):
                        row_repo = (row.get("repo", "") or "").strip()
                        row_fqcn = (row.get("fqcn", "") or "").strip()
                        if not row_repo or not row_fqcn:
                            continue
                        key = (row_repo, row_fqcn)
                        line_total, branch_total = cache.get(key, (0, 0))
                        cache[key] = (
                            max(line_total, _read_int(row, "line_total")),
                            max(branch_total, _read_int(row, "branch_total")),
                        )
            except OSError:
                continue
        setattr(pipeline, "_coverage_incremental_totals_cache", cache)
    return cache.get((repo, fqcn), (0, 0))


def _write_incremental_row(
    *,
    csv_path: Path,
    repo: str,
    fqcn: str,
    variant: str,
    class_name: str,
    class_rank: int,
    added_lines: int,
    line_total: int,
    added_methods: int,
    added_branches: int,
    branch_total: int,
    added_instructions: int,
    status: str,
    problem_category: str,
    problem_detail: str,
    out_dir: str,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                repo,
                fqcn,
                variant,
                class_name,
                "true" if class_name == fqcn else "false",
                class_rank,
                added_lines,
                line_total,
                f"{_percentage(added_lines, line_total):.6f}",
                added_methods,
                added_branches,
                branch_total,
                f"{_percentage(added_branches, branch_total):.6f}",
                added_instructions,
                status,
                problem_category,
                problem_detail,
                out_dir,
            ]
        )


class CoverageIncrementalComparisonStep(Step):
    step_names = ("coverage-incremental",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] coverage-incremental: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            return True

        variants = _selected_incremental_variants(
            getattr(self.pipeline.args, "coverage_incremental_variants", ""),
            self.pipeline.args.auto_variant,
        )
        wrote_any = False
        for variant in variants:
            class_deltas_csv = _class_deltas_csv_for_variant(self.pipeline, variant, ctx.target_id)
            if not class_deltas_csv:
                _write_incremental_row(
                    csv_path=self.pipeline.coverage_incremental_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    class_name="",
                    class_rank=0,
                    added_lines=0,
                    line_total=0,
                    added_methods=0,
                    added_branches=0,
                    branch_total=0,
                    added_instructions=0,
                    status="missing",
                    problem_category="missing_class_deltas",
                    problem_detail="No class_deltas.csv found for variant.",
                    out_dir="",
                )
                wrote_any = True
                continue

            rows_written_for_variant = 0
            target_line_total, target_branch_total = _incremental_target_totals(self.pipeline, ctx.repo, ctx.fqcn)
            with class_deltas_csv.open("r", encoding="utf-8", newline="") as handle:
                for class_rank, row in enumerate(csv.DictReader(handle), start=1):
                    class_name = (row.get("class_name", "") or "").strip()
                    if not class_name:
                        continue
                    if not self.pipeline.args.coverage_incremental_all_classes and class_name != ctx.fqcn:
                        continue
                    line_total = target_line_total if class_name == ctx.fqcn else 0
                    branch_total = target_branch_total if class_name == ctx.fqcn else 0
                    _write_incremental_row(
                        csv_path=self.pipeline.coverage_incremental_csv,
                        repo=ctx.repo,
                        fqcn=ctx.fqcn,
                        variant=variant,
                        class_name=class_name,
                        class_rank=class_rank,
                        added_lines=_read_int(row, "added_lines"),
                        line_total=line_total,
                        added_methods=_read_int(row, "added_methods"),
                        added_branches=_read_int(row, "added_branches"),
                        branch_total=branch_total,
                        added_instructions=_read_int(row, "added_instructions"),
                        status="passed",
                        problem_category="",
                        problem_detail="",
                        out_dir=str(class_deltas_csv.parent),
                    )
                    rows_written_for_variant += 1

            if rows_written_for_variant == 0:
                _write_incremental_row(
                    csv_path=self.pipeline.coverage_incremental_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    class_name=ctx.fqcn if not self.pipeline.args.coverage_incremental_all_classes else "",
                    class_rank=0,
                    added_lines=0,
                    line_total=target_line_total if not self.pipeline.args.coverage_incremental_all_classes else 0,
                    added_methods=0,
                    added_branches=0,
                    branch_total=target_branch_total if not self.pipeline.args.coverage_incremental_all_classes else 0,
                    added_instructions=0,
                    status="passed",
                    problem_category="no_matching_class_delta",
                    problem_detail="class_deltas.csv exists but has no matching class row.",
                    out_dir=str(class_deltas_csv.parent),
                )
            wrote_any = True

        if wrote_any:
            self.pipeline.ran += 1
        return True
