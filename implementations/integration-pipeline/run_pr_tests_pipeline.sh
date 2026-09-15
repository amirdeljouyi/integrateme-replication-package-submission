#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Repository layout anchors; see src/core/layout.py for the canonical defaults.
# Exported so the embedded Python below resolves the same roots.
export ITL_PIPELINE_OUTPUT="${ITL_PIPELINE_OUTPUT:-../../pipeline-output}"
export ITL_DATA_ROOT="${ITL_DATA_ROOT:-../../data}"
export ITL_WORKSPACE_ROOT="${ITL_WORKSPACE_ROOT:-../../workspace}"

INVENTORY="${INVENTORY:-${ITL_DATA_ROOT}/collected-tests/_logs/tests_inventory.csv}"
FATJAR_MAP="${FATJAR_MAP:-${ITL_WORKSPACE_ROOT}/out/cut_to_fatjar_map.csv}"
PR_TESTS_DIR="${PR_TESTS_DIR:-${ITL_PIPELINE_OUTPUT}/pr-tests}"
INCLUDES="${INCLUDES:-*}"
TOP_N="${TOP_N:-2}"
PYTHON="${PYTHON:-python}"
VALIDATE_ONLY="${VALIDATE_ONLY:-0}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

if ! [[ "${TOP_N}" =~ ^[1-9][0-9]*$ ]]; then
  echo "[pr-tests-pipeline] TOP_N must be a positive integer." >&2
  exit 2
fi
if (( TOP_N > 2 )); then
  TOP_N=2
fi

BASE=(
  "${PYTHON}" -m src
  --tests-inventory-csv "${INVENTORY}"
  --cut-to-fatjar-map-csv "${FATJAR_MAP}"
  --generated-dir "${ITL_DATA_ROOT}/collected-tests/generated"
  --manual-dir "${ITL_DATA_ROOT}/collected-tests/manual"
  --pr-tests-dir "${PR_TESTS_DIR}"
  --repos-dir "${ITL_WORKSPACE_ROOT}/repos"
  --libs-cp "vendor/libs/*"
  --build-dir "${ITL_WORKSPACE_ROOT}/pipeline/build/agt"
  --out-dir "${ITL_WORKSPACE_ROOT}/pipeline/tmp"
  --includes "${INCLUDES}"
)

run_step() {
  printf '\n[pr-tests-pipeline] Running:'
  printf ' %q' "${BASE[@]}" "$@"
  printf '\n'
  "${BASE[@]}" "$@"
}

echo "[pr-tests-pipeline] Starting full PR-tests pipeline"
echo "[pr-tests-pipeline] includes=${INCLUDES} top_n=${TOP_N} pr_tests_dir=${PR_TESTS_DIR}"

echo "[pr-tests-pipeline] Running focused regression tests"
"${PYTHON}" -m unittest discover -s tests -q

if [[ "${VALIDATE_ONLY}" != "1" ]]; then
  run_step filter --variants pr-tests
  run_step reduce --variants pr-tests --max-tests "${TOP_N}"
  run_step run --variants pr-tests
  run_step compare --variants pr-tests
  run_step coverage compare --variants pr-tests
  run_step coverage compare-reduced --variants pr-tests --top-n "${TOP_N}"
  run_step coverage incremental --variants pr-tests
  run_step annotation --variants pr-tests --adopted-reduced-out "${ITL_PIPELINE_OUTPUT}/reduced"
  run_step pull-request-maker
else
  echo "[pr-tests-pipeline] VALIDATE_ONLY=1; reusing existing pipeline artifacts"
fi

echo
"${PYTHON}" - "${TOP_N}" "${INCLUDES}" "${PR_TESTS_DIR}" "${INVENTORY}" <<'PY'
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

PIPELINE_OUTPUT_ROOT = os.environ.get("ITL_PIPELINE_OUTPUT", "../../pipeline-output")
DATA_ROOT = os.environ.get("ITL_DATA_ROOT", "../../data")

from src.core.common import repo_to_dir
from src.pipeline.helpers import adopted_test_method_names, individually_runnable_test_method_names, pr_test_path, test_method_names
from src.steps.reduce import _pr_test_method_names

