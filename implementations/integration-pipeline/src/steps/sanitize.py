from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.common import repo_to_dir
from ..pipeline.sanitize import clear_pair_root, materialize_sanitized_pair, sanitize_summary_csv, variant_test_fqcn
from .base import Step

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


def _append_sanitize_summary_row(
    *,
    csv_path: Path,
    ctx: "TargetContext",
    status: str,
    problem_category: str,
    problem_detail: str,
    baseline_test_fqcn: str,
    sanitized_test_fqcn: str,
    baseline_test_source: Path | None,
    sanitized_test_source: Path | None,
    sanitized_scaffolding_source: Path | None,
) -> None:
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                ctx.repo,
                ctx.fqcn,
                status,
                problem_category,
                problem_detail,
                baseline_test_fqcn,
                sanitized_test_fqcn,
                str(baseline_test_source) if baseline_test_source else "",
                str(sanitized_test_source) if sanitized_test_source else "",
                str(sanitized_scaffolding_source) if sanitized_scaffolding_source else "",
            ]
        )


class SanitizeEvoSuiteStep(Step):
    step_names = ("sanitize-es",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True

        summary_csv = sanitize_summary_csv(Path(self.pipeline.args.sanitized_es_dir), self.pipeline.args.includes)
        baseline_test_fqcn = variant_test_fqcn(ctx.generated_test_fqcn or "", "baseline") if ctx.generated_test_fqcn else ""
        baseline_test_source = None
        if baseline_test_fqcn:
            baseline_test_source = self.pipeline.generated_dir / repo_to_dir(ctx.repo) / ctx.fqcn / (
                f"{baseline_test_fqcn.rsplit('.', 1)[-1]}.java"
            )

        if not baseline_test_fqcn:
            _append_sanitize_summary_row(
                csv_path=summary_csv,
                ctx=ctx,
                status="skipped",
                problem_category="missing_generated_test_fqcn",
                problem_detail="Need a generated test FQCN to materialize a sanitized EvoSuite variant.",
                baseline_test_fqcn="",
                sanitized_test_fqcn="",
                baseline_test_source=baseline_test_source,
                sanitized_test_source=None,
                sanitized_scaffolding_source=None,
            )
            return True

        clear_pair_root(Path(self.pipeline.args.sanitized_es_dir), ctx.repo, ctx.fqcn)
        sanitized_pair = materialize_sanitized_pair(
            source_root=self.pipeline.generated_dir,
            sanitized_root=Path(self.pipeline.args.sanitized_es_dir),
            repo=ctx.repo,
            fqcn=ctx.fqcn,
            test_fqcn=baseline_test_fqcn,
        )
        if sanitized_pair is None:
            _append_sanitize_summary_row(
                csv_path=summary_csv,
                ctx=ctx,
                status="skipped",
                problem_category="missing_baseline_es_pair",
                problem_detail="Could not find the baseline EvoSuite test/scaffolding pair to sanitize.",
                baseline_test_fqcn=baseline_test_fqcn,
                sanitized_test_fqcn="",
                baseline_test_source=baseline_test_source,
                sanitized_test_source=None,
                sanitized_scaffolding_source=None,
            )
            return True

        _append_sanitize_summary_row(
            csv_path=summary_csv,
            ctx=ctx,
            status="passed",
            problem_category="",
            problem_detail="",
            baseline_test_fqcn=baseline_test_fqcn,
            sanitized_test_fqcn=sanitized_pair.test_fqcn,
            baseline_test_source=baseline_test_source,
            sanitized_test_source=sanitized_pair.test_src,
            sanitized_scaffolding_source=sanitized_pair.scaffolding_src,
        )
        self.pipeline.ran += 1
        return True
