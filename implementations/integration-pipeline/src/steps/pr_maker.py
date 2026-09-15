from __future__ import annotations

import csv
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING

from ..core.common import parse_package_and_class, repo_to_dir
from ..pipeline.helpers import adopted_test_method_names, individually_runnable_test_method_names, pr_test_path
from .base import Step

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


@dataclass(frozen=True)
class SelectedTest:
    method: str
    component: str = ""
    test_file: str = ""
    measured: bool = False
    added_lines: int = 0
    added_methods: int = 0
    added_branches: int = 0
    added_instructions: int = 0
    target_line_percentage: float = 0.0
    target_branch_percentage: float = 0.0
    target_lines_url: str = ""

    @property
    def rank(self) -> tuple[int, int, int, int, int, str]:
        return (
            -self.added_lines,
            -self.added_branches,
            -self.added_methods,
            -self.added_instructions,
            0 if self.measured else 1,
            self.method,
        )

    @property
    def adds_coverage(self) -> bool:
        return any((self.added_lines, self.added_methods, self.added_branches, self.added_instructions))


def _safe_int(value: str) -> int:
    try:
        return int((value or "0").strip())
    except ValueError:
        return 0


def _safe_float(value: str) -> float:
    try:
        return float((value or "0").strip())
    except ValueError:
        return 0.0


def _humanize_method_name(method: str) -> str:
    text = re.sub(r"^(?:test|should)_?", "", method, flags=re.IGNORECASE)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ").strip().lower()
    return re.sub(r"\s+", " ", text) or method


def _read_test_deltas(csv_path: Path) -> Dict[str, SelectedTest]:
    if not csv_path.exists():
        return {}
    out: Dict[str, SelectedTest] = {}
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                selector = (row.get("test_selector", "") or "").strip()
                method = selector.rsplit("#", 1)[-1]
                if not method:
                    continue
                out[method] = SelectedTest(
                    method=method,
                    measured=True,
                    added_lines=_safe_int(row.get("added_lines", "0")),
                    added_methods=_safe_int(row.get("added_methods", "0")),
                    added_branches=_safe_int(row.get("added_branches", "0")),
                    added_instructions=_safe_int(row.get("added_instructions", "0")),
                )
    except OSError:
        return {}
    return out


def _select_pr_tests(
    *,
    pr_source: Path,
    manual_source: Optional[Path],
    covfilter_dir: Path,
    max_tests: int = 2,
) -> list[SelectedTest]:
    deltas: Dict[str, SelectedTest] = {}
    for name in ("test_deltas_selected.csv", "test_deltas_all.csv", "test_deltas_kept.csv"):
        delta_path = covfilter_dir / name
        if delta_path.exists():
            deltas = _read_test_deltas(delta_path)
            break
    runnable = set(individually_runnable_test_method_names(pr_source))
    selected = [
        deltas[method]
        for method in adopted_test_method_names(pr_source, manual_source)
        if method in runnable and method in deltas and deltas[method].adds_coverage
    ]
    return sorted(selected, key=lambda test: test.rank)[:max_tests]


def _repo_relative_test_path(ctx: "TargetContext", manual_source: Optional[Path], pr_source: Path) -> str:
    repo_root = ctx.repo_root_for_deps
    if repo_root and repo_root.exists():
        expected_pkg, expected_cls = parse_package_and_class(manual_source or pr_source)
        filename = f"{expected_cls}.java" if expected_cls else (manual_source or pr_source).name
        matches: list[Path] = []
        for candidate in repo_root.rglob(filename):
            pkg, cls = parse_package_and_class(candidate)
            if cls == expected_cls and pkg == expected_pkg:
                matches.append(candidate)
        if matches:
            def score(path: Path) -> tuple[int, int, str]:
                parts = set(path.parts)
                generated_penalty = 1 if parts & {"build", "target", ".gradle", "generated"} else 0
                test_source_penalty = 0 if "src" in parts and "test" in parts else 1
                return generated_penalty, test_source_penalty, str(path)

            candidate = sorted(matches, key=score)[0]
            try:
                return str(candidate.relative_to(repo_root))
            except ValueError:
                return str(candidate)
    return str(manual_source or pr_source)


