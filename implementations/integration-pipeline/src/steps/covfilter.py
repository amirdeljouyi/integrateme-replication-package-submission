from __future__ import annotations

import csv
import hashlib
import os
import re
import select
import signal
import shlex
import shutil
import subprocess
import time
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple, TYPE_CHECKING

from ..core.common import candidate_repo_class_dirs, ensure_dir, parse_package_and_class, shlex_join, write_text
from ..core.java import (
    add_throws_exception_to_error_methods,
    add_throws_exception_to_tests,
    comment_compile_errors,
    compile_test_set_smart,
    expand_same_package_support_sources,
    normalize_duplicate_throws_clauses,
    prefer_repo_manual_sources,
    prune_generated_methods_from_compile_output,
    remove_unused_imports,
    resolve_external_classpath_from_imports,
    resolve_external_runtime_classpath_from_output,
    resolve_repo_runtime_classpath,
)
from ..pipeline.config import (
    ADOPTED_LIKE_VARIANTS,
    covfilter_candidate_out_dirs,
    covfilter_variant_out_dir,
    covfilter_variant_summary_csv,
    selected_adopted_variants,
)
from ..pipeline.sanitize import (
    EvoSuitePair,
    clear_pair_root,
    materialize_sanitized_pair,
    sanitize_compare_summary_csv,
    sanitize_compare_target_dir,
    variant_test_fqcn,
    variant_pair,
)
from ..pipeline.helpers import (
    annotated_test_method_names,
    adopted_test_method_names,
    adopted_variants,
    first_test_source_for_fqcn,
    individually_runnable_test_method_names,
    is_empty_generated_test_source,
    libs_dir_from_glob,
    mask_java_comments_and_strings,
    test_method_names,
    test_fqcn_from_source,
)
from .llm import (
    _replace_identifier_outside_comments_and_strings,
    _rewrite_class_name,
    namespace_non_primary_type_name_file,
    normalize_primary_class_name_file,
)
from .base import Step

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext

# Keep this consistent with how you run tests (your EvoSuite headless/module opens fix)
DEFAULT_JAVA_OPTS: List[str] = [
    "--add-opens=java.desktop/java.awt=ALL-UNNAMED",
    "--add-opens=java.base/java.lang=ALL-UNNAMED",
    "--add-opens=java.base/java.util=ALL-UNNAMED",
    "--add-opens=java.base/java.io=ALL-UNNAMED",
    "--add-opens=java.base/java.net=ALL-UNNAMED",
    "--add-opens=java.base/jdk.internal.misc=ALL-UNNAMED",
    "--add-exports=java.base/jdk.internal.misc=ALL-UNNAMED",
    "-XX:+EnableDynamicAgentLoading",
    "-Djava.awt.headless=true",
    "-Dnet.bytebuddy.experimental=true",
]

_COVFILTER_JAVA_FEATURE_VERSION: Optional[int] = None
_COVFILTER_RUNTIME_RETRY_LIMIT = 32
_COVFILTER_SUBPROCESS_TIMEOUT_MS = 900_000
_COVFILTER_KEEP_DROP_TIMEOUT_MS = 0
_FROZEN_REPO_MAIN_CLASS_DIRS: dict[Path, tuple[Path, ...]] = {}
_COVFILTER_AGT_DISCOVERED_RE = re.compile(r"AGT methods discovered:\s*(\d+)")
_COVFILTER_CSV_WRITTEN_MARKER = "CSVs written to:"


def _frozen_repo_main_class_dirs(repo_root: Path) -> tuple[Path, ...]:
    key = repo_root.resolve()
    cached = _FROZEN_REPO_MAIN_CLASS_DIRS.get(key)
    if cached is not None:
        return cached
    found: list[Path] = []
    for current, directories, _ in os.walk(key):
        current_path = Path(current)
        if current_path.name == "target":
            candidate = current_path / "classes"
            if candidate.is_dir():
                found.append(candidate.resolve())
            directories[:] = []
            continue
        if current_path.name == "build":
            candidate = current_path / "classes" / "java" / "main"
            if candidate.is_dir():
                found.append(candidate.resolve())
            found.extend(
                jar.resolve() for jar in (current_path / "libs").glob("*.jar")
                if jar.is_file()
            )
            directories[:] = []
            continue
        directories[:] = [
            name for name in directories
            if name not in {".git", ".gradle", "node_modules", "out"}
        ]
    result = tuple(sorted(set(found)))
    _FROZEN_REPO_MAIN_CLASS_DIRS[key] = result
    return result
_JUNIT3_TEST_METHOD_RE = re.compile(
    r"^(?P<indent>\s*)public\s+(?:(?:final|static)\s+)*void\s+test[A-Za-z0-9_]*\s*\(\s*\)"
)
_VOID_METHOD_START_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|final|static|synchronized|native|abstract|strictfp|default)\s+)*void\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
    re.MULTILINE,
)


def _absolutize_classpath_entry(entry: str) -> str:
    candidate = (entry or "").strip()
    if not candidate:
        return ""
    return str(Path(candidate).expanduser().resolve())


def _absolutize_classpath_string(classpath: str) -> str:
    entries: List[str] = []
    for raw_entry in (classpath or "").split(":"):
        entry = _absolutize_classpath_entry(raw_entry)
        if entry:
            entries.append(entry)
    return ":".join(entries)


def _covfilter_java_feature_version() -> int:
    global _COVFILTER_JAVA_FEATURE_VERSION
    if _COVFILTER_JAVA_FEATURE_VERSION is not None:
        return _COVFILTER_JAVA_FEATURE_VERSION

    feature_version = 17
    try:
        proc = subprocess.run(
            ["java", "-XshowSettings:properties", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_covfilter_java_env(),
        )
        for line in (proc.stdout or "").splitlines():
            marker = "java.specification.version ="
            if marker not in line:
                continue
            feature_version = int(line.split("=", 1)[1].strip().split(".", 1)[0])
            break
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    _COVFILTER_JAVA_FEATURE_VERSION = feature_version
    return feature_version


def _effective_covfilter_timeout_ms(timeout_ms: Optional[int]) -> Optional[int]:
    if timeout_ms is None or timeout_ms <= 0:
        return _COVFILTER_SUBPROCESS_TIMEOUT_MS
    return max(timeout_ms, _COVFILTER_SUBPROCESS_TIMEOUT_MS)


def _covfilter_optional_java_opts() -> List[str]:
    opts = ["-Xshare:off"]
    canonical_baseline = os.environ.get("ITL_COVFILTER_BASELINE_EXEC", "").strip()
    if canonical_baseline:
        opts.append(f"-Dcovfilter.baselineExec={Path(canonical_baseline).resolve()}")
    if _covfilter_java_feature_version() >= 24:
        opts.append("--sun-misc-unsafe-memory-access=allow")
    return opts


def _covfilter_fork_java_opts() -> List[str]:
    opts: List[str] = []
    for candidate in [*DEFAULT_JAVA_OPTS, *_covfilter_optional_java_opts(), "-Dnet.bytebuddy.experimental=true"]:
        if candidate in opts:
            continue
        opts.append(candidate)
    return opts


def _covfilter_java_wrapper_dir(build_dir: Path) -> Path:
    return build_dir / "covfilter-java-bin"


def _ensure_covfilter_java_wrapper(build_dir: Path) -> Optional[Path]:
    real_java = shutil.which("java")
    if not real_java:
        return None

    wrapper_dir = _covfilter_java_wrapper_dir(build_dir)
    ensure_dir(wrapper_dir)
    wrapper_path = wrapper_dir / "java"
    fork_opts = " ".join(shlex.quote(opt) for opt in _covfilter_fork_java_opts())
    script = "#!/bin/sh\n" f"exec {shlex.quote(real_java)} {fork_opts} \"$@\"\n"
    current = ""
    if wrapper_path.exists():
        try:
            current = wrapper_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            current = ""
    if current != script:
        write_text(wrapper_path, script)
        wrapper_path.chmod(0o755)
    return wrapper_dir


def _covfilter_java_env(build_dir: Optional[Path] = None) -> dict[str, str]:
    env = os.environ.copy()
    if build_dir is not None:
        wrapper_dir = _ensure_covfilter_java_wrapper(build_dir)
        if wrapper_dir is not None:
            env["PATH"] = f"{wrapper_dir}{os.pathsep}{env.get('PATH', '')}".rstrip(os.pathsep)
            env.pop("JDK_JAVA_OPTIONS", None)
            return env

    required_flag = "-Dnet.bytebuddy.experimental=true"
    existing = (env.get("JDK_JAVA_OPTIONS") or "").strip()
    if required_flag not in existing.split():
        env["JDK_JAVA_OPTIONS"] = f"{existing} {required_flag}".strip()
    return env


class CovfilterRunner:
    def __init__(
        self,
        *,
        java_opts: Optional[List[str]] = None,
        build_dir: Optional[Path] = None,
        working_dir: Optional[Path] = None,
        timeout_ms: Optional[int] = None,
    ) -> None:
        self.java_opts = list(java_opts) if java_opts else list(DEFAULT_JAVA_OPTS)
        for option in _covfilter_optional_java_opts():
            if option not in self.java_opts:
                self.java_opts.append(option)
        self.build_dir = build_dir
        self.working_dir = working_dir
        self.timeout_ms = timeout_ms

    def run_covfilter(
            self,
            *,
            coverage_filter_jar: Path,
            libs_glob_cp: str,
            extra_runtime_cp: str,
            test_classes_dir: Path,
            sut_classes_dir: Path,
            out_dir: Path,
            manual_test_fqcn: str,
            generated_test_fqcn: str,
            target_fqcn: str,
            jacoco_agent_jar: Path,
            sut_cp_entry: Path,
            libs_dir_arg: Path,
            log_file: Path,
            extra_java_opts: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        ensure_dir(out_dir)
        ensure_dir(log_file.parent)

        java_opts = list(self.java_opts)
        if not target_fqcn.strip():
            raise ValueError("target_fqcn is required for source-file coverage filtering")
        java_opts.append(f"-Dcovfilter.targetFqcn={target_fqcn.strip()}")
        if extra_java_opts:
            java_opts.extend(extra_java_opts)
        if self.working_dir is not None and not any(opt.startswith("-Duser.dir=") for opt in java_opts):
            java_opts.append(f"-Duser.dir={self.working_dir.resolve()}")

        # The parent process performs JaCoCo bytecode analysis, so its bundled
        # JaCoCo/ASM implementation must not be shadowed by an older project
        # dependency. ForkedJacocoRunner constructs a separate child
        # classpath with the project runtime first for actual test execution.
        cp_parts = [_absolutize_classpath_entry(str(test_classes_dir))]
        cp_parts.append(_absolutize_classpath_entry(str(coverage_filter_jar)))
        absolute_extra_runtime_cp = _absolutize_classpath_string(extra_runtime_cp.strip())
        if absolute_extra_runtime_cp:
            cp_parts.append(absolute_extra_runtime_cp)
        cp_parts.append(_absolutize_classpath_entry(libs_glob_cp))
        cp = ":".join(part for part in cp_parts if part)

        cmd = (
                ["java"]
                + java_opts
                + [
                    "-cp",
                    cp,
                    "app.CoverageFilterApp",
                    "filter",
                    str(sut_classes_dir.resolve()),
                    str(out_dir.resolve()),
                    manual_test_fqcn,
                    generated_test_fqcn,
                    str(jacoco_agent_jar.resolve()),
                    str(sut_cp_entry.resolve()),
                    str(libs_dir_arg.resolve()),
                    str(test_classes_dir.resolve()),
                ]
        )

        timeout_seconds = (self.timeout_ms / 1000) if self.timeout_ms and self.timeout_ms > 0 else None
        timed_out = False
        keep_drop_watchdog_hit = False
        completed_after_csv_write = False
        agt_methods_total: Optional[int] = None
        agt_decisions = 0
        keep_drop_deadline: Optional[float] = None
        chunks: List[str] = []

        def _terminate_and_collect(proc: subprocess.Popen[str]) -> str:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                try:
                    proc.terminate()
                except OSError:
                    pass
            try:
                tail_out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    try:
                        proc.kill()
                    except OSError:
                        pass
                tail_out, _ = proc.communicate()
            return tail_out or ""

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=_covfilter_java_env(self.build_dir),
                start_new_session=True,
            )
            deadline = (time.monotonic() + timeout_seconds) if timeout_seconds else None
            fatal_fork_failure = False
            fatal_fork_deadline = None
            while True:
                if proc.stdout is None:
                    break

                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if ready:
                    line = proc.stdout.readline()
                    if line:
                        chunks.append(line)
                        # A fork can report an unrecoverable discovery/classpath
                        # failure while project libraries keep non-daemon threads
                        # alive.  Waiting for the whole-filter timeout adds no
                        # evidence and delays the existing missing-dependency
                        # recovery pass.  Stop this attempt once the parent has
                        # surfaced the failed fork; the collected tail remains
                        # available to the recovery classifier below.
                        if "java.lang.RuntimeException: Fork failed" in line:
                            fatal_fork_failure = True
                            # Give inherited child stderr a brief opportunity to
                            # flush the underlying linkage error used by the
                            # recovery classifier before terminating lingering
                            # project threads.
                            fatal_fork_deadline = time.monotonic() + 2.0
                        discovered_match = _COVFILTER_AGT_DISCOVERED_RE.search(line)
                        if discovered_match:
                            try:
                                agt_methods_total = int(discovered_match.group(1))
                            except ValueError:
                                agt_methods_total = None
                            agt_decisions = 0
                            keep_drop_deadline = None
                        elif line.startswith("[KEEP]") or line.startswith("[DROP]"):
                            if agt_methods_total is not None and agt_decisions < agt_methods_total:
                                agt_decisions += 1
                            keep_drop_deadline = None
                        elif (
                            agt_methods_total is not None
                            and agt_decisions < agt_methods_total
                            and line.startswith("[JUnit4TestRunner] run=")
                            and _COVFILTER_KEEP_DROP_TIMEOUT_MS > 0
                        ):
                            keep_drop_deadline = time.monotonic() + (_COVFILTER_KEEP_DROP_TIMEOUT_MS / 1000)
                        if _COVFILTER_CSV_WRITTEN_MARKER in line and covfilter_output_exists(out_dir):
                            completed_after_csv_write = True
                            tail_out = _terminate_and_collect(proc)
                            if tail_out:
                                chunks.append(tail_out)
                            break
                    elif proc.poll() is not None:
                        break
                elif proc.poll() is not None:
                    break

                now = time.monotonic()
                if fatal_fork_deadline is not None and now >= fatal_fork_deadline:
                    tail_out = _terminate_and_collect(proc)
                    if tail_out:
                        chunks.append(tail_out)
                    break
                if keep_drop_deadline is not None and now >= keep_drop_deadline:
                    keep_drop_watchdog_hit = True
                    timed_out = True
                    raise subprocess.TimeoutExpired(cmd=cmd, timeout=_COVFILTER_KEEP_DROP_TIMEOUT_MS / 1000)
                if deadline is not None and now >= deadline:
                    timed_out = True
                    raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_seconds)

            out = "".join(chunks)
            if completed_after_csv_write:
                out += "\n[agt] covfilter completion marker observed; terminated lingering subprocess after CSV generation.\n"
                ok = True
            elif fatal_fork_failure:
                ok = False
            else:
                tail_out, _ = proc.communicate(timeout=0)
                if tail_out:
                    out += tail_out
                ok = proc.returncode == 0 and not timed_out
        except subprocess.TimeoutExpired:
            timed_out = True
            tail_out = _terminate_and_collect(proc)
            out = "".join(chunks)
            if tail_out:
                out += tail_out
            if keep_drop_watchdog_hit:
                out = (
                    f"{out}\n[agt] covfilter subprocess timed out after waiting "
                    f"{_COVFILTER_KEEP_DROP_TIMEOUT_MS} ms for KEEP/DROP after a test run summary.\n"
                )
            else:
                out = (
                    f"{out}\n[agt] covfilter subprocess timed out after {self.timeout_ms} ms.\n"
                    if self.timeout_ms and self.timeout_ms > 0
                    else f"{out}\n[agt] covfilter subprocess timed out.\n"
                )
            ok = False
        write_text(log_file, f"$ {shlex_join(cmd)}\n\n{out}\n")
        tail = "\n".join(out.splitlines()[-80:])

        return ok, tail

    def run_generate_reduced(
            self,
            *,
            coverage_filter_jar: Path,
            libs_glob_cp: str,
            original_test_java: Path,
            test_deltas_csv: Path,
            top_n: int,
            out_dir: Path,
            log_file: Path,
            extra_java_opts: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        ensure_dir(out_dir)
        ensure_dir(log_file.parent)

        java_opts = list(self.java_opts)
        if extra_java_opts:
            java_opts.extend(extra_java_opts)

        cp = f"{coverage_filter_jar}:{libs_glob_cp}"

        cmd = (
                ["java"]
                + java_opts
                + [
                    "-cp",
                    cp,
                    "app.GenerateReducedAgtTestApp",
                    str(original_test_java),
                    str(test_deltas_csv),
                    str(top_n),
                    str(out_dir),
                    "true",
                ]
        )

        timeout_seconds = (self.timeout_ms / 1000) if self.timeout_ms and self.timeout_ms > 0 else None
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=_covfilter_java_env(self.build_dir),
                timeout=timeout_seconds,
            )
            out = proc.stdout or ""
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired as exc:
            out = exc.output or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            out = (
                f"{out}\n[agt] reduced-test subprocess timed out after {self.timeout_ms} ms.\n"
                if self.timeout_ms and self.timeout_ms > 0
                else f"{out}\n[agt] reduced-test subprocess timed out.\n"
            )
            ok = False
        write_text(log_file, f"$ {shlex_join(cmd)}\n\n{out}\n")
        tail = "\n".join(out.splitlines()[-80:])

        return ok, tail

