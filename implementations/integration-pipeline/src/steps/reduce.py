from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple, TYPE_CHECKING

from ..core.common import parse_package_and_class
from ..pipeline.config import covfilter_candidate_out_dirs, reduce_variant_summary_csv, selected_adopted_variants
from ..pipeline.helpers import (
    annotated_test_method_names,
    adopted_test_method_names,
    adopted_variants,
    find_scaffolding_source,
    fix_reduced_scaffolding_import,
    first_test_source_for_fqcn,
    individually_runnable_test_method_names,
    is_empty_generated_test_source,
    reduced_test_path,
    reduced_variant_test_path,
    test_fqcn_from_source,
    test_method_names,
)
from .base import Step
from .covfilter import (
    CovfilterRunner,
    _remove_pr_test_methods,
    _replace_identifier_outside_comments_and_strings,
    _rewrite_class_name,
)

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


def run_generate_reduced_app(
    *,
    coverage_filter_jar: Path,
    libs_glob_cp: str,
    original_test_java: Path,
    test_deltas_csv: Path,
    top_n: int,
    out_dir: Path,
    log_file: Path,
    extra_java_opts: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    return CovfilterRunner(java_opts=extra_java_opts).run_generate_reduced(
        coverage_filter_jar=coverage_filter_jar,
        libs_glob_cp=libs_glob_cp,
        original_test_java=original_test_java,
        test_deltas_csv=test_deltas_csv,
        top_n=top_n,
        out_dir=out_dir,
        log_file=log_file,
    )


def _find_existing_delta_csv(base_dir: Path, names: List[str]) -> Optional[Path]:
    for name in names:
        cand = base_dir / name
        if cand.exists():
            return cand
    return None


def _auto_delta_csv_names(top_n: int) -> List[str]:
    if top_n == 100:
        return ["test_deltas_all.csv", "test_deltas_kept.csv", "tests_deltas_kept.csv"]
    return ["test_deltas_kept.csv", "tests_deltas_kept.csv"]


def _prepare_auto_top100_deltas(source: Path, deltas_csv: Path, out_dir: Path) -> Path:
    with deltas_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    available_methods = set(test_method_names(source))
    selected_rows: List[dict[str, str]] = []
    seen_methods: set[str] = set()
    for row in rows:
        method = (row.get("test_selector", "") or "").rsplit("#", 1)[-1]
        if not method or method not in available_methods or method in seen_methods:
            continue
        seen_methods.add(method)
        selected_rows.append(row)

    def metric(row: dict[str, str], key: str) -> int:
        try:
            return int((row.get(key, "") or "0").strip())
        except ValueError:
            return 0

    selected_rows.sort(
        key=lambda row: (
            -metric(row, "added_lines"),
            -metric(row, "added_methods"),
            -metric(row, "added_branches"),
            -metric(row, "added_instructions"),
            row.get("test_selector", ""),
        )
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "test_deltas_top100_pool.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected_rows)
    return output


def _count_csv_rows(csv_path: Optional[Path]) -> int:
    if not csv_path or not csv_path.exists():
        return 0
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            return sum(1 for _ in reader)
    except OSError:
        return 0


def _clear_reduce_output_dir(out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)


_TEST_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|final|static|synchronized|native|abstract|strictfp|default)\s+)*void\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\("
)


def _pr_test_method_names(source: Path) -> List[str]:
    return test_method_names(source)


