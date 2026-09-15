from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.core.rq4_native_coverage import native_test_command, resolve_production_source


class Rq4NativeCoverageTest(unittest.TestCase):
    def test_maven_selects_all_existing_and_submitted_test_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "mvnw").write_text("", encoding="utf-8")
            command = native_test_command(
                target_id="T20",
                repo=repo,
                build_tool="maven",
                test_path="street/src/test/java/example/ExistingTest.java",
                test_fqcns=["example.ExistingTest", "example.SubmittedTest"],
            )
        self.assertIn("-pl", command)
        self.assertIn("street", command)
        self.assertIn("-Dtest=example.ExistingTest,example.SubmittedTest", command)

    def test_gradle_repeats_test_selector_for_each_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            command = native_test_command(
                target_id="T03",
                repo=repo,
                build_tool="gradle",
                test_path="core/src/test/java/example/ExistingTest.java",
                test_fqcns=["example.ExistingTest", "example.SubmittedTest"],
            )
        self.assertIn(":iceberg-core:test", command)
        self.assertEqual(command.count("--tests"), 2)

    def test_production_source_resolves_current_package_from_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            source = repo / "street/src/main/java/new/package/VertexLinker.java"
            source.parent.mkdir(parents=True)
            source.write_text("package new.package; public class VertexLinker {}\n", encoding="utf-8")
            self.assertEqual(resolve_production_source(repo, "VertexLinker"), source)


if __name__ == "__main__":
    unittest.main()
