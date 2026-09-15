from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .common import parse_package_and_class


GRADLE_TEST_TASKS = {
    "T03": ":iceberg-core:test",
    "T04": ":clients:test",
    "T16": ":micronaut-inject:test",
    "T17": ":test-suite:test",
    "T18": ":instrumentation-api:test",
    "T27": ":reactor-core:test",
    "T28": ":aeron-client:test",
    "T29": ":aeron-cluster:test",
    "T31": ":jadx-core:test",
    "T32": ":jadx-core:test",
}

JAVA_VERSION_BY_TARGET = {
    "T08": "25.0.1-tem",
    "T15": "17.0.13-tem",
    "T20": "25.0.1-tem",
    "T16": "25.0.1-tem",
    "T17": "25.0.1-tem",
    "T21": "25.0.1-tem",
    "T26": "25.0.1-tem",
}

MAVEN_EXTRA_ARGS = {
    "T05": ["-Dspotbugs.skip=true"],
    "T06": ["-Dspotbugs.skip=true"],
    "T08": ["-DskipChecks=true", "-Dnullaway.skip=true"],
    "T14": ["-Dmaven.gitcommitid.skip=true"],
    # ServerMainTest expects the packaged web console resource. Source-only
    # checkouts do not contain it; the project's own profile downloads and
    # assembles the version pinned by the frozen POM.
    "T26": ["-Pbuild-web-console"],
}

# These projects either run tests through a non-standard Maven fork property or
# declare JaCoCo only in an inactive profile. Instrumenting the test process via
# its inherited environment preserves the project's own test command while
# still writing one phase-local execution file.
MAVEN_ENV_AGENT_TARGETS = {
    "T01", "T02", "T05", "T06", "T07", "T08", "T09", "T10", "T14",
    "T19", "T20", "T21", "T22", "T23", "T24", "T25", "T26", "T30",
}

# Mockito's inline mock maker can no longer self-attach reliably on the JDK
# used for the frozen Spring Boot Admin revision. Resolve the Byte Buddy agent
# from that revision's own test dependency tree rather than borrowing a jar
# from the pipeline runtime.
MAVEN_BYTE_BUDDY_AGENT_TARGETS = {"T09"}
GRADLE_NATIVE_JACOCO_TARGETS = {"T18", "T27"}


def _java_home_for_target(target_id: str) -> Path | None:
    version = JAVA_VERSION_BY_TARGET.get(target_id, "21.0.7-tem")
    configured = os.environ.get(f"RQ4_JAVA_{version.split('.', 1)[0]}_HOME", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path.home() / ".sdkman" / "candidates" / "java" / version,
    ]
    return next((path for path in candidates if path is not None and path.is_dir()), None)


@dataclass(frozen=True)
class NativeCoverageResult:
    status: str
    line_covered: int = 0
    line_total: int = 0
    branch_covered: int = 0
    branch_total: int = 0
    production_fqcn: str = ""
    test_fqcn: str = ""
    command: str = ""
    detail: str = ""


