import unittest

from pathlib import Path
from unittest.mock import Mock, patch
import tempfile
import zipfile

from src.core.java import JavacCompiler, _gradle_runtime_cp, _jar_contains_class_entry, _jar_score_for_class_target, _nearest_build_module_dir


class JavaTest(unittest.TestCase):
    def test_javac_enables_processors_explicitly_on_current_jdks(self) -> None:
        compiler = JavacCompiler(libs_glob_cp="libs/*", sut_jar=Path("sut.jar"))

        command = compiler._javac_cmd([Path("GeneratedTest.java")], Path("classes"))

        self.assertIn("-proc:full", command)
        self.assertIn("-processorpath", command)

    def test_gradle_classpath_task_targets_the_requested_subproject(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "gradlew").touch()
            (root / "test-suite").mkdir()
            completed = Mock(returncode=0, stdout="/deps/test.jar")

            with patch("src.core.java.subprocess.run", return_value=completed) as run:
                classpath = _gradle_runtime_cp(root, "test-suite")

            self.assertEqual("/deps/test.jar", classpath)
            self.assertIn(":test-suite:printAgtClasspath", run.call_args.args[0])
            self.assertIn("--no-configuration-cache", run.call_args.args[0])

    def test_gradle_classpath_falls_back_to_prefixed_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "gradlew").touch()
            (root / "inject").mkdir()
            missing = Mock(returncode=1, stdout="")
            resolved = Mock(returncode=0, stdout="/deps/netty.jar")

            with patch("src.core.java.subprocess.run", side_effect=(missing, resolved)) as run:
                classpath = _gradle_runtime_cp(root, "inject")

            self.assertEqual("/deps/netty.jar", classpath)
            self.assertEqual(2, run.call_count)
            self.assertIn(":micronaut-inject:printAgtClasspath", run.call_args.args[0])

    def test_jar_class_lookup_checks_exact_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            jar_path = Path(temp) / "classes.jar"
            with zipfile.ZipFile(jar_path, "w") as jar:
                jar.writestr("sample/Present.class", b"")
            self.assertTrue(_jar_contains_class_entry(jar_path, "sample/Present.class"))
            self.assertFalse(_jar_contains_class_entry(jar_path, "sample/Missing.class"))

    def test_dependency_jar_scoring_requires_a_distinctive_package_match(self) -> None:
        spark = Path("/.m2/repository/org/apache/spark/spark-core/4.1/spark-core.jar")
        iceberg = Path("/.m2/repository/org/apache/iceberg/iceberg-core/1.0/iceberg-core.jar")
        self.assertEqual(_jar_score_for_class_target(spark, "org.apache.iceberg.Table"), 0)
        self.assertGreater(_jar_score_for_class_target(iceberg, "org.apache.iceberg.Table"), 0)

    def test_nearest_build_module_detects_gradle_subproject_without_build_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "build.gradle").write_text("", encoding="utf-8")
            source = root / "api" / "src" / "test" / "java" / "sample" / "Support.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Support {}", encoding="utf-8")
            (root / "api" / "build" / "classes" / "java" / "test").mkdir(parents=True)

            self.assertEqual(_nearest_build_module_dir(source, root), (root / "api").resolve())

    def test_smart_compile_retries_requested_sources_after_repository_outputs_are_discovered(self) -> None:
        class FakeCompiler(JavacCompiler):
            def __init__(self) -> None:
                super().__init__(libs_glob_cp="libs/*", sut_jar=Path("sut.jar"))
                self.support_seen = False

            def compile_set(self, *, java_files, build_dir, log_file):
                log_file.write_text("cannot find symbol\n", encoding="utf-8")
                if self.support_seen and len(java_files) == 1:
                    return True, ""
                if len(java_files) > 1:
                    self.support_seen = True
                return False, "cannot find symbol"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requested = root / "RequestedTest.java"
            support = root / "Support.java"
            requested.write_text("class RequestedTest {}", encoding="utf-8")
            support.write_text("class Support {}", encoding="utf-8")
            classpath_calls = 0

            def runtime_classpath(*args, **kwargs):
                nonlocal classpath_calls
                classpath_calls += 1
                return "" if classpath_calls == 1 else str(root / "compiled-repository-outputs")

            with (
                patch("src.core.java.resolve_repo_runtime_classpath", side_effect=runtime_classpath),
                patch("src.core.java.extract_missing_symbols_from_javac_log", return_value={"Support"}),
                patch("src.core.java.find_declaring_sources", return_value=[support]),
            ):
                ok, _tail, compiled_sources = FakeCompiler().compile_smart(
                    java_files=[requested],
                    build_dir=root / "build",
                    log_file=root / "compile.log",
                    repo_root_for_deps=root,
                    max_rounds=1,
                )

            self.assertTrue(ok)
            self.assertEqual(compiled_sources, [requested])


if __name__ == "__main__":
    unittest.main()
