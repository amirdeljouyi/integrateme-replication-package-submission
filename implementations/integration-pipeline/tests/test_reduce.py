import unittest

from pathlib import Path
import csv
import tempfile

from support import SOURCE, write_csv

from src.steps.reduce import _auto_delta_csv_names, _prepare_auto_top100_deltas
from src.steps.reduce import _materialize_reduced_pr_source, _materialize_reducible_pr_source, _prepare_pr_test_deltas


class ReduceTest(unittest.TestCase):
    def test_true_top100_uses_all_ranked_tests_but_smaller_reductions_use_kept_tests(self) -> None:
        self.assertEqual(_auto_delta_csv_names(100)[0], "test_deltas_all.csv")
        self.assertEqual(
            _auto_delta_csv_names(5),
            ["test_deltas_kept.csv", "tests_deltas_kept.csv"],
        )

    def test_true_top100_pool_excludes_stale_and_duplicate_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SampleTest.java"
            source.write_text(
                """\
import org.junit.Test;
class SampleTest {
    @Test
    public void first() {}
    @Test
    public void second() {}
}
""",
                encoding="utf-8",
            )
            deltas = root / "test_deltas_all.csv"
            write_csv(
                deltas,
                ["test_selector", "added_lines"],
                [
                    {"test_selector": "SampleTest#missing", "added_lines": "9"},
                    {"test_selector": "SampleTest#second", "added_lines": "6"},
                    {"test_selector": "SampleTest#first", "added_lines": "8"},
                    {"test_selector": "SampleTest#first", "added_lines": "7"},
                ],
            )
            output = _prepare_auto_top100_deltas(source, deltas, root / "out")
            with output.open(newline="", encoding="utf-8") as handle:
                selectors = [row["test_selector"] for row in csv.DictReader(handle)]
            self.assertEqual(selectors, ["SampleTest#first", "SampleTest#second"])

    def test_reduced_pr_source_preserves_helpers_and_rewrites_self_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SampleTest.java"
            source.write_text(SOURCE, encoding="utf-8")
            deltas = root / "test_deltas_selected.csv"
            write_csv(
                deltas,
                ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"],
                [
                    {
                        "test_selector": "sample.SampleTest#adoptedZeroArg",
                        "added_lines": "2",
                        "added_methods": "1",
                        "added_branches": "0",
                        "added_instructions": "4",
                    }
                ],
            )

            reduced = _materialize_reduced_pr_source(source, deltas, 2, root / "reduced")

            self.assertIsNotNone(reduced)
            text = reduced.read_text(encoding="utf-8")
            self.assertIn("class SampleTest_Top2", text)
            self.assertIn("SampleTest_Top2.helper()", text)
            self.assertIn("private static void helper()", text)
            self.assertIn("void adoptedZeroArg()", text)
            self.assertNotIn("manualParameterized", text)
            self.assertNotIn("manualZeroArg", text)
            self.assertNotIn("adoptedParameterized", text)
            self.assertNotIn("adoptedZeroArgParameterized", text)
            self.assertNotIn("adoptedNested", text)

    def test_reducible_source_adds_junit_annotation_only_to_unannotated_junit3_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "LegacyTest.java"
            source.write_text(
                """\
class LegacyTest {
    @Test
    @DisplayName("already annotated")
    public void testAnnotated() {
    }

    public void testLegacy() {
    }
}
""",
                encoding="utf-8",
            )

            staged = _materialize_reducible_pr_source(source, root / "staged", "legacy")
            text = staged.read_text(encoding="utf-8")

            self.assertEqual(text.count("@org.junit.Test"), 1)
            self.assertIn("@org.junit.Test\n    public void testLegacy()", text)
            self.assertNotIn("@org.junit.Test\n    public void testAnnotated()", text)

    def test_prepare_pr_deltas_keeps_only_positive_adopted_rows_and_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SampleTest.java"
            source.write_text(SOURCE, encoding="utf-8")
            existing = root / "test_deltas_all.csv"
            fields = ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"]
            write_csv(
                existing,
                fields,
                [
                    {
                        "test_selector": "sample.SampleTest_PRTests#adoptedZeroArg",
                        "added_lines": "2",
                        "added_methods": "1",
                        "added_branches": "0",
                        "added_instructions": "4",
                    },
                    {
                        "test_selector": "sample.SampleTest_PRTests#adoptedParameterized",
                        "added_lines": "0",
                        "added_methods": "0",
                        "added_branches": "0",
                        "added_instructions": "0",
                    },
                    {
                        "test_selector": "sample.SampleTest_PRTests#manualZeroArg",
                        "added_lines": "100",
                        "added_methods": "1",
                        "added_branches": "1",
                        "added_instructions": "100",
                    },
                ],
            )
            out_dir = root / "out"
            write_csv(
                out_dir / "line_deltas_kept.csv",
                ["test_selector", "class_name", "newly_covered_lines", "upgraded_to_full_lines"],
                [
                    {
                        "test_selector": "sample.SampleTest_PRTests#adoptedZeroArg",
                        "class_name": "sample.Target",
                        "newly_covered_lines": "10-11",
                        "upgraded_to_full_lines": "",
                    },
                    {
                        "test_selector": "sample.SampleTest_PRTests#manualZeroArg",
                        "class_name": "sample.Target",
                        "newly_covered_lines": "1-100",
                        "upgraded_to_full_lines": "",
                    },
                ],
            )

            selected = _prepare_pr_test_deltas(source, None, existing, out_dir)
            with selected.open(encoding="utf-8", newline="") as handle:
                selected_rows = list(csv.DictReader(handle))
            with (out_dir / "line_deltas_selected.csv").open(encoding="utf-8", newline="") as handle:
                line_rows = list(csv.DictReader(handle))

            self.assertEqual([row["test_selector"] for row in selected_rows], ["sample.SampleTest_PRTests#adoptedZeroArg"])
            self.assertEqual([row["test_selector"] for row in line_rows], ["sample.SampleTest#adoptedZeroArg"])


if __name__ == "__main__":
    unittest.main()