def _run(command: list[str], *, cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _maven_byte_buddy_agent(
    *, repo: Path, test_path: str, timeout_seconds: int,
) -> Path | None:
    """Resolve the exact Byte Buddy agent selected by the frozen Maven build."""
    module = _test_module(test_path)
    wrapper = "./mvnw" if (repo / "mvnw").exists() else "mvn"
    command = [wrapper]
    if module:
        command.extend(["-pl", module])
    command.extend([
        "org.apache.maven.plugins:maven-dependency-plugin:3.8.1:tree",
        "-Dincludes=net.bytebuddy:byte-buddy-agent",
        "-DoutputType=text",
    ])
    try:
        completed = _run(command, cwd=repo, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return None
    if completed.returncode:
        return None
    matches = re.findall(
        r"net\.bytebuddy:byte-buddy-agent:jar:([^:\s]+)(?::[^:\s]+)?:test",
        completed.stdout,
    )
    if len(set(matches)) != 1:
        return None
    version = matches[0]
    local_repo = Path.home() / ".m2" / "repository"
    candidate = (
        local_repo / "net" / "bytebuddy" / "byte-buddy-agent" / version
        / f"byte-buddy-agent-{version}.jar"
    )
    return candidate if candidate.is_file() else None


def prepare_shared_checkout(source_repo: Path, destination: Path, sha: str) -> tuple[bool, str]:
    """Materialize one immutable revision as a temporary Git worktree."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    added = _run(
        [
            "git", "-C", str(source_repo), "worktree", "add",
            "--quiet", "--detach", "--force", str(destination), sha,
        ],
        cwd=source_repo,
    )
    if added.returncode:
        return False, added.stdout[-4000:]
    return True, ""


def remove_shared_checkout(source_repo: Path, destination: Path) -> tuple[bool, str]:
    removed = _run(
        ["git", "-C", str(source_repo), "worktree", "remove", "--force", str(destination)],
        cwd=source_repo,
    )
    if removed.returncode:
        return False, removed.stdout[-4000:]
    return True, ""


def prepare_target_checkout(target_id: str, source_repo: Path, checkout: Path) -> None:
    """Apply non-source checkout compatibility needed by a native build."""
    if target_id != "T14":
        return
    # Hazelcast's module-local git-commit-id plugin uses an old JGit version
    # that cannot read modern linked-worktree metadata. Give that temporary
    # module a format-0 metadata repository with one empty commit. This only
    # supplies build metadata; the checked-out tracked sources remain exactly
    # those of the measured base/submitted revision.
    module = checkout / "hazelcast"
    _run(["git", "init", "--quiet"], cwd=module)
    _run(
        [
            "git", "-c", "user.name=IntegrateMe Coverage",
            "-c", "user.email=coverage@invalid", "commit", "--quiet",
            "--allow-empty", "-m", "coverage metadata",
        ],
        cwd=module,
    )


def resolve_production_source(
    repo: Path, simple_class: str, preferred_path: str = ""
) -> Path | None:
    if preferred_path:
        preferred = repo / preferred_path
        if preferred.is_file():
            return preferred
    candidates = sorted(
        path for path in repo.rglob(f"{simple_class}.java")
        if "/src/main/" in path.as_posix()
    )
    if len(candidates) == 1:
        return candidates[0]
    java_candidates = [path for path in candidates if "/src/main/java/" in path.as_posix()]
    return java_candidates[0] if len(java_candidates) == 1 else None


def _test_module(test_path: str) -> str:
    marker = "/src/test/java/"
    return test_path.split(marker, 1)[0] if marker in test_path else ""


def native_test_command(
    *, target_id: str, repo: Path, build_tool: str,
    test_path: str, test_fqcns: list[str],
) -> list[str]:
    if build_tool == "gradle":
        wrapper = "./gradlew" if (repo / "gradlew").exists() else "gradle"
        task = GRADLE_TEST_TASKS.get(target_id)
        if not task:
            module = _test_module(test_path).strip("/")
            task = ":" + module.replace("/", ":") + ":test" if module else "test"
        command = [wrapper, "--no-daemon", task]
        for test_fqcn in test_fqcns:
            command.extend(["--tests", test_fqcn])
        return command + ["--rerun-tasks", "--no-build-cache"]

    wrapper = "./mvnw" if (repo / "mvnw").exists() else "mvn"
    module = _test_module(test_path)
    common = [wrapper]
    if module:
        common.extend(["-pl", module, "-am"])
    if target_id == "T07":
        return common + [
            "-Punit", f"-Dit.test={','.join(test_fqcns)}",
            "-Dfailsafe.failIfNoSpecifiedTests=false", "-DskipTests=false",
            "-Dmaven.test.skip=false", "verify",
        ]
    phase = "package" if target_id in {"T05", "T06"} else "test"
    return common + [
        f"-Dtest={','.join(test_fqcns)}", "-Dsurefire.failIfNoSpecifiedTests=false", phase,
    ]


def _classfiles_root(repo: Path, production_fqcn: str) -> Path | None:
    relative = Path(*production_fqcn.split(".")).with_suffix(".class")
    for class_file in repo.rglob(relative.name):
        try:
            root = class_file.parents[len(relative.parts) - 1]
        except IndexError:
            continue
        if root / relative == class_file:
            return root
    return None


def _maven_has_native_jacoco(repo: Path, test_paths: list[str]) -> bool:
    pom_paths = [repo / "pom.xml"]
    pom_paths.extend(
        repo / _test_module(path) / "pom.xml"
        for path in test_paths if _test_module(path)
    )
    return any(
        path.is_file()
        and "<goal>prepare-agent</goal>" in path.read_text(
            encoding="utf-8", errors="ignore"
        )
        for path in pom_paths
    )


def _native_maven_exec(repo: Path, test_paths: list[str]) -> Path | None:
    module_execs = [
        repo / _test_module(path) / "target" / "jacoco.exec"
        for path in test_paths if _test_module(path)
    ]
    for path in module_execs:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return next(
        (
            path for path in repo.rglob("jacoco.exec")
            if path.is_file() and path.stat().st_size > 0
        ),
        None,
    )


def _native_gradle_exec(repo: Path) -> Path | None:
    return next(
        (
            path for path in repo.rglob("*.exec")
            if path.is_file() and path.stat().st_size > 0
            and "jacoco" in path.as_posix().lower()
        ),
        None,
    )


def _source_file_stats(
    *, jacoco_cli: Path, exec_file: Path, classfiles: Path,
    production_fqcn: str, report_dir: Path,
) -> tuple[int, int, int, int] | None:
    report_dir.mkdir(parents=True, exist_ok=True)
    relative = Path(*production_fqcn.split("."))
    class_parent = classfiles / relative.parent
    analysis_root = report_dir / "cut-classfiles"
    shutil.rmtree(analysis_root, ignore_errors=True)
    focused_classfiles = analysis_root / relative.parent
    focused_classfiles.mkdir(parents=True, exist_ok=True)
    for class_file in class_parent.glob(f"{relative.name}*.class"):
        shutil.copy2(class_file, focused_classfiles / class_file.name)
    xml_path = report_dir / "jacoco.xml"
    report = _run(
        [
            "java", "-jar", str(jacoco_cli), "report", str(exec_file),
            "--classfiles", str(analysis_root), "--xml", str(xml_path), "--quiet",
        ],
        cwd=report_dir,
    )
    (report_dir / "jacoco-report.log").write_text(
        report.stdout, encoding="utf-8", errors="ignore"
    )
    if report.returncode or not xml_path.exists():
        return None
    package_name, simple_name = production_fqcn.rsplit(".", 1)
    package_slash = package_name.replace(".", "/")
    root = ET.parse(xml_path).getroot()
    source = root.find(f"./package[@name='{package_slash}']/sourcefile[@name='{simple_name}.java']")
    if source is None:
        return None
    counters = {counter.attrib["type"]: counter.attrib for counter in source.findall("counter")}
    line = counters.get("LINE", {"covered": "0", "missed": "0"})
    branch = counters.get("BRANCH", {"covered": "0", "missed": "0"})
    line_covered = int(line["covered"])
    branch_covered = int(branch["covered"])
    return (
        line_covered,
        line_covered + int(line["missed"]),
        branch_covered,
        branch_covered + int(branch["missed"]),
    )


def measure_native_test_file(
    *, target_id: str, repo: Path, target_class: str, test_paths: list[str],
    production_path: str,
    build_tool: str, jacoco_agent: Path, jacoco_cli: Path,
    output_dir: Path, timeout_seconds: int,
) -> NativeCoverageResult:
    available_test_sources = [repo / path for path in test_paths if (repo / path).exists()]
    if not available_test_sources:
        return NativeCoverageResult(status="test_file_absent")
    test_fqcns: list[str] = []
    for test_source in available_test_sources:
        package, test_class = parse_package_and_class(test_source)
        if not test_class:
            return NativeCoverageResult(status="invalid_test_source", detail=str(test_source))
        test_fqcns.append(f"{package}.{test_class}" if package else test_class)
    test_fqcn = ";".join(test_fqcns)
    production_source = resolve_production_source(
        repo, target_class, preferred_path=production_path
    )
    if production_source is None:
        return NativeCoverageResult(
            status="production_source_ambiguous", test_fqcn=test_fqcn,
            detail=f"cannot resolve {target_class}.java",
        )
    production_package, production_class = parse_package_and_class(production_source)
    production_fqcn = (
        f"{production_package}.{production_class}" if production_package else production_class
    )
    command = native_test_command(
        target_id=target_id, repo=repo, build_tool=build_tool,
        test_path=test_paths[0], test_fqcns=test_fqcns,
    )
    if build_tool == "maven":
        command[-1:-1] = MAVEN_EXTRA_ARGS.get(target_id, [])
    exec_file = output_dir / "jacoco.exec"
    exec_file.parent.mkdir(parents=True, exist_ok=True)
    exec_file.unlink(missing_ok=True)
    agent_option = (
        f"-javaagent:{jacoco_agent.resolve()}="
        f"destfile={exec_file.resolve()},append=true,excludes=org.jacoco.*"
    )
    env = dict(os.environ)
    java_home = _java_home_for_target(target_id)
    if java_home is not None:
        env["JAVA_HOME"] = str(java_home)
        env["PATH"] = f"{java_home / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    if build_tool == "maven" and target_id in MAVEN_BYTE_BUDDY_AGENT_TARGETS:
        byte_buddy_agent = _maven_byte_buddy_agent(
            repo=repo, test_path=test_paths[0], timeout_seconds=timeout_seconds,
        )
        if byte_buddy_agent is None:
            return NativeCoverageResult(
                status="runtime_agent_unresolved", production_fqcn=production_fqcn,
                test_fqcn=test_fqcn, command=shlex.join(command),
                detail="cannot resolve the frozen build's Byte Buddy agent",
            )
        existing_options = env.get("JAVA_TOOL_OPTIONS", "").strip()
        byte_buddy_option = f"-javaagent:{byte_buddy_agent.resolve()}"
        env["JAVA_TOOL_OPTIONS"] = " ".join(
            option for option in (existing_options, byte_buddy_option) if option
        )
    use_maven_env_agent = build_tool == "maven" and target_id in MAVEN_ENV_AGENT_TARGETS
    native_maven_jacoco = (
        build_tool == "maven"
        and not use_maven_env_agent
        and _maven_has_native_jacoco(repo, test_paths)
    )
    if build_tool == "gradle":
        if target_id not in GRADLE_NATIVE_JACOCO_TARGETS:
            init_script = output_dir / "rq3-jacoco.init.gradle"
            escaped_agent = agent_option.replace("\\", "\\\\").replace("'", "\\'")
            init_script.write_text(
                "allprojects {\n"
                "  tasks.withType(org.gradle.api.tasks.testing.Test).configureEach {\n"
                f"    environment 'JAVA_TOOL_OPTIONS', '{escaped_agent}'\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            command.extend(["--init-script", str(init_script.resolve())])
    elif native_maven_jacoco:
        command[-1:-1] = [
            "-Djacoco.version=0.8.14",
            "-Djacoco-maven-plugin.version=0.8.14",
        ]
    else:
        if use_maven_env_agent:
            command[-1:-1] = ["-Djacoco.skip=true"]
            env["JAVA_TOOL_OPTIONS"] = " ".join(
                option for option in (env.get("JAVA_TOOL_OPTIONS", "").strip(), agent_option)
                if option
            )
        else:
            command[-1:-1] = [
                "-Djacoco.skip=true",
                f"-DargLine={agent_option}",
                f"-DsurefireArgLine={agent_option}",
            ]
    try:
        completed = _run(command, cwd=repo, timeout=timeout_seconds, env=env)
    except subprocess.TimeoutExpired as error:
        (output_dir / "build.log").write_text((error.stdout or "")[-20000:], encoding="utf-8", errors="ignore")
        return NativeCoverageResult(
            status="timeout", production_fqcn=production_fqcn,
            test_fqcn=test_fqcn, command=shlex.join(command),
        )
    (output_dir / "build.log").write_text(completed.stdout, encoding="utf-8", errors="ignore")
    if native_maven_jacoco and not exec_file.exists():
        native_exec = _native_maven_exec(repo, test_paths)
        if native_exec is not None:
            exec_file.write_bytes(native_exec.read_bytes())
    if build_tool == "gradle" and not exec_file.exists():
        native_exec = _native_gradle_exec(repo)
        if native_exec is not None:
            exec_file.write_bytes(native_exec.read_bytes())
    if not exec_file.exists():
        return NativeCoverageResult(
            status="no_exec", production_fqcn=production_fqcn,
            test_fqcn=test_fqcn, command=shlex.join(command),
            detail=f"exit={completed.returncode}",
        )
    classfiles = _classfiles_root(repo, production_fqcn)
    if classfiles is None:
        return NativeCoverageResult(
            status="no_classfiles", production_fqcn=production_fqcn,
            test_fqcn=test_fqcn, command=shlex.join(command),
            detail=f"exit={completed.returncode}",
        )
    stats = _source_file_stats(
        jacoco_cli=jacoco_cli, exec_file=exec_file, classfiles=classfiles,
        production_fqcn=production_fqcn, report_dir=output_dir,
    )
    if stats is None:
        return NativeCoverageResult(
            status="report_failed", production_fqcn=production_fqcn,
            test_fqcn=test_fqcn, command=shlex.join(command),
            detail=f"exit={completed.returncode}; classfiles={classfiles}",
        )
    return NativeCoverageResult(
        status="passed" if completed.returncode == 0 else "test_failed",
        line_covered=stats[0], line_total=stats[1],
        branch_covered=stats[2], branch_total=stats[3],
        production_fqcn=production_fqcn, test_fqcn=test_fqcn,
        command=shlex.join(command), detail=f"exit={completed.returncode}",
    )