def _percentage(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def _coverage_summary(selected: list[SelectedTest]) -> str:
    by_component: Dict[str, tuple[float, float]] = {}
    for test in selected:
        by_component[test.component] = (
            test.target_line_percentage,
            test.target_branch_percentage,
        )

    summaries: list[str] = []
    for component, (line_percentage, branch_percentage) in sorted(by_component.items()):
        if line_percentage > 0:
            summaries.append(f"Coverage for {component} increases by {_percentage(line_percentage)}.")
        elif branch_percentage > 0:
            summaries.append(f"Branch coverage for {component} increases by {_percentage(branch_percentage)}.")
    if not summaries:
        return ""
    return " ".join(summaries)


def _selected_test_markdown(selected: list[SelectedTest]) -> str:
    return "\n".join(f"* Added {test.method}" for test in selected)


def _coverage_impact(selected: list[SelectedTest]) -> str:
    if not selected:
        return ""
    component = selected[0].component
    url = next((test.target_lines_url for test in selected if test.target_lines_url), "")
    if url:
        coverage_area = f"* Covers the relevant {component} logic, including [covered lines]({url})"
    else:
        coverage_area = f"* Covers the relevant {component} logic"
    coverage = _coverage_summary(selected)
    return "\n".join(line for line in (coverage_area, f"* {coverage}" if coverage else "") if line)


def _load_target_coverage_metrics(
    csv_path: Path,
    *,
    repo: str,
    fqcn: str,
    covfilter_dir: Optional[Path] = None,
) -> tuple[float, float]:
    if not csv_path.exists():
        return 0.0, 0.0
    line_total = 0
    line_percentage = 0.0
    branch_percentage = 0.0
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if (
                    (row.get("repo", "") or "").strip() == repo
                    and (row.get("fqcn", "") or "").strip() == fqcn
                    and (row.get("variant", "") or "").strip() == "pr-tests"
                    and (row.get("is_target_class", "") or "").strip().lower() == "true"
                ):
                    line_total = _safe_int(row.get("line_total", "0"))
                    line_percentage = _safe_float(row.get("added_line_percentage", "0"))
                    branch_percentage = _safe_float(row.get("added_branch_percentage", "0"))
                    break
    except OSError:
        pass

    if line_percentage <= 0 and line_total > 0 and covfilter_dir is not None:
        added_lines: set[tuple[str, int]] = set()
        line_deltas = covfilter_dir / "line_deltas_selected.csv"
        try:
            with line_deltas.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    class_name = (row.get("class_name", "") or "").strip()
                    if class_name != fqcn and not class_name.startswith(f"{fqcn}$"):
                        continue
                    for part in (row.get("newly_covered_lines", "") or "").split(";"):
                        part = part.strip()
                        if not part:
                            continue
                        start, _, end = part.partition("-")
                        first = _safe_int(start)
                        last = _safe_int(end) if end else first
                        for line in range(first, last + 1):
                            if line > 0:
                                added_lines.add((class_name, line))
        except OSError:
            pass
        line_percentage = 100.0 * len(added_lines) / line_total

    return line_percentage, branch_percentage


def _target_lines_url(*, repo: str, repo_root: Path, fqcn: str, covfilter_dir: Path) -> str:
    source_path = _source_path_for_fqcn(repo_root, fqcn)
    if source_path is None:
        return ""
    line_spec = _first_target_line_spec(covfilter_dir / "line_deltas_selected.csv", fqcn)
    if not line_spec:
        return ""
    start, end = _line_spec_bounds(line_spec)
    if start <= 0:
        return ""
    try:
        rel = source_path.relative_to(repo_root).as_posix()
    except ValueError:
        return ""
    branch = _default_remote_branch(repo_root)
    suffix = f"#L{start}" if end <= start else f"#L{start}-L{end}"
    return f"https://github.com/{repo}/blob/{branch}/{rel}{suffix}"


def _source_path_for_fqcn(repo_root: Path, fqcn: str) -> Optional[Path]:
    if not repo_root.exists():
        return None
    package, _, class_name = fqcn.rpartition(".")
    filename = f"{class_name}.java"
    expected_suffix = Path(*fqcn.split(".")).with_suffix(".java")
    candidates: list[Path] = []
    direct_roots = (
        "src/main/java",
        "src/test/java",
        "build/generated/sources",
        "target/generated-sources",
    )
    for root in direct_roots:
        candidate = repo_root / root / expected_suffix
        if candidate.exists():
            candidates.append(candidate)
    for candidate in repo_root.rglob(filename):
        pkg, cls = parse_package_and_class(candidate)
        if cls == class_name and pkg == package:
            candidates.append(candidate)
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, str]:
        parts = path.parts
        main_penalty = 0 if "main" in parts else 1
        generated_penalty = 1 if {"build", "target", "generated"} & set(parts) else 0
        return main_penalty + generated_penalty, str(path)

    return sorted(set(candidates), key=score)[0]


