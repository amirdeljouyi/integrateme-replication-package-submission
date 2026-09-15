"""Build exact-target incremental coverage from existing JaCoCo executions.

The existing manual suite is the baseline. For every variant, this module merges
the manual and variant execution data only when they contain the same target CUT
class ID, then reports counters from the exact outer target class file only.
"""

from __future__ import annotations

import csv
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from .layout import DATA_ROOT, PIPELINE_OUTPUT_ROOT, WORKSPACE_ROOT
from ..pipeline.helpers import agentic_test_path, adopted_test_path, improved_test_path, test_method_names


ROOT: Path
RESULTS_DIR: Path
DATA_DIR: Path
REPOS_DIR: Path
TMP_DIR: Path
BUILD_DIR: Path
MATRIX: Path
OUTPUT: Path
WORK: Path
JACOCO: Path
VARIANTS = ("manual", "auto", "auto-100", "improved", "adopted", "agentic")
EXEC_SUFFIX = {
    "manual": "manual",
    "auto": "auto_fixed",
    "auto-100": "auto_reduced",
    "improved": "improved",
    "adopted": "adopted",
    "agentic": "agentic",
}


def variant_exec_candidates(
    tmp_dir: Path,
    target_id: str,
    variant: str,
    *,
    auto_100_uses_full_suite: bool = False,
) -> tuple[Path, ...]:
    """Return current and explicitly compatible historical execution names.

    ``auto_fixed`` replaced the earlier ``auto`` filename. Both represent the
    full generated suite, so the older execution is a valid fallback when its
    exact target class ID matches the verified manual baseline. Auto-100 also
    reuses that execution when the full suite contains at most 100 methods.
    Other variants have no equivalent historical alias and must not borrow
    unrelated runs.
    """
    if variant == "auto" or (variant == "auto-100" and auto_100_uses_full_suite):
        suffixes = ("auto_fixed", "auto")
    else:
        suffixes = (EXEC_SUFFIX[variant],)
    return tuple(tmp_dir / f"{target_id}__{suffix}.exec" for suffix in suffixes)


def full_auto_test_source(
    results_dir: Path, data_dir: Path, repo: str, fqcn: str, target_id: str
) -> Path | None:
    roots = (
        results_dir / "reduced/auto" / target_id,
        results_dir / "generation" / "sanitized-es" / repo.replace("/", "_") / fqcn,
        data_dir / "collected-tests/generated" / repo.replace("/", "_") / fqcn,
    )
    for source_root in roots:
        if not source_root.exists():
            continue
        candidates = sorted(
            path
            for path in source_root.rglob("*ESTest.java")
            if "_Top" not in path.name and "scaffolding" not in path.name.lower()
        )
        if candidates:
            return min(candidates, key=lambda path: ("Sanitized" not in path.name, str(path)))
    return None


def auto_100_test_source(results_dir: Path, target_id: str) -> Path | None:
    target_root = results_dir / "reduced-true-auto100" / "auto" / target_id
    if target_root.exists():
        candidates = sorted(target_root.rglob("*Top100.java"))
        if candidates:
            return candidates[0]
    return None


def variant_test_sources(
    results_dir: Path,
    data_dir: Path,
    repo: str,
    fqcn: str,
    target_id: str,
    variant: str,
    *,
    auto_source: Path | None,
    top_100_source: Path | None,
    auto_100_uses_full_suite: bool,
) -> list[Path]:
    if variant == "manual":
        manual_root = data_dir / "collected-tests/manual" / repo.replace("/", "_") / fqcn
        if not manual_root.exists():
            return []
        return sorted(
            path
            for path in manual_root.rglob("*.java")
            if "scaffolding" not in path.name.lower() and test_method_names(path)
        )
    if variant == "auto":
        return [auto_source] if auto_source is not None else []
    if variant == "auto-100":
        source = auto_source if auto_100_uses_full_suite else top_100_source
        return [source] if source is not None else []

    adopted_root = results_dir / "llm-out"
    if variant == "improved":
        source = improved_test_path(adopted_root, target_id, fqcn)
    elif variant == "adopted":
        source = adopted_test_path(adopted_root, target_id, fqcn)
    elif variant == "agentic":
        source = agentic_test_path(adopted_root, target_id, fqcn)
    else:
        source = None
    return [source] if source is not None and source.exists() else []


