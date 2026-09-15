import unittest

from pathlib import Path
import tempfile

from src.core.common import candidate_repo_class_dirs


class CommonTest(unittest.TestCase):
    def test_candidate_repo_class_dirs_recurses_only_in_target_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            module_output = repo / "module-a" / "nested" / "build" / "classes" / "java" / "test"
            sibling_output = repo / "module-b" / "nested" / "build" / "classes" / "java" / "test"
            root_output = repo / "target" / "classes"
            for path in (module_output, sibling_output, root_output):
                path.mkdir(parents=True)

            candidates = candidate_repo_class_dirs(repo, "module-a")

            self.assertIn(module_output, candidates)
            self.assertIn(root_output, candidates)
            self.assertNotIn(sibling_output, candidates)


if __name__ == "__main__":
    unittest.main()
