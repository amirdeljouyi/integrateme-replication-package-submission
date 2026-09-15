from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.steps.agent import (
    RepoGuidance,
    _build_prompt,
    _collect_repo_guidance,
    _hash_skill_bundle,
    _provision_runtime_skill,
    run_codex_integration,
)


class AgentRuntimeSkillTest(unittest.TestCase):
    def _write_skill(self, root: Path, body: str = "instructions\n") -> Path:
        skill = root / "skill-source"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: integrateme-pipeline\ndescription: Test skill.\n---\n" + body,
            encoding="utf-8",
        )
        (skill / "references" / "details.md").write_text("details\n", encoding="utf-8")
        return skill

    def test_provisioned_skill_is_discoverable_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            skill = self._write_skill(root)

            with _provision_runtime_skill(repo, skill_source=skill) as (linked, bundle_hash):
                self.assertTrue(linked.is_symlink())
                self.assertEqual(skill.resolve(), linked.resolve())
                self.assertEqual(_hash_skill_bundle(skill), bundle_hash)

            self.assertFalse((repo / ".agents").exists())

    def test_provisioning_preserves_existing_agent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            existing = repo / ".agents" / "skills" / "repository-skill"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("repository instructions\n", encoding="utf-8")
            skill = self._write_skill(root)

            with _provision_runtime_skill(repo, skill_source=skill):
                self.assertTrue((repo / ".agents" / "skills" / "integrateme-pipeline").is_symlink())

            self.assertTrue(existing.is_dir())
            self.assertFalse((repo / ".agents" / "skills" / "integrateme-pipeline").exists())

    def test_provisioning_refuses_to_replace_repository_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            collision = repo / ".agents" / "skills" / "integrateme-pipeline"
            collision.mkdir(parents=True)
            skill = self._write_skill(root)

            with self.assertRaises(FileExistsError):
                with _provision_runtime_skill(repo, skill_source=skill):
                    pass

            self.assertTrue(collision.is_dir())

    def test_skill_hash_includes_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = self._write_skill(root)
            before = _hash_skill_bundle(skill)
            (skill / "references" / "details.md").write_text("changed\n", encoding="utf-8")

            self.assertNotEqual(before, _hash_skill_bundle(skill))

    def test_prompt_exposes_skill_only_when_provisioned(self) -> None:
        guidance = RepoGuidance(
            path=Path("CONTRIBUTING.md"),
            text="Use descriptive test names.",
            sha256="abc123",
            truncated=False,
        )
        prompt = _build_prompt(
            rules_text="rules",
            improved_code="class Improved {}",
            manual_code="class Manual {}",
            repo_context=[],
            repo_guidance=[guidance],
            runtime_skill_available=True,
            replication_root=Path("/replication"),
        )
        without_skill = _build_prompt(
            rules_text="rules",
            improved_code="class Improved {}",
            manual_code="class Manual {}",
            repo_context=[],
        )

        self.assertIn("$integrateme-pipeline", prompt)
        self.assertIn("/replication", prompt)
        self.assertIn("Use descriptive test names.", prompt)
        self.assertIn("sha256=abc123", prompt)
        self.assertNotIn("$integrateme-pipeline", without_skill)

    def test_guidance_collection_is_bounded_prioritized_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "README.md").write_text("repository readme\n", encoding="utf-8")
            (repo / "CONTRIBUTING.md").write_text("contribution rules\n", encoding="utf-8")
            (repo / "module").mkdir()
            (repo / "module" / "TESTING.md").write_text("module tests\n", encoding="utf-8")
            ignored = repo / "target"
            ignored.mkdir()
            (ignored / "README.md").write_text("generated readme\n", encoding="utf-8")

            guidance = _collect_repo_guidance(repo, max_files=2, max_chars=1_000)

            self.assertEqual([Path("CONTRIBUTING.md"), Path("README.md")], [item.path for item in guidance])
            self.assertTrue(all(len(item.sha256) == 64 for item in guidance))
            self.assertNotIn(Path("target/README.md"), [item.path for item in guidance])

    def test_codex_run_provisions_skill_and_enables_pipeline_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            (repo / "AGENTS.md").write_text("Use AssertJ for tests.\n", encoding="utf-8")
            (repo / "README.md").write_text("Run the focused module test.\n", encoding="utf-8")
            improved = root / "Target_ESTest_Improved.java"
            manual = root / "TargetTest.java"
            improved.write_text("package sample; class Target_ESTest_Improved {}\n", encoding="utf-8")
            manual.write_text("package sample; class TargetTest {}\n", encoding="utf-8")
            output_root = root / "output"
            log = root / "agent.log"
            codex_calls: list[list[str]] = []
            prompts: list[str] = []

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[0] == "git":
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                codex_calls.append(command)
                prompts.append(str(kwargs["input"]))
                linked = repo / ".agents" / "skills" / "integrateme-pipeline"
                self.assertTrue(linked.is_symlink())
                output_flag = command.index("--output-last-message") + 1
                Path(command[output_flag]).write_text(
                    "package sample; class AgenticOutput {}\n",
                    encoding="utf-8",
                )
                self.assertIn("$integrateme-pipeline", str(kwargs["input"]))
                return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr=None)

            with patch("src.steps.agent.shutil.which", return_value="/usr/bin/codex"), patch(
                "src.steps.agent.subprocess.run", side_effect=fake_run
            ):
                result = run_codex_integration(
                    model="gpt-test",
                    improved_test_path=improved,
                    manual_test_path=manual,
                    repo_root=repo,
                    out_root=output_root,
                    target_id="T00_Target",
                    target_fqcn="sample.AgenticOutput",
                    max_context_files=4,
                    max_context_chars=3_000,
                    max_prompt_chars=2_000,
                    log_file=log,
                )

            self.assertIsNotNone(result)
            self.assertEqual(1, len(codex_calls))
            self.assertLessEqual(len(prompts[0]), 2_000)
            self.assertIn("Use AssertJ for tests.", prompts[0])
            self.assertIn("Run the focused module test.", prompts[0])
            self.assertIn("--ephemeral", codex_calls[0])
            self.assertIn("workspace-write", codex_calls[0])
            self.assertEqual(2, codex_calls[0].count("--add-dir"))
            self.assertTrue(any(value.endswith("/pipeline-output") for value in codex_calls[0]))
            self.assertTrue(any(value.endswith("/workspace") for value in codex_calls[0]))
            self.assertFalse((repo / ".agents").exists())
            log_text = log.read_text(encoding="utf-8")
            self.assertIn(
                "runtime_skill_source=implementations/integration-pipeline/resources/skills/integrateme-pipeline",
                log_text,
            )
            self.assertIn("runtime_skill_path=.agents/skills/integrateme-pipeline", log_text)
            self.assertIn("runtime_skill_sha256=", log_text)
            self.assertIn("guidance_files=2", log_text)
            self.assertIn("guidance_file=AGENTS.md\tsha256=", log_text)
            self.assertIn("guidance_file=README.md\tsha256=", log_text)

    def test_codex_output_is_rejected_when_target_worktree_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            improved = root / "Target_ESTest_Improved.java"
            manual = root / "TargetTest.java"
            improved.write_text("package sample; class Target_ESTest_Improved {}\n", encoding="utf-8")
            manual.write_text("package sample; class TargetTest {}\n", encoding="utf-8")
            log = root / "agent.log"
            git_calls = 0

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                nonlocal git_calls
                if command[0] == "git":
                    git_calls += 1
                    state = "" if git_calls == 1 else " M src/test/java/TargetTest.java\n"
                    return subprocess.CompletedProcess(command, 0, stdout=state, stderr="")
                output_flag = command.index("--output-last-message") + 1
                Path(command[output_flag]).write_text(
                    "package sample; class AgenticOutput {}\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr=None)

            with patch("src.steps.agent.shutil.which", return_value="/usr/bin/codex"), patch(
                "src.steps.agent.subprocess.run", side_effect=fake_run
            ):
                result = run_codex_integration(
                    model="gpt-test",
                    improved_test_path=improved,
                    manual_test_path=manual,
                    repo_root=repo,
                    out_root=root / "output",
                    target_id="T00_Target",
                    target_fqcn="sample.AgenticOutput",
                    max_context_files=0,
                    max_context_chars=0,
                    max_prompt_chars=0,
                    log_file=log,
                )

            self.assertIsNone(result)
            self.assertFalse((repo / ".agents").exists())
            self.assertIn(
                "target repository changed during the Codex run; refusing agent output",
                log.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