FIELDNAMES = (
    "repo",
    "fqcn",
    "variant",
    "number_of_tests",
    "measurement_basis",
    "comparison_cohort_status",
    "target_class_verified",
    "line_set_equivalent",
    "target_class_id",
    "manual_line_covered",
    "combined_line_covered",
    "incremental_line_covered",
    "line_total",
    "incremental_line_percentage",
    "manual_branch_covered",
    "combined_branch_covered",
    "incremental_branch_covered",
    "branch_total",
    "incremental_branch_percentage",
    "branch_measurement_status",
    "measurement_status",
    "test_status",
    "analysis_eligible",
    "problem_detail",
    "manual_exec",
    "variant_exec",
    "target_classfile",
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, errors="replace")


def target_ids(exec_file: Path, fqcn: str) -> set[str]:
    if not exec_file.exists():
        return set()
    proc = run("java", "-jar", str(JACOCO), "execinfo", str(exec_file))
    internal_name = fqcn.replace(".", "/")
    ids: set[str] = set()
    for raw_line in proc.stdout.splitlines():
        parts = raw_line.strip().split()
        if len(parts) >= 5 and parts[-1] == internal_name and re.fullmatch(r"[0-9a-f]{16}", parts[0]):
            ids.add(parts[0])
    return ids


def class_id(class_file: Path) -> str:
    proc = run("java", "-jar", str(JACOCO), "classinfo", "--verbose", str(class_file))
    match = re.search(r"class 0x([0-9a-f]{16})\s", proc.stdout)
    return match.group(1) if match else ""


def report_stats(exec_file: Path, class_file: Path, fqcn: str, csv_file: Path) -> tuple[int, int, int, int] | None:
    csv_file.unlink(missing_ok=True)
    proc = run(
        "java",
        "-jar",
        str(JACOCO),
        "report",
        str(exec_file),
        "--classfiles",
        str(class_file),
        "--csv",
        str(csv_file),
        "--quiet",
    )
    if proc.returncode != 0 or not csv_file.exists():
        return None
    expected_package, expected_class = fqcn.rsplit(".", 1) if "." in fqcn else ("", fqcn)
    with csv_file.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("PACKAGE", "") != expected_package or row.get("CLASS", "") != expected_class:
                continue
            line_covered = int(row["LINE_COVERED"])
            line_total = line_covered + int(row["LINE_MISSED"])
            branch_covered = int(row["BRANCH_COVERED"])
            branch_total = branch_covered + int(row["BRANCH_MISSED"])
            return line_covered, line_total, branch_covered, branch_total
    return None


def merge_exec_files(manual_exec: Path, variant_exec: Path, merged_exec: Path) -> subprocess.CompletedProcess[str]:
    """Merge exactly two executions without retaining data from an earlier report run."""
    merged_exec.unlink(missing_ok=True)
    return run(
        "java",
        "-jar",
        str(JACOCO),
        "merge",
        str(manual_exec),
        str(variant_exec),
        "--destfile",
        str(merged_exec),
        "--quiet",
    )


def report_line_sets(exec_file: Path, class_file: Path, fqcn: str, xml_file: Path) -> tuple[set[int], set[int]] | None:
    xml_file.unlink(missing_ok=True)
    proc = run(
        "java",
        "-jar",
        str(JACOCO),
        "report",
        str(exec_file),
        "--classfiles",
        str(class_file),
        "--xml",
        str(xml_file),
        "--quiet",
    )
    if proc.returncode != 0 or not xml_file.exists():
        return None
    internal_name = fqcn.replace(".", "/")
    root = ET.parse(xml_file).getroot()
    target_class = next((node for node in root.iter("class") if node.get("name") == internal_name), None)
    if target_class is None:
        return None
    source_name = target_class.get("sourcefilename", "")
    package_name = internal_name.rsplit("/", 1)[0] if "/" in internal_name else ""
    package = next((node for node in root.findall("package") if node.get("name") == package_name), None)
    if package is None:
        return None
    source = next((node for node in package.findall("sourcefile") if node.get("name") == source_name), None)
    if source is None:
        return None
    executable: set[int] = set()
    covered: set[int] = set()
    for line in source.findall("line"):
        number = int(line.get("nr", "0"))
        executable.add(number)
        if int(line.get("ci", "0")) > 0:
            covered.add(number)
    return executable, covered


