import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile

from support import write_csv

from src.core import incremental_target_cut


class IncrementalTargetCutTest(unittest.TestCase):
    def test_incremental_target_ids_exclude_nested_and_similarly_named_classes(self) -> None:
        execinfo = """\
CLASS ID         HITS/PROBES   CLASS NAME
1111111111111111    1 of   2   sample/Target
2222222222222222    1 of   2   sample/Target$Nested
3333333333333333    1 of   2   sample/TargetHelper
"""
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(incremental_target_cut, "JACOCO", Path("jacoco.jar"), create=True),
            patch.object(
                incremental_target_cut,
                "run",
                return_value=SimpleNamespace(stdout=execinfo),
            ),
        ):
            exec_file = Path(temp) / "coverage.exec"
            exec_file.write_bytes(b"exec")
            self.assertEqual(
                incremental_target_cut.target_ids(exec_file, "sample.Target"),
                {"1111111111111111"},
            )

    def test_incremental_auto_execution_can_reuse_compatible_prior_filename(self) -> None:
        candidates = incremental_target_cut.variant_exec_candidates(
            Path("workspace/tmp"),
            "repo_sample_Target",
            "auto",
        )
        self.assertEqual(
            candidates,
            (
                Path("workspace/tmp/repo_sample_Target__auto_fixed.exec"),
                Path("workspace/tmp/repo_sample_Target__auto.exec"),
            ),
        )
        self.assertEqual(
            incremental_target_cut.variant_exec_candidates(
                Path("workspace/tmp"),
                "repo_sample_Target",
                "agentic",
            ),
            (Path("workspace/tmp/repo_sample_Target__agentic.exec"),),
        )
        self.assertEqual(
            incremental_target_cut.variant_exec_candidates(
                Path("workspace/tmp"),
                "repo_sample_Target",
                "auto-100",
                auto_100_uses_full_suite=True,
            ),
            candidates,
        )

    def test_incremental_merge_removes_stale_destination_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            merged = Path(temp) / "combined.exec"
            merged.write_bytes(b"stale coverage")

            def fake_run(*_args: str) -> SimpleNamespace:
                self.assertFalse(merged.exists())
                return SimpleNamespace(returncode=0)

            with (
                patch.object(incremental_target_cut, "JACOCO", Path("jacoco.jar"), create=True),
                patch.object(incremental_target_cut, "run", side_effect=fake_run),
            ):
                result = incremental_target_cut.merge_exec_files(
                    Path("manual.exec"),
                    Path("variant.exec"),
                    merged,
                )
            self.assertEqual(result.returncode, 0)

    def test_incremental_unavailable_rows_use_audited_problem_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            coverage = root / "results" / "coverage"
            write_csv(
                coverage / "coverage_filtered32_zero_audit.csv",
                ["repo", "fqcn", "variant", "cause", "evidence"],
                [{"repo": "repo", "fqcn": "sample.Target", "variant": "improved", "cause": "Compile failed.", "evidence": "run.log"}],
            )
            write_csv(
                coverage / "coverage_filtered32_gaps.csv",
                ["repo", "fqcn", "variant", "problem_detail"],
                [{"repo": "repo", "fqcn": "sample.Target", "variant": "auto-100", "problem_detail": "No Top100 source."}],
            )
            details = incremental_target_cut.unavailable_problem_details(root / "results")
            self.assertEqual(details[("repo", "sample.Target", "improved")], "Compile failed. Evidence: run.log")
            self.assertEqual(details[("repo", "sample.Target", "auto-100")], "No Top100 source.")


if __name__ == "__main__":
    unittest.main()