def run_covfilter_app(
        *,
        coverage_filter_jar: Path,
        libs_glob_cp: str,
        extra_runtime_cp: str,
        test_classes_dir: Path,
        sut_classes_dir: Path,
        out_dir: Path,
        manual_test_fqcn: str,
        generated_test_fqcn: str,
        target_fqcn: str,
        jacoco_agent_jar: Path,
        sut_cp_entry: Path,
        libs_dir_arg: Path,
        log_file: Path,
        extra_java_opts: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    return CovfilterRunner(
        java_opts=extra_java_opts,
        build_dir=Path.cwd() / "build" / "agt",
    ).run_covfilter(
        coverage_filter_jar=coverage_filter_jar,
        libs_glob_cp=libs_glob_cp,
        extra_runtime_cp=extra_runtime_cp,
        test_classes_dir=test_classes_dir,
        sut_classes_dir=sut_classes_dir,
        out_dir=out_dir,
        manual_test_fqcn=manual_test_fqcn,
        generated_test_fqcn=generated_test_fqcn,
        target_fqcn=target_fqcn,
        jacoco_agent_jar=jacoco_agent_jar,
        sut_cp_entry=sut_cp_entry,
        libs_dir_arg=libs_dir_arg,
        log_file=log_file,
    )


def _probe_covfilter_list_tests_output(
    *,
    coverage_filter_jar: Path,
    libs_dir: Path,
    test_classes_dir: Path,
    sut_classes_dir: Path,
    test_fqcns: List[str],
    working_dir: Optional[Path],
    timeout_ms: Optional[int],
) -> str:
    cp_entries = [str(coverage_filter_jar)]
    cp_entries.extend(str(path) for path in sorted(libs_dir.glob("*.jar")) if path.is_file())
    cp_entries.extend([str(sut_classes_dir), str(test_classes_dir)])
    classpath = _absolutize_classpath_string(":".join(entry for entry in cp_entries if entry))
    if not classpath:
        return ""

    timeout_seconds = (timeout_ms / 1000) if timeout_ms and timeout_ms > 0 else None
    outputs: List[str] = []
    for test_fqcn in test_fqcns:
        if not test_fqcn:
            continue
        cmd = [
            "java",
            *DEFAULT_JAVA_OPTS,
            *( [f"-Duser.dir={working_dir.resolve()}"] if working_dir is not None else [] ),
            "-cp",
            classpath,
            "app.ListTests",
            test_fqcn,
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=_covfilter_java_env(Path.cwd() / "build" / "agt"),
                timeout=timeout_seconds,
            )
            outputs.append(f"$ {shlex_join(cmd)}\n{proc.stdout or ''}".rstrip())
        except (OSError, subprocess.SubprocessError) as exc:
            outputs.append(f"$ {shlex_join(cmd)}\n{exc}".rstrip())
    return "\n\n".join(output for output in outputs if output).strip()


def covfilter_output_exists(out_dir: Path) -> bool:
    return (out_dir / "test_deltas_all.csv").exists()


def _clear_covfilter_output_dir(out_dir: Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)


def _covfilter_summary_csv(pipeline, variant: str) -> Path:
    return covfilter_variant_summary_csv(
        pipeline.covfilter_out_root,
        pipeline.adopted_covfilter_out_root,
        variant,
        pipeline.args.includes,
        pipeline.agentic_covfilter_out_root,
    )


def _covfilter_status_cache(pipeline) -> dict[tuple[str, str, str], str]:
    cache = getattr(pipeline, "_covfilter_status_cache", None)
    if cache is not None:
        return cache

    cache = {}
    for variant in ("auto", "auto-original", *ADOPTED_LIKE_VARIANTS):
        csv_path = _covfilter_summary_csv(pipeline, variant)
        if not csv_path.exists():
            continue
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    repo = (row.get("repo", "") or "").strip()
                    fqcn = (row.get("fqcn", "") or "").strip()
                    row_variant = (row.get("variant", "") or variant).strip() or variant
                    status = (row.get("status", "") or "").strip()
                    if repo and fqcn and row_variant:
                        cache[(repo, fqcn, row_variant)] = status
        except OSError:
            continue

    setattr(pipeline, "_covfilter_status_cache", cache)
    return cache


def _latest_covfilter_status(pipeline, *, repo: str, fqcn: str, variant: str) -> str:
    return _covfilter_status_cache(pipeline).get((repo, fqcn, variant), "")


def _existing_covfilter_out_dir(pipeline, variant: str, target_id: str) -> Optional[Path]:
    for candidate in covfilter_candidate_out_dirs(
        pipeline.covfilter_out_root,
        pipeline.adopted_covfilter_out_root,
        variant,
        target_id,
        pipeline.agentic_covfilter_out_root,
    ):
        if covfilter_output_exists(candidate):
            return candidate
    return None


def _maybe_skip_existing_covfilter(
    *,
    pipeline,
    ctx: "TargetContext",
    variant: str,
    cov_out: Path,
    cov_log: Path,
    generated_test_fqcn: str,
    step_label: str,
    message_context: str,
) -> bool:
    existing_cov_out = _existing_covfilter_out_dir(pipeline, variant, ctx.target_id)
    if pipeline.args.skip_passed_by_status and _latest_covfilter_status(
        pipeline,
        repo=ctx.repo,
        fqcn=ctx.fqcn,
        variant=variant,
    ) == "passed":
        print(f'[agt] {step_label}: Skip (existing passed status): {message_context}')
        _append_covfilter_summary_row(
            csv_path=_covfilter_summary_csv(pipeline, variant),
            repo=ctx.repo,
            fqcn=ctx.fqcn,
            variant=variant,
            status="skipped",
            problem_category="existing_passed_status",
            problem_detail="Latest covfilter summary row already has status=passed.",
            manual_test_fqcn=ctx.manual_test_fqcn or "",
            generated_test_fqcn=generated_test_fqcn,
            out_dir=existing_cov_out or cov_out,
            log_file=cov_log,
        )
        return True
    if pipeline.args.skip_passed and existing_cov_out is not None:
        print(f'[agt] {step_label}: Skip (existing passed output): {message_context}')
        _append_covfilter_summary_row(
            csv_path=_covfilter_summary_csv(pipeline, variant),
            repo=ctx.repo,
            fqcn=ctx.fqcn,
            variant=variant,
            status="skipped",
            problem_category="existing_passed_output",
            problem_detail="Existing covfilter output found for a previously passed target.",
            manual_test_fqcn=ctx.manual_test_fqcn or "",
            generated_test_fqcn=generated_test_fqcn,
            out_dir=existing_cov_out,
            log_file=cov_log,
        )
        return True
    if pipeline.args.skip_exists and existing_cov_out is not None:
        print(f'[agt] {step_label}: Skip (existing covfilter output): {message_context}')
        _append_covfilter_summary_row(
            csv_path=_covfilter_summary_csv(pipeline, variant),
            repo=ctx.repo,
            fqcn=ctx.fqcn,
            variant=variant,
            status="skipped",
            problem_category="existing_output",
            problem_detail="Existing covfilter output found.",
            manual_test_fqcn=ctx.manual_test_fqcn or "",
            generated_test_fqcn=generated_test_fqcn,
            out_dir=existing_cov_out,
            log_file=cov_log,
        )
        return True
    return False


def _prepare_covfilter_inputs(
    *,
    pipeline,
    ctx: "TargetContext",
    source_files: List[Path],
    compile_fallback_source_files: Optional[List[Path]],
    manual_test_fqcn: str,
    generated_test_fqcn: str,
    reusable_build_dir: Path,
    compile_build_dir: Path,
    compile_log: Path,
    step_label: str,
    compile_context: str,
    duplicate_fix_source: Optional[Path] = None,
) -> tuple[bool, str, Path, List[Path]]:
    uses_canonical_baseline = bool(os.environ.get("ITL_COVFILTER_BASELINE_EXEC", "").strip())
    has_manual_class = (
        uses_canonical_baseline
        or _compiled_test_class_exists(reusable_build_dir, manual_test_fqcn)
    )
    has_generated_class = _compiled_test_class_exists(reusable_build_dir, generated_test_fqcn)
    classes_are_fresh = _compiled_sources_are_fresh(reusable_build_dir, source_files)
    if has_manual_class and has_generated_class and classes_are_fresh:
        return True, "", reusable_build_dir, source_files

    if compile_build_dir.exists():
        shutil.rmtree(compile_build_dir, ignore_errors=True)
    ensure_dir(compile_build_dir)

    attempts = 3 if duplicate_fix_source is not None else 1
    ok_compile = False
    compile_tail = ""
    compiled_sources = source_files
    for _ in range(attempts):
        ok_compile, compile_tail, compiled_sources, used_repo_manual_fallback = _compile_covfilter_sources_with_manual_fallback(
            source_files=source_files,
            build_dir=compile_build_dir,
            libs_glob_cp=pipeline.args.libs_cp,
            sut_jar=ctx.sut_jar,
            log_file=compile_log,
            repo_root_for_deps=ctx.repo_root_for_deps,
            module_rel=ctx.module_rel,
            build_tool=ctx.build_tool,
            max_rounds=pipeline.args.dep_rounds,
            allow_commenting=pipeline.args.covfilter_comment_compile_errors,
        )
        if used_repo_manual_fallback and ok_compile:
            print(f'[agt] {step_label}: compile retry used repo manual sources: {compile_context}')
        if ok_compile:
            break
        if duplicate_fix_source is None:
            break
        duplicate_simple_name = _duplicate_class_simple_name(compile_tail)
        if not duplicate_simple_name or not namespace_non_primary_type_name_file(duplicate_fix_source, duplicate_simple_name):
            break

    if ok_compile:
        # A successful javac invocation is not enough: recovery may have
        # dropped the manual source and compiled only the generated suite.
        # Never proceed to covfilter with a missing selector, especially when
        # a repository's precompiled test has the same simple name in another
        # package (as with Seata's legacy compatibility tests).
        has_manual_class = (
            uses_canonical_baseline
            or _compiled_test_class_exists(compile_build_dir, manual_test_fqcn)
        )
        has_generated_class = _compiled_test_class_exists(compile_build_dir, generated_test_fqcn)
        if has_manual_class and has_generated_class:
            return True, "", compile_build_dir, compiled_sources
        missing_selectors = [
            fqcn
            for fqcn, present in (
                (manual_test_fqcn, has_manual_class),
                (generated_test_fqcn, has_generated_class),
            )
            if not present
        ]
        compile_tail = (
            "covfilter compilation produced no class for required selector(s): "
            + ", ".join(missing_selectors)
        )

    fallback_compile_sources = list(compile_fallback_source_files or [])
    if fallback_compile_sources and fallback_compile_sources != source_files:
        if compile_build_dir.exists():
            shutil.rmtree(compile_build_dir, ignore_errors=True)
        ensure_dir(compile_build_dir)
        (
            fallback_ok_compile,
            fallback_compile_tail,
            fallback_compiled_sources,
            used_repo_manual_fallback,
        ) = _compile_covfilter_sources_with_manual_fallback(
            source_files=fallback_compile_sources,
            build_dir=compile_build_dir,
            libs_glob_cp=pipeline.args.libs_cp,
            sut_jar=ctx.sut_jar,
            log_file=compile_log,
            repo_root_for_deps=ctx.repo_root_for_deps,
            module_rel=ctx.module_rel,
            build_tool=ctx.build_tool,
            max_rounds=pipeline.args.dep_rounds,
            allow_commenting=pipeline.args.covfilter_comment_compile_errors,
        )
        if used_repo_manual_fallback and fallback_ok_compile:
            print(f'[agt] {step_label}: compile retry used repo manual sources: {compile_context}')
        if fallback_ok_compile:
            return True, "", compile_build_dir, fallback_compiled_sources
        compile_tail = fallback_compile_tail or compile_tail
        compiled_sources = fallback_compiled_sources if fallback_compiled_sources else compiled_sources

    fallback_classes_dir = ctx.target_build
    has_manual_fallback = _compiled_test_class_exists(fallback_classes_dir, manual_test_fqcn)
    has_generated_fallback = _compiled_test_class_exists(fallback_classes_dir, generated_test_fqcn)
    if has_manual_fallback and has_generated_fallback:
        print(f'[agt] {step_label}: compile retry falling back to precompiled classes: {compile_context}')
        return True, "", fallback_classes_dir, ctx.final_sources

    return False, compile_tail, compile_build_dir, source_files


def _materialize_adopted_covfilter_source(source: Path, stage_dir: Path) -> Path:
    """Copy an adopted source before compile recovery mutates it."""
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    ensure_dir(stage_dir)
    staged_source = stage_dir / source.name
    shutil.copy2(source, staged_source)
    text = source.read_text(encoding="utf-8", errors="ignore")
    scaffold = re.search(r"\bextends\s+([A-Za-z_$][A-Za-z0-9_$]*_scaffolding)\b", text)
    if scaffold:
        scaffold_source = source.with_name(f"{scaffold.group(1)}.java")
        if scaffold_source.is_file():
            shutil.copy2(scaffold_source, stage_dir / scaffold_source.name)
    _qualify_evosuite_verify_exception_targets(staged_source)
    return staged_source


def _qualify_evosuite_verify_exception_targets(source: Path) -> None:
    """Restore class names shortened inside EvoSuite's string-based oracle.

    Java imports make a shortened type name compile, but ``verifyException``
    receives a string and loads it directly.  An integration rewrite such as
    ``"io.questdb.Bootstrap"`` to ``"Bootstrap"`` therefore breaks the
    oracle at runtime.  Resolve only unqualified names for which the source
    supplies an explicit import; leave every scenario and assertion unchanged.
    """
    text = source.read_text(encoding="utf-8")
    imports = {
        fqcn.rsplit(".", 1)[-1]: fqcn
        for fqcn in re.findall(
            r"(?m)^\s*import\s+(?!static\s+)([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\s*;",
            text,
        )
    }

    def qualify(match: re.Match[str]) -> str:
        class_name = match.group("class_name")
        if "." in class_name:
            return match.group(0)
        outer, separator, nested = class_name.partition("$")
        imported = imports.get(outer)
        if not imported:
            return match.group(0)
        qualified = imported + (separator + nested if separator else "")
        return f'{match.group("prefix")}{qualified}{match.group("suffix")}'

    updated = re.sub(
        r'(?P<prefix>\bverifyException\(\s*")(?P<class_name>[A-Za-z_$][\w$]*)(?P<suffix>"\s*,)',
        qualify,
        text,
    )
    if updated != text:
        source.write_text(updated, encoding="utf-8")


def _filter_covfilter_test_outputs(
    out_dir: Path,
    *,
    allowed_test_methods: Set[str],
    output_test_fqcn: Optional[str],
    failed_test_methods: Optional[Set[str]] = None,
) -> None:
    failed_methods = failed_test_methods or set()
    for csv_path in out_dir.glob("*.csv"):
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                fieldnames = reader.fieldnames or []
                if "test_selector" not in fieldnames:
                    continue
                rows = [dict(row) for row in reader]
        except OSError:
            continue

        filtered_rows: List[dict[str, str]] = []
        for row in rows:
            selector = (row.get("test_selector", "") or "").strip()
            method = selector.rsplit("#", 1)[-1]
            if method not in allowed_test_methods:
                continue
            if method in failed_methods:
                if csv_path.name != "test_deltas_all.csv":
                    continue
                for field in ("added_lines", "added_methods", "added_branches", "added_instructions"):
                    if field in row:
                        row[field] = "0"
            if output_test_fqcn and "#" in selector:
                row["test_selector"] = f"{output_test_fqcn}#{method}"
            filtered_rows.append(row)

        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(filtered_rows)


def _failed_covfilter_candidate_methods(log_file: Path, generated_test_fqcn: str) -> Set[str]:
    try:
        text = log_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    class_prefix = re.escape(generated_test_fqcn)
    patterns = (
        rf"FAILURE in\s+{class_prefix}#([A-Za-z_][A-Za-z0-9_]*)\b",
        rf"Test (?:failed|interrupted):\s+{class_prefix}#([A-Za-z_][A-Za-z0-9_]*)\b",
        rf"\[DROP\]\s+{class_prefix}#([A-Za-z_][A-Za-z0-9_]*)\b[^\n]*\bfork-exit=",
    )
    return {
        method
        for pattern in patterns
        for method in re.findall(pattern, text)
    }


def _remove_pr_test_methods(source: str, method_names: Set[str]) -> str:
    if not method_names:
        return source

    lines = source.splitlines(keepends=True)
    masked_lines = mask_java_comments_and_strings(source).splitlines(keepends=True)
    remove_indexes: Set[int] = set()
    annotation_ranges_by_end: Dict[int, int] = {}
    annotation_index = 0
    while annotation_index < len(masked_lines):
        if not masked_lines[annotation_index].lstrip().startswith("@"):
            annotation_index += 1
            continue
        annotation_start = annotation_index
        depth = 0
        while annotation_index < len(masked_lines):
            depth += masked_lines[annotation_index].count("(") - masked_lines[annotation_index].count(")")
            if depth <= 0:
                break
            annotation_index += 1
        annotation_ranges_by_end[annotation_index] = annotation_start
        annotation_index += 1

    index = 0
    while index < len(lines):
        match = _VOID_METHOD_START_RE.match(masked_lines[index])
        if not match or match.group("name") not in method_names:
            index += 1
            continue

        start = index
        cursor = index - 1
        while cursor >= 0:
            while cursor >= 0 and not masked_lines[cursor].strip():
                cursor -= 1
            annotation_start = annotation_ranges_by_end.get(cursor)
            if annotation_start is None:
                break
            start = annotation_start
            cursor = annotation_start - 1

        depth = 0
        opened = False
        end = index
        while end < len(masked_lines):
            depth += masked_lines[end].count("{") - masked_lines[end].count("}")
            opened = opened or "{" in masked_lines[end]
            if opened and depth <= 0:
                break
            end += 1
        remove_indexes.update(range(start, min(end + 1, len(lines))))
        index = end + 1

    return "".join(line for line_index, line in enumerate(lines) if line_index not in remove_indexes)


def _materialize_pr_covfilter_sources(
    *,
    ctx: "TargetContext",
    pr_source: Path,
    stage_root: Path,
) -> tuple[List[Path], str, Set[str], str]:
    ensure_dir(stage_root)

    def write_if_changed(path: Path, text: str) -> None:
        ensure_dir(path.parent)
        try:
            if path.exists() and path.read_text(encoding="utf-8", errors="ignore") == text:
                return
        except OSError:
            pass
        path.write_text(text, encoding="utf-8")

    package_name, original_class_name = parse_package_and_class(pr_source)
    if not original_class_name:
        return [], "", set(), ""
    candidate_class_name = f"{original_class_name}_PRTests"
    candidate_fqcn = f"{package_name}.{candidate_class_name}" if package_name else candidate_class_name
    original_fqcn = f"{package_name}.{original_class_name}" if package_name else original_class_name
    source_text = pr_source.read_text(encoding="utf-8", errors="ignore")
    manual_source = first_test_source_for_fqcn(ctx.manual_sources, ctx.manual_test_fqcn)
    adopted_methods = set(adopted_test_method_names(pr_source, manual_source))
    all_test_methods = set(test_method_names(pr_source))
    annotated_methods = set(annotated_test_method_names(pr_source))
    runnable_methods = set(individually_runnable_test_method_names(pr_source))
    allowed_methods = adopted_methods & runnable_methods

    baseline_text = _remove_pr_test_methods(source_text, adopted_methods)
    baseline_path = stage_root / "manual" / Path(*package_name.split(".")) / f"{original_class_name}.java"
    write_if_changed(baseline_path, baseline_text)

    candidate_path = stage_root / "candidate" / Path(*package_name.split(".")) / f"{candidate_class_name}.java"
    candidate_text = _remove_pr_test_methods(source_text, all_test_methods - allowed_methods)
    annotated_lines: List[str] = []
    for line in candidate_text.splitlines():
        match = _JUNIT3_TEST_METHOD_RE.match(line)
        if match and match.group(0) and match.group(0).strip():
            method_match = _VOID_METHOD_START_RE.match(line)
            method = method_match.group("name") if method_match else ""
        else:
            method = ""
        if match and method not in annotated_methods:
            annotated_lines.append(f"{match.group('indent')}@org.junit.Test")
        annotated_lines.append(line)
    source_text = "\n".join(annotated_lines) + "\n"
    rewritten = _rewrite_class_name(source_text, candidate_class_name)
    rewritten = _replace_identifier_outside_comments_and_strings(
        rewritten,
        original_class_name,
        candidate_class_name,
    )
    # Assertions and snapshots often include nested test-class names as strings.
    rewritten = re.sub(
        rf"(?<![A-Za-z0-9_$]){re.escape(original_fqcn)}(?![A-Za-z0-9_$])",
        candidate_fqcn,
        rewritten,
    )
    write_if_changed(candidate_path, rewritten)
    return [baseline_path, candidate_path], candidate_fqcn, allowed_methods, original_fqcn


def _run_covfilter_variant(
    *,
    pipeline,
    ctx: "TargetContext",
    variant: str,
    run_variant: str,
    source_files: List[Path],
    generated_test_fqcn: str,
    cov_classes_dir: Path,
    cov_out: Path,
    cov_log: Path,
    cov_compile_log: Path,
    reusable_build_dir: Path,
    compile_build_dir: Path,
    step_label: str,
    run_display_label: str,
    message_context: str,
    compile_tail_tag: str,
    runtime_tail_tag: str,
    compile_fallback_source_files: Optional[List[Path]] = None,
    fallback_test_classes_dir: Optional[Path] = None,
    duplicate_fix_source: Optional[Path] = None,
    manual_test_fqcn_override: Optional[str] = None,
    allowed_test_methods: Optional[Set[str]] = None,
    output_test_fqcn: Optional[str] = None,
    check_existing_skip: bool = True,
) -> None:
    manual_test_fqcn = manual_test_fqcn_override or ctx.manual_test_fqcn or ""
    if check_existing_skip:
        if _maybe_skip_existing_covfilter(
            pipeline=pipeline,
            ctx=ctx,
            variant=variant,
            cov_out=cov_out,
            cov_log=cov_log,
            generated_test_fqcn=generated_test_fqcn,
            step_label=step_label,
            message_context=message_context,
        ):
            return

    ok_compile, compile_tail, test_classes_dir, compiled_sources = _prepare_covfilter_inputs(
        pipeline=pipeline,
        ctx=ctx,
        source_files=source_files,
        compile_fallback_source_files=compile_fallback_source_files,
        manual_test_fqcn=manual_test_fqcn,
        generated_test_fqcn=generated_test_fqcn,
        reusable_build_dir=reusable_build_dir,
        compile_build_dir=compile_build_dir,
        compile_log=cov_compile_log,
        step_label=step_label,
        compile_context=message_context,
        duplicate_fix_source=duplicate_fix_source,
    )
    if not ok_compile:
        print(f'[agt] {step_label}: Skip (compile failed): {message_context} (see {cov_compile_log})')
        print(f"[agt][{compile_tail_tag}-TAIL]\n" + compile_tail)
        _append_covfilter_summary_row(
            csv_path=_covfilter_summary_csv(pipeline, variant),
            repo=ctx.repo,
            fqcn=ctx.fqcn,
            variant=variant,
            status="skipped",
            problem_category="compile_failed",
            problem_detail=compile_tail,
            manual_test_fqcn=manual_test_fqcn,
            generated_test_fqcn=generated_test_fqcn,
            out_dir=cov_out,
            log_file=cov_compile_log,
        )
        return

    print(f'[agt] Running {run_display_label}: {message_context}')
    ok_cov, cov_tail = _run_covfilter_with_retries(
        pipeline=pipeline,
        ctx=ctx,
        variant=run_variant,
        source_files=compiled_sources,
        coverage_filter_jar=pipeline.covfilter_jar,
        libs_glob_cp=pipeline.args.libs_cp,
        test_classes_dir=test_classes_dir,
        sut_classes_dir=cov_classes_dir,
        out_dir=cov_out,
        manual_test_fqcn=manual_test_fqcn,
        generated_test_fqcn=generated_test_fqcn,
        jacoco_agent_jar=pipeline.jacoco_agent,
        sut_cp_entry=cov_classes_dir,
        log_file=cov_log,
        fallback_test_classes_dir=fallback_test_classes_dir,
    )
    if not ok_cov or not covfilter_output_exists(cov_out):
        problem_category = "covfilter_failed" if not ok_cov else "missing_output"
        problem_detail = cov_tail if not ok_cov else "Covfilter finished without producing test_deltas_all.csv."
        _append_covfilter_summary_row(
            csv_path=_covfilter_summary_csv(pipeline, variant),
            repo=ctx.repo,
            fqcn=ctx.fqcn,
            variant=variant,
            status="failed",
            problem_category=problem_category,
            problem_detail=problem_detail,
            manual_test_fqcn=manual_test_fqcn,
            generated_test_fqcn=generated_test_fqcn,
            out_dir=cov_out,
            log_file=cov_log,
        )
    else:
        if allowed_test_methods is not None:
            _filter_covfilter_test_outputs(
                cov_out,
                allowed_test_methods=allowed_test_methods,
                output_test_fqcn=output_test_fqcn,
                failed_test_methods=_failed_covfilter_candidate_methods(cov_log, generated_test_fqcn),
            )
        _append_covfilter_summary_row(
            csv_path=_covfilter_summary_csv(pipeline, variant),
            repo=ctx.repo,
            fqcn=ctx.fqcn,
            variant=variant,
            status="passed",
            problem_category="",
            problem_detail="",
            manual_test_fqcn=manual_test_fqcn,
            generated_test_fqcn=generated_test_fqcn,
            out_dir=cov_out,
            log_file=cov_log,
        )
        pipeline.ran += 1
    if not ok_cov:
        print(f'[agt] {step_label}: FAIL (see {cov_log})')
        print(f"[agt][{runtime_tail_tag}-TAIL]\n" + cov_tail)


def _merge_classpath_strings(*parts: str) -> str:
    merged: List[str] = []
    seen = set()
    artifact_positions: dict[str, int] = {}
    for part in parts:
        if not part:
            continue
        for entry in part.split(":"):
            candidate = entry.strip()
            if not candidate or candidate in seen:
                continue
            artifact_key = _covfilter_runtime_jar_artifact_key(candidate)
            if artifact_key and artifact_key in artifact_positions:
                existing_index = artifact_positions[artifact_key]
                existing_candidate = merged[existing_index]
                if _covfilter_runtime_jar_preference(candidate) > _covfilter_runtime_jar_preference(existing_candidate):
                    merged[existing_index] = candidate
                    seen.add(candidate)
                continue
            seen.add(candidate)
            if artifact_key:
                artifact_positions[artifact_key] = len(merged)
            merged.append(candidate)
    return ":".join(merged)


def _source_module_dir(java_file: Path) -> Optional[Path]:
    parts = list(java_file.parts)
    for marker in (
        ("src", "test", "java"),
        ("src", "test", "kotlin"),
        ("src", "main", "java"),
        ("src", "main", "kotlin"),
    ):
        marker_len = len(marker)
        for idx in range(len(parts) - marker_len + 1):
            if tuple(parts[idx : idx + marker_len]) == marker:
                return Path(*parts[:idx])
    return None


def _covfilter_runtime_workdir(*, ctx: "TargetContext", source_files: Sequence[Path]) -> Optional[Path]:
    if ctx.repo_root_for_deps and ctx.repo_root_for_deps.exists():
        rel = (ctx.module_rel or "").strip()
        module_root = ctx.repo_root_for_deps if not rel or rel in {".", "root"} else (ctx.repo_root_for_deps / rel)
        if module_root.exists():
            return module_root
    for source in source_files:
        candidate = _source_module_dir(source)
        if candidate is not None and candidate.exists():
            return candidate
    return None


def _extract_missing_service_provider_classes(output: str) -> List[str]:
    providers: List[str] = []
    seen = set()
    pattern = re.compile(
        r"ServiceConfigurationError:\s+[A-Za-z0-9_.$]+:\s+Provider\s+([A-Za-z0-9_.$]+)\s+not found"
    )
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        provider = match.group(1).strip()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        providers.append(provider)
    return providers


def _has_covfilter_duplicate_class_conflict(output: str) -> bool:
    return "Can't add different class with same name:" in output


def _has_hazelcast_service_provider_contamination(output: str) -> bool:
    return (
        "SamplingTestExecutionListener" in output
        or "NoClassDefFoundError: com.hazelcast.logging.Logger" in output
        or "ClassNotFoundException: com.hazelcast.logging.Logger" in output
        or (
            "ServiceConfigurationError:" in output
            and "com.hazelcast." in output
            and "could not be instantiated" in output
        )
    )


def _covfilter_timed_out(output: str) -> bool:
    return "covfilter subprocess timed out" in output


def _source_path_for_provider_fqcn(
    *,
    provider_fqcn: str,
    module_dir: Optional[Path],
    repo_root_for_deps: Optional[Path],
    module_rel: str,
) -> Optional[Path]:
    rel_path = Path(*provider_fqcn.split(".")).with_suffix(".java")
    roots: List[Path] = []
    seen = set()

    def add_root(root: Optional[Path]) -> None:
        if root is None:
            return
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            return
        seen.add(key)
        roots.append(root)

    if module_dir is not None:
        add_root(module_dir / "src" / "test" / "java")
        add_root(module_dir / "src" / "main" / "java")

    if repo_root_for_deps and repo_root_for_deps.exists():
        rel = (module_rel or "").strip()
        if rel and rel not in {".", "root"}:
            module_root = repo_root_for_deps / rel
            add_root(module_root / "src" / "test" / "java")
            add_root(module_root / "src" / "main" / "java")
        add_root(repo_root_for_deps / "src" / "test" / "java")
        add_root(repo_root_for_deps / "src" / "main" / "java")

    for root in roots:
        candidate = root / rel_path
        if candidate.exists():
            return candidate
    return None


def _runtime_top_level_fqcn(class_fqcn: str) -> str:
    normalized = (class_fqcn or "").strip().replace("/", ".")
    if "$" in normalized:
        normalized = normalized.split("$", 1)[0]
    return normalized


def _extract_missing_runtime_classes(output: str) -> List[str]:
    missing: List[str] = []
    seen = set()
    patterns = (
        re.compile(r"(?:NoClassDefFoundError|ClassNotFoundException):\s+([A-Za-z0-9_.$/]+)"),
        re.compile(r"TypeNotPresentException:\s+Type\s+([A-Za-z0-9_.$/]+)\s+not present"),
    )
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = next((candidate.search(line) for candidate in patterns if candidate.search(line)), None)
        if not match:
            continue
        # A JVM may wrap a prior initializer failure as
        # ``NoClassDefFoundError: Could not initialize class ...``.  The
        # referenced class is present, so treating the first word (``Could``)
        # as a missing FQCN causes pointless source/jar scans and retries.
        if match.group(1) == "Could" and "Could not initialize class" in line:
            continue
        candidate = _runtime_top_level_fqcn(match.group(1))
        if (
            not candidate
            or candidate.startswith(("java.", "javax.", "jdk.", "sun.", "kotlin."))
            or candidate in seen
        ):
            continue
        seen.add(candidate)
        missing.append(candidate)
    return missing


def _source_path_for_runtime_fqcn(
    *,
    runtime_fqcn: str,
    module_dir: Optional[Path],
    repo_root_for_deps: Optional[Path],
    module_rel: str,
) -> Optional[Path]:
    direct = _source_path_for_provider_fqcn(
        provider_fqcn=runtime_fqcn,
        module_dir=module_dir,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
    )
    if direct is not None:
        return direct

    if not repo_root_for_deps or not repo_root_for_deps.exists():
        return None

    pkg, _, cls = runtime_fqcn.rpartition(".")
    if not cls:
        return None

    excluded_parts = {"target", "build", ".git", ".gradle", ".idea"}
    for candidate in sorted(repo_root_for_deps.rglob(f"{cls}.java")):
        if not candidate.is_file():
            continue
        if set(candidate.parts) & excluded_parts:
            continue
        candidate_pkg, candidate_cls = parse_package_and_class(candidate)
        if candidate_cls != cls:
            continue
        if pkg and candidate_pkg != pkg:
            continue
        return candidate
    return None


def _copy_runtime_class_from_repo_outputs(
    *,
    runtime_fqcn: str,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
    target_dir: Path,
) -> bool:
    if not repo_root_for_deps or not repo_root_for_deps.exists():
        return False
    class_rel = Path(*runtime_fqcn.split(".")).with_suffix(".class")
    for class_dir in candidate_repo_class_dirs(repo_root_for_deps, module_rel):
        if not class_dir.exists() or not class_dir.is_dir():
            continue
        source_class = class_dir / class_rel
        if not source_class.exists() or not source_class.is_file():
            continue
        target_package_dir = target_dir / class_rel.parent
        ensure_dir(target_package_dir)
        base_name = source_class.stem
        for candidate_class in sorted(source_class.parent.glob(f"{base_name}*.class")):
            if candidate_class.name != f"{base_name}.class" and not candidate_class.name.startswith(f"{base_name}$"):
                continue
            shutil.copy2(candidate_class, target_package_dir / candidate_class.name)
        return True
    return False


def _source_path_for_fqcn(source_files: Sequence[Path], fqcn: str) -> Optional[Path]:
    target = (fqcn or "").strip()
    if not target:
        return None
    for source in source_files:
        pkg, cls = parse_package_and_class(source)
        if not cls:
            continue
        current = f"{pkg}.{cls}" if pkg else cls
        if current == target:
            return source
    return None


def _extract_missing_selector_methods(output: str, generated_test_fqcn: str) -> List[str]:
    if not generated_test_fqcn:
        return []
    methods: List[str] = []
    seen = set()
    pattern = re.compile(r"Could not find method with name \[([^\]]+)\] in class \[([A-Za-z0-9_.$]+)\]")
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        method_name = match.group(1).strip()
        class_name = match.group(2).strip()
        if class_name != generated_test_fqcn:
            continue
        if not re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", method_name):
            continue
        if method_name in seen:
            continue
        seen.add(method_name)
        methods.append(method_name)
    return methods


def _extract_jacoco_analyzer_failed_classes(output: str) -> List[str]:
    failed: List[str] = []
    seen = set()
    pattern = re.compile(r"Error while analyzing\s+(.+?\.class)\s+with JaCoCo")
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        class_path = match.group(1).strip()
        if not class_path or class_path in seen:
            continue
        seen.add(class_path)
        failed.append(class_path)
    return failed


def _append_placeholder_test_method(source_file: Path, method_name: str) -> bool:
    try:
        text = source_file.read_text(encoding="utf-8")
    except OSError:
        return False

    if re.search(rf"\b{re.escape(method_name)}\s*\(\s*\)", text):
        return False

    insert_at = text.rfind("}")
    if insert_at < 0:
        return False

    placeholder = (
        "\n    @org.junit.jupiter.api.Test\n"
        f"    void {method_name}() {{\n"
        "    }\n"
    )
    updated = text[:insert_at] + placeholder + text[insert_at:]
    if updated == text:
        return False
    source_file.write_text(updated, encoding="utf-8")
    return True


def _compile_service_provider_sources(
    *,
    provider_sources: Sequence[Path],
    existing_sources: Sequence[Path],
    build_dir: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    log_file: Path,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
    build_tool: str,
    max_rounds: int,
) -> bool:
    if not provider_sources:
        return False
    compile_sources = expand_same_package_support_sources(repo_root_for_deps, list(provider_sources))
    effective_libs_cp = _effective_compile_libs_cp_for_sources(
        libs_glob_cp=libs_glob_cp,
        sources=[*existing_sources, *compile_sources],
    )
    ok, _tail, _compiled_sources = compile_test_set_smart(
        java_files=compile_sources,
        build_dir=build_dir,
        libs_glob_cp=effective_libs_cp,
        sut_jar=sut_jar,
        log_file=log_file,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
        build_tool=build_tool,
        max_rounds=max_rounds,
    )
    return ok


def _compile_with_generated_pruning(
    *,
    java_files: Sequence[Path],
    build_dir: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    log_file: Path,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
    build_tool: str,
    max_rounds: int,
    allow_commenting: bool,
) -> Tuple[bool, str, List[Path]]:
    generated_candidates = [
        source
        for source in java_files
        if "_ESTest" in source.name and not source.name.endswith("_ESTest_scaffolding.java")
    ]
    for candidate in generated_candidates:
        remove_unused_imports(str(candidate), False)
        normalize_duplicate_throws_clauses(str(candidate), False)

    ok, tail, compiled_sources = compile_test_set_smart(
        java_files=java_files,
        build_dir=build_dir,
        libs_glob_cp=libs_glob_cp,
        sut_jar=sut_jar,
        log_file=log_file,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
        build_tool=build_tool,
        max_rounds=max_rounds,
    )
    if ok:
        return ok, tail, compiled_sources

    compile_output = log_file.read_text(encoding="utf-8", errors="ignore")
    remediated_generated_sources = False
    for candidate in generated_candidates:
        remediated_generated_sources = remove_unused_imports(str(candidate), False) or remediated_generated_sources
        remediated_generated_sources = normalize_duplicate_throws_clauses(str(candidate), False) or remediated_generated_sources
        remediated_generated_sources = add_throws_exception_to_tests(str(candidate), False) or remediated_generated_sources
        remediated_generated_sources = (
            add_throws_exception_to_error_methods(str(candidate), compile_output, False) or remediated_generated_sources
        )

    pruned_generated_methods = prune_generated_methods_from_compile_output(java_files, compile_output)
    if remediated_generated_sources or pruned_generated_methods:
        ok, tail, compiled_sources = compile_test_set_smart(
            java_files=java_files,
            build_dir=build_dir,
            libs_glob_cp=libs_glob_cp,
            sut_jar=sut_jar,
            log_file=log_file,
            repo_root_for_deps=repo_root_for_deps,
            module_rel=module_rel,
            build_tool=build_tool,
            max_rounds=max_rounds,
        )
        if ok:
            return ok, tail, compiled_sources
        second_output = log_file.read_text(encoding="utf-8", errors="ignore")
        if second_output != compile_output and prune_generated_methods_from_compile_output(java_files, second_output):
            ok, tail, compiled_sources = compile_test_set_smart(
                java_files=java_files,
                build_dir=build_dir,
                libs_glob_cp=libs_glob_cp,
                sut_jar=sut_jar,
                log_file=log_file,
                repo_root_for_deps=repo_root_for_deps,
                module_rel=module_rel,
                build_tool=build_tool,
                max_rounds=max_rounds,
            )
            if ok:
                return ok, tail, compiled_sources

    if allow_commenting and not ok and generated_candidates:
        additional_sources = [source for source in java_files if source not in set(generated_candidates)]
        commented_any = False
        for candidate in generated_candidates:
            comment_log = log_file.with_name(f"{log_file.stem}.{candidate.stem}.comment.log")
            commented = comment_compile_errors(
                test_file=candidate,
                build_dir=build_dir,
                log_file=comment_log,
                libs_glob_cp=libs_glob_cp,
                sut_jar=sut_jar,
                additional_java_files=additional_sources,
                candidate_java_files=java_files,
                max_iterations=30,
            )
            commented_any = commented or commented_any
        if commented_any:
            ok, tail, compiled_sources = compile_test_set_smart(
                java_files=java_files,
                build_dir=build_dir,
                libs_glob_cp=libs_glob_cp,
                sut_jar=sut_jar,
                log_file=log_file,
                repo_root_for_deps=repo_root_for_deps,
                module_rel=module_rel,
                build_tool=build_tool,
                max_rounds=max_rounds,
            )
    return ok, tail, compiled_sources


def _compile_covfilter_sources_with_manual_fallback(
    *,
    source_files: Sequence[Path],
    build_dir: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    log_file: Path,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
    build_tool: str,
    max_rounds: int,
    allow_commenting: bool,
) -> Tuple[bool, str, List[Path], bool]:
    requested_sources = list(source_files)
    fallback_sources = prefer_repo_manual_sources(repo_root_for_deps, requested_sources)

    def _attempt(
        attempt_sources: List[Path],
        *,
        use_repo_resolution: bool,
        output_dir: Optional[Path] = None,
        extra_libs_cp: str = "",
    ) -> Tuple[bool, str, List[Path]]:
        attempt_build_dir = output_dir or build_dir
        if attempt_build_dir.exists():
            shutil.rmtree(attempt_build_dir, ignore_errors=True)
        ensure_dir(attempt_build_dir)
        attempt_libs_cp = _effective_compile_libs_cp_for_sources(
            libs_glob_cp=_merge_classpath_strings(libs_glob_cp, extra_libs_cp),
            sources=attempt_sources,
            resolve_imports=not use_repo_resolution,
        )
        return _compile_with_generated_pruning(
            java_files=attempt_sources,
            build_dir=attempt_build_dir,
            libs_glob_cp=attempt_libs_cp,
            sut_jar=sut_jar,
            log_file=log_file,
            repo_root_for_deps=repo_root_for_deps if use_repo_resolution else None,
            module_rel=module_rel if use_repo_resolution else "",
            build_tool=build_tool if use_repo_resolution else "",
            max_rounds=max_rounds if use_repo_resolution else 0,
            allow_commenting=allow_commenting,
        )

    ok_compile, compile_tail, compiled_sources = _attempt(requested_sources, use_repo_resolution=True)
    if ok_compile:
        return True, compile_tail, compiled_sources, False

    if fallback_sources != requested_sources:
        ok_fallback, fallback_tail, compiled_fallback = _attempt(fallback_sources, use_repo_resolution=True)
        if ok_fallback:
            return True, fallback_tail or compile_tail, compiled_fallback, True
        compile_tail = fallback_tail or compile_tail
        compiled_sources = compiled_fallback if compiled_fallback else compiled_sources

    # A generated source can make joint javac recovery fail even when both
    # source groups compile independently. Preserve the frozen manual baseline
    # by compiling the groups separately and merging their class outputs before
    # considering the generated-only/precompiled-manual fallback.
    generated_sources = [source for source in requested_sources if "_ESTest" in source.name]
    generated_source_set = set(generated_sources)
    manual_sources = [source for source in requested_sources if source not in generated_source_set]
    if manual_sources and generated_sources:
        manual_build_dir = build_dir.with_name(f"{build_dir.name}-manual")
        generated_build_dir = build_dir.with_name(f"{build_dir.name}-generated")
        ok_manual, manual_tail, compiled_manual = _attempt(
            manual_sources,
            use_repo_resolution=True,
            output_dir=manual_build_dir,
        )
        if ok_manual:
            ok_generated, generated_tail, compiled_generated = _attempt(
                generated_sources,
                use_repo_resolution=True,
                output_dir=generated_build_dir,
                extra_libs_cp=str(manual_build_dir),
            )
            if ok_generated:
                if build_dir.exists():
                    shutil.rmtree(build_dir, ignore_errors=True)
                ensure_dir(build_dir)
                shutil.copytree(manual_build_dir, build_dir, dirs_exist_ok=True)
                shutil.copytree(generated_build_dir, build_dir, dirs_exist_ok=True)
                shutil.rmtree(manual_build_dir, ignore_errors=True)
                shutil.rmtree(generated_build_dir, ignore_errors=True)
                return (
                    True,
                    generated_tail or manual_tail or compile_tail,
                    [*compiled_manual, *compiled_generated],
                    False,
                )
            compile_tail = generated_tail or manual_tail or compile_tail
        else:
            compile_tail = manual_tail or compile_tail
        shutil.rmtree(manual_build_dir, ignore_errors=True)
        shutil.rmtree(generated_build_dir, ignore_errors=True)

    # Some repositories cannot compile a single manual test source in
    # isolation because it relies on a large test-support graph.  Their
    # already-built test output is still added to covfilter's runtime
    # classpath, so compile the generated pair alone before giving up.  This
    # keeps a broken standalone manual-source compile from blocking AGT
    # filtering (for example Hazelcast's ConfigXmlGeneratorTest).
    generated_only_sources = [source for source in requested_sources if "_ESTest" in source.name]
    if generated_only_sources and generated_only_sources != requested_sources:
        ok_generated, generated_tail, compiled_generated = _attempt(
            generated_only_sources,
            use_repo_resolution=True,
        )
        if ok_generated:
            return True, generated_tail or compile_tail, compiled_generated, False
        compile_tail = generated_tail or compile_tail
        compiled_sources = compiled_generated if compiled_generated else compiled_sources

    ok_plain, plain_tail, plain_compiled = _attempt(requested_sources, use_repo_resolution=False)
    if ok_plain:
        return True, plain_tail or compile_tail, plain_compiled, False
    compile_tail = plain_tail or compile_tail
    compiled_sources = plain_compiled if plain_compiled else compiled_sources

    if fallback_sources != requested_sources:
        ok_fallback_plain, fallback_plain_tail, fallback_plain_compiled = _attempt(
            fallback_sources,
            use_repo_resolution=False,
        )
        if ok_fallback_plain:
            return True, fallback_plain_tail or compile_tail, fallback_plain_compiled, True
        compile_tail = fallback_plain_tail or compile_tail
        compiled_sources = fallback_plain_compiled if fallback_plain_compiled else compiled_sources

    return False, compile_tail, compiled_sources, False


def _uses_regular_mockito_evosuite_runtime(text: str) -> bool:
    return (
        "org.mockito.Mockito" in text
        # EvoSuite may emit the constructor either through an import or as a
        # fully qualified name.  In both cases it is passed to regular Mockito
        # and therefore needs the unshaded runtime implementation.
        and "ViolatedAssumptionAnswer()" in text
        and "org.evosuite.shaded.org.mockito.stubbing.Answer" not in text
    )


def _filter_evosuite_standalone_runtime(classpath: str) -> str:
    filtered: List[str] = []
    for entry in classpath.split(":"):
        candidate = entry.strip()
        if not candidate:
            continue
        if Path(candidate).name.startswith("evosuite-standalone-runtime-"):
            continue
        filtered.append(candidate)
    return ":".join(filtered)


def _filter_conflicting_slf4j_bindings(classpath: str) -> str:
    entries = [entry.strip() for entry in classpath.split(":") if entry.strip()]
    if not entries:
        return ""

    artifact_keys = {entry: _covfilter_runtime_jar_artifact_key(entry) for entry in entries}
    binding_keys = {
        "logback-classic",
        "log4j-slf4j-impl",
        "log4j-slf4j2-impl",
        "slf4j-simple",
        "slf4j-nop",
        "slf4j-jdk14",
        "slf4j-log4j12",
        "slf4j-reload4j",
    }
    binding_preference = [
        "logback-classic",
        "log4j-slf4j2-impl",
        "log4j-slf4j-impl",
        "slf4j-simple",
        "slf4j-jdk14",
        "slf4j-log4j12",
        "slf4j-reload4j",
        "slf4j-nop",
    ]
    selected_binding = next((key for key in binding_preference if key in artifact_keys.values()), "")
    filtered = entries
    if selected_binding:
        filtered = [
            entry
            for entry in entries
            if artifact_keys.get(entry, "") not in binding_keys or artifact_keys.get(entry, "") == selected_binding
        ]

    # logback 1.2.x is an SLF4J 1.7 backend; force the API family to match.
    logback_entries = [entry for entry in filtered if artifact_keys.get(entry, "") == "logback-classic"]
    logback_version = _covfilter_runtime_jar_version(logback_entries[0]) if logback_entries else ""
    logback_major = _covfilter_runtime_version_major(logback_version)
    logback_minor = _covfilter_runtime_version_minor(logback_version)
    if selected_binding == "log4j-slf4j2-impl" or (logback_major >= 1 and logback_minor >= 3):
        filtered = [
            entry
            for entry in filtered
            if not (
                artifact_keys.get(entry, "") == "slf4j-api"
                and 0 < _covfilter_runtime_version_major(_covfilter_runtime_jar_version(entry)) < 2
            )
        ]
    elif logback_major == 1 and logback_minor <= 2:
        filtered = [
            entry
            for entry in filtered
            if not (
                artifact_keys.get(entry, "") == "slf4j-api"
                and _covfilter_runtime_version_major(_covfilter_runtime_jar_version(entry)) >= 2
            )
        ]
        if not any(artifact_keys.get(entry, "") == "slf4j-api" for entry in filtered):
            slf4j_api_1x = _latest_m2_artifact_jar_for_prefix("org/slf4j", "slf4j-api", "1.7.")
            if not slf4j_api_1x:
                slf4j_api_1x = _m2_artifact_jar("org/slf4j", "slf4j-api", "1.7.36")
            if slf4j_api_1x:
                filtered.append(slf4j_api_1x)
    elif any(artifact_keys.get(entry, "") == "slf4j-api" for entry in filtered):
        preferred_simple = _latest_m2_artifact_jar_for_prefix("org/slf4j", "slf4j-simple", "2.")
        if not preferred_simple:
            preferred_simple = _latest_m2_artifact_jar_for_prefix("org/slf4j", "slf4j-simple", "1.7.")
        if not preferred_simple:
            preferred_simple = _m2_artifact_jar("org/slf4j", "slf4j-simple")
        if preferred_simple:
            filtered.append(preferred_simple)
    return _merge_classpath_strings(":".join(filtered))


def _filter_external_slf4j_bindings(classpath: str) -> str:
    """Drop jar-based providers when the repository supplies its own provider classes."""
    binding_keys = {
        "logback-classic",
        "log4j-slf4j-impl",
        "log4j-slf4j2-impl",
        "slf4j-simple",
        "slf4j-nop",
        "slf4j-jdk14",
        "slf4j-log4j12",
        "slf4j-reload4j",
    }
    return _merge_classpath_strings(
        ":".join(
            entry.strip()
            for entry in classpath.split(":")
            if entry.strip() and _covfilter_runtime_jar_artifact_key(entry.strip()) not in binding_keys
        )
    )


def _sources_use_regular_mockito_evosuite_runtime(sources: List[Path]) -> bool:
    for source in sources:
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _uses_regular_mockito_evosuite_runtime(text):
            return True
    return False


def _covfilter_runtime_jar_artifact_key(entry: str) -> str:
    path = Path(entry)
    if not path.exists() or not path.is_file() or path.suffix != ".jar":
        return ""

    resolved = path.resolve()
    parts = resolved.parts
    if ".m2" in parts and "repository" in parts and resolved.parent.parent.name:
        artifact = resolved.parent.parent.name
    elif ".gradle" in parts and "files-2.1" in parts and resolved.parent.parent.parent.name:
        artifact = resolved.parent.parent.parent.name
    else:
        match = re.match(r"^(?P<artifact>.+)-\d", path.stem)
        artifact = (match.group("artifact") if match else path.stem).lower()

    # RxJava 2 and 3 intentionally coexist and use different Java packages.
    if artifact == "rxjava":
        major = _covfilter_runtime_version_major(_covfilter_runtime_jar_version(entry))
        return f"rxjava-{major}" if major else artifact
    return artifact


def _covfilter_runtime_jar_version(entry: str) -> str:
    path = Path(entry)
    if not path.exists() or not path.is_file() or path.suffix != ".jar":
        return ""

    resolved = path.resolve()
    parts = resolved.parts
    if ".m2" in parts and "repository" in parts:
        return resolved.parent.name
    if ".gradle" in parts and "files-2.1" in parts:
        return resolved.parent.parent.name

    match = re.match(r"^.+-(?P<version>\d[^/]*)$", path.stem)
    return match.group("version") if match else ""


def _covfilter_runtime_version_major(version: str) -> int:
    match = re.match(r"^(\d+)", version or "")
    return int(match.group(1)) if match else 0


def _covfilter_runtime_version_minor(version: str) -> int:
    match = re.match(r"^\d+\.(\d+)", version or "")
    return int(match.group(1)) if match else 0


def _matching_opentelemetry_incubator_jar(classpath: str) -> str:
    """Return the API-incubator jar aligned with the newest SDK on the classpath."""
    sdk_versions: List[str] = []
    for entry in classpath.split(":"):
        name = Path(entry.strip()).name
        match = re.fullmatch(r"opentelemetry-sdk-(\d+\.\d+\.\d+)\.jar", name)
        if match:
            sdk_versions.append(match.group(1))
    if not sdk_versions:
        return ""
    sdk_version = max(sdk_versions, key=_covfilter_runtime_version_sort_key)
    return _m2_artifact_jar(
        "io/opentelemetry",
        "opentelemetry-api-incubator",
        f"{sdk_version}-alpha",
    )


def _covfilter_runtime_jar_preference(entry: str) -> tuple[int, tuple]:
    path = Path(entry)
    if not path.exists() or not path.is_file() or path.suffix != ".jar":
        return (0, ())

    resolved = path.resolve()
    parts = resolved.parts
    version = ""
    repository_rank = 0
    if ".m2" in parts and "repository" in parts:
        repository_rank = 2
        version = resolved.parent.name
    elif ".gradle" in parts and "files-2.1" in parts:
        repository_rank = 1
        version = resolved.parent.parent.name
    else:
        match = re.match(r"^.+-(?P<version>\d[^/]*)$", path.stem)
        if match:
            version = match.group("version")

    return repository_rank, _covfilter_runtime_version_sort_key(version)


def _covfilter_runtime_version_sort_key(version: str) -> tuple:
    parts = re.split(r"([0-9]+)", version or "")
    key = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((1, int(part)))
        else:
            key.append((0, part.lower()))
    return tuple(key)


def _test_framework_runtime_cp(test_src: Path) -> str:
    try:
        text = test_src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    jars: List[str] = []
    seen_jars = set()
    fallback_targets: List[str] = []

    def add_jar(group_path: str, artifact_name: str, version: str = "") -> None:
        jar_path = _m2_artifact_jar(group_path, artifact_name, version)
        if not jar_path or jar_path in seen_jars:
            return
        seen_jars.add(jar_path)
        jars.append(jar_path)

    if "org.evosuite.runtime" in text:
        add_jar("org/utgen", "evosuite-standalone-runtime")
        for artifact_name in ("asm", "asm-commons", "asm-tree", "asm-analysis", "asm-util"):
            add_jar("org/ow2/asm", artifact_name)
        add_jar("org/slf4j", "slf4j-api")
    if "org.testng" in text:
        add_jar("org/testng", "testng")
        add_jar("com/beust", "jcommander")
        fallback_targets.append("org.junit.support.testng.engine.TestNGTestEngine")
    if "org.junit.jupiter" in text:
        add_jar("io/kotest", "kotest-common-jvm")
        add_jar("io/kotest", "kotest-framework-discovery-jvm")
    if "org.hamcrest" in text:
        # Use the dependency declared by the frozen projects rather than the
        # newer split Hamcrest artifacts available elsewhere in ~/.m2.
        add_jar("org/hamcrest", "hamcrest-all", "1.3")
    if "ch.qos.logback" in text:
        add_jar("ch/qos/logback", "logback-core")
        add_jar("ch/qos/logback", "logback-classic")
        add_jar("org/slf4j", "slf4j-api")
        add_jar("org/slf4j", "jul-to-slf4j")
    if "MockitoExtension" in text or "org.mockito.Mock" in text:
        add_jar("org/mockito", "mockito-core")
        add_jar("org/mockito", "mockito-junit-jupiter")
    if "com.codahale.metrics" in text:
        add_jar("io/dropwizard/metrics", "metrics-core")
        add_jar("io/dropwizard/metrics", "metrics-annotation")
        add_jar("io/dropwizard/metrics", "metrics-jersey3")
    if "org.jboss.arquillian.junit.Arquillian" in text:
        fallback_targets.extend(
            [
                "org.jboss.arquillian.junit.Arquillian",
                "org.jboss.arquillian.container.test.api.Deployment",
                "org.jboss.arquillian.core.impl.loadable.LoadableExtensionLoader",
                "org.jboss.arquillian.test.impl.EventTestRunnerAdaptor",
                "org.jboss.shrinkwrap.api.asset.Asset",
                "org.jboss.shrinkwrap.api.ShrinkWrap",
                "org.jboss.shrinkwrap.api.spec.JavaArchive",
            ]
        )
    if "org.glassfish.jersey.test.grizzly" in text:
        add_jar("org/glassfish/grizzly", "grizzly-http")
        add_jar("org/glassfish/grizzly", "grizzly-framework")
    fallback_cp = ""
    if fallback_targets:
        fallback_cp = resolve_external_runtime_classpath_from_output(
            "\n".join(f"ClassNotFoundException: {target.replace('.', '/')}" for target in fallback_targets)
        )
    framework_cp = _merge_classpath_strings(":".join(jars), fallback_cp)
    if _uses_regular_mockito_evosuite_runtime(text):
        framework_cp = _filter_evosuite_standalone_runtime(framework_cp)
    return framework_cp


def _framework_runtime_cp_for_sources(sources: List[Path]) -> str:
    merged = ""
    for source in sources:
        merged = _merge_classpath_strings(merged, _test_framework_runtime_cp(source))
    if _sources_use_regular_mockito_evosuite_runtime(sources):
        merged = _filter_evosuite_standalone_runtime(merged)
    return merged


def _effective_compile_libs_cp_for_sources(
    *,
    libs_glob_cp: str,
    sources: List[Path],
    resolve_imports: bool = True,
) -> str:
    effective_cp = _merge_classpath_strings(
        libs_glob_cp,
        _framework_runtime_cp_for_sources(sources),
        _spring_runtime_cp_for_sources(sources),
        resolve_external_classpath_from_imports(sources) if resolve_imports else "",
    )
    if _sources_use_regular_mockito_evosuite_runtime(sources):
        effective_cp = _filter_evosuite_standalone_runtime(effective_cp)
    return effective_cp


def _latest_matching_dependency_jars(group_path: str, artifact_names: List[str]) -> str:
    jars: List[str] = []
    for artifact_name in artifact_names:
        matches = sorted((Path.home() / ".m2" / "repository" / group_path / artifact_name).glob(f"*/{artifact_name}-*.jar"))
        if matches:
            jars.append(str(matches[-1]))
    return ":".join(jars)


def _latest_m2_artifact_version(group_path: str, artifact_name: str) -> str:
    artifact_root = Path.home() / ".m2" / "repository" / group_path / artifact_name
    if not artifact_root.exists():
        return ""
    versions = sorted(
        (path.name for path in artifact_root.iterdir() if path.is_dir()),
        key=_covfilter_runtime_version_sort_key,
    )
    return versions[-1] if versions else ""


def _latest_m2_artifact_jar_for_prefix(group_path: str, artifact_name: str, version_prefix: str) -> str:
    artifact_root = Path.home() / ".m2" / "repository" / group_path / artifact_name
    if not artifact_root.exists():
        return ""
    versions = [path.name for path in artifact_root.iterdir() if path.is_dir() and path.name.startswith(version_prefix)]
    if not versions:
        return ""
    version = max(versions, key=_covfilter_runtime_version_sort_key)
    matches = sorted((artifact_root / version).glob(f"{artifact_name}-*.jar"))
    return str(matches[-1]) if matches else ""


def _m2_artifact_jar(group_path: str, artifact_name: str, version: str = "") -> str:
    artifact_root = Path.home() / ".m2" / "repository" / group_path / artifact_name
    if not artifact_root.exists():
        return ""
    if version:
        matches = sorted((artifact_root / version).glob(f"{artifact_name}-*.jar"))
        if matches:
            return str(matches[-1])
    version_dirs = sorted(
        (path for path in artifact_root.iterdir() if path.is_dir()),
        key=lambda path: _covfilter_runtime_version_sort_key(path.name),
    )
    for version_dir in reversed(version_dirs):
        matches = sorted(version_dir.glob(f"{artifact_name}-*.jar"))
        if matches:
            return str(matches[-1])
    return ""


def _gradle_artifact_jar(group_id: str, artifact_name: str, version: str) -> str:
    artifact_root = (
        Path.home()
        / ".gradle"
        / "caches"
        / "modules-2"
        / "files-2.1"
        / group_id
        / artifact_name
        / version
    )
    matches = sorted(artifact_root.glob(f"*/{artifact_name}-{version}.jar"))
    return str(matches[-1]) if matches else ""


def _spring_runtime_cp_for_sources(sources: List[Path]) -> str:
    try:
        if not any("org.springframework" in source.read_text(encoding="utf-8", errors="ignore") for source in sources):
            return ""
    except OSError:
        return ""

    spring_artifacts = [
        "spring-aop",
        "spring-beans",
        "spring-context",
        "spring-core",
        "spring-expression",
        "spring-jcl",
        "spring-test",
        "spring-web",
        "spring-webmvc",
    ]
    anchor_version = (
        _latest_m2_artifact_version("org/springframework", "spring-webmvc")
        or _latest_m2_artifact_version("org/springframework", "spring-test")
        or _latest_m2_artifact_version("org/springframework", "spring-context")
    )

    jars: List[str] = []
    seen = set()

    for artifact_name in spring_artifacts:
        jar_path = _m2_artifact_jar("org/springframework", artifact_name, anchor_version)
        if not jar_path:
            jar_path = _m2_artifact_jar("org/springframework", artifact_name)
        if jar_path and jar_path not in seen:
            seen.add(jar_path)
            jars.append(jar_path)

    for group_path, artifact_name in (
        ("jakarta/annotation", "jakarta.annotation-api"),
        ("jakarta/servlet", "jakarta.servlet-api"),
    ):
        jar_path = _m2_artifact_jar(group_path, artifact_name)
        if jar_path and jar_path not in seen:
            seen.add(jar_path)
            jars.append(jar_path)

    return ":".join(jars)


def _classpath_dir_entries(classpath: str) -> List[Path]:
    entries: List[Path] = []
    seen = set()
    for raw_entry in classpath.split(":"):
        entry = raw_entry.strip()
        if not entry:
            continue
        path = Path(entry)
        if not path.exists() or not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        entries.append(resolved)
    return entries


def _classpath_jar_entries(classpath: str) -> List[Path]:
    entries: List[Path] = []
    seen = set()
    for raw_entry in classpath.split(":"):
        entry = raw_entry.strip()
        if not entry:
            continue
        path = Path(entry)
        if not path.exists() or not path.is_file() or path.suffix != ".jar":
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        entries.append(resolved)
    return entries


def _covfilter_safe_jar_name(path: Path, *, prefix: str = "extra") -> str:
    stem = "".join(ch if ch.isalnum() else "_" for ch in path.stem).strip("_") or "dir"
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{stem}_{digest}.jar"


def _is_covfilter_main_output_dir(path: Path) -> bool:
    normalized = path.as_posix().rstrip("/")
    return normalized.endswith(
        (
            "/target/classes",
            "/target/generated-classes",
            "/target/generated-classes/jacoco",
            "/build/classes/java/main",
            "/build/classes/kotlin/main",
            "/build/resources/main",
        )
    )


def _is_covfilter_test_output_dir(path: Path) -> bool:
    normalized = path.as_posix().rstrip("/")
    return normalized.endswith(
        (
            "/target/test-classes",
            "/build/classes/java/test",
            "/build/classes/kotlin/test",
            "/build/resources/test",
        )
    )


def _looks_like_hazelcast_runtime_entry(entry: str) -> bool:
    lowered = entry.lower()
    artifact_key = _covfilter_runtime_jar_artifact_key(entry).lower()
    return (
        artifact_key.startswith("hazelcast")
        or "/com/hazelcast/" in lowered
        or "hazelcast-" in lowered
        or "/hazelcast/" in lowered
    )


def _filter_covfilter_runtime_classpath(
    classpath: str,
    *,
    drop_main_output_dirs: bool = False,
    drop_test_output_dirs: bool = False,
    drop_hazelcast_entries: bool = False,
) -> str:
    filtered: List[str] = []
    for raw_entry in classpath.split(":"):
        entry = raw_entry.strip()
        if not entry:
            continue
        if drop_hazelcast_entries and _looks_like_hazelcast_runtime_entry(entry):
            continue
        path = Path(entry)
        if path.exists() and path.is_dir():
            resolved = path.resolve()
            if drop_main_output_dirs and _is_covfilter_main_output_dir(resolved):
                continue
            if drop_test_output_dirs and _is_covfilter_test_output_dir(resolved):
                continue
        filtered.append(entry)
    return _merge_classpath_strings(":".join(filtered))


def _should_skip_covfilter_runtime_entry(entry_name: str) -> bool:
    normalized = entry_name.replace("\\", "/").lstrip("/")
    lowered = normalized.lower()
    if not normalized:
        return True
    if lowered.endswith(".scl.lombok") or lowered.startswith("scl.lombok/") or "/scl.lombok/" in lowered:
        return True
    if lowered.startswith("meta-inf/license/") or lowered.startswith("meta-inf/licenses/"):
        return True
    if lowered.startswith("meta-inf/"):
        base = lowered.rsplit("/", 1)[-1]
        if base in {
            "license",
            "license.txt",
            "license.md",
            "license-notice.md",
            "notice",
            "notice.txt",
            "dependencies",
            "index.list",
            "al2.0",
            "lgpl2.1",
        }:
            return True
        if base.endswith((".sf", ".rsa", ".dsa", ".ec")):
            return True
    return False


def _write_sanitized_covfilter_jar(*, source: Path, target: Path) -> None:
    ensure_dir(target.parent)
    seen_entries = set()
    with zipfile.ZipFile(source) as input_jar, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output_jar:
        for entry in input_jar.infolist():
            if entry.is_dir():
                continue
            entry_name = entry.filename.replace("\\", "/").lstrip("/")
            if _should_skip_covfilter_runtime_entry(entry_name) or entry_name in seen_entries:
                continue
            seen_entries.add(entry_name)
            # Some locally built shaded test artifacts intentionally overlap
            # relocated entry byte ranges. Python warns once per entry even
            # though ZipInfo-based reads are deterministic here; keep logs
            # bounded while preserving the exact local bytes.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=r"Overlapped entries:.*")
                content = input_jar.read(entry)
            output_jar.writestr(entry_name, content)


def _is_slf4j_provider_entry(entry_name: str) -> bool:
    lowered = entry_name.replace("\\", "/").lstrip("/").lower()
    return lowered in {
        "org/slf4j/impl/staticloggerbinder.class",
        "org/slf4j/impl/staticmdcbinder.class",
        "org/slf4j/impl/staticmarkerbinder.class",
        "meta-inf/services/org.slf4j.spi.slf4jserviceprovider",
    }


def _write_directory_as_covfilter_jar(*, source_dir: Path, target: Path) -> None:
    if _is_covfilter_test_output_dir(source_dir.resolve()):
        target.unlink(missing_ok=True)
        return
    ensure_dir(target.parent)
    seen_entries = set()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output_jar:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            rel_name = path.relative_to(source_dir).as_posix()
            if rel_name.startswith(".agt-source-stamps/"):
                continue
            if _is_slf4j_provider_entry(rel_name):
                continue
            if _should_skip_covfilter_runtime_entry(rel_name) or rel_name in seen_entries:
                continue
            seen_entries.add(rel_name)
            output_jar.write(path, arcname=rel_name)


def _class_internal_name_from_bytes(data: bytes) -> str:
    try:
        if len(data) < 10 or data[:4] != b"\xCA\xFE\xBA\xBE":
            return ""
        cp_count = int.from_bytes(data[8:10], "big")
        idx = 10
        utf8_entries: dict[int, str] = {}
        class_name_index: dict[int, int] = {}
        cp_index = 1
        while cp_index < cp_count:
            tag = data[idx]
            idx += 1
            if tag == 1:  # CONSTANT_Utf8
                length = int.from_bytes(data[idx:idx + 2], "big")
                idx += 2
                utf8_entries[cp_index] = data[idx:idx + length].decode("utf-8", errors="replace")
                idx += length
            elif tag in (3, 4):  # Integer, Float
                idx += 4
            elif tag in (5, 6):  # Long, Double
                idx += 8
                cp_index += 1
            elif tag == 7:  # Class
                class_name_index[cp_index] = int.from_bytes(data[idx:idx + 2], "big")
                idx += 2
            elif tag in (8, 16, 19, 20):  # String, MethodType, Module, Package
                idx += 2
            elif tag in (9, 10, 11, 12, 17, 18):  # refs, NameAndType, Dynamic, InvokeDynamic
                idx += 4
            elif tag == 15:  # MethodHandle
                idx += 3
            else:
                return ""
            cp_index += 1
        if idx + 6 > len(data):
            return ""
        this_class_idx = int.from_bytes(data[idx + 2:idx + 4], "big")
        name_idx = class_name_index.get(this_class_idx, 0)
        internal_name = utf8_entries.get(name_idx, "").strip()
        return internal_name.replace(".", "/")
    except (IndexError, ValueError, UnicodeDecodeError):
        return ""


def _class_internal_name_from_file(path: Path) -> str:
    try:
        return _class_internal_name_from_bytes(path.read_bytes())
    except OSError:
        return ""


def _canonical_covfilter_class_rel_name(rel_name: str, *, class_bytes: Optional[bytes] = None, class_path: Optional[Path] = None) -> str:
    if not rel_name.endswith(".class"):
        return rel_name
    internal_name = ""
    if class_bytes is not None:
        internal_name = _class_internal_name_from_bytes(class_bytes)
    elif class_path is not None:
        internal_name = _class_internal_name_from_file(class_path)
    if not internal_name:
        return rel_name
    return f"{internal_name}.class"


def _merge_covfilter_class_dirs(
    *,
    source_dirs: List[Path],
    merged_dir: Path,
    package_prefix: str = "",
) -> Optional[Path]:
    if not source_dirs:
        return None
    if merged_dir.exists():
        shutil.rmtree(merged_dir, ignore_errors=True)
    ensure_dir(merged_dir)
    copied_any = False
    selected_versions: dict[Path, int] = {}
    java_feature_version = _covfilter_java_feature_version()
    for source_dir in source_dirs:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or path.suffix != ".class":
                continue
            rel_name, version = _normalized_covfilter_sut_entry_name(path.relative_to(source_dir).as_posix())
            rel_name = _canonical_covfilter_class_rel_name(rel_name, class_path=path)
            if package_prefix and not rel_name.startswith(package_prefix):
                continue
            if version > java_feature_version:
                continue
            target = merged_dir / Path(rel_name)
            previous_version = selected_versions.get(target, -1)
            # Match JVM/JAR lookup semantics for duplicate entries: the first
            # entry at a given multi-release version wins.  Replacing it with a
            # later duplicate can make JaCoCo analyze a different class ID than
            # the one the forked JVM actually executes.
            if previous_version >= version:
                continue
            source_is_instrumented = _covfilter_class_is_instrumented(path)
            if target.exists():
                target_is_instrumented = _covfilter_class_is_instrumented(target)
                if version > previous_version or (target_is_instrumented and not source_is_instrumented):
                    shutil.copy2(path, target)
                    selected_versions[target] = version
                    copied_any = True
                continue
            ensure_dir(target.parent)
            shutil.copy2(path, target)
            selected_versions[target] = version
            copied_any = True
    return merged_dir if copied_any else None


def _covfilter_class_is_instrumented(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return b"$jacocoData" in data or b"$jacocoInit" in data


def _augment_covfilter_source_dirs(source_dirs: List[Path]) -> List[Path]:
    augmented: List[Path] = []
    seen = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if not path.is_dir() or resolved in seen:
            return
        seen.add(resolved)
        augmented.append(path)

    for source_dir in source_dirs:
        add(source_dir)
        if source_dir.name == "classes" and source_dir.parent.name == "target":
            add(source_dir.parent / "generated-classes")
            add(source_dir.parent / "generated-classes" / "jacoco")
    return augmented


def _normalized_covfilter_sut_entry_name(entry_name: str) -> tuple[str, int]:
    normalized = entry_name.replace("\\", "/").lstrip("/")
    for prefix in ("BOOT-INF/classes/", "WEB-INF/classes/", "APP-INF/classes/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    if normalized.startswith("META-INF/versions/"):
        remainder = normalized[len("META-INF/versions/") :]
        version_text, _, rel_name = remainder.partition("/")
        if version_text.isdigit() and rel_name:
            return rel_name, int(version_text)
    return normalized, 0


def _covfilter_package_prefix_from_fqcn(fqcn: str) -> str:
    package_name, _, _ = (fqcn or "").rpartition(".")
    if not package_name:
        return ""
    return "/".join(part for part in package_name.split(".") if part) + "/"


def _covfilter_has_multi_release_layout(path: Path) -> bool:
    versions_dir = path / "META-INF" / "versions"
    return versions_dir.is_dir() and any(versions_dir.rglob("*.class"))


def _extract_covfilter_sut_classes(
    *,
    sut_jar: Path,
    target_dir: Path,
    package_prefix: str = "",
    excluded_classes: Optional[Set[str]] = None,
) -> Path:
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    ensure_dir(target_dir)
    selected_versions: dict[str, int] = {}
    excluded = {entry.replace("\\", "/").lstrip("/") for entry in (excluded_classes or set())}
    java_feature_version = _covfilter_java_feature_version()
    with zipfile.ZipFile(sut_jar) as jar_file:
        for entry in jar_file.infolist():
            if entry.is_dir():
                continue
            entry_name, version = _normalized_covfilter_sut_entry_name(entry.filename)
            lowered = entry_name.lower()
            is_class = lowered.endswith(".class")
            is_service_resource = lowered.startswith("meta-inf/services/")
            is_general_resource = not is_class and not lowered.startswith("meta-inf/")
            is_embedded_archive = lowered.endswith((".jar", ".zip", ".war", ".ear"))
            if (
                not entry_name
                or version > java_feature_version
                or (not is_class and not is_service_resource and not is_general_resource)
                or (is_general_resource and is_embedded_archive)
                or (lowered.startswith("meta-inf/") and not is_service_resource)
                or lowered.endswith(".scl.lombok")
                or lowered.startswith("scl.lombok/")
                or "/scl.lombok/" in lowered
            ):
                continue
            if is_class and entry_name in excluded:
                continue
            if is_class and package_prefix and not entry_name.startswith(package_prefix):
                continue
            content = jar_file.read(entry)
            canonical_entry_name = _canonical_covfilter_class_rel_name(
                entry_name,
                class_bytes=content if is_class else None,
            )
            if is_class and canonical_entry_name in excluded:
                continue
            previous_version = selected_versions.get(canonical_entry_name, -1)
            # A JAR can contain duplicate paths.  ClassLoader resolves the
            # first equal-version entry, so retain it here as well; otherwise
            # JaCoCo may analyze a different class ID than the fork executes.
            if previous_version >= version:
                continue
            selected_versions[canonical_entry_name] = version
            target = target_dir / canonical_entry_name
            ensure_dir(target.parent)
            target.write_bytes(content)
    return target_dir


def _prepare_covfilter_test_classes_dir(
    *,
    compiled_test_classes_dir: Path,
    extra_runtime_cp: str,
    merged_dir: Path,
    test_resource_dirs: Optional[Sequence[Path]] = None,
) -> Path:
    source_dirs: List[Path] = []
    seen_dirs = set()
    for candidate in [compiled_test_classes_dir, *_augment_covfilter_source_dirs(_classpath_dir_entries(extra_runtime_cp))]:
        if not candidate.exists() or not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in seen_dirs:
            continue
        seen_dirs.add(resolved)
        source_dirs.append(candidate)
    if len(source_dirs) < 2:
        return compiled_test_classes_dir
    merged = _merge_covfilter_class_dirs(source_dirs=source_dirs, merged_dir=merged_dir)
    if merged is None:
        return compiled_test_classes_dir

    # Keep test resources on a directory classpath. Tests frequently convert a
    # resource URL to a File, which fails after the resource is repackaged into
    # the runtime-libs jars as a jar:file URL.
    resource_dirs = (
        list(test_resource_dirs)
        if test_resource_dirs is not None
        else [source_dir for source_dir in source_dirs if _is_covfilter_test_output_dir(source_dir)]
    )
    for source_dir in resource_dirs:
        if not source_dir.exists() or not source_dir.is_dir():
            continue
        if not _is_covfilter_test_output_dir(source_dir):
            continue
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or path.suffix == ".class":
                continue
            rel_path = path.relative_to(source_dir)
            if rel_path.as_posix().startswith(".agt-source-stamps/"):
                continue
            target = merged / rel_path
            if target.exists():
                continue
            ensure_dir(target.parent)
            shutil.copy2(path, target)
    return merged


def _covfilter_target_test_resource_dirs(module_dir: Optional[Path]) -> List[Path]:
    if module_dir is None:
        return []
    candidates = [
        module_dir / "target" / "test-classes",
        module_dir / "build" / "resources" / "test",
    ]
    return [candidate for candidate in candidates if candidate.exists() and candidate.is_dir()]


def _covfilter_dir_contains(path: Path, pattern: str) -> bool:
    return path.is_dir() and any(path.rglob(pattern))


def _prepare_covfilter_libs_dir(
    *,
    base_libs_dir: Path,
    extra_runtime_cp: str,
    merged_dir: Path,
) -> Path:
    extra_jars = _classpath_jar_entries(extra_runtime_cp)
    # Test outputs are merged into the direct test-classes directory. Packaging
    # them again here makes jar:file resources shadow the filesystem resources.
    extra_dirs = [
        path
        for path in _classpath_dir_entries(extra_runtime_cp)
        if not _is_covfilter_test_output_dir(path)
    ]
    if not extra_jars and not extra_dirs:
        return base_libs_dir

    if merged_dir.exists():
        shutil.rmtree(merged_dir, ignore_errors=True)
    ensure_dir(merged_dir)

    seen_names = set()
    seen_artifacts = set()
    for jar_file in sorted(base_libs_dir.glob("*.jar")):
        target = merged_dir / jar_file.name
        shutil.copy2(jar_file, target)
        seen_names.add(jar_file.name)
        artifact_key = _covfilter_runtime_jar_artifact_key(str(jar_file))
        if artifact_key:
            seen_artifacts.add(artifact_key)

    for extra_dir in extra_dirs:
        target_name = _covfilter_safe_jar_name(extra_dir, prefix="extra_dir")
        while target_name in seen_names:
            target_name = _covfilter_safe_jar_name(extra_dir / target_name, prefix="extra_dir")
        _write_directory_as_covfilter_jar(source_dir=extra_dir, target=merged_dir / target_name)
        seen_names.add(target_name)

    for jar_file in extra_jars:
        artifact_key = _covfilter_runtime_jar_artifact_key(str(jar_file))
        if artifact_key and artifact_key in seen_artifacts:
            # ``base_libs_dir`` is a broad compatibility cache and can contain
            # a newer, incompatible version (for example Spring 7) than the
            # target module was compiled against.  The repository-resolved
            # runtime classpath is authoritative, so replace its artifact
            # rather than silently keeping the generic cached copy.
            for existing in list(merged_dir.glob("*.jar")):
                if _covfilter_runtime_jar_artifact_key(str(existing)) == artifact_key:
                    existing.unlink(missing_ok=True)
                    seen_names.discard(existing.name)
            seen_artifacts.discard(artifact_key)
        target_name = jar_file.name
        if target_name in seen_names:
            target_name = _covfilter_safe_jar_name(jar_file, prefix="extra")
        _write_sanitized_covfilter_jar(source=jar_file, target=merged_dir / target_name)
        seen_names.add(target_name)
        if artifact_key:
            seen_artifacts.add(artifact_key)

    _prune_covfilter_duplicate_artifacts(merged_dir)
    _ensure_slf4j_binding_present(merged_dir)
    return merged_dir


def _ensure_covfilter_extra_jars_present(*, libs_dir: Path, extra_runtime_cp: str) -> None:
    if not libs_dir.exists() or not libs_dir.is_dir():
        return
    existing_names = {path.name for path in libs_dir.glob("*.jar")}
    existing_artifacts = {
        artifact_key
        for artifact_key in (_covfilter_runtime_jar_artifact_key(str(path)) for path in libs_dir.glob("*.jar"))
        if artifact_key
    }
    for jar_file in _classpath_jar_entries(extra_runtime_cp):
        artifact_key = _covfilter_runtime_jar_artifact_key(str(jar_file))
        if artifact_key and artifact_key in existing_artifacts:
            continue
        target_name = jar_file.name
        if target_name in existing_names:
            continue
        _write_sanitized_covfilter_jar(source=jar_file, target=libs_dir / target_name)
        existing_names.add(target_name)
        if artifact_key:
            existing_artifacts.add(artifact_key)
    _prune_covfilter_duplicate_artifacts(libs_dir)
    _ensure_slf4j_binding_present(libs_dir)


def _prune_covfilter_duplicate_artifacts(libs_dir: Path) -> None:
    candidates: dict[str, list[Path]] = {}
    for jar_file in libs_dir.glob("*.jar"):
        artifact_key = _covfilter_runtime_jar_artifact_key(str(jar_file))
        if not artifact_key:
            continue
        candidates.setdefault(artifact_key, []).append(jar_file)

    for paths in candidates.values():
        if len(paths) < 2:
            continue
        winner = max(paths, key=lambda path: _covfilter_runtime_jar_preference(str(path)))
        for jar_file in paths:
            if jar_file == winner:
                continue
            jar_file.unlink(missing_ok=True)


def _jar_contains_slf4j_provider(jar_path: Path) -> bool:
    try:
        with zipfile.ZipFile(jar_path) as handle:
            for entry_name in handle.namelist():
                if _is_slf4j_provider_entry(entry_name):
                    return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def _jar_contains_legacy_slf4j_binder(jar_path: Path) -> bool:
    try:
        with zipfile.ZipFile(jar_path) as handle:
            return "org/slf4j/impl/StaticLoggerBinder.class" in handle.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _ensure_slf4j_binding_present(libs_dir: Path) -> None:
    if not libs_dir.exists() or not libs_dir.is_dir():
        return

    jars = list(libs_dir.glob("*.jar"))

    needs_legacy_binding = any(path.name.startswith("slf4j-api-1.7.") for path in jars) or any(
        "evosuite-standalone-runtime" in path.name for path in jars
    )
    if needs_legacy_binding and not any(_jar_contains_legacy_slf4j_binder(path) for path in jars):
        candidate_legacy = _latest_m2_artifact_jar_for_prefix("org/slf4j", "slf4j-simple", "1.7.")
        if candidate_legacy:
            source = Path(candidate_legacy)
            target = libs_dir / source.name
            if not target.exists():
                _write_sanitized_covfilter_jar(source=source, target=target)
            jars.append(target)

    has_logback_core = any(path.name.startswith("logback-core-") for path in libs_dir.glob("*.jar"))
    has_logback_classic = any(path.name.startswith("logback-classic-") for path in libs_dir.glob("*.jar"))
    if has_logback_core and not has_logback_classic:
        candidate_classic = _m2_artifact_jar("ch/qos/logback", "logback-classic")
        if candidate_classic:
            source = Path(candidate_classic)
            target = libs_dir / source.name
            if not target.exists():
                _write_sanitized_covfilter_jar(source=source, target=target)
            for simple_jar in libs_dir.glob("slf4j-simple-*.jar"):
                simple_jar.unlink(missing_ok=True)

    if any(_jar_contains_slf4j_provider(path) for path in libs_dir.glob("*.jar")):
        return

    candidate = _latest_m2_artifact_jar_for_prefix("org/slf4j", "slf4j-simple", "2.")
    if not candidate:
        candidate = _latest_m2_artifact_jar_for_prefix("org/slf4j", "slf4j-simple", "1.7.")
    if not candidate:
        candidate = _m2_artifact_jar("org/slf4j", "slf4j-simple")
    if not candidate:
        return

    source = Path(candidate)
    target = libs_dir / source.name
    if target.exists():
        return
    _write_sanitized_covfilter_jar(source=source, target=target)


def _install_jetty_slf4j_provider_service(libs_dir: Path) -> None:
    """Use Jetty's compiled provider instead of a generic cached binding."""
    provider_bindings = (
        "logback-classic-*.jar",
        "log4j-slf4j-impl-*.jar",
        "log4j-slf4j2-impl-*.jar",
        "slf4j-simple-*.jar",
        "slf4j-nop-*.jar",
        "slf4j-jdk14-*.jar",
        "slf4j-log4j12-*.jar",
        "slf4j-reload4j-*.jar",
    )
    for pattern in provider_bindings:
        for provider_jar in libs_dir.glob(pattern):
            provider_jar.unlink(missing_ok=True)

    service_jar = libs_dir / "jetty-native-slf4j-provider-service.jar"
    with zipfile.ZipFile(service_jar, "w", compression=zipfile.ZIP_DEFLATED) as output_jar:
        output_jar.writestr(
            "META-INF/services/org.slf4j.spi.SLF4JServiceProvider",
            "org.eclipse.jetty.logging.JettyLoggingServiceProvider\n",
        )


def _prepare_covfilter_sut_classes_dir(
    *,
    sut_classes_input: Path,
    extra_runtime_cp: str,
    merged_dir: Path,
    prefer_sut_input_only: bool = False,
    target_package_prefix: str = "",
    excluded_classes: Optional[Set[str]] = None,
) -> Path:
    sut_input_dir: Optional[Path] = None
    if sut_classes_input.is_file():
        if sut_classes_input.suffix.lower() == ".jar":
            try:
                sut_input_dir = _extract_covfilter_sut_classes(
                    sut_jar=sut_classes_input,
                    target_dir=merged_dir.with_name(f"{merged_dir.name}-sut-input"),
                    package_prefix=target_package_prefix,
                    excluded_classes=excluded_classes,
                )
            except (FileNotFoundError, OSError, zipfile.BadZipFile):
                print(f"[agt] covfilter: Ignore invalid sut jar input: {sut_classes_input}")
        else:
            print(f"[agt] covfilter: Ignore non-jar sut file input: {sut_classes_input}")
    elif sut_classes_input.is_dir():
        sut_input_dir = sut_classes_input

    if not prefer_sut_input_only:
        main_output_dirs = _augment_covfilter_source_dirs(
            [path for path in _classpath_dir_entries(extra_runtime_cp) if _is_covfilter_main_output_dir(path)]
        )
        # ForkedJacocoRunner resolves an explicit SUT JAR before supplementary
        # repository output directories. Preserve that same precedence while
        # staging analyzer inputs; otherwise duplicate FQCNs can make JaCoCo
        # analyze a different class ID than the test JVM executes.
        source_dirs = (
            [sut_input_dir, *main_output_dirs]
            if sut_classes_input.is_file() and sut_input_dir is not None
            else [*main_output_dirs, *([sut_input_dir] if sut_input_dir is not None else [])]
        )
        merged_main_dir = _merge_covfilter_class_dirs(
            source_dirs=source_dirs,
            merged_dir=merged_dir,
            package_prefix=target_package_prefix,
        )
        if merged_main_dir is not None:
            return merged_main_dir

    if sut_input_dir is not None:
        sanitized_dir = _merge_covfilter_class_dirs(
            source_dirs=[sut_input_dir],
            merged_dir=merged_dir,
            package_prefix=target_package_prefix,
        )
        selected_dir = sanitized_dir if sanitized_dir is not None else sut_input_dir
        if excluded_classes:
            for rel_path in excluded_classes:
                (selected_dir / rel_path).unlink(missing_ok=True)
        return selected_dir
    ensure_dir(merged_dir)
    return merged_dir


def _matching_covfilter_execution_classes_dir(
    analysis_classes_dir: Path,
    runtime_entries: Sequence[str],
    target_fqcn: str,
) -> Path:
    """Use a coherent project output only when all analyzed CUT bytes match."""
    relative_outer = Path(*target_fqcn.split(".")).with_suffix(".class")
    analysis_outer = analysis_classes_dir / relative_outer
    if not analysis_outer.is_file():
        return analysis_classes_dir

    family = sorted(analysis_outer.parent.glob(f"{analysis_outer.stem}*.class"))
    for raw_entry in runtime_entries:
        candidate = Path(raw_entry)
        if not candidate.is_dir():
            continue
        try:
            if all(
                (candidate / class_file.relative_to(analysis_classes_dir)).is_file()
                and (candidate / class_file.relative_to(analysis_classes_dir)).read_bytes()
                == class_file.read_bytes()
                for class_file in family
            ):
                return candidate
        except OSError:
            continue
    return analysis_classes_dir


def _compiled_test_class_exists(classes_dir: Path, fqcn: str) -> bool:
    if not fqcn:
        return False
    class_file = (classes_dir / Path(*fqcn.split("."))).with_suffix(".class")
    return class_file.exists()


def _compiled_sources_are_fresh(classes_dir: Path, source_files: List[Path]) -> bool:
    for source_file in source_files:
        try:
            pkg, cls = parse_package_and_class(source_file)
        except Exception:
            continue
        if not cls:
            continue
        rel_parts = [part for part in pkg.split(".") if part]
        class_file = classes_dir.joinpath(*rel_parts, f"{cls}.class")
        if not class_file.exists():
            return False
        try:
            if class_file.stat().st_mtime_ns < source_file.stat().st_mtime_ns:
                return False
        except OSError:
            return False
    return True


def _count_csv_rows(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            return sum(1 for _ in reader)
    except OSError:
        return 0


def _covfilter_output_counts(out_dir: Path) -> tuple[int, int, int]:
    return (
        _count_csv_rows(out_dir / "test_deltas_all.csv"),
        _count_csv_rows(out_dir / "test_deltas_kept.csv"),
        _count_csv_rows(out_dir / "line_deltas_kept.csv"),
    )


def _duplicate_class_simple_name(compile_output: str) -> str:
    import re

    match = re.search(r"duplicate class:\s+(?:[A-Za-z_][A-Za-z0-9_$.]*\.)?([A-Za-z_][A-Za-z0-9_]*)", compile_output or "")
    return match.group(1) if match else ""


@dataclass(frozen=True)
class AutoCovfilterCandidate:
    label: str
    pair: Optional[EvoSuitePair]
    source_files: List[Path]
    generated_test_fqcn: str
    build_dir: Path
    out_dir: Path
    log_file: Path
    compile_log: Path


@dataclass(frozen=True)
class AutoCovfilterResult:
    candidate: AutoCovfilterCandidate
    status: str
    problem_category: str
    problem_detail: str

    @property
    def label(self) -> str:
        return self.candidate.label

    @property
    def generated_test_fqcn(self) -> str:
        return self.candidate.generated_test_fqcn

    @property
    def out_dir(self) -> Path:
        return self.candidate.out_dir

    @property
    def log_file(self) -> Path:
        return self.candidate.log_file

    @property
    def compile_log(self) -> Path:
        return self.candidate.compile_log

    @property
    def pair(self) -> Optional[EvoSuitePair]:
        return self.candidate.pair

    def counts(self) -> tuple[int, int, int]:
        return _covfilter_output_counts(self.out_dir)


def _pair_source_files(pair: Optional[EvoSuitePair]) -> List[Path]:
    if pair is None:
        return []
    files = [pair.test_src]
    if pair.scaffolding_src is not None and pair.scaffolding_src.exists():
        files.append(pair.scaffolding_src)
    return files


def _materialize_sanitized_pair_for_ctx(pipeline, ctx: "TargetContext") -> Optional[EvoSuitePair]:
    current_generated_fqcn = (ctx.generated_test_fqcn or "").strip()
    if not current_generated_fqcn:
        return None
    generated_test_fqcn = variant_test_fqcn(current_generated_fqcn, "baseline").strip()
    sanitized_root = Path(pipeline.args.sanitized_es_dir)
    clear_pair_root(sanitized_root, ctx.repo, ctx.fqcn)
    return materialize_sanitized_pair(
        source_root=pipeline.generated_dir,
        sanitized_root=sanitized_root,
        repo=ctx.repo,
        fqcn=ctx.fqcn,
        test_fqcn=generated_test_fqcn,
    )


def _replace_generated_sources(ctx: "TargetContext", pair: EvoSuitePair) -> None:
    generated_sources = _pair_source_files(pair)
    combined = list(ctx.manual_sources) + generated_sources
    deduped: List[Path] = []
    seen = set()
    for source in combined:
        if source in seen:
            continue
        seen.add(source)
        deduped.append(source)
    ctx.sources = list(deduped)
    ctx.final_sources = list(deduped)
    ctx.generated_test_fqcn = pair.test_fqcn


def _adopted_covfilter_source_sets(ctx: "TargetContext", adopted_src: Path) -> tuple[List[Path], List[Path]]:
    """Prefer the frozen manual source so repository test output cannot become the baseline."""
    supporting_sources: List[Path] = []
    text = (
        adopted_src.read_text(encoding="utf-8", errors="ignore")
        if adopted_src.is_file()
        else ""
    )
    scaffold = re.search(r"\bextends\s+([A-Za-z_$][A-Za-z0-9_$]*_scaffolding)\b", text)
    if scaffold:
        scaffold_source = adopted_src.with_name(f"{scaffold.group(1)}.java")
        if scaffold_source.is_file():
            supporting_sources.append(scaffold_source)
    primary = [*ctx.manual_sources, adopted_src, *supporting_sources]
    fallback = [adopted_src, *supporting_sources]
    return primary, fallback


def _copy_covfilter_result_artifacts(result: AutoCovfilterResult, *, target_out_dir: Path, target_log_file: Path, target_compile_log: Path) -> None:
    if target_out_dir.exists():
        shutil.rmtree(target_out_dir, ignore_errors=True)
    if result.out_dir.exists():
        shutil.copytree(result.out_dir, target_out_dir)
    if result.log_file.exists():
        ensure_dir(target_log_file.parent)
        shutil.copy2(result.log_file, target_log_file)
    if result.compile_log.exists():
        ensure_dir(target_compile_log.parent)
        shutil.copy2(result.compile_log, target_compile_log)


def _append_sanitize_compare_row(
    *,
    csv_path: Path,
    ctx: "TargetContext",
    baseline: AutoCovfilterResult,
    sanitized: AutoCovfilterResult,
    selected_variant: str,
    selection_reason: str,
    selected_pair: Optional[EvoSuitePair],
    selected_out_dir: Path,
    selected_log_file: Path,
) -> None:
    baseline_all, baseline_kept, baseline_lines = baseline.counts()
    sanitized_all, sanitized_kept, sanitized_lines = sanitized.counts()
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                ctx.repo,
                ctx.fqcn,
                baseline.status,
                baseline.problem_category,
                baseline.problem_detail,
                baseline.generated_test_fqcn,
                baseline_all,
                baseline_kept,
                baseline_lines,
                str(baseline.out_dir),
                str(baseline.log_file),
                sanitized.status,
                sanitized.problem_category,
                sanitized.problem_detail,
                sanitized.generated_test_fqcn,
                sanitized_all,
                sanitized_kept,
                sanitized_lines,
                str(sanitized.out_dir),
                str(sanitized.log_file),
                selected_variant,
                selection_reason,
                selected_pair.test_fqcn if selected_pair else "",
                str(selected_pair.test_src) if selected_pair else "",
                str(selected_out_dir),
                str(selected_log_file),
            ]
        )


def _select_auto_covfilter_result(
    baseline: AutoCovfilterResult,
    sanitized: AutoCovfilterResult,
) -> tuple[AutoCovfilterResult, str]:
    if sanitized.status == "passed":
        return sanitized, "prefer_sanitized"
    if baseline.status == "passed":
        return baseline, "fallback_to_baseline"
    if sanitized.pair is not None:
        return sanitized, "sanitized_failed_without_baseline_recovery"
    if baseline.pair is not None:
        return baseline, "baseline_failed_last_fallback"
    return sanitized, "missing_generated_variant"


def _run_auto_covfilter_candidate(
    *,
    pipeline,
    ctx: "TargetContext",
    candidate: AutoCovfilterCandidate,
    cov_classes_dir: Path,
) -> AutoCovfilterResult:
    if candidate.build_dir.exists():
        shutil.rmtree(candidate.build_dir, ignore_errors=True)
    ensure_dir(candidate.build_dir)
    if candidate.out_dir.exists():
        shutil.rmtree(candidate.out_dir, ignore_errors=True)

    if not candidate.generated_test_fqcn or candidate.pair is None:
        return AutoCovfilterResult(
            candidate=candidate,
            status="skipped",
            problem_category="missing_generated_test_fqcn",
            problem_detail="Generated test variant is missing.",
        )

    ok_compile, compile_tail, compiled_sources, used_repo_manual_fallback = (
        _compile_covfilter_sources_with_manual_fallback(
            source_files=candidate.source_files,
            build_dir=candidate.build_dir,
            libs_glob_cp=pipeline.args.libs_cp,
            sut_jar=ctx.sut_jar,
            log_file=candidate.compile_log,
            repo_root_for_deps=ctx.repo_root_for_deps,
            module_rel=ctx.module_rel,
            build_tool=ctx.build_tool,
            max_rounds=pipeline.args.dep_rounds,
            allow_commenting=pipeline.args.covfilter_comment_compile_errors,
        )
    )
    if used_repo_manual_fallback and ok_compile:
        print(
            f'[agt] covfilter: compile retry used repo manual sources: repo="{ctx.repo}" fqcn="{ctx.fqcn}" candidate="{candidate.label}"'
        )

    test_classes_dir = candidate.build_dir
    if not ok_compile:
        fallback_classes_dir = ctx.target_build
        has_manual_class = _compiled_test_class_exists(fallback_classes_dir, ctx.manual_test_fqcn or "")
        has_generated_class = _compiled_test_class_exists(fallback_classes_dir, candidate.generated_test_fqcn)
        if has_manual_class and has_generated_class:
            print(
                f'[agt] covfilter: compile retry falling back to precompiled classes: repo="{ctx.repo}" fqcn="{ctx.fqcn}" candidate="{candidate.label}"'
            )
            test_classes_dir = fallback_classes_dir
            compiled_sources = ctx.final_sources
        else:
            return AutoCovfilterResult(
                candidate=candidate,
                status="skipped",
                problem_category="compile_failed",
                problem_detail=compile_tail,
            )

    ok_cov, cov_tail = _run_covfilter_with_retries(
        pipeline=pipeline,
        ctx=ctx,
        variant=f"compare-{candidate.label}",
        source_files=compiled_sources,
        coverage_filter_jar=pipeline.covfilter_jar,
        libs_glob_cp=pipeline.args.libs_cp,
        test_classes_dir=test_classes_dir,
        sut_classes_dir=cov_classes_dir,
        out_dir=candidate.out_dir,
        manual_test_fqcn=ctx.manual_test_fqcn or "",
        generated_test_fqcn=candidate.generated_test_fqcn,
        jacoco_agent_jar=pipeline.jacoco_agent,
        sut_cp_entry=cov_classes_dir,
        log_file=candidate.log_file,
    )
    if not ok_cov:
        return AutoCovfilterResult(
            candidate=candidate,
            status="failed",
            problem_category="covfilter_failed",
            problem_detail=cov_tail,
        )
    if not covfilter_output_exists(candidate.out_dir):
        return AutoCovfilterResult(
            candidate=candidate,
            status="failed",
            problem_category="missing_output",
            problem_detail="Covfilter finished without producing test_deltas_all.csv.",
        )
    return AutoCovfilterResult(
        candidate=candidate,
        status="passed",
        problem_category="",
        problem_detail="",
    )


def _run_auto_sanitize_compare(
    *,
    pipeline,
    ctx: "TargetContext",
    cov_classes_dir: Path,
    cov_out: Path,
    cov_log: Path,
    cov_compile_log: Path,
) -> bool:
    compare_root = Path(pipeline.args.sanitize_compare_out)
    compare_csv = sanitize_compare_summary_csv(compare_root, pipeline.args.includes)
    baseline_fqcn = variant_test_fqcn(ctx.generated_test_fqcn or "", "baseline") if ctx.generated_test_fqcn else ""
    baseline_pair = variant_pair(pipeline.generated_dir, ctx.repo, ctx.fqcn, baseline_fqcn, "baseline")
    sanitized_pair = _materialize_sanitized_pair_for_ctx(pipeline, ctx)

    baseline_candidate = AutoCovfilterCandidate(
        label="baseline",
        pair=baseline_pair,
        source_files=list(ctx.manual_sources) + _pair_source_files(baseline_pair),
        generated_test_fqcn=baseline_pair.test_fqcn if baseline_pair else "",
        build_dir=pipeline.build_dir / "covfilter-compare-classes" / ctx.target_id / "baseline",
        out_dir=sanitize_compare_target_dir(compare_root, ctx.target_id, "baseline"),
        log_file=pipeline.logs_dir / f"{ctx.target_id}.baseline.covfilter.log",
        compile_log=pipeline.logs_dir / f"{ctx.target_id}.baseline.covfilter.compile.log",
    )
    sanitized_candidate = AutoCovfilterCandidate(
        label="sanitized",
        pair=sanitized_pair,
        source_files=list(ctx.manual_sources) + _pair_source_files(sanitized_pair),
        generated_test_fqcn=sanitized_pair.test_fqcn if sanitized_pair else "",
        build_dir=pipeline.build_dir / "covfilter-compare-classes" / ctx.target_id / "sanitized",
        out_dir=sanitize_compare_target_dir(compare_root, ctx.target_id, "sanitized"),
        log_file=pipeline.logs_dir / f"{ctx.target_id}.sanitized.covfilter.log",
        compile_log=pipeline.logs_dir / f"{ctx.target_id}.sanitized.covfilter.compile.log",
    )

    print(f'[agt] Running covfilter compare: repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
    sanitized_result = _run_auto_covfilter_candidate(
        pipeline=pipeline,
        ctx=ctx,
        candidate=sanitized_candidate,
        cov_classes_dir=cov_classes_dir,
    )
    if sanitized_result.status == "passed":
        baseline_result = AutoCovfilterResult(
            candidate=baseline_candidate,
            status="skipped",
            problem_category="not_run_prefer_sanitized",
            problem_detail="Sanitized variant passed; baseline/original variant not run.",
        )
    else:
        baseline_result = _run_auto_covfilter_candidate(
            pipeline=pipeline,
            ctx=ctx,
            candidate=baseline_candidate,
            cov_classes_dir=cov_classes_dir,
        )
    selected_result, selection_reason = _select_auto_covfilter_result(baseline_result, sanitized_result)
    if selected_result.pair is not None:
        _replace_generated_sources(ctx, selected_result.pair)

    _copy_covfilter_result_artifacts(
        selected_result,
        target_out_dir=cov_out,
        target_log_file=cov_log,
        target_compile_log=cov_compile_log,
    )
    selected_log_for_summary = cov_compile_log if selected_result.problem_category == "compile_failed" else cov_log
    _append_sanitize_compare_row(
        csv_path=compare_csv,
        ctx=ctx,
        baseline=baseline_result,
        sanitized=sanitized_result,
        selected_variant=selected_result.label,
        selection_reason=selection_reason,
        selected_pair=selected_result.pair,
        selected_out_dir=cov_out,
        selected_log_file=selected_log_for_summary,
    )
    _append_covfilter_summary_row(
        csv_path=_covfilter_summary_csv(pipeline, "auto"),
        repo=ctx.repo,
        fqcn=ctx.fqcn,
        variant="auto",
        status=selected_result.status,
        problem_category=selected_result.problem_category,
        problem_detail=selected_result.problem_detail,
        manual_test_fqcn=ctx.manual_test_fqcn or "",
        generated_test_fqcn=selected_result.pair.test_fqcn if selected_result.pair else selected_result.generated_test_fqcn,
        out_dir=cov_out,
        log_file=selected_log_for_summary,
    )
    if selected_result.status == "passed":
        pipeline.ran += 1
        return True

    if selected_result.problem_category == "compile_failed":
        print(f'[agt] covfilter: Skip (compile failed): repo="{ctx.repo}" fqcn="{ctx.fqcn}" (see {selected_log_for_summary})')
        print("[agt][COVFILTER-COMPILE-TAIL]\n" + selected_result.problem_detail)
    elif selected_result.status == "failed":
        print(f'[agt] covfilter: FAIL (see {selected_log_for_summary})')
        print("[agt][COVFILTER-TAIL]\n" + selected_result.problem_detail)
    return True


def _append_covfilter_summary_row(
    *,
    csv_path: Path,
    repo: str,
    fqcn: str,
    variant: str,
    status: str,
    problem_category: str,
    problem_detail: str,
    manual_test_fqcn: str,
    generated_test_fqcn: str,
    out_dir: Path,
    log_file: Path,
) -> None:
    # Keep existing outputs only when the skip reason explicitly indicates reuse.
    preserve_existing_output = (
        status == "skipped"
        and problem_category in {"existing_passed_status", "existing_passed_output", "existing_output"}
    )
    if status != "passed" and not preserve_existing_output:
        _clear_covfilter_output_dir(out_dir)

    test_all_count, test_kept_count, line_kept_count = _covfilter_output_counts(out_dir)
    row = {
        "repo": repo,
        "fqcn": fqcn,
        "variant": variant,
        "status": status,
        "problem_category": problem_category,
        "problem_detail": problem_detail,
        "manual_test_fqcn": manual_test_fqcn,
        "generated_test_fqcn": generated_test_fqcn,
        "test_deltas_all_count": str(test_all_count),
        "test_deltas_kept_count": str(test_kept_count),
        "line_deltas_kept_count": str(line_kept_count),
        "out_dir": str(out_dir),
        "log_file": str(log_file),
    }
    fieldnames = list(row.keys())
    rows: List[dict[str, str]] = []
    replaced = False
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            existing_fieldnames = reader.fieldnames or []
            if existing_fieldnames:
                fieldnames = existing_fieldnames
            for existing_row in reader:
                if (
                    existing_row.get("repo", "") == repo
                    and existing_row.get("fqcn", "") == fqcn
                    and existing_row.get("variant", "") == variant
                ):
                    if not replaced:
                        rows.append({name: row.get(name, "") for name in fieldnames})
                        replaced = True
                    continue
                rows.append({name: existing_row.get(name, "") for name in fieldnames})
    if not replaced:
        rows.append({name: row.get(name, "") for name in fieldnames})
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Keep the status beside the evidence it describes. Aggregate summaries are
    # mutable views; downstream stages need a run-local record so a newer failed
    # attempt cannot outrank the latest mechanically verified run.
    if not preserve_existing_output:
        run_summary = out_dir / "covfilter_summary.csv"
        run_summary.parent.mkdir(parents=True, exist_ok=True)
        with run_summary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
            writer.writeheader()
            writer.writerow(row)


def _run_covfilter_with_retries(
    *,
    pipeline,
    ctx: "TargetContext",
    variant: str,
    source_files: List[Path],
    coverage_filter_jar: Path,
    libs_glob_cp: str,
    test_classes_dir: Path,
    sut_classes_dir: Path,
    out_dir: Path,
    manual_test_fqcn: str,
    generated_test_fqcn: str,
    jacoco_agent_jar: Path,
    sut_cp_entry: Path,
    log_file: Path,
    fallback_test_classes_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    module_dir = _covfilter_runtime_workdir(ctx=ctx, source_files=source_files)
    canonical_sut_override = os.environ.get("ITL_COVFILTER_SUT_CLASSES", "").strip()

    runtime_cp_sources: List[Path] = list(source_files)
    seen_runtime_cp_sources = {source.resolve() for source in runtime_cp_sources if source.exists()}
    for manual_source in getattr(ctx, "manual_sources", []) or []:
        if not manual_source.exists():
            continue
        resolved_manual = manual_source.resolve()
        if resolved_manual in seen_runtime_cp_sources:
            continue
        seen_runtime_cp_sources.add(resolved_manual)
        runtime_cp_sources.append(manual_source)

    repo_runtime_cp = ""
    repo_class_dirs_cp = ""
    if ctx.repo_root_for_deps and ctx.repo_root_for_deps.exists():
        repo_runtime_cp = resolve_repo_runtime_classpath(
            ctx.repo_root_for_deps,
            ctx.module_rel,
            ctx.build_tool,
            source_files=runtime_cp_sources,
        )
        repo_class_dirs_cp = ":".join(
            str(path)
            for path in candidate_repo_class_dirs(ctx.repo_root_for_deps, ctx.module_rel)
            if path.exists()
        )
        if canonical_sut_override and ctx.repo != "micronaut-projects/micronaut-core":
            frozen_revision_classes = _frozen_repo_main_class_dirs(
                ctx.repo_root_for_deps
            )
            repo_class_dirs_cp = _merge_classpath_strings(
                repo_class_dirs_cp,
                *(str(path) for path in frozen_revision_classes),
            )
    framework_runtime_cp = _framework_runtime_cp_for_sources(runtime_cp_sources)
    if _sources_use_regular_mockito_evosuite_runtime(runtime_cp_sources):
        framework_runtime_cp = _filter_evosuite_standalone_runtime(framework_runtime_cp)
        repo_runtime_cp = _filter_evosuite_standalone_runtime(repo_runtime_cp)
    extra_runtime_cp = _merge_classpath_strings(
        framework_runtime_cp,
        _spring_runtime_cp_for_sources(runtime_cp_sources),
        repo_runtime_cp,
        repo_class_dirs_cp,
        # The focused source-file directory remains first on the forked
        # classpath and is the only input analyzed by JaCoCo. The frozen fat
        # JAR follows it and supplies the target revision's other production
        # classes and dependencies. Keeping it for canonical source-file runs
        # is safe because the curated CUT bytecode has classpath precedence.
        # Micronaut's repository-wide fat JAR contains service registrations
        # from modules that are not on the target module's native test
        # classpath. Loading those registrations (notably Netty converters)
        # changes the inject module's runtime and introduces dependencies that
        # its own Gradle test task does not require. Its resolved module outputs
        # above are the faithful runtime support for this comparison.
        str(ctx.sut_jar.resolve())
        if (
            ctx.sut_jar
            and ctx.sut_jar.exists()
            and ctx.repo != "micronaut-projects/micronaut-core"
        )
        else "",
        str(fallback_test_classes_dir.resolve())
        if fallback_test_classes_dir is not None and fallback_test_classes_dir.exists()
        else "",
    )
    if ctx.repo == "dropwizard/dropwizard":
        dropwizard_sibling_classes = (
            sorted(ctx.repo_root_for_deps.glob("*/target/classes"))
            if ctx.repo_root_for_deps else []
        )
        extra_runtime_cp = _merge_classpath_strings(
            extra_runtime_cp,
            *(str(path.resolve()) for path in dropwizard_sibling_classes if path.exists()),
            _m2_artifact_jar("ch/qos/logback", "logback-core"),
            _m2_artifact_jar("ch/qos/logback", "logback-classic"),
            _m2_artifact_jar("org/slf4j", "jul-to-slf4j"),
            _m2_artifact_jar("org/glassfish/grizzly", "grizzly-http"),
            _m2_artifact_jar("org/glassfish/grizzly", "grizzly-framework"),
        )
    if ctx.repo == "eclipse/jetty.project" and ctx.repo_root_for_deps:
        # Jetty's manual tests use StacklessLogging from a sibling module that
        # is not part of the nested module's ordinary runtime dependency set.
        # Include its built outputs before the first coverage attempt so large
        # suites do not need a full missing-class discovery pass.
        jetty_logging_module = ctx.repo_root_for_deps / "jetty-core" / "jetty-slf4j-impl"
        extra_runtime_cp = _merge_classpath_strings(
            extra_runtime_cp,
            *(str(path.resolve()) for path in (
                jetty_logging_module / "target" / "classes",
                jetty_logging_module / "target" / "test-classes",
            ) if path.exists()),
        )
    if ctx.repo == "open-telemetry/opentelemetry-java-instrumentation":
        # The curated CUT directory intentionally contains only the target
        # class so an old shaded API cannot shadow it. The current module's
        # other production classes are still required at runtime (for example
        # internal.SpanKeyProvider used by InstrumenterTest and Mockito).
        module_class_candidates = [
            module_dir / "build" / "classes" / "java" / "main"
            if module_dir else Path("__missing__")
        ]
        if ctx.repo_root_for_deps:
            module_class_candidates.extend(
                (
                    ctx.repo_root_for_deps / ctx.module_rel / "build" / "classes" / "java" / "main",
                    ctx.repo_root_for_deps / "instrumentation-api" / "build" / "classes" / "java" / "main",
                    ctx.repo_root_for_deps / "build" / "classes" / "java" / "main",
                )
            )
        module_main_classes = next(
            (path for path in module_class_candidates if path.exists()),
            None,
        )
        if module_main_classes is not None:
            extra_runtime_cp = _merge_classpath_strings(
                extra_runtime_cp,
                str(module_main_classes.resolve()),
            )
        # The archived all-in-one instrumentation JAR bundles an older copy of
        # opentelemetry-api-incubator.  It shadows the 1.62 API required by the
        # frozen SDK test extension and fails during agentic-test class
        # initialization.  The CUT itself comes from the frozen class directory,
        # while the matching API/SDK artifacts are already resolved separately.
        extra_runtime_cp = os.pathsep.join(
            entry for entry in extra_runtime_cp.split(os.pathsep)
            if not (
                "opentelemetry-instrumentation-api-" in Path(entry).name
                and Path(entry).name.endswith("-agt-all.jar")
            )
        )
    if ctx.repo.startswith("hazelcast/"):
        extra_runtime_cp = _merge_classpath_strings(
            extra_runtime_cp,
            _m2_artifact_jar("com/google/collections", "google-collections"),
            _m2_artifact_jar("com/google/guava", "guava"),
        )
    if ctx.repo == "micronaut-projects/micronaut-core" and ctx.repo_root_for_deps:
        # The studied tests live in the inject module but inherit types from
        # Micronaut's sibling core module.  Gradle supplies these project
        # outputs during normal test execution; the standalone coverage fork
        # must receive the same module output explicitly.
        micronaut_core_outputs = (
            ctx.repo_root_for_deps / "core" / "build" / "classes" / "java" / "main",
            ctx.repo_root_for_deps / "core" / "build" / "resources" / "main",
        )
        extra_runtime_cp = _merge_classpath_strings(
            extra_runtime_cp,
            *(str(path.resolve()) for path in micronaut_core_outputs if path.exists()),
            _m2_artifact_jar(
                "jakarta/annotation", "jakarta.annotation-api", "2.1.1"
            ),
            _m2_artifact_jar("jakarta/inject", "jakarta.inject-api", "2.0.1"),
            _gradle_artifact_jar(
                "org.spockframework", "spock-core", "2.4-groovy-5.0"
            ),
            _gradle_artifact_jar("org.apache.groovy", "groovy", "5.0.3"),
        )
    if ctx.target_id == "T14_ConfigXmlGenerator":
        # T14's archived target JAR is from the parent of Hazelcast PR
        # d45fe97.  The checked-out repository has subsequently advanced;
        # allowing its target/classes directory onto the fork classpath mixes
        # revisions and makes the manual baseline cover the PR change itself.
        # Retain test-output support, but let the archived SUT JAR provide all
        # production classes for this historical measurement.
        extra_runtime_cp = _filter_covfilter_runtime_classpath(
            extra_runtime_cp,
            drop_main_output_dirs=True,
            drop_test_output_dirs=False,
        )
    if ctx.repo == "apache/flink" and ctx.repo_root_for_deps:
        # Flink's runtime test classes reference support code that lives in sibling
        # test-output directories rather than in flink-runtime's runtime dependency
        # graph.  Compilation discovers those directories, but the forked JaCoCo
        # runner needs them explicitly as well.
        flink_support = (
            ctx.repo_root_for_deps / "flink-core" / "target" / "test-classes",
            ctx.repo_root_for_deps
            / "flink-test-utils-parent"
            / "flink-test-utils-junit"
            / "target"
            / "classes",
        )
        extra_runtime_cp = _merge_classpath_strings(
            extra_runtime_cp,
            *(str(path) for path in flink_support if path.exists()),
        )
    if ctx.repo == "apache/iceberg" and ctx.repo_root_for_deps:
        # Iceberg's core tests use TestHelpers from the API module's test output.
        # Gradle exposes it via testArtifacts, but the forked JaCoCo runner needs
        # that directory explicitly on its runtime classpath.
        iceberg_test_support = (
            ctx.repo_root_for_deps / "api" / "build" / "classes" / "java" / "test",
        )
        extra_runtime_cp = _merge_classpath_strings(
            extra_runtime_cp,
            *(str(path) for path in iceberg_test_support if path.exists()),
        )
    extra_runtime_cp = _filter_conflicting_slf4j_bindings(extra_runtime_cp)
    if ctx.repo == "eclipse/jetty.project":
        # jetty-slf4j-impl above contributes Jetty's native SLF4J service
        # provider as compiled sibling classes. Generated-test imports must not
        # select Logback or slf4j-simple and thereby change the frozen manual
        # baseline from one variant to another.
        extra_runtime_cp = _filter_external_slf4j_bindings(extra_runtime_cp)
    attempted_service_providers: Set[str] = set()
    attempted_missing_runtime_classes: Set[str] = set()
    attempted_missing_selector_methods: Set[str] = set()
    excluded_sut_classes: Set[str] = set()
    package_prefix_filter = _covfilter_package_prefix_from_fqcn(ctx.fqcn)
    use_package_prefix_filter = bool(package_prefix_filter)
    preserve_test_output_dirs = ctx.repo.startswith(("opentripplanner/", "hazelcast/"))
    # The curated analysis directory may draw support classes from repository
    # runtime outputs. ForkedJacocoRunner executes this same directory first so
    # JaCoCo class IDs match the classes analyzed below.
    prefer_sut_input_only = False
    hazelcast_runtime_pruned = False
    timeout_runtime_cp_pruned = False
    timeout_ms = _effective_covfilter_timeout_ms(pipeline.args.timeout_ms)
    timeout_extended = False
    runtime_support_classes_dir = pipeline.build_dir / f"{variant}-filter-runtime-support-classes" / ctx.target_id
    if runtime_support_classes_dir.exists():
        shutil.rmtree(runtime_support_classes_dir, ignore_errors=True)
    ensure_dir(runtime_support_classes_dir)

    for _ in range(_COVFILTER_RUNTIME_RETRY_LIMIT):
        # Missing-class recovery can add the SDK after the initial classpath
        # is assembled, so align the incubator API at the start of every
        # attempt rather than only once before the retry loop.
        matching_otel_incubator = _matching_opentelemetry_incubator_jar(extra_runtime_cp)
        if matching_otel_incubator:
            extra_runtime_cp = _merge_classpath_strings(extra_runtime_cp, matching_otel_incubator)
        base_libs_dir = libs_dir_from_glob(libs_glob_cp)
        libs_dir_arg = _prepare_covfilter_libs_dir(
            base_libs_dir=base_libs_dir,
            extra_runtime_cp=extra_runtime_cp,
            merged_dir=pipeline.build_dir / f"{variant}-filter-runtime-libs" / ctx.target_id,
        )
        _ensure_covfilter_extra_jars_present(libs_dir=libs_dir_arg, extra_runtime_cp=extra_runtime_cp)
        if ctx.repo == "dropwizard/dropwizard":
            # Dropwizard initializes its test logging through Logback.  Leaving
            # the generic SLF4J-simple provider in the fork selects it instead
            # and makes DropwizardResourceConfigTest's static setup fail.
            for simple_jar in libs_dir_arg.glob("slf4j-simple-*.jar"):
                simple_jar.unlink(missing_ok=True)
        if ctx.repo == "eclipse/jetty.project":
            # Runtime-directory sanitization deliberately strips service
            # descriptors. Restore Jetty's own descriptor after generic
            # binding recovery, and remove providers from the compatibility
            # cache so generated-test imports cannot alter manual coverage.
            _install_jetty_slf4j_provider_service(libs_dir_arg)
        merged_test_classes_dir = (
            pipeline.build_dir / f"{variant}-filter-runtime-classes" / ctx.target_id
        )
        if ctx.repo == "questdb/questdb":
            # QuestDB's TestOs resolves its bundled native test library by
            # locating the Maven `/target/` segment in the resource URL.
            # Preserve that expected layout when test classes/resources from
            # several directories must be merged for the forked runner.
            merged_test_classes_dir = merged_test_classes_dir / "target" / "test-classes"
        cov_test_classes_dir = _prepare_covfilter_test_classes_dir(
            compiled_test_classes_dir=test_classes_dir,
            extra_runtime_cp=extra_runtime_cp,
            merged_dir=merged_test_classes_dir,
            test_resource_dirs=_covfilter_target_test_resource_dirs(module_dir),
        )
        if canonical_sut_override:
            # The validation driver supplies a frozen, already-curated
            # source-file class directory.  Use it directly: rebuilding this
            # directory from the project's runtime classpath can silently
            # displace the exact CUT bytecode or leave an empty staging tree.
            cov_sut_classes_dir = Path(canonical_sut_override).resolve()
            target_class = (
                cov_sut_classes_dir / Path(*ctx.fqcn.split("."))
            ).with_suffix(".class")
            if not target_class.is_file():
                raise FileNotFoundError(
                    f"Canonical covfilter CUT bytecode is missing: {target_class}"
                )
        else:
            cov_sut_classes_dir = _prepare_covfilter_sut_classes_dir(
                sut_classes_input=sut_classes_dir,
                extra_runtime_cp=extra_runtime_cp,
                merged_dir=pipeline.build_dir / f"{variant}-filter-sut-classes" / ctx.target_id,
                prefer_sut_input_only=prefer_sut_input_only,
                target_package_prefix=package_prefix_filter if use_package_prefix_filter else "",
                excluded_classes=excluded_sut_classes,
            )
        runtime_sut_entries = [
            entry for entry in extra_runtime_cp.split(os.pathsep) if entry
        ]
        execution_sut_classes_dir = _matching_covfilter_execution_classes_dir(
            cov_sut_classes_dir,
            runtime_sut_entries,
            ctx.fqcn,
        )
        ok_cov, cov_tail = CovfilterRunner(
            build_dir=Path.cwd() / "build" / "agt",
            working_dir=module_dir,
            timeout_ms=timeout_ms,
        ).run_covfilter(
            coverage_filter_jar=coverage_filter_jar,
            libs_glob_cp=libs_glob_cp,
            extra_runtime_cp=extra_runtime_cp,
            test_classes_dir=cov_test_classes_dir,
            sut_classes_dir=cov_sut_classes_dir,
            out_dir=out_dir,
            manual_test_fqcn=manual_test_fqcn,
            generated_test_fqcn=generated_test_fqcn,
            target_fqcn=ctx.fqcn,
            jacoco_agent_jar=jacoco_agent_jar,
            # Execute a complete project output only when every analyzed CUT
            # class is byte-identical. Otherwise use the curated directory;
            # accepting an unmatched fat JAR produced a variant-local baseline
            # for T14 (939 covered lines versus the canonical 916).
            sut_cp_entry=execution_sut_classes_dir,
            libs_dir_arg=libs_dir_arg,
            log_file=log_file,
            extra_java_opts=(
                ["-Dcovfilter.runtimeSutCp=" + os.pathsep.join(runtime_sut_entries)]
                if runtime_sut_entries else None
            ),
        )
        full_output = log_file.read_text(encoding="utf-8", errors="ignore")
        # A completed covfilter run may legitimately contain failures from individual
        # generated candidates. Those candidates are recorded with zero deltas and are
        # excluded from retention, so a partial set of failures must not trigger a full
        # retry. If *every* candidate fails with linkage-shaped output, however, the
        # variant has an incomplete runtime rather than ordinary test failures; allow the
        # recovery logic below to add the missing dependency and retry.
        if ok_cov and covfilter_output_exists(out_dir):
            try:
                with (out_dir / "test_deltas_all.csv").open(
                    "r", encoding="utf-8", newline=""
                ) as handle:
                    candidate_count = sum(1 for _ in csv.DictReader(handle))
            except OSError:
                candidate_count = 0
            failed_count = len(
                _failed_covfilter_candidate_methods(log_file, generated_test_fqcn)
            )
            missing_runtime_classes = _extract_missing_runtime_classes(full_output)
            globally_linkage_blocked = bool(
                candidate_count
                and failed_count >= candidate_count
                and missing_runtime_classes
            )
            if not globally_linkage_blocked:
                return ok_cov, cov_tail
        jacoco_failed_classes = _extract_jacoco_analyzer_failed_classes(full_output)
        if jacoco_failed_classes:
            excluded_added = False
            for class_path in jacoco_failed_classes:
                class_file = Path(class_path)
                try:
                    rel = class_file.resolve().relative_to(cov_sut_classes_dir.resolve()).as_posix()
                except (OSError, ValueError):
                    continue
                if rel in excluded_sut_classes:
                    continue
                excluded_sut_classes.add(rel)
                excluded_added = True
            if excluded_added:
                continue

        if _has_covfilter_duplicate_class_conflict(full_output) and not canonical_sut_override:
            retried_runtime_cp = _filter_covfilter_runtime_classpath(
                extra_runtime_cp,
                drop_main_output_dirs=True,
                drop_test_output_dirs=not preserve_test_output_dirs,
            )
            if not prefer_sut_input_only or retried_runtime_cp != extra_runtime_cp:
                prefer_sut_input_only = True
                if retried_runtime_cp:
                    extra_runtime_cp = retried_runtime_cp
                continue

        if (
            not hazelcast_runtime_pruned
            and not ctx.repo.startswith("hazelcast/")
            and _has_hazelcast_service_provider_contamination(full_output)
        ):
            retried_runtime_cp = _filter_covfilter_runtime_classpath(
                extra_runtime_cp,
                drop_hazelcast_entries=True,
                drop_test_output_dirs=not preserve_test_output_dirs,
            )
            if retried_runtime_cp and retried_runtime_cp != extra_runtime_cp:
                extra_runtime_cp = retried_runtime_cp
                hazelcast_runtime_pruned = True
                continue

        missing_runtime_classes = [
            class_name
            for class_name in _extract_missing_runtime_classes(full_output)
            if class_name not in attempted_missing_runtime_classes
        ]
        runtime_classes_restored_from_outputs: Set[str] = set()
        if missing_runtime_classes:
            for runtime_class in missing_runtime_classes:
                if _copy_runtime_class_from_repo_outputs(
                    runtime_fqcn=runtime_class,
                    repo_root_for_deps=ctx.repo_root_for_deps,
                    module_rel=ctx.module_rel,
                    target_dir=runtime_support_classes_dir,
                ):
                    runtime_classes_restored_from_outputs.add(runtime_class)
            if runtime_classes_restored_from_outputs:
                extra_runtime_cp = _merge_classpath_strings(
                    extra_runtime_cp,
                    str(runtime_support_classes_dir.resolve()),
                )
                attempted_missing_runtime_classes.update(runtime_classes_restored_from_outputs)
                continue

        runtime_classes_with_sources: Set[str] = set()
        unresolved_runtime_classes = [
            runtime_class
            for runtime_class in missing_runtime_classes
            if runtime_class not in runtime_classes_restored_from_outputs
        ]
        if unresolved_runtime_classes:
            runtime_sources: List[Path] = []
            seen_runtime_sources = set()
            for runtime_class in unresolved_runtime_classes:
                source_path = _source_path_for_runtime_fqcn(
                    runtime_fqcn=runtime_class,
                    module_dir=module_dir,
                    repo_root_for_deps=ctx.repo_root_for_deps,
                    module_rel=ctx.module_rel,
                )
                if source_path is None or source_path in seen_runtime_sources:
                    continue
                seen_runtime_sources.add(source_path)
                runtime_sources.append(source_path)
                runtime_classes_with_sources.add(runtime_class)

            if runtime_sources:
                runtime_compile_log = log_file.with_name(f"{log_file.stem}.missing-runtime.compile.log")
                if _compile_service_provider_sources(
                    provider_sources=runtime_sources,
                    existing_sources=source_files,
                    build_dir=runtime_support_classes_dir,
                    libs_glob_cp=libs_glob_cp,
                    sut_jar=ctx.sut_jar,
                    log_file=runtime_compile_log,
                    repo_root_for_deps=ctx.repo_root_for_deps,
                    module_rel=ctx.module_rel,
                    build_tool=ctx.build_tool,
                    max_rounds=pipeline.args.dep_rounds,
                ):
                    extra_runtime_cp = _merge_classpath_strings(
                        extra_runtime_cp,
                        str(runtime_support_classes_dir.resolve()),
                    )
                    attempted_missing_runtime_classes.update(runtime_classes_with_sources)
                    continue

        missing_selector_methods = [
            method
            for method in _extract_missing_selector_methods(full_output, generated_test_fqcn)
            if method not in attempted_missing_selector_methods
        ]
        if missing_selector_methods:
            generated_source = _source_path_for_fqcn(source_files, generated_test_fqcn)
            if generated_source is not None:
                patched_any = False
                for method_name in missing_selector_methods:
                    attempted_missing_selector_methods.add(method_name)
                    patched_any = _append_placeholder_test_method(generated_source, method_name) or patched_any
                if patched_any:
                    selector_compile_log = log_file.with_name(f"{log_file.stem}.selector.compile.log")
                    selector_libs_cp = _effective_compile_libs_cp_for_sources(
                        libs_glob_cp=libs_glob_cp,
                        sources=source_files,
                    )
                    ok_selector_compile, _tail, _compiled_sources = compile_test_set_smart(
                        java_files=source_files,
                        build_dir=test_classes_dir,
                        libs_glob_cp=selector_libs_cp,
                        sut_jar=ctx.sut_jar,
                        log_file=selector_compile_log,
                        repo_root_for_deps=ctx.repo_root_for_deps,
                        module_rel=ctx.module_rel,
                        build_tool=ctx.build_tool,
                        max_rounds=pipeline.args.dep_rounds,
                    )
                    if ok_selector_compile:
                        continue

        if _covfilter_timed_out(full_output):
            # For a frozen source-file run the repository outputs provide
            # required sibling classes.  Removing all main/test outputs after
            # a timeout can turn a merely long generated suite into a false
            # missing-class failure.  Preserve the exact project runtime and
            # only extend the timeout for these canonical reruns.
            if not canonical_sut_override and not timeout_runtime_cp_pruned:
                retried_runtime_cp = _filter_covfilter_runtime_classpath(
                    extra_runtime_cp,
                    drop_main_output_dirs=True,
                    drop_test_output_dirs=not preserve_test_output_dirs,
                )
                timeout_runtime_cp_pruned = True
                if retried_runtime_cp and retried_runtime_cp != extra_runtime_cp:
                    extra_runtime_cp = retried_runtime_cp
                    continue
            if not timeout_extended and timeout_ms and timeout_ms > 0:
                timeout_ms = timeout_ms * 2
                timeout_extended = True
                continue

        missing_providers = [
            provider
            for provider in _extract_missing_service_provider_classes(full_output)
            if provider not in attempted_service_providers
        ]
        if missing_providers:
            attempted_service_providers.update(missing_providers)
            provider_sources: List[Path] = []
            seen_provider_sources = set()
            for provider in missing_providers:
                source_path = _source_path_for_provider_fqcn(
                    provider_fqcn=provider,
                    module_dir=module_dir,
                    repo_root_for_deps=ctx.repo_root_for_deps,
                    module_rel=ctx.module_rel,
                )
                if source_path is None or source_path in seen_provider_sources:
                    continue
                seen_provider_sources.add(source_path)
                provider_sources.append(source_path)

            if provider_sources:
                service_compile_log = log_file.with_name(f"{log_file.stem}.service-provider.compile.log")
                if _compile_service_provider_sources(
                    provider_sources=provider_sources,
                    existing_sources=source_files,
                    build_dir=test_classes_dir,
                    libs_glob_cp=libs_glob_cp,
                    sut_jar=ctx.sut_jar,
                    log_file=service_compile_log,
                    repo_root_for_deps=ctx.repo_root_for_deps,
                    module_rel=ctx.module_rel,
                    build_tool=ctx.build_tool,
                    max_rounds=pipeline.args.dep_rounds,
                ):
                    continue

        if "NoSuchMethodError" in full_output:
            matching_otel_incubator = _matching_opentelemetry_incubator_jar(extra_runtime_cp)
            aligned_runtime_cp = _merge_classpath_strings(extra_runtime_cp, matching_otel_incubator)
            if matching_otel_incubator and aligned_runtime_cp != extra_runtime_cp:
                extra_runtime_cp = aligned_runtime_cp
                continue

        external_runtime_cp = resolve_external_runtime_classpath_from_output(full_output)
        if "Fork failed (exit=1) main=app.ListTests" in full_output:
            list_tests_output = _probe_covfilter_list_tests_output(
                coverage_filter_jar=coverage_filter_jar,
                libs_dir=libs_dir_arg,
                test_classes_dir=cov_test_classes_dir,
                sut_classes_dir=cov_sut_classes_dir,
                test_fqcns=[generated_test_fqcn, manual_test_fqcn],
                working_dir=module_dir,
                timeout_ms=_effective_covfilter_timeout_ms(pipeline.args.timeout_ms),
            )
            if list_tests_output:
                full_output = f"{full_output.rstrip()}\n\n[agt] list-tests probe:\n{list_tests_output}\n"
                write_text(log_file, full_output)
                external_runtime_cp = _merge_classpath_strings(
                    external_runtime_cp,
                    resolve_external_runtime_classpath_from_output(list_tests_output),
                )
        unresolved_missing_runtime_classes = [
            class_name for class_name in missing_runtime_classes if class_name not in runtime_classes_with_sources
        ]
        retried_runtime_cp = _filter_conflicting_slf4j_bindings(
            _merge_classpath_strings(extra_runtime_cp, external_runtime_cp)
        )
        if not retried_runtime_cp or retried_runtime_cp == extra_runtime_cp:
            attempted_missing_runtime_classes.update(unresolved_missing_runtime_classes)
            return ok_cov, cov_tail
        attempted_missing_runtime_classes.update(unresolved_missing_runtime_classes)
        extra_runtime_cp = retried_runtime_cp
    return ok_cov, cov_tail


class CovfilterStep(Step):
    step_names = ("filter",)

    def run(self, ctx: "TargetContext") -> bool:
        if not (self.pipeline.do_covfilter and self.should_run()):
            return True
        auto_variant = self.pipeline.args.auto_variant
        if self.pipeline.args.skip_empty_tests:
            generated_test_src = first_test_source_for_fqcn(ctx.final_sources or ctx.sources, ctx.generated_test_fqcn)
            if generated_test_src is None:
                generated_candidates = [
                    source for source in (ctx.final_sources or ctx.sources) if source not in set(ctx.manual_sources)
                ]
                if generated_candidates:
                    generated_test_src = generated_candidates[0]
            if is_empty_generated_test_source(generated_test_src):
                print(f'[agt] covfilter: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                cov_out = covfilter_variant_out_dir(
                    self.pipeline.covfilter_out_root,
                    self.pipeline.adopted_covfilter_out_root,
                    auto_variant,
                    ctx.target_id,
                    self.pipeline.agentic_covfilter_out_root,
                )
                _append_covfilter_summary_row(
                    csv_path=_covfilter_summary_csv(self.pipeline, auto_variant),
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=auto_variant,
                    status="skipped",
                    problem_category="empty_generated_tests",
                    problem_detail="Generated test source is empty (notGeneratedAnyTest).",
                    manual_test_fqcn=ctx.manual_test_fqcn or "",
                    generated_test_fqcn=ctx.generated_test_fqcn or "",
                    out_dir=cov_out,
                    log_file=self.pipeline.logs_dir / f"{ctx.target_id}.{auto_variant}.covfilter.log",
                )
                return True
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] covfilter: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            cov_out = covfilter_variant_out_dir(
                self.pipeline.covfilter_out_root,
                self.pipeline.adopted_covfilter_out_root,
                auto_variant,
                ctx.target_id,
                self.pipeline.agentic_covfilter_out_root,
            )
            _append_covfilter_summary_row(
                csv_path=_covfilter_summary_csv(self.pipeline, auto_variant),
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variant=auto_variant,
                status="skipped",
                problem_category="agt_line_covered_zero",
                problem_detail="Target excluded because AGT coverage summary reports zero covered lines.",
                manual_test_fqcn=ctx.manual_test_fqcn or "",
                generated_test_fqcn=ctx.generated_test_fqcn or "",
                out_dir=cov_out,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.{auto_variant}.covfilter.log",
            )
            return True
        if self.pipeline.covfilter_jar is None or not self.pipeline.covfilter_jar.exists():
            print(f'[agt] covfilter: Skip (missing --covfilter-jar): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            cov_out = covfilter_variant_out_dir(
                self.pipeline.covfilter_out_root,
                self.pipeline.adopted_covfilter_out_root,
                auto_variant,
                ctx.target_id,
                self.pipeline.agentic_covfilter_out_root,
            )
            _append_covfilter_summary_row(
                csv_path=_covfilter_summary_csv(self.pipeline, auto_variant),
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variant=auto_variant,
                status="skipped",
                problem_category="missing_covfilter_jar",
                problem_detail="Coverage filter jar is missing.",
                manual_test_fqcn=ctx.manual_test_fqcn or "",
                generated_test_fqcn=ctx.generated_test_fqcn or "",
                out_dir=cov_out,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.{auto_variant}.covfilter.log",
            )
            return True
        if not ctx.manual_test_fqcn or not ctx.generated_test_fqcn:
            if not ctx.manual_test_fqcn and not ctx.generated_test_fqcn:
                problem_category = "missing_test_fqcn"
                problem_detail = "Need both manual and generated test FQCNs to run covfilter."
            elif not ctx.manual_test_fqcn:
                problem_category = "missing_manual_test_fqcn"
                problem_detail = "Need a manual test FQCN to run covfilter."
            else:
                problem_category = "missing_generated_test_fqcn"
                problem_detail = "Need a generated test FQCN to run covfilter."
            print(f'[agt] covfilter: Skip (need both manual+generated test fqcn): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            cov_out = covfilter_variant_out_dir(
                self.pipeline.covfilter_out_root,
                self.pipeline.adopted_covfilter_out_root,
                auto_variant,
                ctx.target_id,
                self.pipeline.agentic_covfilter_out_root,
            )
            _append_covfilter_summary_row(
                csv_path=_covfilter_summary_csv(self.pipeline, auto_variant),
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variant=auto_variant,
                status="skipped",
                problem_category=problem_category,
                problem_detail=problem_detail,
                manual_test_fqcn=ctx.manual_test_fqcn or "",
                generated_test_fqcn=ctx.generated_test_fqcn or "",
                out_dir=cov_out,
                log_file=self.pipeline.logs_dir / f"{ctx.target_id}.{auto_variant}.covfilter.log",
            )
            return True

        if self.pipeline.sut_classes_dir is not None and self.pipeline.sut_classes_dir.exists():
            cov_classes_dir = self.pipeline.sut_classes_dir
        else:
            cov_classes_dir = ctx.sut_jar

        cov_out = covfilter_variant_out_dir(
            self.pipeline.covfilter_out_root,
            self.pipeline.adopted_covfilter_out_root,
            auto_variant,
            ctx.target_id,
            self.pipeline.agentic_covfilter_out_root,
        )
        cov_log = self.pipeline.logs_dir / f"{ctx.target_id}.{auto_variant}.covfilter.log"
        cov_compile_log = self.pipeline.logs_dir / f"{ctx.target_id}.{auto_variant}.covfilter.compile.log"
        if _maybe_skip_existing_covfilter(
            pipeline=self.pipeline,
            ctx=ctx,
            variant=auto_variant,
            cov_out=cov_out,
            cov_log=cov_log,
            generated_test_fqcn=ctx.generated_test_fqcn or "",
            step_label="covfilter",
            message_context=f'repo="{ctx.repo}" fqcn="{ctx.fqcn}"',
        ):
            return True
        if self.pipeline.args.sanitize_compare and self.pipeline.args.auto_variant != "auto-original":
            return _run_auto_sanitize_compare(
                pipeline=self.pipeline,
                ctx=ctx,
                cov_classes_dir=cov_classes_dir,
                cov_out=cov_out,
                cov_log=cov_log,
                cov_compile_log=cov_compile_log,
            )
        _run_covfilter_variant(
            pipeline=self.pipeline,
            ctx=ctx,
            variant=auto_variant,
            run_variant="covfilter",
            source_files=ctx.final_sources,
            generated_test_fqcn=ctx.generated_test_fqcn or "",
            cov_classes_dir=cov_classes_dir,
            cov_out=cov_out,
            cov_log=cov_log,
            cov_compile_log=cov_compile_log,
            reusable_build_dir=ctx.target_build,
            compile_build_dir=self.pipeline.build_dir / "covfilter-classes" / ctx.target_id,
            step_label="covfilter",
            run_display_label="covfilter",
            message_context=f'repo="{ctx.repo}" fqcn="{ctx.fqcn}"',
            compile_tail_tag="COVFILTER-COMPILE",
            runtime_tail_tag="COVFILTER",
            check_existing_skip=False,
        )
        return True


class AdoptedFilterStep(Step):
    step_names = ("adopted-filter",)

    def run(self, ctx: "TargetContext") -> bool:
        if not (self.pipeline.do_covfilter and self.should_run()):
            return True
        selected_variants = selected_adopted_variants(self.pipeline.args.adopted_filter_variants)
        selected_variant_set = set(selected_variants)
        if self.pipeline.args.skip_empty_tests and selected_variant_set != {"pr-tests"}:
            generated_test_src = first_test_source_for_fqcn(ctx.final_sources or ctx.sources, ctx.generated_test_fqcn)
            if generated_test_src is None:
                generated_candidates = [
                    source for source in (ctx.final_sources or ctx.sources) if source not in set(ctx.manual_sources)
                ]
                if generated_candidates:
                    generated_test_src = generated_candidates[0]
            if is_empty_generated_test_source(generated_test_src):
                print(f'[agt] adopted-covfilter: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                for variant in selected_variants:
                    cov_out = covfilter_variant_out_dir(
                        self.pipeline.covfilter_out_root,
                        self.pipeline.adopted_covfilter_out_root,
                        variant,
                        ctx.target_id,
                        self.pipeline.agentic_covfilter_out_root,
                    )
                    _append_covfilter_summary_row(
                        csv_path=_covfilter_summary_csv(self.pipeline, variant),
                        repo=ctx.repo,
                        fqcn=ctx.fqcn,
                        variant=variant,
                        status="skipped",
                        problem_category="empty_generated_tests",
                        problem_detail="Generated test source is empty (notGeneratedAnyTest).",
                        manual_test_fqcn=ctx.manual_test_fqcn or "",
                        generated_test_fqcn="",
                        out_dir=cov_out,
                        log_file=self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.covfilter.log",
                    )
                return True
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] adopted-covfilter: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            for variant in selected_variants:
                cov_out = covfilter_variant_out_dir(
                    self.pipeline.covfilter_out_root,
                    self.pipeline.adopted_covfilter_out_root,
                    variant,
                    ctx.target_id,
                    self.pipeline.agentic_covfilter_out_root,
                )
                _append_covfilter_summary_row(
                    csv_path=_covfilter_summary_csv(self.pipeline, variant),
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    status="skipped",
                    problem_category="agt_line_covered_zero",
                    problem_detail="Target excluded because AGT coverage summary reports zero covered lines.",
                    manual_test_fqcn=ctx.manual_test_fqcn or "",
                    generated_test_fqcn="",
                    out_dir=cov_out,
                    log_file=self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.covfilter.log",
                )
            return True
        if self.pipeline.covfilter_jar is None or not self.pipeline.covfilter_jar.exists():
            print(f'[agt] adopted-covfilter: Skip (missing --covfilter-jar): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            for variant in selected_variants:
                cov_out = covfilter_variant_out_dir(
                    self.pipeline.covfilter_out_root,
                    self.pipeline.adopted_covfilter_out_root,
                    variant,
                    ctx.target_id,
                    self.pipeline.agentic_covfilter_out_root,
                )
                _append_covfilter_summary_row(
                    csv_path=_covfilter_summary_csv(self.pipeline, variant),
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    status="skipped",
                    problem_category="missing_covfilter_jar",
                    problem_detail="Coverage filter jar is missing.",
                    manual_test_fqcn=ctx.manual_test_fqcn or "",
                    generated_test_fqcn="",
                    out_dir=cov_out,
                    log_file=self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.covfilter.log",
                )
            return True
        if not ctx.manual_sources:
            print(f'[agt] adopted-covfilter: Skip (missing manual test source): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            for variant in selected_variants:
                cov_out = covfilter_variant_out_dir(
                    self.pipeline.covfilter_out_root,
                    self.pipeline.adopted_covfilter_out_root,
                    variant,
                    ctx.target_id,
                    self.pipeline.agentic_covfilter_out_root,
                )
                _append_covfilter_summary_row(
                    csv_path=_covfilter_summary_csv(self.pipeline, variant),
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    status="skipped",
                    problem_category="missing_manual_test_source",
                    problem_detail="Missing manual test source.",
                    manual_test_fqcn=ctx.manual_test_fqcn or "",
                    generated_test_fqcn="",
                    out_dir=cov_out,
                    log_file=self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.covfilter.log",
                )
            return True

        variants = [
            (variant, source)
            for variant, source in adopted_variants(
                self.pipeline.adopted_root,
                ctx.target_id,
                ctx.fqcn,
                self.pipeline.pr_tests_root,
                self.pipeline.out_dir / "pr-tests",
            )
            if variant in selected_variant_set
        ]
        if not variants:
            print(f'[agt] adopted-covfilter: Skip (missing adopted tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            for variant in selected_variants:
                cov_out = covfilter_variant_out_dir(
                    self.pipeline.covfilter_out_root,
                    self.pipeline.adopted_covfilter_out_root,
                    variant,
                    ctx.target_id,
                    self.pipeline.agentic_covfilter_out_root,
                )
                _append_covfilter_summary_row(
                    csv_path=_covfilter_summary_csv(self.pipeline, variant),
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    status="skipped",
                    problem_category="missing_adopted_tests",
                    problem_detail="Missing adopted test source.",
                    manual_test_fqcn=ctx.manual_test_fqcn or "",
                    generated_test_fqcn="",
                    out_dir=cov_out,
                    log_file=self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.covfilter.log",
                )
            return True

        if self.pipeline.sut_classes_dir is not None and self.pipeline.sut_classes_dir.exists():
            cov_classes_dir = self.pipeline.sut_classes_dir
        else:
            cov_classes_dir = ctx.sut_jar

        for variant, adopted_src in variants:
            adopted_src = _materialize_adopted_covfilter_source(
                adopted_src,
                self.pipeline.out_dir / "adopted-covfilter-sources" / variant / ctx.target_id,
            )
            normalize_primary_class_name_file(adopted_src)
            adopted_test_fqcn = test_fqcn_from_source(adopted_src)
            cov_out = covfilter_variant_out_dir(
                self.pipeline.covfilter_out_root,
                self.pipeline.adopted_covfilter_out_root,
                variant,
                ctx.target_id,
                self.pipeline.agentic_covfilter_out_root,
            )
            cov_log = self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.covfilter.log"
            if not adopted_test_fqcn or not ctx.manual_test_fqcn:
                print(
                    f'[agt] adopted-covfilter: Skip (cannot parse test fqcn): repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"'
                )
                _append_covfilter_summary_row(
                    csv_path=_covfilter_summary_csv(self.pipeline, variant),
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    status="skipped",
                    problem_category="parse_test_fqcn_failed",
                    problem_detail="Could not parse adopted or manual test FQCN.",
                    manual_test_fqcn=ctx.manual_test_fqcn or "",
                    generated_test_fqcn=adopted_test_fqcn or "",
                    out_dir=cov_out,
                    log_file=cov_log,
                )
                continue
            adopted_build = self.pipeline.build_dir / "adopted-filter-classes" / variant / ctx.target_id
            adopt_compile_log = self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.covfilter.compile.log"
            manual_test_fqcn_override = None
            allowed_test_methods = None
            output_test_fqcn = None
            check_existing_skip = True
            duplicate_fix_source = adopted_src
            if variant == "pr-tests":
                (
                    primary_adopted_sources,
                    adopted_test_fqcn,
                    allowed_test_methods,
                    output_test_fqcn,
                ) = _materialize_pr_covfilter_sources(
                    ctx=ctx,
                    pr_source=adopted_src,
                    stage_root=self.pipeline.out_dir / "pr-tests-covfilter" / ctx.target_id,
                )
                fallback_adopted_sources = primary_adopted_sources
                manual_test_fqcn_override = ctx.manual_test_fqcn
                duplicate_fix_source = primary_adopted_sources[-1]
                check_existing_skip = False
            else:
                primary_adopted_sources, fallback_adopted_sources = _adopted_covfilter_source_sets(
                    ctx,
                    adopted_src,
                )
            _run_covfilter_variant(
                pipeline=self.pipeline,
                ctx=ctx,
                variant=variant,
                run_variant=f"adopted-{variant}",
                source_files=primary_adopted_sources,
                generated_test_fqcn=adopted_test_fqcn or "",
                cov_classes_dir=cov_classes_dir,
                cov_out=cov_out,
                cov_log=cov_log,
                cov_compile_log=adopt_compile_log,
                reusable_build_dir=adopted_build,
                compile_build_dir=adopted_build,
                step_label="adopted-covfilter",
                run_display_label=f"adopted covfilter ({variant})",
                message_context=f'repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"',
                compile_tail_tag="ADOPTED-COMPILE",
                runtime_tail_tag="ADOPTED-COVFILTER",
                compile_fallback_source_files=fallback_adopted_sources,
                fallback_test_classes_dir=ctx.target_build,
                duplicate_fix_source=duplicate_fix_source,
                manual_test_fqcn_override=manual_test_fqcn_override,
                allowed_test_methods=allowed_test_methods,
                output_test_fqcn=output_test_fqcn,
                check_existing_skip=check_existing_skip,
            )
        return True