def percentage(covered: int, total: int) -> str:
    return f"{100.0 * covered / total:.6f}" if total else "0.000000"


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def row_status(row: dict[str, str] | None) -> str:
    if row is None:
        return "missing"
    if row.get("timeout") == "1":
        return "timeout"
    if row.get("failed") == "1":
        return "failed"
    if row.get("skipped") == "1":
        return "skipped"
    return "passed"


def unavailable_problem_details(results_dir: Path) -> dict[tuple[str, str, str], str]:
    details: dict[tuple[str, str, str], str] = {}
    audit_path = results_dir / "coverage/coverage_filtered32_zero_audit.csv"
    if audit_path.exists():
        with audit_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (row.get("repo", ""), row.get("fqcn", ""), row.get("variant", ""))
                cause = (row.get("cause", "") or "").strip()
                evidence = (row.get("evidence", "") or "").strip()
                if cause:
                    details[key] = f"{cause} Evidence: {evidence}" if evidence else cause

    gaps_path = results_dir / "coverage/coverage_filtered32_gaps.csv"
    if gaps_path.exists():
        with gaps_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                key = (row.get("repo", ""), row.get("fqcn", ""), row.get("variant", ""))
                problem = (row.get("problem_detail", "") or "").strip()
                if problem:
                    details[key] = problem
    return details


def blank_result(repo: str, fqcn: str, variant: str) -> dict[str, str]:
    return {field: "" for field in FIELDNAMES} | {
        "repo": repo,
        "fqcn": fqcn,
        "variant": variant,
        "target_class_verified": "false",
        "line_set_equivalent": "false",
        "analysis_eligible": "false",
    }


