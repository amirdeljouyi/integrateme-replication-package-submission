import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import csv
import tempfile

from support import SOURCE, write_csv

from src.steps.covfilter import _compile_covfilter_sources_with_manual_fallback, _covfilter_runtime_jar_artifact_key, _covfilter_target_test_resource_dirs, _extract_missing_runtime_classes, _failed_covfilter_candidate_methods, _filter_covfilter_test_outputs, _matching_covfilter_execution_classes_dir, _matching_opentelemetry_incubator_jar, _materialize_adopted_covfilter_source, _materialize_pr_covfilter_sources, _prepare_covfilter_libs_dir, _prepare_covfilter_sut_classes_dir, _prepare_covfilter_test_classes_dir, _qualify_evosuite_verify_exception_targets, _remove_pr_test_methods, _test_framework_runtime_cp, _uses_regular_mockito_evosuite_runtime, _write_directory_as_covfilter_jar


class CovfilterTest(unittest.TestCase):
    def test_covfilter_qualifies_imported_evosuite_exception_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "TargetTest.java"
            source.write_text(
                "import example.Target;\n"
                "verifyException(\"Target\", error);\n"
                "verifyException(\"Target$Inner\", error);\n"
                "verifyException(\"Already.Qualified\", error);\n"
                "verifyException(\"Unknown\", error);\n",
                encoding="utf-8",
            )

            _qualify_evosuite_verify_exception_targets(source)
            updated = source.read_text(encoding="utf-8")

        self.assertIn('verifyException("example.Target", error)', updated)
        self.assertIn('verifyException("example.Target$Inner", error)', updated)
        self.assertIn('verifyException("Already.Qualified", error)', updated)
        self.assertIn('verifyException("Unknown", error)', updated)

    def test_covfilter_executes_complete_matching_project_classes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            analysis = root / "analysis"
            project = root / "project"
            for directory in (analysis, project):
                target = directory / "example" / "Target.class"
                inner = directory / "example" / "Target$Inner.class"
                target.parent.mkdir(parents=True)
                target.write_bytes(b"outer")
                inner.write_bytes(b"inner")
            (project / "example" / "Sibling.class").write_bytes(b"sibling")

            selected = _matching_covfilter_execution_classes_dir(
                analysis, [str(project)], "example.Target"
            )

        self.assertEqual(project, selected)

    def test_covfilter_rejects_project_classes_from_another_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            analysis = root / "analysis"
            project = root / "project"
            for directory, content in ((analysis, b"frozen"), (project, b"different")):
                target = directory / "example" / "Target.class"
                target.parent.mkdir(parents=True)
                target.write_bytes(content)

            selected = _matching_covfilter_execution_classes_dir(
                analysis, [str(project)], "example.Target"
            )

        self.assertEqual(analysis, selected)

    def test_covfilter_adds_declared_hamcrest_runtime_for_hamcrest_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch(
            "src.steps.covfilter._m2_artifact_jar",
            return_value="/deps/hamcrest-all-1.3.jar",
        ) as artifact_jar:
            source = Path(temp) / "TargetTest.java"
            source.write_text("import static org.hamcrest.Matchers.equalTo;\n", encoding="utf-8")

            classpath = _test_framework_runtime_cp(source)

        self.assertEqual("/deps/hamcrest-all-1.3.jar", classpath)
        artifact_jar.assert_called_once_with("org/hamcrest", "hamcrest-all", "1.3")

    def test_covfilter_stages_adopted_source_before_compile_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "canonical" / "Target_ESTest_Adopted.java"
            source.parent.mkdir()
            source.write_text("class Target_ESTest_Adopted {}\n", encoding="utf-8")

            staged = _materialize_adopted_covfilter_source(source, root / "staged")
            staged.write_text("// compile recovery edit\n", encoding="utf-8")

            self.assertEqual("class Target_ESTest_Adopted {}\n", source.read_text(encoding="utf-8"))
            self.assertEqual("// compile recovery edit\n", staged.read_text(encoding="utf-8"))

    def test_covfilter_stages_and_compiles_referenced_evosuite_scaffolding(self) -> None:
        from src.steps.covfilter import _adopted_covfilter_source_sets

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            canonical = root / "canonical"
            canonical.mkdir()
            source = canonical / "Target_ESTest_Adopted.java"
            source.write_text(
                "class Target_ESTest_Adopted extends Target_ESTest_scaffolding {}\n",
                encoding="utf-8",
            )
            scaffold = canonical / "Target_ESTest_scaffolding.java"
            scaffold.write_text("class Target_ESTest_scaffolding {}\n", encoding="utf-8")

            staged = _materialize_adopted_covfilter_source(source, root / "staged")
            staged_scaffold = staged.with_name(scaffold.name)
            primary, fallback = _adopted_covfilter_source_sets(
                SimpleNamespace(manual_sources=[Path("/frozen/ManualTest.java")]),
                staged,
            )

            self.assertTrue(staged_scaffold.is_file())
            self.assertEqual(primary, [Path("/frozen/ManualTest.java"), staged, staged_scaffold])
            self.assertEqual(fallback, [staged, staged_scaffold])

    def test_covfilter_aligns_opentelemetry_incubator_with_sdk(self) -> None:
        classpath = "/deps/opentelemetry-sdk-1.57.0.jar:/deps/opentelemetry-sdk-1.62.0.jar"

        with patch(
            "src.steps.covfilter._m2_artifact_jar",
            return_value="/deps/opentelemetry-api-incubator-1.62.0-alpha.jar",
        ) as artifact_jar:
            selected = _matching_opentelemetry_incubator_jar(classpath)

        self.assertEqual("/deps/opentelemetry-api-incubator-1.62.0-alpha.jar", selected)
        artifact_jar.assert_called_once_with(
            "io/opentelemetry",
            "opentelemetry-api-incubator",
            "1.62.0-alpha",
        )

    def test_covfilter_detects_fully_qualified_evosuite_assumption_answer(self) -> None:
        source = """
            org.mockito.stubbing.Answer<?> answer =
                new org.evosuite.runtime.ViolatedAssumptionAnswer();
            org.mockito.Mockito.mock(java.util.List.class, answer);
        """

        self.assertTrue(_uses_regular_mockito_evosuite_runtime(source))

    def test_covfilter_does_not_replace_explicit_shaded_evosuite_runtime(self) -> None:
        source = """
            org.evosuite.shaded.org.mockito.stubbing.Answer<?> answer =
                new org.evosuite.runtime.ViolatedAssumptionAnswer();
            org.mockito.Mockito.mock(java.util.List.class, answer);
        """

        self.assertFalse(_uses_regular_mockito_evosuite_runtime(source))

    def test_covfilter_compiles_generated_pair_without_standalone_manual_source(self) -> None:
        manual = Path("ManualTest.java")
        generated = Path("Target_ESTest.java")
        scaffolding = Path("Target_ESTest_scaffolding.java")
        attempts: list[list[Path]] = []

        def compile_attempt(**kwargs):
            sources = list(kwargs["java_files"])
            attempts.append(sources)
            if sources == [generated, scaffolding]:
                return True, "", sources
            return False, "compile failed", sources

        with tempfile.TemporaryDirectory() as temp, patch(
            "src.steps.covfilter._compile_with_generated_pruning",
            side_effect=compile_attempt,
        ):
            ok, _tail, compiled, _used_repo_manual = _compile_covfilter_sources_with_manual_fallback(
                source_files=[manual, generated, scaffolding],
                build_dir=Path(temp) / "classes",
                libs_glob_cp="libs/*",
                sut_jar=Path(temp) / "sut.jar",
                log_file=Path(temp) / "compile.log",
                repo_root_for_deps=None,
                module_rel="",
                build_tool="",
                max_rounds=1,
                allow_commenting=False,
            )

        self.assertTrue(ok)
        self.assertEqual([generated, scaffolding], compiled)
        self.assertEqual(
            [
                [manual, generated, scaffolding],
                [manual],
                [generated, scaffolding],
            ],
            attempts,
        )

    def test_covfilter_does_not_treat_initializer_failure_as_missing_class(self) -> None:
        output = (
            "java.lang.NoClassDefFoundError: Could not initialize class example.Widget$State\n"
            "Caused by: java.lang.NoClassDefFoundError: example/dependency/MissingType\n"
        )

        self.assertEqual(["example.dependency.MissingType"], _extract_missing_runtime_classes(output))

    def test_covfilter_analysis_prefers_runtime_main_output_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sut = root / "sut"
            runtime = root / "module" / "target" / "classes"
            (sut / "sample").mkdir(parents=True)
            (runtime / "sample").mkdir(parents=True)
            (sut / "sample" / "Target.class").write_bytes(b"old")
            (runtime / "sample" / "Target.class").write_bytes(b"runtime")

            selected = _prepare_covfilter_sut_classes_dir(
                sut_classes_input=sut,
                extra_runtime_cp=str(runtime),
                merged_dir=root / "merged",
                prefer_sut_input_only=False,
            )

            self.assertEqual((selected / "sample" / "Target.class").read_bytes(), b"runtime")

    def test_adopted_covfilter_compiles_frozen_manual_source_before_repo_fallback(self) -> None:
        from src.steps.covfilter import _adopted_covfilter_source_sets

        manual = Path("/frozen/ManualTest.java")
        adopted = Path("/llm-out/AdoptedTest.java")
        ctx = SimpleNamespace(manual_sources=[manual])

        primary, fallback = _adopted_covfilter_source_sets(ctx, adopted)

        self.assertEqual(primary, [manual, adopted])
        self.assertEqual(fallback, [adopted])

    def test_method_removal_removes_multiline_annotations_and_body(self) -> None:
        reduced = _remove_pr_test_methods(SOURCE, {"manualParameterized"})

        self.assertNotIn("@CsvSource", reduced)
        self.assertNotIn("void manualParameterized(", reduced)
        self.assertIn("void manualZeroArg()", reduced)
        self.assertIn("private static void helper()", reduced)

    def test_remove_pr_test_methods_supports_dollar_sign_identifiers(self) -> None:
        source = """class GeneratedTest {
    @org.junit.Test
    public void testGenerated$Variant() {
        fail();
    }
}
"""
        reduced = _remove_pr_test_methods(source, {"testGenerated$Variant"})
        self.assertNotIn("testGenerated$Variant", reduced)

    def test_pr_covfilter_candidate_contains_only_supported_adopted_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SampleTest.java"
            source.write_text(SOURCE, encoding="utf-8")
            ctx = SimpleNamespace(manual_sources=[], manual_test_fqcn=None)

            staged, candidate_fqcn, allowed, original_fqcn = _materialize_pr_covfilter_sources(
                ctx=ctx,
                pr_source=source,
                stage_root=root / "staged",
            )

            baseline = staged[0].read_text(encoding="utf-8")
            candidate = staged[1].read_text(encoding="utf-8")
            self.assertEqual(candidate_fqcn, "sample.SampleTest_PRTests")
            self.assertEqual(original_fqcn, "sample.SampleTest")
            self.assertEqual(allowed, {"adoptedZeroArg"})
            self.assertIn("manualParameterized", baseline)
            self.assertNotIn("adoptedZeroArg", baseline)
            self.assertNotIn("manualParameterized", candidate)
            self.assertNotIn("adoptedParameterized", candidate)
            self.assertNotIn("adoptedZeroArgParameterized", candidate)
            self.assertNotIn("adoptedNested", candidate)
            self.assertIn("void adoptedZeroArg()", candidate)
            self.assertIn("SampleTest_PRTests.helper()", candidate)
            self.assertNotIn("sample.SampleTest.Helper", candidate)
            self.assertIn("private static void helper()", candidate)

    def test_pr_covfilter_rewrites_test_class_names_inside_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "SampleTest.java"
            source.write_text(
                """\
package sample;
class SampleTest {
    static final String HELPER = "sample.SampleTestingUtils";

    // Adopted Tests
    @Test void adopted() {
        assertName("sample.SampleTest.Helper");
    }
}
""",
                encoding="utf-8",
            )
            ctx = SimpleNamespace(manual_sources=[], manual_test_fqcn=None)

            staged, _, _, _ = _materialize_pr_covfilter_sources(
                ctx=ctx,
                pr_source=source,
                stage_root=root / "staged",
            )

            candidate = staged[1].read_text(encoding="utf-8")
            self.assertIn('"sample.SampleTest_PRTests.Helper"', candidate)
            self.assertIn('"sample.SampleTestingUtils"', candidate)

    def test_covfilter_test_classes_keep_test_resources_as_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compiled = root / "compiled"
            compiled.mkdir()
            (compiled / "Sample.class").write_bytes(b"not-a-real-class")
            resources = root / "module" / "build" / "resources" / "test"
            resource = resources / "fixtures" / "sample.json"
            resource.parent.mkdir(parents=True)
            resource.write_text("{}", encoding="utf-8")

            merged = _prepare_covfilter_test_classes_dir(
                compiled_test_classes_dir=compiled,
                extra_runtime_cp=str(resources),
                merged_dir=root / "merged",
            )

            self.assertEqual((merged / "fixtures" / "sample.json").read_text(encoding="utf-8"), "{}")

    def test_covfilter_test_classes_only_copy_explicit_target_module_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compiled = root / "compiled"
            compiled.mkdir()
            (compiled / "Sample.class").write_bytes(b"not-a-real-class")
            target_resources = root / "target-module" / "build" / "resources" / "test"
            target_resource = target_resources / "fixtures" / "target.json"
            target_resource.parent.mkdir(parents=True)
            target_resource.write_text("target", encoding="utf-8")
            unrelated_resources = root / "other-module" / "build" / "resources" / "test"
            unrelated_resource = unrelated_resources / "fixtures" / "unrelated.json"
            unrelated_resource.parent.mkdir(parents=True)
            unrelated_resource.write_text("unrelated", encoding="utf-8")

            merged = _prepare_covfilter_test_classes_dir(
                compiled_test_classes_dir=compiled,
                extra_runtime_cp=f"{target_resources}:{unrelated_resources}",
                merged_dir=root / "merged",
                test_resource_dirs=[target_resources],
            )

            self.assertEqual((merged / "fixtures" / "target.json").read_text(encoding="utf-8"), "target")
            self.assertFalse((merged / "fixtures" / "unrelated.json").exists())

    def test_covfilter_target_test_resource_dirs_only_uses_target_module_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            module = Path(temp) / "module"
            maven_resources = module / "target" / "test-classes"
            gradle_resources = module / "build" / "resources" / "test"
            maven_resources.mkdir(parents=True)
            gradle_resources.mkdir(parents=True)

            self.assertEqual(
                _covfilter_target_test_resource_dirs(module),
                [maven_resources, gradle_resources],
            )

    def test_covfilter_libs_do_not_repackage_test_output_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base_libs = root / "libs"
            base_libs.mkdir()
            test_output = root / "module" / "build" / "resources" / "test"
            test_output.mkdir(parents=True)
            (test_output / "fixture.json").write_text("{}", encoding="utf-8")

            merged = _prepare_covfilter_libs_dir(
                base_libs_dir=base_libs,
                extra_runtime_cp=str(test_output),
                merged_dir=root / "merged-libs",
            )

            self.assertEqual(merged, base_libs)
            self.assertEqual(list(base_libs.glob("*.jar")), [])

    def test_covfilter_directory_jar_writer_rejects_test_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            test_output = root / "module" / "target" / "test-classes"
            test_output.mkdir(parents=True)
            (test_output / "fixture.json").write_text("{}", encoding="utf-8")
            target = root / "test-output.jar"

            _write_directory_as_covfilter_jar(source_dir=test_output, target=target)

            self.assertFalse(target.exists())

    def test_covfilter_failed_candidate_is_retained_with_zero_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_csv(
                root / "test_deltas_all.csv",
                ["test_selector", "added_lines", "added_methods", "added_branches", "added_instructions"],
                [
                    {
                        "test_selector": "sample.SampleTest_PRTests#passing",
                        "added_lines": "2",
                        "added_methods": "1",
                        "added_branches": "0",
                        "added_instructions": "5",
                    },
                    {
                        "test_selector": "sample.SampleTest_PRTests#failing",
                        "added_lines": "20",
                        "added_methods": "10",
                        "added_branches": "2",
                        "added_instructions": "50",
                    },
                ],
            )
            write_csv(
                root / "test_deltas_kept.csv",
                ["test_selector", "added_lines"],
                [
                    {"test_selector": "sample.SampleTest_PRTests#passing", "added_lines": "2"},
                    {"test_selector": "sample.SampleTest_PRTests#failing", "added_lines": "20"},
                ],
            )

            _filter_covfilter_test_outputs(
                root,
                allowed_test_methods={"passing", "failing"},
                output_test_fqcn="sample.SampleTest",
                failed_test_methods={"failing"},
            )

            with (root / "test_deltas_all.csv").open(newline="") as handle:
                all_rows = list(csv.DictReader(handle))
            with (root / "test_deltas_kept.csv").open(newline="") as handle:
                kept_rows = list(csv.DictReader(handle))
            failing = next(row for row in all_rows if row["test_selector"].endswith("#failing"))
            self.assertEqual(
                [failing[field] for field in ("added_lines", "added_methods", "added_branches", "added_instructions")],
                ["0", "0", "0", "0"],
            )
            self.assertEqual([row["test_selector"] for row in kept_rows], ["sample.SampleTest#passing"])

    def test_failed_covfilter_candidate_method_parser_targets_candidate_class(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "covfilter.log"
            log.write_text(
                "[JUnit5TestRunner] FAILURE in sample.ManualTest#same -> failed\n"
                "[JUnit5TestRunner] FAILURE in sample.CandidateTest#failing -> failed\n",
                encoding="utf-8",
            )

            self.assertEqual(_failed_covfilter_candidate_methods(log, "sample.CandidateTest"), {"failing"})

    def test_failed_covfilter_candidate_parser_includes_discovery_and_fork_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "covfilter.log"
            log.write_text(
                "java.lang.RuntimeException: Test failed: sample.CandidateTest#discoveryFailure\n"
                "[DROP] sample.CandidateTest#forkFailure  fork-exit=1 +lines=0\n",
                encoding="utf-8",
            )

            self.assertEqual(
                _failed_covfilter_candidate_methods(log, "sample.CandidateTest"),
                {"discoveryFailure", "forkFailure"},
            )

    def test_covfilter_keeps_rxjava_two_and_three_as_distinct_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rxjava2 = root / "rxjava-2.2.21.jar"
            rxjava3 = root / "rxjava-3.1.12.jar"
            rxjava2.touch()
            rxjava3.touch()

            self.assertEqual(_covfilter_runtime_jar_artifact_key(str(rxjava2)), "rxjava-2")
            self.assertEqual(_covfilter_runtime_jar_artifact_key(str(rxjava3)), "rxjava-3")


if __name__ == "__main__":
    unittest.main()
