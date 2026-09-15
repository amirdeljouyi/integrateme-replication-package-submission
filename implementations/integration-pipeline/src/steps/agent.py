from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, NamedTuple, Optional, Tuple, TYPE_CHECKING

from ..core.common import ensure_dir, parse_package_and_class, repo_to_dir
from ..pipeline.helpers import agentic_test_path, first_test_source_for_fqcn, improved_test_path
from .base import Step
from .llm import (
    _append_llm_summary_row,
    _output_class_name_from_path,
    _rewrite_class_name,
    _target_selected_for_zero_kept_retry,
    write_adopted_output,
)

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _pipeline_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _replication_root() -> Path:
    return _pipeline_root().parents[1]


def _pipeline_writable_roots() -> Tuple[Path, Path]:
    root = _replication_root()
    return root / "pipeline-output", root / "workspace"


def _runtime_skill_source() -> Path:
    return _pipeline_root() / "resources" / "skills" / "integrateme-pipeline"


def _hash_skill_bundle(skill_root: Path) -> str:
    """Hash skill instructions and resources in a path-independent order."""
    digest = hashlib.sha256()
    files = sorted(path for path in skill_root.rglob("*") if path.is_file())
    if not files:
        raise FileNotFoundError(f"Runtime skill contains no files: {skill_root}")
    for path in files:
        relative = path.relative_to(skill_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@contextmanager
def _provision_runtime_skill(
    repo_root: Path,
    *,
    skill_source: Optional[Path] = None,
) -> Iterator[Tuple[Path, str]]:
    """Expose the packaged skill through Codex's repository skill discovery.

    The link exists only for the duration of one agent run. Existing repository
    instructions are preserved, and a same-named skill is never overwritten.
    """
    source = (skill_source or _runtime_skill_source()).resolve()
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"Runtime skill is missing SKILL.md: {source}")

    agents_dir = repo_root / ".agents"
    skills_dir = agents_dir / "skills"
    provisioned = skills_dir / "integrateme-pipeline"
    if provisioned.exists() or provisioned.is_symlink():
        raise FileExistsError(
            "Refusing to replace the target repository's existing runtime skill: "
            f"{provisioned}"
        )

    created_agents_dir = not agents_dir.exists()
    created_skills_dir = not skills_dir.exists()
    skills_dir.mkdir(parents=True, exist_ok=True)
    provisioned.symlink_to(source, target_is_directory=True)
    bundle_hash = _hash_skill_bundle(source)

    try:
        yield provisioned, bundle_hash
    finally:
        if provisioned.is_symlink():
            provisioned.unlink()
        elif provisioned.exists():
            raise RuntimeError(
                "The provisioned runtime-skill link was replaced during the agent run; "
                f"left unexpected path untouched: {provisioned}"
            )
        if created_skills_dir:
            try:
                skills_dir.rmdir()
            except OSError:
                pass
        if created_agents_dir:
            try:
                agents_dir.rmdir()
            except OSError:
                pass


def _git_worktree_state(repo_root: Path) -> Optional[str]:
    """Return tracked and non-ignored worktree state, or None outside Git."""
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


class RepoGuidance(NamedTuple):
    path: Path
    text: str
    sha256: str
    truncated: bool