def build_incremental_target_cut_report(
    *,
    matrix: Path,
    output: Path,
    workspace_root: Path | None = None,
    work_dir: Path | None = None,
    jacoco_cli: Path | None = None,
) -> Path:
    global ROOT, RESULTS_DIR, DATA_DIR, REPOS_DIR, TMP_DIR, BUILD_DIR, MATRIX, OUTPUT, WORK, JACOCO
    ROOT = (workspace_root or Path.cwd()).resolve()
    RESULTS_DIR = (ROOT / PIPELINE_OUTPUT_ROOT).resolve()
    DATA_DIR = (ROOT / DATA_ROOT).resolve()
    REPOS_DIR = (ROOT / WORKSPACE_ROOT / "repos").resolve()
    TMP_DIR = (ROOT / WORKSPACE_ROOT / "pipeline" / "tmp").resolve()
    BUILD_DIR = (ROOT / WORKSPACE_ROOT / "pipeline" / "build" / "agt").resolve()
    MATRIX = matrix.resolve()
    OUTPUT = output.resolve()
    WORK = (work_dir or BUILD_DIR / "incremental-target-cut").resolve()
    JACOCO = (jacoco_cli or ROOT / "vendor/jacoco/org.jacoco.cli-run-0.8.14.jar").resolve()
    if not MATRIX.exists():
        raise FileNotFoundError(f"coverage matrix does not exist: {MATRIX}")
    if not JACOCO.exists():
        raise FileNotFoundError(f"JaCoCo CLI does not exist: {JACOCO}")

    with MATRIX.open(encoding="utf-8", newline="") as handle:
        matrix_rows = list(csv.DictReader(handle))
    by_key = {(row["repo"], row["fqcn"], row["variant"]): row for row in matrix_rows}
    targets = sorted({(row["repo"], row["fqcn"]) for row in matrix_rows})
    unavailable_details = unavailable_problem_details(RESULTS_DIR)

    WORK.mkdir(parents=True, exist_ok=True)
    output_rows: list[dict[str, str]] = []

    for repo, fqcn in targets:
        target_id = repo.replace("/", "_") + "_" + fqcn.replace(".", "_")
        safe = target_id.replace("/", "_")
        auto_source = full_auto_test_source(RESULTS_DIR, DATA_DIR, repo, fqcn, target_id)
        auto_methods = test_method_names(auto_source) if auto_source is not None else []
        auto_test_count = len(auto_methods) if auto_source is not None else None
        top_100_source = auto_100_test_source(RESULTS_DIR, target_id)
        top_100_methods = test_method_names(top_100_source) if top_100_source is not None else []
        manual_row = by_key[(repo, fqcn, "manual")]
        manual_exec = TMP_DIR / f"{target_id}__manual.exec"
        manual_ids = target_ids(manual_exec, fqcn)
        rel_class = Path(*fqcn.split(".")).with_suffix(".class")
        candidates = sorted(BUILD_DIR.glob(f"*/{target_id}/{rel_class}"))
        repo_root = REPOS_DIR / repo.replace("/", "_")
        for pattern in (f"**/target/classes/{rel_class}", f"**/build/classes/java/main/{rel_class}"):
            for candidate in sorted(repo_root.glob(pattern)):
                if candidate not in candidates:
                    candidates.append(candidate)

        selected_classfile: Path | None = None
        selected_class_id = ""
        manual_stats: tuple[int, int, int, int] | None = None
        expected_manual = (
            int(manual_row["line_covered"]),
            int(manual_row["line_total"]),
            int(manual_row["branch_covered"]),
            int(manual_row["branch_total"]),
        )
        candidate_by_id: dict[str, Path] = {}
        for candidate in candidates:
            candidate_id = class_id(candidate)
            if candidate_id and candidate_id not in candidate_by_id:
                candidate_by_id[candidate_id] = candidate
        seen_class_ids: set[str] = set()
        for candidate in candidates:
            candidate_id = class_id(candidate)
            if not candidate_id or candidate_id in seen_class_ids or candidate_id not in manual_ids:
                continue
            seen_class_ids.add(candidate_id)
            candidate_stats = report_stats(
                manual_exec,
                candidate,
                fqcn,
                WORK / f"{safe}.manual.{candidate_id}.csv",
            )
            if candidate_stats == expected_manual:
                selected_classfile = candidate
                selected_class_id = candidate_id
                manual_stats = candidate_stats
                break

        for variant in VARIANTS:
            matrix_row = by_key.get((repo, fqcn, variant))
            auto_100_uses_full_suite = variant == "auto-100" and auto_test_count is not None and auto_test_count <= 100
            if auto_100_uses_full_suite:
                matrix_row = by_key.get((repo, fqcn, "auto"))
            result = blank_result(repo, fqcn, variant)
            variant_sources = variant_test_sources(
                RESULTS_DIR,
                DATA_DIR,
                repo,
                fqcn,
                target_id,
                variant,
                auto_source=auto_source,
                top_100_source=top_100_source,
                auto_100_uses_full_suite=auto_100_uses_full_suite,
            )
            if variant_sources:
                result["number_of_tests"] = str(sum(len(test_method_names(source)) for source in variant_sources))
            if variant == "auto-100" and auto_test_count is not None:
                if auto_100_uses_full_suite:
                    result["measurement_basis"] = "full_auto_suite_le_100"
                    result["comparison_cohort_status"] = "verified_same_execution_as_auto"
                else:
                    result["measurement_basis"] = "coverage_ranked_top_100"
                    result["comparison_cohort_status"] = "verified_100_method_subset_of_full_auto"
            elif variant == "manual":
                result["measurement_basis"] = "manual_baseline"
                result["comparison_cohort_status"] = "baseline"
            elif variant in {"improved", "adopted", "agentic"}:
                result["measurement_basis"] = "variant_execution"
                result["comparison_cohort_status"] = "unverified_historical_input_lineage"
            else:
                result["measurement_basis"] = "variant_execution"
                result["comparison_cohort_status"] = "full_auto_execution"
            result["test_status"] = row_status(matrix_row)
            result["manual_exec"] = str(manual_exec.relative_to(ROOT))
            if selected_classfile is None or manual_stats is None:
                result["measurement_status"] = "unavailable"
                result["problem_detail"] = "Could not verify a target CUT class ID whose manual counters match the matrix baseline."
                output_rows.append(result)
                continue

            result["target_class_verified"] = "true"
            result["line_set_equivalent"] = "true"
            result["target_class_id"] = selected_class_id
            result["target_classfile"] = display_path(selected_classfile)
            result["manual_line_covered"] = str(manual_stats[0])
            result["line_total"] = str(manual_stats[1])
            result["manual_branch_covered"] = str(manual_stats[2])
            result["branch_total"] = str(manual_stats[3])

            if variant == "manual":
                result.update(
                    measurement_status="baseline",
                    test_status="passed",
                    analysis_eligible="true",
                    combined_line_covered=str(manual_stats[0]),
                    incremental_line_covered="0",
                    incremental_line_percentage="0.000000",
                    combined_branch_covered=str(manual_stats[2]),
                    incremental_branch_covered="0",
                    incremental_branch_percentage="0.000000",
                    branch_measurement_status="baseline",
                    variant_exec=str(manual_exec.relative_to(ROOT)),
                )
                output_rows.append(result)
                continue

            if variant == "auto-100" and not auto_100_uses_full_suite:
                if auto_test_count is None:
                    result["measurement_status"] = "unavailable"
                    result["comparison_cohort_status"] = "unverified"
                    result["problem_detail"] = "Could not locate the full auto suite needed to verify Top-100 selection."
                    output_rows.append(result)
                    continue
                if top_100_source is None or len(top_100_methods) != 100:
                    result["measurement_status"] = "unavailable"
                    result["comparison_cohort_status"] = "invalid_or_missing_top_100_source"
                    result["problem_detail"] = "Full auto has more than 100 tests, but no verified 100-method reduced source is available."
                    output_rows.append(result)
                    continue
                if not set(top_100_methods).issubset(set(auto_methods)):
                    result["measurement_status"] = "unavailable"
                    result["comparison_cohort_status"] = "invalid_top_100_selection"
                    result["problem_detail"] = "The Top-100 methods are not a subset of the full auto suite."
                    output_rows.append(result)
                    continue

            if matrix_row is None:
                result["measurement_status"] = "unavailable"
                result["problem_detail"] = unavailable_details.get(
                    (repo, fqcn, variant),
                    "Variant row is absent from the all-variants matrix.",
                )
                output_rows.append(result)
                continue
            if int(matrix_row["line_total"]) == 0:
                result["measurement_status"] = "unavailable"
                result["problem_detail"] = unavailable_details.get(
                    (repo, fqcn, variant),
                    f"Variant did not produce a coverage report ({row_status(matrix_row)}).",
                )
                output_rows.append(result)
                continue

            available_execs = [
                path
                for path in variant_exec_candidates(
                    TMP_DIR,
                    target_id,
                    variant,
                    auto_100_uses_full_suite=auto_100_uses_full_suite,
                )
                if path.exists()
            ]
            if not available_execs:
                result["measurement_status"] = "unavailable"
                result["problem_detail"] = "Variant JaCoCo execution data is missing."
                output_rows.append(result)
                continue

            exec_ids = [(path, target_ids(path, fqcn)) for path in available_execs]
            # Candidate order is provenance order: use the current execution
            # whenever it exists. Do not silently prefer an older execution
            # merely because its CUT class ID happens to match the manual run.
            variant_exec, variant_ids = exec_ids[0]
            result["variant_exec"] = str(variant_exec.relative_to(ROOT))
            if variant == "auto" and variant_exec.name.endswith("__auto.exec"):
                result["comparison_cohort_status"] = "historical_auto_execution_fallback"
            if not variant_ids:
                combined = manual_stats
                test_status = row_status(matrix_row)
                result.update(
                    measurement_status="measured_no_target_hit",
                    analysis_eligible="true" if test_status == "passed" else "false",
                    combined_line_covered=str(combined[0]),
                    incremental_line_covered="0",
                    incremental_line_percentage="0.000000",
                    combined_branch_covered=str(combined[2]),
                    incremental_branch_covered="0",
                    incremental_branch_percentage="0.000000",
                    branch_measurement_status="exact_no_target_hit",
                    problem_detail="" if test_status == "passed" else "Coverage was measured, but the variant test execution was not fully passing.",
                )
                output_rows.append(result)
                continue

            if selected_class_id not in variant_ids:
                expected_variant = (
                    int(matrix_row["line_covered"]),
                    int(matrix_row["line_total"]),
                    int(matrix_row["branch_covered"]),
                    int(matrix_row["branch_total"]),
                )
                variant_classfile: Path | None = None
                variant_class_id = ""
                for candidate_id in sorted(variant_ids):
                    candidate = candidate_by_id.get(candidate_id)
                    if candidate is None:
                        continue
                    candidate_stats = report_stats(
                        variant_exec,
                        candidate,
                        fqcn,
                        WORK / f"{safe}.{variant}.{candidate_id}.absolute.csv",
                    )
                    if candidate_stats == expected_variant:
                        variant_classfile = candidate
                        variant_class_id = candidate_id
                        break
                if variant_classfile is None:
                    result["measurement_status"] = "incompatible_target_cut"
                    result["problem_detail"] = "No exact target class file matched the variant class ID and its recorded counters."
                    output_rows.append(result)
                    continue
                manual_sets = report_line_sets(
                    manual_exec,
                    selected_classfile,
                    fqcn,
                    WORK / f"{safe}.manual.lines.xml",
                )
                variant_sets = report_line_sets(
                    variant_exec,
                    variant_classfile,
                    fqcn,
                    WORK / f"{safe}.{variant}.lines.xml",
                )
                if manual_sets is None or variant_sets is None or manual_sets[0] != variant_sets[0]:
                    result["measurement_status"] = "incompatible_target_cut"
                    result["problem_detail"] = "Manual and variant target classes do not have the same executable-line universe."
                    output_rows.append(result)
                    continue
                incremental_lines = len(variant_sets[1] - manual_sets[1])
                combined_lines = len(manual_sets[1] | variant_sets[1])
                test_status = row_status(matrix_row)
                result.update(
                    target_class_verified="true",
                    line_set_equivalent="true",
                    target_class_id=f"{selected_class_id}|{variant_class_id}",
                    target_classfile=f"{display_path(selected_classfile)}|{display_path(variant_classfile)}",
                    measurement_status="measured_equivalent_target_lines",
                    analysis_eligible="true" if test_status == "passed" else "false",
                    combined_line_covered=str(combined_lines),
                    incremental_line_covered=str(incremental_lines),
                    incremental_line_percentage=percentage(incremental_lines, manual_stats[1]),
                    combined_branch_covered="",
                    incremental_branch_covered="",
                    incremental_branch_percentage="",
                    branch_measurement_status="unavailable_different_class_id",
                    problem_detail="Branch increment is unavailable because JaCoCo cannot exactly merge different class IDs."
                    if test_status == "passed"
                    else "Line increment is exact, but branch increment is unavailable and the variant tests were not fully passing.",
                )
                output_rows.append(result)
                continue

            merged_exec = WORK / f"{safe}.{variant}.merged.exec"
            merge_proc = merge_exec_files(manual_exec, variant_exec, merged_exec)
            if merge_proc.returncode != 0:
                result["measurement_status"] = "unavailable"
                result["problem_detail"] = "JaCoCo could not merge the manual and variant execution data."
                output_rows.append(result)
                continue
            combined = report_stats(
                merged_exec,
                selected_classfile,
                fqcn,
                WORK / f"{safe}.{variant}.csv",
            )
            if combined is None or combined[1] != manual_stats[1] or combined[3] != manual_stats[3]:
                result["measurement_status"] = "unavailable"
                result["problem_detail"] = "Combined report did not preserve the verified target CUT denominators."
                output_rows.append(result)
                continue

            incremental_lines = combined[0] - manual_stats[0]
            incremental_branches = combined[2] - manual_stats[2]
            if incremental_lines < 0 or incremental_branches < 0:
                result["measurement_status"] = "unavailable"
                result["problem_detail"] = "Combined counters were lower than the manual baseline."
                output_rows.append(result)
                continue
            test_status = row_status(matrix_row)
            result.update(
                measurement_status="measured",
                analysis_eligible="true" if test_status == "passed" else "false",
                combined_line_covered=str(combined[0]),
                incremental_line_covered=str(incremental_lines),
                incremental_line_percentage=percentage(incremental_lines, manual_stats[1]),
                combined_branch_covered=str(combined[2]),
                incremental_branch_covered=str(incremental_branches),
                incremental_branch_percentage=percentage(incremental_branches, manual_stats[3]),
                branch_measurement_status="exact_same_class_id",
                problem_detail="" if test_status == "passed" else "Coverage was measured, but the variant test execution was not fully passing.",
            )
            output_rows.append(result)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"[agt] wrote {len(output_rows)} exact-target incremental rows to {OUTPUT}")
    return OUTPUT