def _prepare_pr_test_deltas(
    source: Path,
    manual_source: Optional[Path],
    existing_csv: Optional[Path],
    out_dir: Path,
) -> Path:
    fieldnames = ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"]
    rows: List[dict[str, str]] = []
    if existing_csv and existing_csv.exists():
        try:
            with existing_csv.open("r", encoding="utf-8", newline="") as handle:
                rows = [dict(row) for row in csv.DictReader(handle)]
        except OSError:
            rows = []

    def metric(row: dict[str, str], key: str) -> int:
        try:
            return int((row.get(key, "") or "0").strip())
        except ValueError:
            return 0

    adopted_methods = set(adopted_test_method_names(source, manual_source))
    allowed_methods = adopted_methods.intersection(individually_runnable_test_method_names(source))
    rows = [
        row
        for row in rows
        if (
            (row.get("test_selector", "") or "").rsplit("#", 1)[-1] in allowed_methods
            and any(
                metric(row, key) > 0
                for key in ("added_lines", "added_methods", "added_branches", "added_instructions")
            )
        )
    ]
    rows.sort(
        key=lambda row: (
            -metric(row, "added_lines"),
            -metric(row, "added_methods"),
            -metric(row, "added_branches"),
            -metric(row, "added_instructions"),
            row.get("test_selector", ""),
        )
    )

    test_fqcn = test_fqcn_from_source(source) or source.stem
    selected_methods = {
        (row.get("test_selector", "") or "").rsplit("#", 1)[-1]
        for row in rows
        if (row.get("test_selector", "") or "").strip()
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    selected_csv = out_dir / "test_deltas_selected.csv"
    with selected_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    line_fieldnames = ["test_selector", "class_name", "newly_covered_lines", "upgraded_to_full_lines"]
    line_rows: List[dict[str, str]] = []
    line_source = out_dir / "line_deltas_kept.csv"
    if line_source.exists():
        try:
            with line_source.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    selector = (row.get("test_selector", "") or "").strip()
                    method = selector.rsplit("#", 1)[-1]
                    if method not in selected_methods:
                        continue
                    normalized = dict(row)
                    normalized["test_selector"] = f"{test_fqcn}#{method}"
                    line_rows.append(normalized)
        except OSError:
            line_rows = []

    line_csv = out_dir / "line_deltas_selected.csv"
    with line_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=line_fieldnames)
        writer.writeheader()
        writer.writerows(line_rows)
    return selected_csv


def _materialize_reducible_pr_source(source: Path, stage_root: Path, target_id: str) -> Path:
    try:
        lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return source

    new_lines: List[str] = []
    added_annotation = False
    annotated_methods = set(annotated_test_method_names(source))
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("@"):
            new_lines.append(line)
            continue

        match = _TEST_METHOD_RE.match(line)
        if match:
            method = match.group("name")
            if method.startswith("test") and re.search(r"\bpublic\b", line) and method not in annotated_methods:
                indent = line[: len(line) - len(line.lstrip())]
                new_lines.append(f"{indent}@org.junit.Test")
                added_annotation = True
            new_lines.append(line)
            continue

        new_lines.append(line)

    if not added_annotation:
        return source

    staged = stage_root / target_id / source.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return staged


def _materialize_reduced_pr_source(
    source: Path,
    test_deltas_csv: Path,
    top_n: int,
    out_dir: Path,
) -> Optional[Path]:
    try:
        with test_deltas_csv.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        source_text = source.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    selected_methods = {
        (row.get("test_selector", "") or "").rsplit("#", 1)[-1]
        for row in rows[:top_n]
        if (row.get("test_selector", "") or "").strip()
    }
    if not selected_methods:
        return None

    package_name, original_class_name = parse_package_and_class(source)
    if not original_class_name:
        return None
    reduced_class_name = f"{original_class_name}_Top{top_n}"
    all_test_methods = set(test_method_names(source))
    reduced_text = _remove_pr_test_methods(source_text, all_test_methods - selected_methods)
    reduced_text = _rewrite_class_name(reduced_text, reduced_class_name)
    reduced_text = _replace_identifier_outside_comments_and_strings(
        reduced_text,
        original_class_name,
        reduced_class_name,
    )

    package_dir = Path(*package_name.split(".")) if package_name else Path()
    reduced_path = out_dir / package_dir / f"{reduced_class_name}.java"
    reduced_path.parent.mkdir(parents=True, exist_ok=True)
    reduced_path.write_text(reduced_text, encoding="utf-8")
    return reduced_path