_GUIDANCE_PREFIXES = ("readme", "contributing", "testing", "development")
_GUIDANCE_EXACT_NAMES = {
    "agents.md",
    "claude.md",
    "copilot-instructions.md",
}
_GUIDANCE_SUFFIXES = {"", ".md", ".markdown", ".rst", ".txt", ".adoc"}
_GUIDANCE_PRUNE_DIRS = {
    ".agents",
    ".git",
    ".gradle",
    ".idea",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


def _is_guidance_file(path: Path) -> bool:
    name = path.name.lower()
    if name in _GUIDANCE_EXACT_NAMES:
        return True
    return path.suffix.lower() in _GUIDANCE_SUFFIXES and any(
        name.startswith(prefix) for prefix in _GUIDANCE_PREFIXES
    )


def _guidance_priority(path: Path, repo_root: Path) -> Tuple[int, int, str]:
    relative = path.relative_to(repo_root)
    parts = relative.parts
    scope = 0 if len(parts) == 1 else (1 if parts[0].lower() == ".github" else 2)
    name = path.name.lower()
    if name == "agents.md":
        kind = 0
    elif name.startswith("contributing"):
        kind = 1
    elif name.startswith("readme"):
        kind = 2
    elif name.startswith("testing"):
        kind = 3
    elif name.startswith("development"):
        kind = 4
    else:
        kind = 5
    return scope, kind, relative.as_posix().lower()


def _collect_repo_guidance(
    repo_root: Path,
    *,
    max_files: int,
    max_chars: int,
    max_depth: int = 4,
) -> List[RepoGuidance]:
    """Collect a bounded, hashed set of public project guidance files."""
    if not repo_root.exists() or max_files <= 0 or max_chars <= 0:
        return []

    candidates: List[Path] = []
    for current, dirnames, filenames in os.walk(repo_root):
        current_path = Path(current)
        depth = len(current_path.relative_to(repo_root).parts)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _GUIDANCE_PRUNE_DIRS and depth < max_depth
        )
        for filename in sorted(filenames):
            path = current_path / filename
            if _is_guidance_file(path):
                candidates.append(path)

    remaining = max_chars
    selected: List[RepoGuidance] = []
    for path in sorted(candidates, key=lambda candidate: _guidance_priority(candidate, repo_root)):
        if len(selected) >= max_files or remaining <= 0:
            break
        raw = path.read_bytes()
        full_text = raw.decode("utf-8", errors="ignore")
        text = full_text[:remaining]
        if not text:
            continue
        selected.append(
            RepoGuidance(
                path=path.relative_to(repo_root),
                text=text,
                sha256=hashlib.sha256(raw).hexdigest(),
                truncated=len(text) < len(full_text),
            )
        )
        remaining -= len(text)
    return selected


def _truncate_guidance(
    guidance: Iterable[RepoGuidance],
    max_chars: int,
) -> List[RepoGuidance]:
    remaining = max_chars
    out: List[RepoGuidance] = []
    for document in guidance:
        if remaining <= 0:
            break
        text = document.text[:remaining]
        if not text:
            continue
        out.append(
            RepoGuidance(
                path=document.path,
                text=text,
                sha256=document.sha256,
                truncated=document.truncated or len(text) < len(document.text),
            )
        )
        remaining -= len(text)
    return out


def _collect_repo_context(
    repo_root: Path,
    package_name: str,
    *,
    max_files: int,
    max_chars: int,
) -> List[Tuple[Path, str]]:
    if not repo_root or not repo_root.exists():
        return []

    pkg_path = Path(*package_name.split(".")) if package_name else None
    roots = [
        repo_root / "src" / "main" / "java",
        repo_root / "src" / "test" / "java",
    ]

    candidates: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if pkg_path:
            pkg_dir = root / pkg_path
            if pkg_dir.exists():
                candidates.extend(sorted(pkg_dir.rglob("*.java")))
        else:
            candidates.extend(sorted(root.rglob("*.java")))

    seen: set[Path] = set()
    out: List[Tuple[Path, str]] = []
    remaining = max_chars

    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if len(out) >= max_files or remaining <= 0:
            break
        text = _read_text(p)
        if not text:
            continue
        if len(text) > remaining:
            text = text[:remaining]
        out.append((p, text))
        remaining -= len(text)

    return out