def _first_target_line_spec(line_deltas_csv: Path, fqcn: str) -> str:
    if not line_deltas_csv.exists():
        return ""
    try:
        with line_deltas_csv.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                class_name = (row.get("class_name", "") or "").strip()
                if class_name != fqcn and not class_name.startswith(f"{fqcn}$"):
                    continue
                spec = (row.get("newly_covered_lines", "") or "").strip()
                if spec:
                    return spec.split(";", 1)[0].strip()
    except OSError:
        return ""
    return ""


def _line_spec_bounds(spec: str) -> tuple[int, int]:
    start, _, end = (spec or "").partition("-")
    first = _safe_int(start)
    last = _safe_int(end) if end else first
    return first, last


def _default_remote_branch(repo_root: Path) -> str:
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo_root), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    value = (proc.stdout or "").strip()
    if value.startswith("origin/"):
        return value.split("/", 1)[1]
    return "main"


def _load_tailored_pr_field(
    csv_path: Path,
    *,
    repo: str,
    selected: list[SelectedTest],
    field: str,
) -> str:
    if not csv_path.exists():
        return ""
    selected_methods = sorted(test.method for test in selected)
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if (row.get("repo", "") or "").strip() != repo:
                    continue
                methods = sorted(
                    method.strip()
                    for method in (row.get("methods", "") or "").split(";")
                    if method.strip()
                )
                if methods == selected_methods:
                    return (row.get(field, "") or "").strip()
    except OSError:
        pass
    return ""


def _load_tailored_description(
    csv_path: Path,
    *,
    repo: str,
    selected: list[SelectedTest],
) -> str:
    return _load_tailored_pr_field(csv_path, repo=repo, selected=selected, field="description")


def _load_tailored_summary_topic(
    csv_path: Path,
    *,
    repo: str,
    selected: list[SelectedTest],
) -> str:
    return _load_tailored_pr_field(csv_path, repo=repo, selected=selected, field="summary_topic")


def _fallback_description(selected: list[SelectedTest]) -> str:
    behaviors = [_humanize_method_name(test.method) for test in selected]
    if len(behaviors) == 1:
        return f"This adds a regression test for {behaviors[0]}."
    return f"This adds regression tests for {behaviors[0]} and {behaviors[1]}."


def _tests_heading(selected_count: int) -> str:
    return "Test Added" if selected_count == 1 else "Tests Added"


def _title(component_label: str, *, selected_count: int) -> str:
    noun = "regression test" if selected_count == 1 else "regression tests"
    return f"test: add {noun} for {component_label}"


def _summary_sentence(
    components: list[str],
    selected: list[SelectedTest],
    description: str,
    summary_topic: str = "",
) -> str:
    class_names = " and ".join(components)
    noun = "a regression test" if len(selected) == 1 else f"{_count_word(len(selected))} regression tests"
    topic = (summary_topic or _short_cover_detail(description)).strip()
    return f"I added {noun} for {class_names} around {topic}."


def _count_word(count: int) -> str:
    return {1: "one", 2: "two"}.get(count, str(count))