def _append_reduce_summary_row(
    *,
    summary_csv: Path,
    ctx: "TargetContext",
    variant: str,
    status: str,
    problem_category: str,
    problem_detail: str,
    input_test_source: Optional[Path],
    test_deltas_csv: Optional[Path],
    selected_top_n: int,
    selected_test_count: int,
    reduced_test_path: Optional[Path],
    out_dir: Path,
    log_file: Path,
) -> None:
    with summary_csv.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                ctx.repo,
                ctx.fqcn,
                variant,
                status,
                problem_category,
                problem_detail,
                ctx.manual_test_fqcn or "",
                ctx.generated_test_fqcn or "",
                str(input_test_source) if input_test_source else "",
                str(test_deltas_csv) if test_deltas_csv else "",
                _count_csv_rows(test_deltas_csv),
                selected_top_n,
                selected_test_count,
                str(reduced_test_path) if reduced_test_path else "",
                str(out_dir),
                str(log_file),
            ]
        )


class ReduceStep(Step):
    step_names = ("reduce",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        auto_variant = self.pipeline.args.auto_variant
        reduced_root = Path(self.pipeline.args.reduced_out)
        summary_csv = reduce_variant_summary_csv(reduced_root, auto_variant, self.pipeline.args.includes)
        reduced_out = reduced_root / auto_variant / ctx.target_id
        reduced_log = self.pipeline.logs_dir / f"{ctx.target_id}.{auto_variant}.reduce.log"
        _clear_reduce_output_dir(reduced_out)
        top_n = max(1, min(self.pipeline.args.reduce_max_tests, 100))
        test_deltas_csv = None
        test_deltas_base = None
        for cov_base in covfilter_candidate_out_dirs(
            self.pipeline.covfilter_out_root,
            self.pipeline.adopted_covfilter_out_root,
            auto_variant,
            ctx.target_id,
            self.pipeline.agentic_covfilter_out_root,
        ):
            delta_names = (
                ["test_deltas_all.csv", "test_deltas_kept.csv"]
                if auto_variant == "pr-tests"
                else _auto_delta_csv_names(top_n)
            )
            test_deltas_csv = _find_existing_delta_csv(cov_base, delta_names)
            if test_deltas_csv:
                test_deltas_base = cov_base
                break
        generated_test_src = first_test_source_for_fqcn(ctx.final_sources, ctx.generated_test_fqcn)
        if auto_variant == "pr-tests" and generated_test_src and test_deltas_csv and test_deltas_base:
            manual_src = first_test_source_for_fqcn(ctx.manual_sources, ctx.manual_test_fqcn)
            test_deltas_csv = _prepare_pr_test_deltas(generated_test_src, manual_src, test_deltas_csv, test_deltas_base)
        if self.pipeline.args.skip_empty_tests:
            generated_test_src_for_empty_check = generated_test_src
            if generated_test_src_for_empty_check is None:
                generated_candidates = [
                    source for source in (ctx.final_sources or ctx.sources) if source not in set(ctx.manual_sources)
                ]
                if generated_candidates:
                    generated_test_src_for_empty_check = generated_candidates[0]
            if is_empty_generated_test_source(generated_test_src_for_empty_check):
                print(f'[agt] reduce: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                return True
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] reduce: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=auto_variant,
                status="skipped",
                problem_category="excluded_by_agt_coverage",
                problem_detail="Target excluded because AGT coverage summary reports zero covered lines.",
                input_test_source=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=0,
                reduced_test_path=None,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
            return True
        if self.pipeline.covfilter_jar is None or not self.pipeline.covfilter_jar.exists():
            print(f'[agt] reduce: Skip (missing --covfilter-jar): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=auto_variant,
                status="skipped",
                problem_category="missing_covfilter_jar",
                problem_detail="Reduce step requires --covfilter-jar.",
                input_test_source=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=0,
                reduced_test_path=None,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
            return True
        if not generated_test_src or not generated_test_src.exists():
            print(f'[agt] reduce: Skip (missing generated test source): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=auto_variant,
                status="skipped",
                problem_category="missing_input_test_source",
                problem_detail="Missing generated test source.",
                input_test_source=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=0,
                reduced_test_path=None,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
            return True
        if not test_deltas_csv:
            print(f'[agt] reduce: Skip (missing test(s)_deltas_kept.csv): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=auto_variant,
                status="skipped",
                problem_category="missing_test_deltas_csv",
                problem_detail="Missing test_deltas_kept.csv or tests_deltas_kept.csv.",
                input_test_source=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=0,
                reduced_test_path=None,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
            return True
        if _count_csv_rows(test_deltas_csv) == 0:
            print(f'[agt] reduce: Skip (no kept tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=auto_variant,
                status="skipped",
                problem_category="zero_kept_tests",
                problem_detail="test_deltas_kept.csv exists but has zero kept tests.",
                input_test_source=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=0,
                reduced_test_path=None,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
            return True

        if top_n == 100 and test_deltas_csv.name == "test_deltas_all.csv":
            test_deltas_csv = _prepare_auto_top100_deltas(
                generated_test_src,
                test_deltas_csv,
                reduced_out,
            )

        print(f'[agt] Reducing AGT tests (top {top_n}): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
        reduced_src: Optional[Path] = None
        if top_n == 100 and test_deltas_csv.name == "test_deltas_top100_pool.csv":
            reduced_src = _materialize_reduced_pr_source(
                generated_test_src,
                test_deltas_csv,
                top_n,
                reduced_out,
            )
            ok_red = reduced_src is not None
            red_tail = "" if ok_red else "Could not materialize the source-preserving Top-100 test."
        else:
            ok_red, red_tail = run_generate_reduced_app(
                coverage_filter_jar=self.pipeline.covfilter_jar,
                libs_glob_cp=self.pipeline.args.libs_cp,
                original_test_java=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                top_n=top_n,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
        if not ok_red:
            print(f'[agt] reduce: FAIL (see {reduced_log})')
            print("[agt][REDUCE-TAIL]\n" + red_tail)
            _clear_reduce_output_dir(reduced_out)
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=auto_variant,
                status="failed",
                problem_category="reduce_generation_failed",
                problem_detail=red_tail,
                input_test_source=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=0,
                reduced_test_path=None,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
            return True

        reduced_src = reduced_src or reduced_test_path(
            reduced_root,
            ctx.target_id,
            generated_test_src,
            top_n,
            preferred_variants=[auto_variant],
        )
        if reduced_src and reduced_src.exists():
            scaffolding_src = find_scaffolding_source(generated_test_src, list(ctx.final_sources))
            fix_reduced_scaffolding_import(reduced_src, scaffolding_src)
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=auto_variant,
                status="passed",
                problem_category="",
                problem_detail="",
                input_test_source=generated_test_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=min(top_n, _count_csv_rows(test_deltas_csv)),
                reduced_test_path=reduced_src,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
            return True
        _append_reduce_summary_row(
            summary_csv=summary_csv,
            ctx=ctx,
            variant=auto_variant,
            status="failed",
            problem_category="missing_reduced_output",
            problem_detail="Reduce command completed without writing the expected reduced test file.",
            input_test_source=generated_test_src,
            test_deltas_csv=test_deltas_csv,
            selected_top_n=top_n,
            selected_test_count=0,
            reduced_test_path=None,
            out_dir=reduced_out,
            log_file=reduced_log,
        )
        return True


class AdoptedReduceStep(Step):
    step_names = ("adopted-reduce",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        selected_variants = selected_adopted_variants(self.pipeline.args.adopted_filter_variants)
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] adopted-reduce: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            for variant in selected_variants:
                _append_reduce_summary_row(
                    summary_csv=reduce_variant_summary_csv(
                        self.pipeline.adopted_reduced_out_root, variant, self.pipeline.args.includes
                    ),
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category="excluded_by_agt_coverage",
                    problem_detail="Target excluded because AGT coverage summary reports zero covered lines.",
                    input_test_source=None,
                    test_deltas_csv=None,
                    selected_top_n=max(1, min(self.pipeline.args.adopted_reduce_max_tests, 100)),
                    selected_test_count=0,
                    reduced_test_path=None,
                    out_dir=self.pipeline.adopted_reduced_out_root / variant / ctx.target_id,
                    log_file=self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.reduce.log",
                )
            return True
        if self.pipeline.covfilter_jar is None or not self.pipeline.covfilter_jar.exists():
            print(f'[agt] adopted-reduce: Skip (missing --covfilter-jar): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            for variant in selected_variants:
                _append_reduce_summary_row(
                    summary_csv=reduce_variant_summary_csv(
                        self.pipeline.adopted_reduced_out_root, variant, self.pipeline.args.includes
                    ),
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category="missing_covfilter_jar",
                    problem_detail="Reduce step requires --covfilter-jar.",
                    input_test_source=None,
                    test_deltas_csv=None,
                    selected_top_n=max(1, min(self.pipeline.args.adopted_reduce_max_tests, 100)),
                    selected_test_count=0,
                    reduced_test_path=None,
                    out_dir=self.pipeline.adopted_reduced_out_root / variant / ctx.target_id,
                    log_file=self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.reduce.log",
                )
            return True

        variant_sources = {
            variant: source
            for variant, source in adopted_variants(
                self.pipeline.adopted_root,
                ctx.target_id,
                ctx.fqcn,
                self.pipeline.pr_tests_root,
                self.pipeline.out_dir / "pr-tests",
            )
        }
        if not variant_sources:
            print(f'[agt] adopted-reduce: Skip (missing adopted tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
        top_n = max(1, min(self.pipeline.args.adopted_reduce_max_tests, 100))
        for variant in selected_variants:
            adopted_src = variant_sources.get(variant)
            summary_csv = reduce_variant_summary_csv(
                self.pipeline.adopted_reduced_out_root, variant, self.pipeline.args.includes
            )
            reduced_out = self.pipeline.adopted_reduced_out_root / variant / ctx.target_id
            reduced_log = self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.reduce.log"
            _clear_reduce_output_dir(reduced_out)
            if not adopted_src or not adopted_src.exists():
                problem_category = "missing_adopted_tests" if not variant_sources else "missing_input_test_source"
                problem_detail = "Missing adopted tests." if not variant_sources else f'Missing {variant} test source.'
                _append_reduce_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category=problem_category,
                    problem_detail=problem_detail,
                    input_test_source=adopted_src,
                    test_deltas_csv=None,
                    selected_top_n=top_n,
                    selected_test_count=0,
                    reduced_test_path=None,
                    out_dir=reduced_out,
                    log_file=reduced_log,
                )
                continue
            if self.pipeline.args.skip_empty_tests and is_empty_generated_test_source(adopted_src):
                print(
                    f'[agt] adopted-reduce: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"'
                )
                continue
            test_deltas_csv = None
            cov_bases = covfilter_candidate_out_dirs(
                self.pipeline.covfilter_out_root,
                self.pipeline.adopted_covfilter_out_root,
                variant,
                ctx.target_id,
                self.pipeline.agentic_covfilter_out_root,
            )
            for cov_base in cov_bases:
                delta_names = (
                    ["test_deltas_all.csv", "test_deltas_kept.csv"]
                    if variant == "pr-tests"
                    else ["test_deltas_kept.csv", "tests_deltas_kept.csv"]
                )
                test_deltas_csv = _find_existing_delta_csv(cov_base, delta_names)
                if test_deltas_csv:
                    break
            if variant == "pr-tests":
                manual_src = first_test_source_for_fqcn(ctx.manual_sources, ctx.manual_test_fqcn)
                test_deltas_csv = _prepare_pr_test_deltas(adopted_src, manual_src, test_deltas_csv, cov_bases[0])
            reduce_input_src = (
                _materialize_reducible_pr_source(
                    adopted_src,
                    self.pipeline.out_dir / "pr-tests-reducible",
                    ctx.target_id,
                )
                if variant == "pr-tests"
                else adopted_src
            )
            if not test_deltas_csv:
                print(
                    f'[agt] adopted-reduce: Skip (missing test(s)_deltas_kept.csv): repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"'
                )
                _append_reduce_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category="missing_test_deltas_csv",
                    problem_detail=(
                        "Missing test_deltas_kept.csv and test_deltas_all.csv."
                        if variant == "pr-tests"
                        else "Missing test_deltas_kept.csv or tests_deltas_kept.csv."
                    ),
                    input_test_source=adopted_src,
                    test_deltas_csv=test_deltas_csv,
                    selected_top_n=top_n,
                    selected_test_count=0,
                    reduced_test_path=None,
                    out_dir=reduced_out,
                    log_file=reduced_log,
                )
                continue
            if _count_csv_rows(test_deltas_csv) == 0:
                print(
                    f'[agt] adopted-reduce: Skip (no kept tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"'
                )
                _append_reduce_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category="zero_kept_tests",
                    problem_detail=(
                        "No adopted PR test has a positive measured coverage delta."
                        if variant == "pr-tests"
                        else "test_deltas_kept.csv exists but has zero kept tests."
                    ),
                    input_test_source=adopted_src,
                    test_deltas_csv=test_deltas_csv,
                    selected_top_n=top_n,
                    selected_test_count=0,
                    reduced_test_path=None,
                    out_dir=reduced_out,
                    log_file=reduced_log,
                )
                continue
            print(f'[agt] Reducing adopted tests ({variant}, top {top_n}): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            if variant == "pr-tests":
                reduced_src = _materialize_reduced_pr_source(
                    reduce_input_src,
                    test_deltas_csv,
                    top_n,
                    reduced_out,
                )
                ok_red = reduced_src is not None
                red_tail = "" if ok_red else "Could not materialize the selected PR tests."
            else:
                ok_red, red_tail = run_generate_reduced_app(
                    coverage_filter_jar=self.pipeline.covfilter_jar,
                    libs_glob_cp=self.pipeline.args.libs_cp,
                    original_test_java=reduce_input_src,
                    test_deltas_csv=test_deltas_csv,
                    top_n=top_n,
                    out_dir=reduced_out,
                    log_file=reduced_log,
                )
            if not ok_red:
                print(f'[agt] adopted-reduce: FAIL (see {reduced_log})')
                print("[agt][ADOPTED-REDUCE-TAIL]\n" + red_tail)
                _clear_reduce_output_dir(reduced_out)
                _append_reduce_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="failed",
                    problem_category="reduce_generation_failed",
                    problem_detail=red_tail,
                    input_test_source=adopted_src,
                    test_deltas_csv=test_deltas_csv,
                    selected_top_n=top_n,
                    selected_test_count=0,
                    reduced_test_path=None,
                    out_dir=reduced_out,
                    log_file=reduced_log,
                )
                continue
            reduced_src = reduced_variant_test_path(self.pipeline.adopted_reduced_out_root, variant, ctx.target_id, top_n)
            if reduced_src and reduced_src.exists():
                _append_reduce_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="passed",
                    problem_category="",
                    problem_detail="",
                    input_test_source=adopted_src,
                    test_deltas_csv=test_deltas_csv,
                    selected_top_n=top_n,
                    selected_test_count=min(top_n, _count_csv_rows(test_deltas_csv)),
                    reduced_test_path=reduced_src,
                    out_dir=reduced_out,
                    log_file=reduced_log,
                )
                continue
            _append_reduce_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=variant,
                status="failed",
                problem_category="missing_reduced_output",
                problem_detail="Reduce command completed without writing the expected reduced test file.",
                input_test_source=adopted_src,
                test_deltas_csv=test_deltas_csv,
                selected_top_n=top_n,
                selected_test_count=0,
                reduced_test_path=None,
                out_dir=reduced_out,
                log_file=reduced_log,
            )
        return True