def _build_prompt(
    *,
    rules_text: str,
    improved_code: str,
    manual_code: str,
    repo_context: Iterable[Tuple[Path, str]],
    repo_guidance: Iterable[RepoGuidance] = (),
    max_output_tests: Optional[int] = None,
    runtime_skill_available: bool = False,
    replication_root: Optional[Path] = None,
) -> str:
    lines = [
        rules_text.strip(),
        "",
        "For this automated pipeline, output ONLY the full merged Java test code.",
        "Do not include a patch/diff or summary.",
        "",
    ]
    if runtime_skill_available:
        lines.extend(
            [
                "The `$integrateme-pipeline` runtime skill is available for this run.",
                "Use it when compilation or coverage evidence is needed; do not invent a separate Maven/Gradle route.",
                f"The replication-package root is {replication_root}.",
                "Keep pipeline operations scoped to this target and variant, and do not modify the target repository.",
                "",
            ]
        )
    guidance = list(repo_guidance)
    if guidance:
        lines.extend(
            [
                "Repository guidance (read before integration; excerpts may be truncated):",
                "Use the applicable instructions for test conventions and validation. More specific repository instructions take priority.",
            ]
        )
        for document in guidance:
            lines.append(
                f"File: {document.path} (sha256={document.sha256}, truncated={str(document.truncated).lower()})"
            )
            lines.append("```text")
            lines.append(document.text)
            lines.append("```")
            lines.append("")
    if max_output_tests is not None and max_output_tests > 0:
        lines.extend(
            [
                f"Return a complete class containing at most {max_output_tests} test methods, selecting the most meaningful generated behaviors.",
                "The package, imports, class declaration, helper members, and closing brace must all be present.",
                "",
            ]
        )
    lines.extend(
        [
            "[MWT]",
            manual_code,
            "[/MWT]",
            "",
            "[IGT]",
            improved_code,
            "[/IGT]",
            "",
        ]
    )

    ctx = list(repo_context)
    if ctx:
        lines.append("Repo context (read-only, may be partial):")
        for path, text in ctx:
            lines.append(f"File: {path}")
            lines.append("```java")
            lines.append(text)
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _strip_code_fences(text: str) -> str:
    s = text.strip()
    lines = s.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _looks_incomplete_merged_output(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "keep the rest of existing manual tests unchanged",
        "keep remaining existing manual tests and helpers unchanged",
    )
    if any(marker in lowered for marker in markers):
        return True
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("// ... keep"):
            return True
    return False


def _load_agent_rules() -> Tuple[str, str]:
    """Load the rule set sent to the model by the agentic integration step.

    Resolved explicitly rather than by searching upwards. The previous version
    walked every ancestor of this file looking for the first ``AGENTS.md``,
    which ran past the repository root and would silently adopt an unrelated
    file of that name - the conventional filename for coding-agent
    instructions - as the prompt, changing generated tests with no error.

    Set ITL_AGENT_RULES to point at a different rule set for an experiment.
    """
    default_rules = "Follow the repo conventions when merging [MWT] and [IGT]."

    override = os.environ.get("ITL_AGENT_RULES", "").strip()
    candidates = [Path(override)] if override else []
    # src/steps/agent.py -> the pipeline root
    candidates.append(_pipeline_root() / "resources" / "AGENTIC_INTEGRATION_RULES.md")

    for path in candidates:
        if path.exists():
            return _read_text(path), str(path)

    return default_rules, "fallback-default"


