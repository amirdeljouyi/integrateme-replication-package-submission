import unittest

from pathlib import Path
from types import SimpleNamespace
import tempfile

from support import SOURCE, write_csv

from src.steps.pr_maker import SelectedTest, _coverage_impact, _coverage_summary, _load_tailored_description, _load_tailored_summary_topic, _load_target_coverage_metrics, _select_pr_tests, _should_reset_pr_drafts, _summary_sentence, _target_selected_tests, _title, _why_paragraph, pr_test_draft_path


class PrMakerTest(unittest.TestCase):
    def test_pr_draft_reset_is_limited_to_full_runs(self) -> None:
        self.assertTrue(_should_reset_pr_drafts("*"))
        self.assertTrue(_should_reset_pr_drafts(""))
        self.assertFalse(_should_reset_pr_drafts("com.hazelcast.config.ConfigXmlGenerator"))

    def test_pr_draft_path_is_target_specific(self) -> None:
        self.assertEqual(
            pr_test_draft_path(Path("results/pr"), "repo_dir_sample_Target"),
            Path("results/pr/repo_dir_sample_Target.pr-tests.md"),
        )

    def test_pr_selection_is_target_specific_for_duplicate_repo_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pr_root = root / "pr-tests"
            cov_root = root / "covfilter"
            repo = "sample/repo"
            target_a = "sample_repo_sample_TargetA"
            target_b = "sample_repo_sample_TargetB"
            for target_id, cls, method in (
                (target_a, "TargetATest", "adoptedA"),
                (target_b, "TargetBTest", "adoptedB"),
            ):
                source = pr_root / target_id / "sample" / f"{cls}.java"
                source.parent.mkdir(parents=True)
                source.write_text(
                    f"""\
package sample;

class {cls} {{
    // Adopted Tests
    @org.junit.jupiter.api.Test
    void {method}() {{
    }}
}}
""",
                    encoding="utf-8",
                )
                write_csv(
                    cov_root / "pr-tests" / target_id / "test_deltas_selected.csv",
                    ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"],
                    [
                        {
                            "test_selector": f"sample.{cls}#{method}",
                            "added_lines": "1",
                            "added_methods": "0",
                            "added_branches": "0",
                            "added_instructions": "1",
                        }
                    ],
                )

            pipeline = SimpleNamespace(
                inv_rows=[
                    {"repo": repo, "fqcn": "sample.TargetA", "manual_files": ""},
                    {"repo": repo, "fqcn": "sample.TargetB", "manual_files": ""},
                ],
                pr_tests_root=pr_root,
                manual_dir=root / "manual",
                adopted_covfilter_out_root=cov_root,
                coverage_incremental_csv=root / "coverage.csv",
            )
            ctx = SimpleNamespace(
                repo=repo,
                fqcn="sample.TargetB",
                target_id=target_b,
                repo_root_for_deps=root / "repo",
            )

            selected = _target_selected_tests(pipeline, ctx, max_tests=2)

            self.assertEqual([test.method for test in selected], ["adoptedB"])

    def test_pr_body_helpers_keep_tests_and_description_concise(self) -> None:
        selected = [
            SelectedTest(
                method="testPackageConversion",
                component="Target",
                target_line_percentage=1.25,
                target_branch_percentage=0.5,
                target_lines_url="https://github.com/example/repo/blob/main/src/Target.java#L10-L20",
            )
        ]

        self.assertIn(
            "* Covers the relevant Target logic, including [covered lines](https://github.com/example/repo/blob/main/src/Target.java#L10-L20)",
            _coverage_impact(selected),
        )
        self.assertEqual(
            _coverage_summary(selected),
            "Coverage for Target increases by 1.25%.",
        )
        self.assertNotIn("instruction", _coverage_summary(selected))
        self.assertNotIn("method", _coverage_summary(selected))
        self.assertEqual(
            _summary_sentence(["Target"], selected, "Confirms package conversion."),
            "I added a regression test for Target around package conversion.",
        )
        self.assertEqual(
            _summary_sentence(["Target"], selected, "Confirms that package conversion is stable."),
            "I added a regression test for Target around the case where package conversion is stable.",
        )
        self.assertEqual(
            _summary_sentence(
                ["Target"],
                selected,
                "Adds coverage for cloning configured data sources, verifying that copied settings are preserved.",
                "the cloning behavior",
            ),
            "I added a regression test for Target around the cloning behavior.",
        )
        self.assertIn(
            "While looking through the Target implementation",
            _why_paragraph("Target", "Confirms package conversion.", selected_count=1),
        )
        self.assertNotIn(
            "\n\n",
            _why_paragraph("Target", "Confirms package conversion.", selected_count=1),
        )
        self.assertIn(
            "XML generation",
            _why_paragraph("Target", "Confirms that XML generation is skipped.", selected_count=1),
        )
        self.assertEqual(_title("Target", selected_count=1), "test: add regression test for Target")
        self.assertEqual(_title("Target", selected_count=2), "test: add regression tests for Target")

    def test_tailored_pr_description_requires_exact_selected_methods(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            descriptions = Path(temp) / "descriptions.csv"
            write_csv(
                descriptions,
                ["repo", "methods", "summary_topic", "description"],
                [
                    {
                        "repo": "sample/repo",
                        "methods": "testOne;testTwo",
                        "summary_topic": "the sample behavior",
                        "description": "Explains the exercised behavior.",
                    }
                ],
            )
            selected = [SelectedTest(method="testTwo"), SelectedTest(method="testOne")]

            self.assertEqual(
                _load_tailored_description(descriptions, repo="sample/repo", selected=selected),
                "Explains the exercised behavior.",
            )
            self.assertEqual(
                _load_tailored_summary_topic(descriptions, repo="sample/repo", selected=selected),
                "the sample behavior",
            )
            self.assertEqual(
                _load_tailored_description(
                    descriptions,
                    repo="sample/repo",
                    selected=[SelectedTest(method="testOne")],
                ),
                "",
            )

    def test_target_coverage_metrics_fall_back_to_nested_target_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            coverage = root / "coverage.csv"
            write_csv(
                coverage,
                [
                    "repo",
                    "fqcn",
                    "variant",
                    "is_target_class",
                    "line_total",
                    "added_line_percentage",
                    "added_branch_percentage",
                ],
                [
                    {
                        "repo": "sample/repo",
                        "fqcn": "sample.Target",
                        "variant": "pr-tests",
                        "is_target_class": "true",
                        "line_total": "100",
                        "added_line_percentage": "0",
                        "added_branch_percentage": "0",
                    }
                ],
            )
            covfilter_dir = root / "covfilter"
            write_csv(
                covfilter_dir / "line_deltas_selected.csv",
                ["class_name", "newly_covered_lines"],
                [
                    {
                        "class_name": "sample.Target$Nested",
                        "newly_covered_lines": "10-11;15",
                    }
                ],
            )

            self.assertEqual(
                _load_target_coverage_metrics(
                    coverage,
                    repo="sample/repo",
                    fqcn="sample.Target",
                    covfilter_dir=covfilter_dir,
                ),
                (3.0, 0.0),
            )

    def test_pr_selection_returns_at_most_two_positive_pr_tests_by_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SampleTest.java"
            source.write_text(
                SOURCE.replace(
                    "    private static void helper()",
                    "    @Test\n    void adoptedSecond() {}\n\n    @Test\n    void adoptedZero() {}\n\n    private static void helper()",
                ),
                encoding="utf-8",
            )
            write_csv(
                root / "covfilter" / "test_deltas_all.csv",
                ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"],
                [
                    {"test_selector": "x#adoptedZeroArg", "added_lines": "2", "added_methods": "0", "added_branches": "0", "added_instructions": "5"},
                    {"test_selector": "x#adoptedSecond", "added_lines": "4", "added_methods": "0", "added_branches": "0", "added_instructions": "3"},
                    {"test_selector": "x#adoptedZeroArgParameterized", "added_lines": "100", "added_methods": "1", "added_branches": "1", "added_instructions": "100"},
                    {"test_selector": "x#adoptedZero", "added_lines": "0", "added_methods": "0", "added_branches": "0", "added_instructions": "0"},
                ],
            )
            write_csv(
                root / "covfilter" / "test_deltas_kept.csv",
                ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"],
                [
                    {"test_selector": "x#adoptedZeroArg", "added_lines": "2", "added_methods": "0", "added_branches": "0", "added_instructions": "5"},
                ],
            )

            selected = _select_pr_tests(
                pr_source=source,
                manual_source=None,
                covfilter_dir=root / "covfilter",
                max_tests=2,
            )

            self.assertEqual([test.method for test in selected], ["adoptedSecond", "adoptedZeroArg"])

    def test_pr_selection_uses_all_positive_pr_deltas_when_selected_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SampleTest.java"
            source.write_text(SOURCE, encoding="utf-8")
            fields = ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"]
            write_csv(root / "covfilter" / "test_deltas_kept.csv", fields, [])
            write_csv(
                root / "covfilter" / "test_deltas_all.csv",
                fields,
                [
                    {
                        "test_selector": "x#adoptedZeroArg",
                        "added_lines": "2",
                        "added_methods": "0",
                        "added_branches": "0",
                        "added_instructions": "5",
                    }
                ],
            )

            selected = _select_pr_tests(
                pr_source=source,
                manual_source=None,
                covfilter_dir=root / "covfilter",
                max_tests=2,
            )

            self.assertEqual([test.method for test in selected], ["adoptedZeroArg"])


if __name__ == "__main__":
    unittest.main()