def _short_cover_detail(description: str) -> str:
    text = (description or "").strip()
    if not text:
        return "expected behavior that was not explicitly covered"
    text = re.sub(r"^(Adds coverage for|Covers|Verifies|Confirms)\s+", "", text)
    text = re.split(r",\s+(?:including|verifying|while|and verifies|and confirms)\b", text, maxsplit=1)[0]
    text = _lower_initial_word(text)
    if text.startswith("that "):
        text = "the case where " + text[len("that "):]
    text = text.rstrip(".")
    return text


def _lower_initial_word(text: str) -> str:
    if len(text) >= 2 and text[0].isupper() and text[1].isupper():
        return text
    return text[:1].lower() + text[1:] if text else text


def _why_paragraph(component_label: str, description: str, *, selected_count: int) -> str:
    assertion = _why_assertion_sentence(description, selected_count=selected_count)
    if not assertion:
        subject = "This test" if selected_count == 1 else "These tests"
        assertion = f"{subject} makes sure {component_label} keeps the expected behavior."
    return (
        f"While looking through the {component_label} implementation, I noticed this behavior wasn't directly "
        f"verified by the existing test suite. {assertion}"
    )


def _why_assertion_sentence(description: str, *, selected_count: int) -> str:
    text = (description or "").strip()
    if not text:
        return ""
    text = text.rstrip(".")
    subject = "This test" if selected_count == 1 else "These tests"

    for prefix, verb in (
        ("Adds coverage for ", "cover"),
        ("Covers ", "cover"),
        ("Verifies that ", "verify that"),
        ("Verifies ", "verify"),
        ("Confirms that ", "make sure that"),
        ("Confirms ", "confirm"),
    ):
        if text.startswith(prefix):
            detail = text[len(prefix):]
            if verb == "make sure that":
                predicate = "makes sure that" if selected_count == 1 else "make sure that"
            elif verb == "cover":
                predicate = "covers" if selected_count == 1 else "cover"
            elif verb == "verify":
                predicate = "verifies" if selected_count == 1 else "verify"
            elif verb == "verify that":
                predicate = "verifies that" if selected_count == 1 else "verify that"
            elif verb == "confirm":
                predicate = "confirms" if selected_count == 1 else "confirm"
            else:
                predicate = verb
            return f"{subject} {predicate} {_lower_initial_word(detail)}."

    return f"{subject} cover {_lower_initial_word(text)}." if selected_count != 1 else (
        f"{subject} covers {_lower_initial_word(text)}."
    )


def _render_pr_template(template: str, replacements: Dict[str, str]) -> str:
    out = template
    for key, value in replacements.items():
        out = out.replace(f"{{{{{key}}}}}", value)
    return out


def _should_reset_pr_drafts(includes: str) -> bool:
    normalized = (includes or "").strip()
    return not normalized or normalized == "*"


def pr_test_draft_path(pr_out_root: Path, target_id: str) -> Path:
    return pr_out_root / f"{target_id}.pr-tests.md"


def _manual_source_for_inventory_row(pipeline, row: Dict[str, str]) -> Optional[Path]:
    filenames = [
        name.strip()
        for name in (row.get("manual_files", "") or "").split(";")
        if name.strip()
    ]
    for filename in filenames:
        candidate = pipeline.manual_dir / repo_to_dir(row["repo"]) / row["fqcn"] / filename
        if candidate.exists():
            return candidate
    return None