def run_codex_integration(
    *,
    model: str,
    improved_test_path: Path,
    manual_test_path: Path,
    repo_root: Optional[Path],
    out_root: Path,
    target_id: str,
    target_fqcn: Optional[str],
    max_context_files: int,
    max_context_chars: int,
    max_prompt_chars: int,
    max_output_tests: Optional[int] = None,
    log_file: Optional[Path] = None,
) -> Optional[Path]:
    def read_log() -> str:
        if not log_file or not log_file.exists():
            return ""
        return log_file.read_text(encoding="utf-8", errors="ignore")

    def write_log(msg: str) -> None:
        if not log_file:
            return
        ensure_dir(log_file.parent)
        log_file.write_text(msg, encoding="utf-8", errors="ignore")

    codex_bin = shutil.which("codex")
    if not codex_bin:
        write_log("Missing codex CLI in PATH.\n")
        return None

    improved_code = _read_text(improved_test_path)
    manual_code = _read_text(manual_test_path)
    if not improved_code or not manual_code:
        write_log("Missing improved or manual test source.\n")
        return None

    pkg, _cls = parse_package_and_class(manual_test_path)
    guidance_file_budget = min(6, max(0, max_context_files))
    guidance_char_budget = min(16_000, max(0, max_context_chars) // 3)
    repo_guidance = (
        _collect_repo_guidance(
            repo_root,
            max_files=guidance_file_budget,
            max_chars=guidance_char_budget,
        )
        if repo_root
        else []
    )
    guidance_chars = sum(len(document.text) for document in repo_guidance)
    repo_ctx = (
        _collect_repo_context(
            repo_root,
            pkg,
            max_files=max(0, max_context_files - len(repo_guidance)),
            max_chars=max(0, max_context_chars - guidance_chars),
        )
        if repo_root
        else []
    )

    rules_text, rules_source = _load_agent_rules()
    runtime_skill_available = bool(repo_root and repo_root.exists())
    replication_root = _replication_root()
    prompt = _build_prompt(
        rules_text=rules_text,
        improved_code=improved_code,
        manual_code=manual_code,
        repo_context=repo_ctx,
        repo_guidance=repo_guidance,
        max_output_tests=max_output_tests,
        runtime_skill_available=runtime_skill_available,
        replication_root=replication_root,
    )
    if max_prompt_chars > 0 and len(prompt) > max_prompt_chars:
        # Keep a bounded guidance excerpt, then divide the remaining prompt
        # budget among the rules and both test inputs.
        guidance_trim = _truncate_guidance(repo_guidance, max_prompt_chars // 5)
        fixed_prompt = _build_prompt(
            rules_text="",
            improved_code="",
            manual_code="",
            repo_context=[],
            repo_guidance=guidance_trim,
            max_output_tests=max_output_tests,
            runtime_skill_available=runtime_skill_available,
            replication_root=replication_root,
        )
        remaining = max(0, max_prompt_chars - len(fixed_prompt))
        keep_rules = min(len(rules_text), 8000, remaining // 4)
        remaining -= keep_rules
        keep_manual = min(len(manual_code), remaining // 2)
        keep_improved = min(len(improved_code), remaining - keep_manual)
        unused = remaining - keep_manual - keep_improved
        if unused > 0 and keep_manual < len(manual_code):
            extra = min(unused, len(manual_code) - keep_manual)
            keep_manual += extra
            unused -= extra
        if unused > 0 and keep_improved < len(improved_code):
            keep_improved += min(unused, len(improved_code) - keep_improved)
        rules_trim = _truncate_text(rules_text, keep_rules)
        manual_trim = _truncate_text(manual_code, keep_manual)
        improved_trim = _truncate_text(improved_code, keep_improved)
        prompt = _build_prompt(
            rules_text=rules_trim,
            improved_code=improved_trim,
            manual_code=manual_trim,
            repo_context=[],
            repo_guidance=guidance_trim,
            max_output_tests=max_output_tests,
            runtime_skill_available=runtime_skill_available,
            replication_root=replication_root,
        )
        write_log(
            read_log()
            + f"prompt trimmed: rules={keep_rules} guidance={sum(len(document.text) for document in guidance_trim)} "
            + f"manual={keep_manual} improved={keep_improved}\n"
        )
        repo_guidance = guidance_trim
        repo_ctx = []
    guidance_log = "".join(
        "guidance_file="
        + document.path.as_posix()
        + f"\tsha256={document.sha256}\tprompt_chars={len(document.text)}"
        + f"\ttruncated={str(document.truncated).lower()}\n"
        for document in repo_guidance
    )
    write_log(
        read_log()
        + "agent request\n"
        f"model={model or '<cli-default>'}\n"
        f"improved_test={improved_test_path}\n"
        f"manual_test={manual_test_path}\n"
        f"repo_root={repo_root}\n"
        f"rules_source={rules_source}\n"
        f"runtime_skill_requested={runtime_skill_available}\n"
        f"guidance_files={len(repo_guidance)}\n"
        + guidance_log
        + f"context_files={len(repo_ctx)}\n"
        f"prompt_chars={len(prompt)}\n"
    )

    output_file = log_file.with_suffix(".codex.out") if log_file else Path("codex_last_message.txt")
    cmd = [codex_bin, "exec", "--ephemeral"]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(["--output-last-message", str(output_file)])
    if repo_root and repo_root.exists():
        cmd.extend(["--sandbox", "workspace-write"])
        for writable_root in _pipeline_writable_roots():
            ensure_dir(writable_root)
            cmd.extend(["--add-dir", str(writable_root)])
        cmd.extend(["-C", str(repo_root)])
    cmd.append("-")

    before_state = _git_worktree_state(repo_root) if runtime_skill_available and repo_root else None
    if runtime_skill_available and before_state is None:
        write_log("Unable to record the target repository's initial Git state; refusing agent run.\n")
        return None
    try:
        if runtime_skill_available and repo_root:
            with _provision_runtime_skill(repo_root) as (skill_path, skill_hash):
                write_log(
                    read_log()
                    + "runtime_skill_source="
                    + str(_runtime_skill_source().relative_to(replication_root))
                    + "\n"
                    + f"runtime_skill_path={skill_path.relative_to(repo_root)}\n"
                    + f"runtime_skill_sha256={skill_hash}\n"
                    + "codex cmd: "
                    + " ".join(cmd)
                    + "\n"
                )
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
        else:
            write_log(read_log() + "runtime_skill_path=<unavailable-no-repository>\n")
            write_log(read_log() + "codex cmd: " + " ".join(cmd) + "\n")
            proc = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
    except (FileNotFoundError, FileExistsError, RuntimeError) as exc:
        write_log(read_log() + f"runtime skill provisioning failed: {exc}\n")
        return None

    write_log(read_log() + f"codex exit={proc.returncode}\nstdout:\n{proc.stdout or ''}\n")
    after_state = _git_worktree_state(repo_root) if runtime_skill_available and repo_root else None
    if runtime_skill_available and after_state is None:
        write_log(read_log() + "Unable to record the target repository's final Git state; refusing agent output.\n")
        return None
    if before_state is not None and after_state is not None and before_state != after_state:
        write_log(
            read_log()
            + "target repository changed during the Codex run; refusing agent output\n"
            + "before git status:\n"
            + before_state
            + "after git status:\n"
            + after_state
        )
        return None
    if proc.returncode != 0:
        return None

    if not output_file.exists():
        write_log(read_log() + "missing codex output file\n")
        return None

    text = output_file.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        write_log(read_log() + "codex output file empty\n")
        return None
    text = _strip_code_fences(text)
    if not text:
        write_log(read_log() + "codex output empty after stripping fences\n")
        return None
    if _looks_incomplete_merged_output(text):
        write_log(read_log() + "codex output appears incomplete; refusing placeholder output\n")
        return None

    out_name = _output_class_name_from_path(improved_test_path, "_Adopted_Agentic")
    rewritten = _rewrite_class_name(text, out_name)
    ensure_dir(out_root)
    return write_adopted_output(out_root=out_root, target_id=target_id, source=rewritten, target_fqcn=target_fqcn)


class AgentStep(Step):
    step_names = ("llm-agent",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        summary_csv = self.pipeline.llm_agentic_summary_csv
        log_file = self.pipeline.logs_dir / f"{ctx.target_id}.agent.log"
        improved_src = improved_test_path(self.pipeline.adopted_root, ctx.target_id, ctx.fqcn)
        manual_test_src = first_test_source_for_fqcn(ctx.manual_sources or ctx.final_sources, ctx.manual_test_fqcn)
        manual_pkg, _ = parse_package_and_class(manual_test_src) if manual_test_src and manual_test_src.exists() else ("", "")
        improved_pkg, _ = parse_package_and_class(improved_src) if improved_src and improved_src.exists() else ("", "")
        expected_agentic_pkg = manual_pkg or improved_pkg
        expected_agentic_fqcn = f"{expected_agentic_pkg}.AgenticOutput" if expected_agentic_pkg else ctx.fqcn
        existing_agentic_src = agentic_test_path(self.pipeline.adopted_root, ctx.target_id, expected_agentic_fqcn)
        existing_agentic_pkg, _ = (
            parse_package_and_class(existing_agentic_src) if existing_agentic_src and existing_agentic_src.exists() else ("", "")
        )

        if expected_agentic_pkg and existing_agentic_pkg and expected_agentic_pkg != existing_agentic_pkg:
            print(
                f'[agt] agent: Ignore stale existing output (package mismatch): '
                f'repo="{ctx.repo}" fqcn="{ctx.fqcn}" expected_pkg="{expected_agentic_pkg}" '
                f'found_pkg="{existing_agentic_pkg}"'
            )
            existing_agentic_src = None

        if self.pipeline.args.retry_zero_kept_tests and not _target_selected_for_zero_kept_retry(
            self.pipeline,
            ctx,
            variants=("agentic",),
        ):
            print(
                f'[agt] agent: Skip (not marked zero_kept_tests for retry): '
                f'repo="{ctx.repo}" fqcn="{ctx.fqcn}"'
            )
            _append_llm_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                phase="agent",
                prompt_type="agent_merge",
                output_suffix="_Adopted_Agentic",
                status="skipped",
                problem_category="not_zero_kept_retry_target",
                problem_detail=(
                    "Retry mode is enabled and this target was not marked "
                    "zero_kept_tests in the agentic reduce summary."
                ),
                agt_source=improved_src,
                mwt_source=manual_test_src,
                output_source=existing_agentic_src,
                log_file=log_file,
            )
            return True

        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] agent: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_llm_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                phase="agent",
                prompt_type="agent_merge",
                output_suffix="_Adopted_Agentic",
                status="skipped",
                problem_category="excluded_by_agt_coverage",
                problem_detail="Target excluded because AGT coverage summary reports zero covered lines.",
                agt_source=improved_src,
                mwt_source=manual_test_src,
                output_source=existing_agentic_src,
                log_file=log_file,
            )
            return True
        if not shutil.which("codex"):
            print(f'[agt] agent: Skip (missing codex CLI): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_llm_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                phase="agent",
                prompt_type="agent_merge",
                output_suffix="_Adopted_Agentic",
                status="skipped",
                problem_category="missing_codex_cli",
                problem_detail="codex CLI is not available in PATH.",
                agt_source=improved_src,
                mwt_source=manual_test_src,
                output_source=existing_agentic_src,
                log_file=log_file,
            )
            return True

        if self.pipeline.args.skip_exists and existing_agentic_src and existing_agentic_src.exists():
            print(f'[agt] agent: Skip (existing agent output): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_llm_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                phase="agent",
                prompt_type="agent_merge",
                output_suffix="_Adopted_Agentic",
                status="skipped",
                problem_category="existing_output",
                problem_detail="Skipping because agentic output already exists and --skip-exists is enabled.",
                agt_source=improved_src,
                mwt_source=manual_test_src,
                output_source=existing_agentic_src,
                log_file=log_file,
            )
            return True
        if not improved_src or not improved_src.exists():
            print(f'[agt] agent: Skip (missing improved test): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_llm_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                phase="agent",
                prompt_type="agent_merge",
                output_suffix="_Adopted_Agentic",
                status="skipped",
                problem_category="missing_improved_test",
                problem_detail="Agent integration requires improved AGT test output.",
                agt_source=improved_src,
                mwt_source=manual_test_src,
                output_source=existing_agentic_src,
                log_file=log_file,
            )
            return True
        if not manual_test_src or not manual_test_src.exists():
            print(f'[agt] agent: Skip (missing manual test source): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_llm_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                phase="agent",
                prompt_type="agent_merge",
                output_suffix="_Adopted_Agentic",
                status="skipped",
                problem_category="missing_manual_test",
                problem_detail="Manual test source is required for agent integration.",
                agt_source=improved_src,
                mwt_source=manual_test_src,
                output_source=existing_agentic_src,
                log_file=log_file,
            )
            return True

        output_target_fqcn = ctx.fqcn
        if expected_agentic_pkg:
            output_target_fqcn = f"{expected_agentic_pkg}.AgenticOutput"

        repo_root = self.pipeline.repos_dir / repo_to_dir(ctx.repo)
        out_path = run_codex_integration(
            model=self.pipeline.args.agent_model,
            improved_test_path=improved_src,
            manual_test_path=manual_test_src,
            repo_root=repo_root if repo_root.exists() else None,
            out_root=self.pipeline.adopted_root,
            target_id=ctx.target_id,
            target_fqcn=output_target_fqcn,
            max_context_files=self.pipeline.args.agent_max_context_files,
            max_context_chars=self.pipeline.args.agent_max_context_chars,
            max_prompt_chars=self.pipeline.args.agent_max_prompt_chars,
            log_file=log_file,
        )
        if not out_path:
            print(f'[agt] agent: FAIL (no output): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            _append_llm_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                phase="agent",
                prompt_type="agent_merge",
                output_suffix="_Adopted_Agentic",
                status="failed",
                problem_category="agent_send_failed",
                problem_detail="Agent run completed without producing an output test source.",
                agt_source=improved_src,
                mwt_source=manual_test_src,
                output_source=existing_agentic_src,
                log_file=log_file,
            )
            return True
        _append_llm_summary_row(
            summary_csv=summary_csv,
            ctx=ctx,
            phase="agent",
            prompt_type="agent_merge",
            output_suffix="_Adopted_Agentic",
            status="passed",
            problem_category="",
            problem_detail="",
            agt_source=improved_src,
            mwt_source=manual_test_src,
            output_source=out_path,
            log_file=log_file,
        )
        return True
