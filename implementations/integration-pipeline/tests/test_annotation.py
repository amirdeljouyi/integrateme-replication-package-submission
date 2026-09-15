import unittest

from pathlib import Path
import tempfile

from src.steps.annotation import LineEntry, MethodDelta, _build_annotation_comment


class AnnotationTest(unittest.TestCase):
    def test_zero_target_annotation_explains_positive_overall_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            comment = _build_annotation_comment(
                "",
                repo="sample/repo",
                repo_root=Path(temp),
                repo_ref="HEAD",
                target_class="sample.Target",
                method_delta=MethodDelta(
                    added_lines=3,
                    added_methods=1,
                    added_branches=2,
                    added_instructions=10,
                    target_added_lines=0,
                    line_entries=[LineEntry(class_name="sample.Related", newly_covered_lines="4-6")],
                ),
                denominator_line_total=20,
                source_cache={},
                source_lines_cache={},
            )

            text = "\n".join(comment)
            self.assertIn("Overall delta: +3 lines", text)
            self.assertIn("Target-class added line coverage: 0.00%", text)
            self.assertIn("added line coverage is in the related classes listed below", text)
            self.assertIn("sample.Related (lines 4-6)", text)


if __name__ == "__main__":
    unittest.main()