def _target_selected_tests(pipeline, ctx: "TargetContext", *, max_tests: int = 2) -> list[SelectedTest]:
    candidates: list[SelectedTest] = []
    for row in pipeline.inv_rows:
        if (row.get("repo", "") or "").strip() != ctx.repo:
            continue
        fqcn = (row.get("fqcn", "") or "").strip()
        if fqcn != ctx.fqcn:
            continue
        target_id = f"{repo_to_dir(ctx.repo)}_{fqcn.replace('.', '_')}"
        pr_source = pr_test_path(pipeline.pr_tests_root, target_id, fqcn)
        if not pr_source:
            continue
        manual_source = _manual_source_for_inventory_row(pipeline, row)
        covfilter_dir = pipeline.adopted_covfilter_out_root / "pr-tests" / target_id
        component = fqcn.rsplit(".", 1)[-1]
        test_file = _repo_relative_test_path(ctx, manual_source, pr_source)
        line_percentage, branch_percentage = _load_target_coverage_metrics(
            pipeline.coverage_incremental_csv,
            repo=ctx.repo,
            fqcn=fqcn,
            covfilter_dir=covfilter_dir,
        )
        target_lines_url = (
            _target_lines_url(
                repo=ctx.repo,
                repo_root=ctx.repo_root_for_deps,
                fqcn=fqcn,
                covfilter_dir=covfilter_dir,
            )
            if ctx.repo_root_for_deps is not None
            else ""
        )
        candidates.extend(
            replace(
                test,
                component=component,
                test_file=test_file,
                target_line_percentage=line_percentage,
                target_branch_percentage=branch_percentage,
                target_lines_url=target_lines_url,
            )
            for test in _select_pr_tests(
                pr_source=pr_source,
                manual_source=manual_source,
                covfilter_dir=covfilter_dir,
                max_tests=100,
            )
        )
    return sorted(candidates, key=lambda test: test.rank)[:max_tests]


def write_pr_test_draft(
    *,
    template_path: Path,
    out_path: Path,
    ctx: "TargetContext",
    selected: list[SelectedTest],
    descriptions_path: Optional[Path] = None,
) -> bool:
    if not template_path.exists():
        return False
    if not selected:
        return False

    components = sorted({test.component for test in selected})
    component_label = " and ".join(components)
    pr_text_path = descriptions_path or template_path.with_name("PR_DESCRIPTIONS.csv")
    description = _load_tailored_description(
        pr_text_path,
        repo=ctx.repo,
        selected=selected,
    ) or _fallback_description(selected)
    summary_topic = _load_tailored_summary_topic(
        pr_text_path,
        repo=ctx.repo,
        selected=selected,
    )
    replacements = {
        "title": _title(component_label, selected_count=len(selected)),
        "summary": _summary_sentence(components, selected, description, summary_topic),
        "coverage_impact": _coverage_impact(selected),
        "why": _why_paragraph(component_label, description, selected_count=len(selected)),
        "useful_object": "this test" if len(selected) == 1 else "these tests",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        _render_pr_template(template_path.read_text(encoding="utf-8"), replacements),
        encoding="utf-8",
    )
    return True


class PullRequestMakerStep(Step):
    step_names = ("pull-request-maker",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        if not self.pipeline.pr_template.exists():
            print(f'[agt] pull-request-maker: Skip (missing template): {self.pipeline.pr_template}')
            return True

        if not hasattr(self.pipeline, "_pr_test_draft_targets"):
            self.pipeline.pr_out_root.mkdir(parents=True, exist_ok=True)
            includes = getattr(self.pipeline.args, "includes", "")
            if _should_reset_pr_drafts(includes):
                for existing in self.pipeline.pr_out_root.glob("*.pr-tests.md"):
                    existing.unlink()
            setattr(self.pipeline, "_pr_test_draft_targets", set())
        written_targets = getattr(self.pipeline, "_pr_test_draft_targets")
        if ctx.target_id in written_targets:
            return True
        written_targets.add(ctx.target_id)
        setattr(self.pipeline, "_pr_test_draft_targets", written_targets)

        selected = _target_selected_tests(self.pipeline, ctx, max_tests=2)
        out_path = pr_test_draft_path(self.pipeline.pr_out_root, ctx.target_id)
        if write_pr_test_draft(
            template_path=self.pipeline.pr_template,
            out_path=out_path,
            ctx=ctx,
            selected=selected,
            descriptions_path=self.pipeline.pr_template.with_name("PR_DESCRIPTIONS.csv"),
        ):
            print(f'[agt] pull-request-maker: wrote {out_path}')
        else:
            print(f'[agt] pull-request-maker: Skip (no coverage-adding adopted tests) for {ctx.target_id}')
        return True