top_n = int(sys.argv[1])
includes = sys.argv[2]
pr_tests_dir = Path(sys.argv[3])
inventory_path = Path(sys.argv[4])
manual_root = Path(DATA_ROOT) / "collected-tests/manual"
suffix = ".csv" if includes == "*" else ".includes.csv"
errors = []
warnings = []
inventory_rows = list(csv.DictReader(inventory_path.open(newline=""))) if inventory_path.exists() else []
inventory_by_target = {
    ((row.get("repo", "") or "").strip(), (row.get("fqcn", "") or "").strip()): row
    for row in inventory_rows
}

filter_summary = Path(f"{PIPELINE_OUTPUT_ROOT}/covfilter/pr-tests/covfilter_summary{suffix}")
filter_rows = list(csv.DictReader(filter_summary.open(newline=""))) if filter_summary.exists() else []
if not filter_rows:
    errors.append(f"missing or empty PR-test filter summary: {filter_summary}")
failed_filter = [row for row in filter_rows if row.get("status") != "passed"]
if failed_filter:
    errors.append(f"{len(failed_filter)} PR-test coverage-filter targets did not pass")
positive_targets = set()
mapped_pr_sources = set()
adopted_method_count = 0
runnable_adopted_method_count = 0
discovered_method_count = 0
candidate_failure_targets = set()
candidate_failure_methods_by_target = {}
for row in filter_rows:
    repo = row.get("repo", "")
    fqcn = row.get("fqcn", "")
    target_id = f"{repo_to_dir(repo)}_{fqcn.replace('.', '_')}"
    source = pr_test_path(pr_tests_dir, target_id, fqcn)
    if source:
        mapped_pr_sources.add(source)
        adopted = set(adopted_test_method_names(source, None))
        runnable_adopted = adopted.intersection(
            individually_runnable_test_method_names(source)
        )
        adopted_method_count += len(adopted)
        runnable_adopted_method_count += len(runnable_adopted)
        inventory_row = inventory_by_target.get((repo, fqcn), {})
        manual_source = next(
            (
                manual_root / repo_to_dir(repo) / fqcn / filename.strip()
                for filename in (inventory_row.get("manual_files", "") or "").split(";")
                if filename.strip() and (manual_root / repo_to_dir(repo) / fqcn / filename.strip()).exists()
            ),
            None,
        )
        if manual_source is None:
            errors.append(f"missing manual source for marker-placement validation: {repo} {fqcn}")
        else:
            manual_methods = set(test_method_names(manual_source))
            expected_adopted = [method for method in test_method_names(source) if method not in manual_methods]
            marked_adopted = adopted_test_method_names(source, None)
            if marked_adopted != expected_adopted:
                errors.append(
                    f"{source} places the Adopted Tests marker incorrectly; "
                    "methods below it do not match methods absent from the manual test"
                )
    else:
        runnable_adopted = set()
        errors.append(f"missing mapped PR-test source for {repo} {fqcn}")
    log_path = Path(row.get("log_file", ""))
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    discovered_matches = re.findall(r"AGT methods discovered:\s*(\d+)", log_text)
    if not discovered_matches:
        errors.append(f"{repo} {fqcn} filter log does not report discovered PR-test methods")
        discovered = 0
    else:
        discovered = int(discovered_matches[-1])
    discovered_method_count += discovered
    if discovered != len(runnable_adopted):
        errors.append(
            f"{repo} {fqcn} discovered {discovered} PR-test methods; "
            f"expected {len(runnable_adopted)} individually runnable adopted methods"
        )
    generated_test_fqcn = row.get("generated_test_fqcn", "")
    candidate_failure_methods = set(
        re.findall(
            rf"FAILURE in\s+{re.escape(generated_test_fqcn)}#([A-Za-z_][A-Za-z0-9_]*)\b",
            log_text,
        )
    )
    if candidate_failure_methods:
        candidate_failure_targets.add((repo, fqcn))
        candidate_failure_methods_by_target[(repo, fqcn)] = candidate_failure_methods
    deltas_csv = Path(row.get("out_dir", "")) / "test_deltas_all.csv"
    deltas = list(csv.DictReader(deltas_csv.open(newline=""))) if deltas_csv.exists() else []
    if len(deltas) != discovered:
        errors.append(f"{repo} {fqcn} produced {len(deltas)} per-test deltas for {discovered} discovered methods")
    positive_methods = {
        (delta.get("test_selector", "") or "").rsplit("#", 1)[-1]
        for delta in deltas
        if any(int((delta.get(field, "") or "0").strip()) > 0 for field in (
            "added_lines",
            "added_methods",
            "added_branches",
            "added_instructions",
        ))
    }
    unsupported = positive_methods - runnable_adopted
    if unsupported:
        errors.append(f"{repo} {fqcn} has positive deltas for unsupported/non-adopted tests: {sorted(unsupported)}")
    kept_deltas_csv = Path(row.get("out_dir", "")) / "test_deltas_kept.csv"
    kept_deltas = list(csv.DictReader(kept_deltas_csv.open(newline=""))) if kept_deltas_csv.exists() else deltas
    if any(
        any(int((delta.get(field, "") or "0").strip()) > 0 for field in (
            "added_lines",
            "added_methods",
            "added_branches",
            "added_instructions",
        ))
        for delta in kept_deltas
        if (delta.get("test_selector", "") or "").rsplit("#", 1)[-1] in runnable_adopted
    ):
        positive_targets.add((repo, fqcn))

