import unittest

from pathlib import Path
import tempfile

from support import SOURCE

from src.pipeline.helpers import adopted_test_method_names, individually_runnable_test_method_names, mask_java_comments_and_strings, reduced_test_path, reduced_variant_test_path, test_method_names


class HelpersTest(unittest.TestCase):
    def test_java_test_method_names_allow_dollar_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "DollarTest.java"
            source.write_text(
                "class DollarTest {\n@org.junit.Test\npublic void testType$Nested() {}\n}\n",
                encoding="utf-8",
            )
            self.assertEqual(test_method_names(source), ["testType$Nested"])

    def test_reduced_variant_path_can_use_the_available_selected_top_n(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = root / "pr-tests" / "target" / "sample" / "SampleTest_Top2.java"
            selected.parent.mkdir(parents=True)
            selected.write_text("class SampleTest_Top2 {}", encoding="utf-8")

            self.assertEqual(
                reduced_variant_test_path(root, "pr-tests", "target", 5, allow_any_top_n=True),
                selected,
            )

    def test_method_discovery_handles_multiline_and_qualified_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "SampleTest.java"
            source.write_text(SOURCE, encoding="utf-8")

            self.assertEqual(
                test_method_names(source),
                [
                    "manualParameterized",
                    "manualZeroArg",
                    "adoptedZeroArg",
                    "adoptedParameterized",
                    "adoptedZeroArgParameterized",
                    "adoptedNested",
                ],
            )
            self.assertEqual(
                adopted_test_method_names(source, None),
                ["adoptedZeroArg", "adoptedParameterized", "adoptedZeroArgParameterized", "adoptedNested"],
            )
            self.assertEqual(
                individually_runnable_test_method_names(source),
                ["manualZeroArg", "adoptedZeroArg"],
            )

    def test_existing_marker_is_authoritative_even_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "SampleTest.java"
            source.write_text(
                "class SampleTest {\n@Test void existing() {}\n// Adopted Tests\n}\n",
                encoding="utf-8",
            )

            self.assertEqual(adopted_test_method_names(source, None), [])

    def test_testng_tests_are_excluded_from_junit_only_individual_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "SampleTest.java"
            source.write_text(
                """\
import org.testng.annotations.Test;

class SampleTest {
    // Adopted Tests
    @Test
    public void adoptedTestNgTest() {
    }
}
""",
                encoding="utf-8",
            )

            self.assertEqual(test_method_names(source), ["adoptedTestNgTest"])
            self.assertEqual(individually_runnable_test_method_names(source), [])

    def test_java_mask_preserves_layout_and_masks_delimiters_in_literals(self) -> None:
        source = 'call("value(with)paren"); // comment(with)paren\nnext();\n'
        masked = mask_java_comments_and_strings(source)

        self.assertEqual(len(masked), len(source))
        self.assertEqual(masked.count("\n"), source.count("\n"))
        self.assertEqual(masked.count("("), 2)
        self.assertEqual(masked.count(")"), 2)

    def test_reduced_test_path_falls_back_to_best_available_top_n(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = root / "Sample_ESTest.java"
            generated.write_text("package sample; class Sample_ESTest {}", encoding="utf-8")
            target = "sample_target"
            top3 = root / "reduced" / "auto" / target / "sample" / "Sample_Sanitized_ESTest_Top3.java"
            top5 = root / "reduced" / "auto" / target / "sample" / "Sample_Sanitized_ESTest_Top5.java"
            top3.parent.mkdir(parents=True)
            top3.write_text("", encoding="utf-8")
            top5.write_text("", encoding="utf-8")

            selected = reduced_test_path(
                root / "reduced",
                target,
                generated,
                100,
                preferred_variants=["auto"],
                allow_any_top_n=True,
            )

            self.assertEqual(selected, top5)


if __name__ == "__main__":
    unittest.main()
