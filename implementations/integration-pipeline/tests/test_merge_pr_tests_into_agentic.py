from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.oneoff.merge_pr_tests_into_agentic import _all_members, _members, _merge, _method_name, _required_imports, _semantic_replacement_matches


class MergePrTestsIntoAgenticTest(unittest.TestCase):
    def test_same_name_is_replaced_and_duplicates_are_removed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reduced = root / "SampleTest_Top2.java"
            agentic = root / "SampleAgentic.java"
            reduced.write_text(
                """package sample;

import org.junit.jupiter.api.Test;

class SampleTest_Top2 {
    // Adopted Tests
    @Test
    void sameName() {
        org.junit.jupiter.api.Assertions.assertEquals("PR", "PR");
    }
}
""",
                encoding="utf-8",
            )
            agentic.write_text(
                """package sample;

import org.junit.jupiter.api.Test;

class SampleAgentic {
    @Test
    void sameName() {
        org.junit.jupiter.api.Assertions.fail("old first copy");
    }

    @Test
    void sameName() {
        org.junit.jupiter.api.Assertions.fail("old duplicate");
    }
}
""",
                encoding="utf-8",
            )

            changed, names = _merge(reduced, agentic)
            merged = agentic.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertEqual(["sameName"], names)
            self.assertEqual(1, [_method_name(member) for member in _members(merged)].count("sameName"))
            self.assertIn('assertEquals("PR", "PR")', merged)
            self.assertNotIn("old first copy", merged)
            self.assertNotIn("old duplicate", merged)
            self.assertIn("    @Test\n    void sameName()", merged)

            changed_again, _ = _merge(reduced, agentic)
            self.assertFalse(changed_again)

    def test_semantically_similar_pr_test_replaces_differently_named_agentic_test(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reduced = root / "SampleTest_Top1.java"
            agentic = root / "SampleAgentic.java"
            reduced.write_text(
                """package sample;
import org.junit.Test;
class SampleTest_Top1 {
    // Adopted Tests
    @Test
    public void cloneCopiesConfiguration() {
        Widget clone = widget.cloneWidget();
        assertEquals(widget.getUrl(), clone.getUrl());
    }
}
""",
                encoding="utf-8",
            )
            agentic.write_text(
                """package sample;
import org.junit.Test;
class SampleAgentic {
    @Test
    public void testCloneWidgetPreservesUrl() {
        Widget cloned = widget.cloneWidget();
        assertEquals(widget.getUrl(), cloned.getUrl());
    }
    @Test
    public void testUnrelatedCounter() { assertEquals(0, widget.count()); }
}
""",
                encoding="utf-8",
            )

            changed, names = _merge(reduced, agentic)
            merged = agentic.read_text(encoding="utf-8")
            merged_names = [_method_name(member) for member in _members(merged)]

            self.assertTrue(changed)
            self.assertEqual(["cloneCopiesConfiguration"], names)
            self.assertIn("cloneCopiesConfiguration", merged_names)
            self.assertNotIn("testCloneWidgetPreservesUrl", merged_names)
            self.assertIn("testUnrelatedCounter", merged_names)
            self.assertEqual(2, len(merged_names))

    def test_semantic_matcher_does_not_replace_unrelated_test(self) -> None:
        selected = _members(
            """package sample; class Sample { @org.junit.Test public void clonesWidget() { widget.cloneWidget(); } }"""
        )[0]
        agentic = """package sample; class SampleAgentic {
            @org.junit.Test public void parsesConfiguration() { parser.parse(config); }
        }"""

        self.assertEqual({}, _semantic_replacement_matches({"clonesWidget": selected}, agentic))

    def test_semantic_matcher_does_not_fall_back_when_best_match_is_already_claimed(self) -> None:
        selected_members = {
            _method_name(member): member
            for member in _members(
                """package sample; class Sample {
                    @org.junit.Test public void copyPreservesName() { item.copy().getName(); }
                    @org.junit.Test public void copyPreservesNameAndValue() { item.copy().getName(); item.getValue(); }
                }"""
            )
        }
        agentic = """package sample; class SampleAgentic {
            @org.junit.Test public void copyReturnsNamedItem() { item.copy().getName(); item.getValue(); }
            @org.junit.Test public void unrelatedValueTest() { item.getValue(); }
        }"""

        matches = _semantic_replacement_matches(selected_members, agentic)

        self.assertEqual(1, len(matches))
        self.assertEqual("copyReturnsNamedItem", next(iter(matches.values()))[0])

    def test_raw_mode_only_adds_support_declared_after_adopted_marker(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "SampleTest.java"
            agentic = root / "SampleAgentic.java"
            source.write_text(
                """package sample;

import org.junit.Test;

class SampleTest {
    int unrelatedField;

    void unrelatedManualHelper() {}

    private void requiredPreMarkerHelper() {}

    // Adopted Tests
    @Test
    void addedTest() {
        requiredPreMarkerHelper();
        addedHelper();
    }

    private void addedHelper() {}
}
""",
                encoding="utf-8",
            )
            agentic.write_text(
                """package sample;

import org.junit.Test;

class SampleAgentic {
}
""",
                encoding="utf-8",
            )

            changed, names = _merge(source, agentic, include_pre_marker_support=False)
            merged = agentic.read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertEqual(["addedTest"], names)
            self.assertIn("void addedTest()", merged)
            self.assertIn("void addedHelper()", merged)
            self.assertIn("void requiredPreMarkerHelper()", merged)
            self.assertNotIn("unrelatedField", merged)
            self.assertNotIn("unrelatedManualHelper", merged)

    def test_package_rewrite_and_source_replacement(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "LegacyTest.java"
            agentic = root / "CurrentAgentic.java"
            source.write_text(
                """package legacy.sample;

import org.junit.jupiter.api.Test;

class LegacyTest {
    // Adopted Tests
    @Test
    void readsConfiguration() {
        Configuration value = ConfigurationFactory.getInstance();
    }
}
""",
                encoding="utf-8",
            )
            agentic.write_text(
                """package current.sample;

import org.junit.jupiter.api.Test;

class CurrentAgentic {
}
""",
                encoding="utf-8",
            )

            _merge(
                source,
                agentic,
                include_pre_marker_support=False,
                allow_package_rewrite=True,
                source_replacements=(("ConfigurationFactory.getInstance()", "new FileConfiguration()"),),
            )
            merged = agentic.read_text(encoding="utf-8")

            self.assertIn("Configuration value = new FileConfiguration();", merged)
            self.assertEqual(1, [_method_name(member) for member in _members(merged)].count("readsConfiguration"))

    def test_required_imports_only_keeps_referenced_simple_names(self) -> None:
        members = [
            _members(
                """package sample;
class Sample {
    void test() { MethodSpec method = null; assertThat(method, nullValue()); }
}
"""
            )[0]
        ]
        imports = [
            "import com.squareup.javapoet.MethodSpec;",
            "import com.squareup.javapoet.TypeSpec;",
            "import static org.hamcrest.MatcherAssert.assertThat;",
            "import static org.hamcrest.Matchers.nullValue;",
        ]

        self.assertEqual(
            [imports[0], imports[2], imports[3]],
            _required_imports(imports, members),
        )

    def test_member_key_ignores_braces_inside_annotation_arguments(self) -> None:
        members = _members(
            """package sample;
class Sample {
    @org.testng.annotations.Test(groups = { "unit" })
    public void adoptedTest() {}
}
"""
        )

        self.assertEqual(["adoptedTest"], [_method_name(member) for member in members])

    def test_raw_mode_extracts_adopted_tests_from_nested_class(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "NestedTest.java"
            agentic = root / "NestedAgentic.java"
            source.write_text(
                """package sample;
import org.junit.jupiter.api.Test;
class NestedTest {
    class Group {
        // Adopted Tests
        @Test
        void nestedAdoptedTest() { nestedHelper(); }
        private void nestedHelper() {}
    }
}
""",
                encoding="utf-8",
            )
            agentic.write_text(
                """package sample;
import org.junit.jupiter.api.Test;
class NestedAgentic {}
""",
                encoding="utf-8",
            )

            _merge(source, agentic, include_pre_marker_support=False)
            merged = agentic.read_text(encoding="utf-8")
            names = [_method_name(member) for member in _all_members(merged)]

            self.assertEqual(1, names.count("nestedAdoptedTest"))
            self.assertEqual(1, names.count("nestedHelper"))


if __name__ == "__main__":
    unittest.main()
