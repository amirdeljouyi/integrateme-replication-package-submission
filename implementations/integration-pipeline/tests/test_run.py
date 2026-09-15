import unittest

from pathlib import Path
import tempfile
import zipfile

from src.steps.run import _build_runtime_cp, _classpath_has_class, _partition_runtime_cp_around_sut, _selected_adopted_run_variants


class RunTest(unittest.TestCase):
    def test_generated_runtime_classpath_keeps_cut_behind_framework_but_ahead_of_repo_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compiled = root / "compiled-tests"
            test_class = compiled / "sample" / "Target_ESTest.class"
            test_class.parent.mkdir(parents=True)
            test_class.write_bytes(b"test")
            sut = root / "instrumented-sut.jar"
            repo_classes = root / "repo" / "target" / "classes"
            evosuite = root / "deps" / "evosuite-runtime.jar"
            asm = root / "deps" / "asm-9.jar"
            ordinary = root / "deps" / "ordinary.jar"

            classpath, _selected, _expected, _checked = _build_runtime_cp(
                compiled_tests_dir=compiled,
                fallback_test_class_dirs=[],
                libs_glob_cp=str(root / "libs" / "*"),
                sut_jar=sut,
                extra_runtime_cp=":".join(map(str, (repo_classes, evosuite, asm, ordinary))),
                tool_jar=root / "tool.jar",
                test_selector="sample.Target_ESTest",
            )
            entries = classpath.split(":")
            self.assertLess(entries.index(str(evosuite.resolve())), entries.index(str(sut.resolve())))
            self.assertLess(entries.index(str(asm.resolve())), entries.index(str(sut.resolve())))
            self.assertLess(entries.index(str(sut.resolve())), entries.index(str(repo_classes.resolve())))
            self.assertLess(entries.index(str(sut.resolve())), entries.index(str(ordinary.resolve())))

    def test_non_generated_runtime_classpath_is_not_prioritized(self) -> None:
        priority, deferred = _partition_runtime_cp_around_sut(
            "repo/target/classes:deps/evosuite-runtime.jar",
            "sample.ManualTest",
        )
        self.assertEqual(priority, "")
        self.assertEqual(deferred, "repo/target/classes:deps/evosuite-runtime.jar")

    def test_auxiliary_coverage_variants_do_not_select_unrequested_adopted_runs(self) -> None:
        self.assertEqual(_selected_adopted_run_variants("improved"), {"improved"})
        self.assertEqual(_selected_adopted_run_variants("auto-100"), {"auto-100"})
        self.assertEqual(
            _selected_adopted_run_variants("adopted,agentic"),
            {"adopted", "agentic"},
        )

    def test_jboss_logmanager_lookup_skips_unrelated_maven_jars(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            unrelated = root / ".m2" / "repository" / "other" / "large.jar"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_bytes(b"not a zip and must not be opened")
            assembled = root / "target-with-dependencies.jar"
            with zipfile.ZipFile(assembled, "w") as jar:
                jar.writestr("org/jboss/logmanager/LogManager.class", b"")

            self.assertTrue(
                _classpath_has_class(
                    f"{unrelated}:{assembled}",
                    "org/jboss/logmanager/LogManager.class",
                )
            )


if __name__ == "__main__":
    unittest.main()