if candidate_failure_targets:
    failed_names = ", ".join(
        f"{repo} ({fqcn.rsplit('.', 1)[-1]})"
        for repo, fqcn in sorted(candidate_failure_targets)
    )
    warnings.append(
        f"{len(candidate_failure_targets)} targets reported candidate-test failures; failed methods are zeroed "
        f"and cannot be selected: {failed_names}"
    )
print(
    f"[pr-tests-pipeline] Adopted methods: {adopted_method_count}; "
    f"individually runnable/discovered: {runnable_adopted_method_count}/{discovered_method_count}; "
    f"excluded from the JUnit-only individual runner: {adopted_method_count - runnable_adopted_method_count}"
)

marker_variant = re.compile(
    r"^\s*//\s*(?:the\s+)?(?:added|adopted)(?:\s+agt)?\s+tests?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
canonical_marker = re.compile(r"^\s*// Adopted Tests\s*$", re.MULTILINE)
for source in sorted(mapped_pr_sources):
    text = source.read_text(encoding="utf-8", errors="ignore")
    if len(marker_variant.findall(text)) != 1 or len(canonical_marker.findall(text)) != 1:
        errors.append(f"{source} must contain exactly one canonical // Adopted Tests marker")
for label, summary, variant in (
    ("run", Path(f"{PIPELINE_OUTPUT_ROOT}/coverage/adopted_coverage_summary{suffix}"), "pr-tests"),
    ("coverage compare", Path(f"{PIPELINE_OUTPUT_ROOT}/coverage/coverage_compare{suffix}"), "pr-tests"),
    ("reduced coverage compare", Path(f"{PIPELINE_OUTPUT_ROOT}/coverage/coverage_compare_reduced{suffix}"), "pr-tests-reduced"),
):
    rows = list(csv.DictReader(summary.open(newline=""))) if summary.exists() else []
    selected = [row for row in rows if row.get("variant") == variant]
    failed = [
        row
        for row in selected
        if any((row.get(field, "0") or "0").strip() not in {"", "0"} for field in ("failed", "timeout", "skipped"))
    ]
    if failed:
        errors.append(f"{len(failed)} positive-coverage PR targets failed or skipped during {label}")
    selected_targets = {(row.get("repo", ""), row.get("fqcn", "")) for row in selected}
    if selected_targets != positive_targets:
        errors.append(f"{label} results do not match the positive-coverage PR target set")

compare_csv = Path(f"{PIPELINE_OUTPUT_ROOT}/compare/compare{suffix}")
compare_rows = list(csv.DictReader(compare_csv.open(newline=""))) if compare_csv.exists() else []
compare_targets = {
    (row.get("repo", ""), row.get("fqcn", ""))
    for row in compare_rows
    if row.get("variant") == "pr-tests"
}
if compare_targets != positive_targets:
    errors.append("compare results do not match the positive-coverage PR target set")
tri_compare_csv = Path(f"{PIPELINE_OUTPUT_ROOT}/compare/tri_compare{suffix}")
tri_compare_rows = list(csv.DictReader(tri_compare_csv.open(newline=""))) if tri_compare_csv.exists() else []
tri_compare_groups = {
    row.get("group_id", "")
    for row in tri_compare_rows
    if row.get("variant") == "pr-tests" and row.get("group_id")
}
expected_tri_compare_groups = {
    f"{repo_to_dir(repo)}_{fqcn.replace('.', '_')}.auto.pr-tests"
    for repo, fqcn in positive_targets
}
if tri_compare_groups != expected_tri_compare_groups:
    errors.append("tri-compare results do not match the positive-coverage PR target set")

reduced_methods_by_target = {}
zero_target_line_annotations = 0
all_zero_overall_annotations = 0
for label, summary, path_field in (
    ("Reduced", Path(f"{PIPELINE_OUTPUT_ROOT}/reduced/pr-tests/reduce_summary{suffix}"), "reduced_test_path"),
    ("Annotated", Path(f"{PIPELINE_OUTPUT_ROOT}/annotation/summary{suffix}"), "annotated_test_path"),
):
    rows = list(csv.DictReader(summary.open(newline=""))) if summary.exists() else []
    failed = [row for row in rows if row.get("variant") == "pr-tests" and row.get("status") == "failed"]
    if failed:
        errors.append(f"{len(failed)} {label.lower()} targets failed")
    passed_targets = {
        (row.get("repo", ""), row.get("fqcn", ""))
        for row in rows
        if row.get("variant") == "pr-tests" and row.get("status") == "passed"
    }
    if passed_targets != positive_targets:
        errors.append(f"{label.lower()} outputs do not match the positive-coverage PR target set")
    files = sorted(
        Path(row[path_field])
        for row in rows
        if row.get("variant") == "pr-tests" and row.get("status") == "passed" and row.get(path_field)
    )
    counts = Counter(len(_pr_test_method_names(path)) for path in files)
    print(f"[pr-tests-pipeline] {label} outputs: {len(files)}; selected-method counts: {dict(sorted(counts.items()))}")
    for path in files:
        methods = _pr_test_method_names(path)
        count = len(methods)
        if not 1 <= count <= top_n:
            errors.append(f"{path} contains {count} selected tests; expected 1..{top_n}")
        matching_row = next(
            (
                row
                for row in rows
                if row.get("variant") == "pr-tests"
                and row.get("status") == "passed"
                and row.get(path_field) == str(path)
            ),
            None,
        )
        target = (
            matching_row.get("repo", ""),
            matching_row.get("fqcn", ""),
        ) if matching_row else None
        if label == "Reduced" and matching_row:
            deltas_path = Path(matching_row.get("test_deltas_csv", ""))
            delta_rows = list(csv.DictReader(deltas_path.open(newline=""))) if deltas_path.exists() else []
            expected_methods = {
                (row.get("test_selector", "") or "").rsplit("#", 1)[-1]
                for row in delta_rows[:top_n]
            }
            if set(methods) != expected_methods:
                errors.append(f"{path} selected methods do not match its top positive coverage deltas")
            selected_failures = set(methods).intersection(candidate_failure_methods_by_target.get(target, set()))
            if selected_failures:
                errors.append(f"{path} selected failed candidate tests: {sorted(selected_failures)}")
            reduced_methods_by_target[target] = set(methods)
        if label == "Annotated":
            if target and set(methods) != reduced_methods_by_target.get(target, set()):
                errors.append(f"{path} methods do not match the reduced PR-test output")
            text = path.read_text(encoding="utf-8", errors="ignore")
            zero_target_line_annotations += text.count("Target-class added line coverage: 0.00%")
            deltas = [
                tuple(map(int, match))
                for match in re.findall(
                    r"Overall delta: \+(\d+) lines, \+(\d+) methods, \+(\d+) branches, \+(\d+) instructions",
                    text,
                )
            ]
            if len(deltas) != count:
                errors.append(f"{path} has {len(deltas)} coverage annotations for {count} tests")
            all_zero_count = sum(not any(delta) for delta in deltas)
            all_zero_overall_annotations += all_zero_count
            if all_zero_count:
                errors.append(f"{path} contains an all-zero overall coverage annotation")

print(
    f"[pr-tests-pipeline] Annotation metrics: {zero_target_line_annotations} selected tests add no new "
    f"target-class lines but have non-zero overall coverage; {all_zero_overall_annotations} selected tests "
    "have an all-zero overall delta."
)

drafts = sorted(Path(f"{PIPELINE_OUTPUT_ROOT}/pr").glob("*.pr-tests.md"))
expected_drafts = {
    Path(f"{PIPELINE_OUTPUT_ROOT}/pr") / f"{repo_to_dir(repo)}_{fqcn.replace('.', '_')}.pr-tests.md"
    for repo, fqcn in positive_targets
}
if set(drafts) != expected_drafts:
    errors.append("PR draft files do not match the positive-coverage target set")
for path in drafts:
    text = path.read_text(encoding="utf-8", errors="ignore")
    body_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(body_lines) < 9:
        errors.append(f"{path} is too short for the conversational PR format")
    if not body_lines or body_lines[1:2] != ["Hey 👋"]:
        errors.append(f"{path} must greet with Hey 👋 after the title")
    if "Impact on coverage:" not in body_lines:
        errors.append(f"{path} is missing the Impact on coverage section")
    if "Why:" not in body_lines:
        errors.append(f"{path} is missing the Why section")
    selected_methods = re.findall(r"^\* Added [A-Za-z_][A-Za-z0-9_]*\s*$", text, re.MULTILINE)
    impact_lines = re.findall(r"^\* .+$", text, re.MULTILINE)
    if len(impact_lines) != 2:
        errors.append(f"{path} must contain exactly two impact bullets")
    if selected_methods:
        errors.append(f"{path} should not list Added methods in the PR body")
    selected_count = 1 if "I added a regression test" in text else 2
    title_line = body_lines[0] if body_lines else ""
    if selected_count == 1 and not re.match(r"^# test: add regression test for .+$", title_line):
        errors.append(f"{path} must use singular regression-test title")
    if selected_count > 1 and not re.match(r"^# test: add regression tests for .+$", title_line):
        errors.append(f"{path} must use plural regression-tests title")
    summary_line = body_lines[2] if len(body_lines) > 2 else ""
    if selected_count == 1 and "I added a regression test" not in summary_line:
        errors.append(f"{path} must use singular summary text for one selected test")
    if selected_count > 1 and not re.search(r"I added \w+ regression tests", summary_line):
        errors.append(f"{path} must use plural summary text for multiple selected tests")
    if " around " not in summary_line:
        errors.append(f"{path} summary must use the concise 'around ...' phrasing")
    coverage_lines = re.findall(r"^\* (?:Coverage|Branch coverage) for [A-Za-z0-9_$]+ increases by \d+(?:\.\d+)?%\.$", text, re.MULTILINE)
    if len(coverage_lines) != 1:
        errors.append(f"{path} contains more than one coverage summary")
    if "I hope you find this test useful." not in text and "I hope you find these tests useful." not in text:
        errors.append(f"{path} is missing the closing usefulness sentence")
    if "Thanks," not in body_lines:
        errors.append(f"{path} is missing Thanks closing")
    if any(marker in text for marker in ("Measured coverage delta:", "instructions", "selected from the adopted", "## Test Added", "## Tests Added", "## What changed", "## Why")):
        errors.append(f"{path} contains removed PR boilerplate")

if errors:
    print("[pr-tests-pipeline] VALIDATION FAILED:")
    for error in errors:
        print(f"[pr-tests-pipeline] - {error}")
    raise SystemExit(1)
for warning in warnings:
    print(f"[pr-tests-pipeline] WARNING: {warning}")
print(
    f"[pr-tests-pipeline] Validation passed: {len(filter_rows)} filtered targets, "
    f"{len(drafts)} coverage-adding PR drafts, maximum 2 tests each."
)
PY

echo "[pr-tests-pipeline] Selected tests: /reduced/pr-tests/"
echo "[pr-tests-pipeline] Annotated selected tests: /annotation/pr-tests/"
echo "[pr-tests-pipeline] Tailored PR drafts: /pr/*.pr-tests.md"
echo "[pr-tests-pipeline] Done."
