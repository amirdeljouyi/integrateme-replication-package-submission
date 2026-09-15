from __future__ import annotations

import csv
import glob
import hashlib
import os
import re
import select
import shutil
import signal
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, TYPE_CHECKING, Optional, Sequence, Set

from ..core.common import candidate_repo_class_dirs, detect_junit_version, ensure_dir, parse_package_and_class, shlex_join
from ..core.coverage import write_coverage_diagnostic_row, write_coverage_error_row, write_coverage_row
from ..core.java import (
    add_throws_exception_to_tests,
    add_throws_exception_to_error_methods,
    categorize_compile_problem,
    compile_test_set_smart,
    expand_same_package_support_sources,
    extract_compile_problem_detail,
    prefer_repo_manual_sources,
    prune_generated_methods_from_compile_output,
    resolve_external_runtime_classpath_from_output,
    resolve_repo_runtime_classpath,
)
from ..pipeline.helpers import (
    adopted_variants,
    expand_manual_sources,
    find_scaffolding_source,
    first_test_fqcn_from_sources,
    first_test_source_for_fqcn,
    is_empty_generated_test_source,
    improved_test_path,
    reduced_test_path,
    reduced_variant_test_path,
)
from ..pipeline.config import selected_adopted_variants
from .base import Step
from .covfilter import _materialize_pr_covfilter_sources

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext

JAVA_OPENS = [
    "--add-opens=java.desktop/java.awt=ALL-UNNAMED",
    "--add-opens=java.base/java.lang=ALL-UNNAMED",
    "--add-opens=java.base/java.util=ALL-UNNAMED",
    "--add-opens=java.base/java.io=ALL-UNNAMED",
    "--add-opens=java.base/java.net=ALL-UNNAMED",
    "--add-opens=java.base/jdk.internal.misc=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.code=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.comp=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.file=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.main=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.model=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.parser=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.processing=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.tree=ALL-UNNAMED",
    "--add-opens=jdk.compiler/com.sun.tools.javac.util=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.code=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.comp=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.file=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.main=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.model=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.parser=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.processing=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.tree=ALL-UNNAMED",
    "--add-exports=jdk.compiler/com.sun.tools.javac.util=ALL-UNNAMED",
    "--add-exports=java.base/jdk.internal.misc=ALL-UNNAMED",
    "-Djava.awt.headless=true",
    "-Dnet.bytebuddy.experimental=true",
]


def _absolutize_cp_entry(entry: str) -> str:
    candidate = (entry or "").strip()
    if not candidate:
        return ""
    if "*" in candidate:
        star_index = candidate.index("*")
        prefix = candidate[:star_index]
        suffix = candidate[star_index + 1 :]
        abs_prefix = Path(prefix or ".").resolve()
        return f"{abs_prefix}*{suffix}" if str(abs_prefix).endswith("/") else f"{abs_prefix}/*{suffix}"
    return str(Path(candidate).resolve())


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


def _expand_same_module_imported_test_sources(
    repo_root: Optional[Path], java_files: Sequence[Path]
) -> List[Path]:
    expanded: List[Path] = []
    seen: Set[Path] = set()
    queue: List[Path] = []
    import_pattern = re.compile(r"^\s*import\s+(static\s+)?([A-Za-z_][A-Za-z0-9_$.]*)(\.\*)?\s*;")

    def add(path: Path) -> None:
        if path in seen or not path.exists():
            return
        seen.add(path)
        expanded.append(path)
        queue.append(path)

    for java_file in java_files:
        add(java_file)

    while queue:
        java_file = queue.pop(0)
        source = java_file
        if repo_root is not None and repo_root.exists():
            counterpart = prefer_repo_manual_sources(repo_root, [java_file])
            if counterpart and counterpart[0].exists():
                source = counterpart[0]

        module_dir = _source_module_dir(source)
        if module_dir is None:
            continue

        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for raw_line in text.splitlines():
            match = import_pattern.match(raw_line)
            if not match or match.group(3):
                continue
            target = match.group(2)
            imported_type = target.rsplit(".", 1)[0] if match.group(1) else target
            if "." not in imported_type:
                continue
            rel_path = Path(*imported_type.split(".")).with_suffix(".java")
            for test_root in (module_dir / "src" / "test" / "java", module_dir / "src" / "test" / "kotlin"):
                candidate = test_root / rel_path
                if candidate.exists():
                    add(candidate)
                    break

    return expanded


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _runtime_workdir_and_resources(
    *,
    test_src: Path,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
) -> tuple[Optional[Path], List[Path]]:
    source_for_module = test_src
    if repo_root_for_deps and repo_root_for_deps.exists() and "/collected-tests/manual/" in test_src.as_posix():
        preferred_sources = prefer_repo_manual_sources(repo_root_for_deps, [test_src])
        if preferred_sources and preferred_sources[0].exists():
            source_for_module = preferred_sources[0]

    module_dir = _source_module_dir(source_for_module)
    if module_dir is None and repo_root_for_deps and repo_root_for_deps.exists():
        rel = (module_rel or "").strip()
        candidate = repo_root_for_deps if not rel or rel in {".", "root"} else (repo_root_for_deps / rel)
        if candidate.exists():
            module_dir = candidate

    resources: List[Path] = []
    seen = set()

    def add(path: Optional[Path]) -> None:
        if path is None or not path.exists() or path in seen:
            return
        seen.add(path)
        resources.append(path)

    if module_dir is not None:
        add(module_dir)
        add(module_dir / "src" / "test" / "resources")
        add(module_dir / "src" / "main" / "resources")
        add(module_dir / "build" / "resources" / "test")
        add(module_dir / "build" / "resources" / "main")
    return module_dir, resources


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


def _compile_service_provider_sources(
    *,
    provider_sources: Sequence[Path],
    compiled_tests_dir: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    log_file: Path,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
    build_tool: str,
) -> bool:
    if not provider_sources:
        return False
    expanded_sources = expand_same_package_support_sources(repo_root_for_deps, list(provider_sources))
    expanded_sources = _expand_same_module_imported_test_sources(repo_root_for_deps, expanded_sources)
    ok, _tail, _compiled_sources = compile_test_set_smart(
        java_files=expanded_sources,
        build_dir=compiled_tests_dir,
        libs_glob_cp=libs_glob_cp,
        sut_jar=sut_jar,
        log_file=log_file,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
        build_tool=build_tool,
        max_rounds=3,
    )
    return ok


def _test_framework_runtime_cp(test_src: Path) -> str:
    try:
        text = test_src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    targets: List[str] = []
    if "org.evosuite.runtime" in text:
        targets.extend(
            [
                "org.evosuite.runtime.EvoRunner",
                "org.evosuite.runtime.ViolatedAssumptionAnswer",
                "org.evosuite.runtime.annotation.EvoSuiteClassExclude",
                "org.evosuite.runtime.vnet.NonFunctionalRequirementRule",
                "org.objectweb.asm.ClassVisitor",
                "org.objectweb.asm.commons.Method",
                "org.objectweb.asm.tree.ClassNode",
                "org.objectweb.asm.tree.analysis.Analyzer",
                "org.objectweb.asm.util.Printer",
                "org.slf4j.LoggerFactory",
            ]
        )
    if "org.testng" in text:
        targets.extend(
            [
                "org.testng.annotations.Test",
                "org.junit.support.testng.engine.TestNGTestEngine",
                "com.beust.jcommander.ParameterException",
            ]
        )
    if "org.junit.jupiter" in text:
        targets.extend(
            [
                "io.kotest.mpp.Logger",
            ]
        )
    if "MockitoExtension" in text or "org.mockito.Mock" in text:
        targets.extend(
            [
                "org.mockito.Mockito",
                "org.mockito.junit.jupiter.MockitoExtension",
            ]
        )
    if "org.jboss.arquillian.junit.Arquillian" in text:
        targets.extend(
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
    if not targets:
        return ""
    return resolve_external_runtime_classpath_from_output(
        "\n".join(f"ClassNotFoundException: {target.replace('.', '/')}" for target in targets)
    )


def _expand_classpath_entries(classpath: str) -> List[str]:
    entries: List[str] = []
    for raw_entry in classpath.split(":"):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "*" in entry:
            matches = sorted(glob.glob(entry))
            if matches:
                entries.extend(matches)
                continue
        entries.append(entry)
    return entries


def _uses_regular_mockito_evosuite_runtime(text: str) -> bool:
    return (
        "org.mockito.Mockito" in text
        and "new ViolatedAssumptionAnswer()" in text
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


def _junit_platform_jar(artifact: str, version: str) -> str:
    m2_candidate = (
        Path.home()
        / ".m2"
        / "repository"
        / "org"
        / "junit"
        / "platform"
        / artifact
        / version
        / f"{artifact}-{version}.jar"
    )
    if m2_candidate.exists():
        return str(m2_candidate)
    gradle_root = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1" / "org.junit.platform" / artifact / version
    matches = sorted(gradle_root.glob(f"*/{artifact}-{version}.jar"))
    return str(matches[-1]) if matches else ""


def _junit_jupiter_jar(artifact: str, version: str) -> str:
    m2_candidate = (
        Path.home()
        / ".m2"
        / "repository"
        / "org"
        / "junit"
        / "jupiter"
        / artifact
        / version
        / f"{artifact}-{version}.jar"
    )
    if m2_candidate.exists():
        return str(m2_candidate)
    gradle_root = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1" / "org.junit.jupiter" / artifact / version
    matches = sorted(gradle_root.glob(f"*/{artifact}-{version}.jar"))
    return str(matches[-1]) if matches else ""


def _version_sort_key(version: str) -> tuple:
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


def _junit_platform_version_for_jupiter_version(jupiter_version: str) -> str:
    major, _, rest = jupiter_version.partition(".")
    if major == "5" and rest:
        return f"1.{rest}"
    return jupiter_version


def _ensure_junit_jupiter_engine(classpath: str, *, test_text: str) -> str:
    if "org.junit.jupiter" not in test_text:
        return classpath

    entries = [entry.strip() for entry in classpath.split(":") if entry.strip()]
    if any(re.match(r"junit-jupiter-engine-(.+)\.jar$", Path(entry).name) for entry in entries):
        return classpath

    versions: Set[str] = set()
    for entry in entries:
        match = re.match(r"junit-jupiter-(?:api|params)-(.+)\.jar$", Path(entry).name)
        if match:
            versions.add(match.group(1))

    additions: List[str] = []
    for version in sorted(versions, key=_version_sort_key, reverse=True):
        engine_jar = _junit_jupiter_jar("junit-jupiter-engine", version)
        if not engine_jar:
            continue
        additions.append(engine_jar)
        platform_version = _junit_platform_version_for_jupiter_version(version)
        for artifact in ("junit-platform-commons", "junit-platform-engine", "junit-platform-launcher"):
            jar_path = _junit_platform_jar(artifact, platform_version)
            if jar_path:
                additions.append(jar_path)
        break

    if additions:
        return _merge_classpath_strings(":".join(additions), classpath)

    fallback_cp = resolve_external_runtime_classpath_from_output(
        "ClassNotFoundException: org/junit/jupiter/engine/JupiterTestEngine"
    )
    if not fallback_cp:
        return classpath
    return _merge_classpath_strings(fallback_cp, classpath)


def _matching_junit_platform_jars(classpath: str) -> str:
    candidates: List[tuple[str, str]] = []
    seen_versions: Set[str] = set()
    for entry in classpath.split(":"):
        candidate = entry.strip()
        if not candidate:
            continue
        name = Path(candidate).name
        jupiter_match = re.match(r"junit-jupiter-engine-(.+)\.jar$", name)
        if jupiter_match:
            jupiter_version = jupiter_match.group(1)
            platform_version = _junit_platform_version_for_jupiter_version(jupiter_version)
            if platform_version not in seen_versions:
                seen_versions.add(platform_version)
                candidates.append((platform_version, jupiter_version))
    for entry in classpath.split(":"):
        candidate = entry.strip()
        if not candidate:
            continue
        name = Path(candidate).name
        match = re.match(r"junit-platform-(?:engine|commons)-(.+)\.jar$", name)
        if match and match.group(1) not in seen_versions:
            seen_versions.add(match.group(1))
            candidates.append((match.group(1), ""))
    if not candidates:
        return ""

    fallback_jars: List[str] = []
    for version, jupiter_version in candidates:
        jars: List[str] = []
        if jupiter_version:
            api_jar = _junit_jupiter_jar("junit-jupiter-api", jupiter_version)
            if api_jar:
                jars.append(api_jar)
        platform_jars = [
            _junit_platform_jar(artifact, version)
            for artifact in ("junit-platform-commons", "junit-platform-engine", "junit-platform-launcher")
        ]
        jars.extend(jar for jar in platform_jars if jar)
        if not fallback_jars:
            fallback_jars = jars
        if all(platform_jars):
            return ":".join(jars)
    return ":".join(fallback_jars)


def _with_matching_junit_platform_jars(classpath: str) -> str:
    platform_jars = _matching_junit_platform_jars(classpath)
    if not platform_jars:
        return classpath
    return _merge_classpath_strings(platform_jars, classpath)


def _matching_gradle_artifact_jar(group_path: str, artifact: str, version: str) -> str:
    gradle_root = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1" / group_path / artifact / version
    matches = sorted(gradle_root.glob(f"*/{artifact}-{version}.jar"))
    return str(matches[-1]) if matches else ""


def _align_logback_core(entries: Sequence[str]) -> List[str]:
    classic_version = ""
    for entry in entries:
        match = re.match(r"logback-classic-(.+)\.jar$", Path(entry).name)
        if match:
            classic_version = match.group(1)
            break
    if not classic_version:
        return list(entries)

    matching_core = ""
    for entry in entries:
        if Path(entry).name == f"logback-core-{classic_version}.jar":
            matching_core = entry
            break
    if not matching_core:
        matching_core = _matching_gradle_artifact_jar(
            "ch.qos.logback",
            "logback-core",
            classic_version,
        )
    if not matching_core:
        return list(entries)

    aligned: List[str] = []
    inserted_core = False
    for entry in entries:
        name = Path(entry).name
        if name.startswith("logback-core-") and name.endswith(".jar"):
            continue
        if not inserted_core and name == f"logback-classic-{classic_version}.jar":
            aligned.append(matching_core)
            inserted_core = True
        aligned.append(entry)
    if not inserted_core:
        aligned.insert(0, matching_core)
    return aligned


def _effective_libs_cp_for_test(*, libs_glob_cp: str, test_src: Path) -> str:
    try:
        text = test_src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libs_glob_cp

    if "org.evosuite.runtime" not in text:
        return libs_glob_cp

    uses_regular_mockito_evosuite_runtime = _uses_regular_mockito_evosuite_runtime(text)
    framework_cp = _test_framework_runtime_cp(test_src)
    if uses_regular_mockito_evosuite_runtime:
        framework_cp = _filter_evosuite_standalone_runtime(framework_cp)
    filtered: List[str] = []
    seen = set()
    removed_legacy_runtime = False
    for entry in [*_expand_classpath_entries(libs_glob_cp), *_expand_classpath_entries(framework_cp)]:
        path = Path(entry)
        if (
            path.name.startswith("utgen-runtime-")
            and path.suffix == ".jar"
            and not uses_regular_mockito_evosuite_runtime
        ):
            removed_legacy_runtime = True
            continue
        normalized = str(path.resolve() if path.exists() else path)
        if normalized in seen:
            continue
        seen.add(normalized)
        filtered.append(normalized)

    if not removed_legacy_runtime and not framework_cp:
        return libs_glob_cp
    return ":".join(filtered)


@dataclass(frozen=True)
class TestExecutionResult:
    status: str
    error_category: str = ""
    error_detail: str = ""
    class_origin: str = ""
    class_origin_detail: str = ""


@dataclass(frozen=True)
class CoverageObservation:
    category: str = ""
    detail: str = ""


AGT_REMEDIATION_IGNORE = 'AGT remediation: failing generated assertion'


def _select_test_root(
    *,
    compiled_tests_dir: Path,
    fallback_test_class_dirs: List[Path],
    test_selector: str,
) -> tuple[Optional[Path], Path, List[Path]]:
    rel_class = Path(*test_selector.split(".")).with_suffix(".class")
    candidate_test_roots: List[Path] = [compiled_tests_dir, *fallback_test_class_dirs]
    ordered_roots: List[Path] = []
    seen_roots = set()
    for root in candidate_test_roots:
        if root in seen_roots:
            continue
        seen_roots.add(root)
        ordered_roots.append(root)

    selected_test_root: Optional[Path] = None
    expected_test_class = compiled_tests_dir / rel_class
    checked_locations: List[Path] = []
    for root in ordered_roots:
        expected = root / rel_class
        checked_locations.append(expected)
        if expected.exists():
            selected_test_root = root
            expected_test_class = expected
            break
    return selected_test_root, expected_test_class, checked_locations


def _build_runtime_cp(
    *,
    compiled_tests_dir: Path,
    fallback_test_class_dirs: List[Path],
    libs_glob_cp: str,
    sut_jar: Optional[Path],
    extra_runtime_cp: str,
    tool_jar: Path,
    test_selector: str,
    resource_roots: Optional[Sequence[Path]] = None,
    allowed_test_output_dirs: Optional[Sequence[Path]] = None,
) -> tuple[str, Optional[Path], Path, List[Path]]:
    selected_test_root, expected_test_class, checked_locations = _select_test_root(
        compiled_tests_dir=compiled_tests_dir,
        fallback_test_class_dirs=fallback_test_class_dirs,
        test_selector=test_selector,
    )
    cp_parts: List[str] = []
    ordered_roots: List[Path] = []
    seen_roots = set()
    allowed_test_output_dirs = list(allowed_test_output_dirs or [])
    for root in [compiled_tests_dir, *fallback_test_class_dirs]:
        if root in seen_roots:
            continue
        seen_roots.add(root)
        ordered_roots.append(root)

    filtered_runtime_cp = _sanitize_runtime_cp(extra_runtime_cp)
    if selected_test_root == compiled_tests_dir:
        filtered_runtime_cp = _filter_repo_test_output_entries(
            filtered_runtime_cp,
            keep_paths=allowed_test_output_dirs,
        )
    priority_runtime_cp, deferred_runtime_cp = _partition_runtime_cp_around_sut(
        filtered_runtime_cp,
        test_selector,
    )

    if selected_test_root is not None:
        cp_parts.append(_absolutize_cp_entry(str(selected_test_root)))
    if priority_runtime_cp:
        cp_parts.extend(_absolutize_cp_entry(part) for part in priority_runtime_cp.split(":") if part.strip())

    # The module's freshly built main output is the authoritative CUT. Fat JARs
    # can retain an older copy after a package migration (for example Seata's
    # io.seata -> org.apache.seata move), which changes both behavior and JaCoCo
    # denominators. Keep test outputs isolated, but load current main classes
    # ahead of the dependency fat JAR.
    priority_repo_roots = [
        root for root in ordered_roots
        if root.exists() and not _is_test_output_path(root)
    ]
    for root in priority_repo_roots:
        cp_parts.append(_absolutize_cp_entry(str(root)))
    if sut_jar is not None:
        cp_parts.append(_absolutize_cp_entry(str(sut_jar)))
    for root in ordered_roots:
        if selected_test_root is not None and root == selected_test_root:
            continue
        if root in priority_repo_roots:
            continue
        if (
            selected_test_root == compiled_tests_dir
            and _is_test_output_path(root)
            and not any(_path_is_within(root, allowed_root) for allowed_root in allowed_test_output_dirs)
        ):
            continue
        if root.exists():
            cp_parts.append(_absolutize_cp_entry(str(root)))
    if resource_roots:
        for root in resource_roots:
            if root.exists():
                cp_parts.append(_absolutize_cp_entry(str(root)))
    if deferred_runtime_cp:
        cp_parts.extend(_absolutize_cp_entry(part) for part in deferred_runtime_cp.split(":") if part.strip())
    cp_parts.append(_absolutize_cp_entry(libs_glob_cp))
    cp_parts.append(_absolutize_cp_entry(str(tool_jar)))
    return ":".join(cp_parts), selected_test_root, expected_test_class, checked_locations


def _partition_runtime_cp_around_sut(classpath: str, test_selector: str) -> tuple[str, str]:
    """Keep test-framework support before the SUT without shadowing the CUT.

    Generated tests need EvoSuite/ASM support early on the classpath, but moving
    the entire resolved repository classpath ahead of the instrumented SUT can
    load a different copy of the target class. Only framework jars receive
    priority; repository outputs and ordinary dependencies remain after the SUT.
    """
    if not classpath:
        return "", ""
    if "_ESTest" not in test_selector:
        return "", classpath

    priority: List[str] = []
    deferred: List[str] = []
    for raw_entry in classpath.split(":"):
        entry = raw_entry.strip()
        if not entry:
            continue
        name = Path(entry).name.lower()
        if name.startswith("asm-") or "evosuite" in name:
            priority.append(entry)
        else:
            deferred.append(entry)
    return ":".join(priority), ":".join(deferred)


def _run_verbose_junit5_probe(
    *,
    test_selector: str,
    classpath: str,
    timeout_ms: int | None,
    working_dir: Optional[Path] = None,
) -> str:
    java_args = list(JAVA_OPENS)
    if _classpath_has_class(classpath, "org/jboss/logmanager/LogManager.class"):
        java_args.append("-Djava.util.logging.manager=org.jboss.logmanager.LogManager")
    cmd = [
        "java",
        *java_args,
        "-Dcustomrunner.fullStackTraces=true",
        "-cp",
        classpath,
        "custom_runner.CustomRunnerJUnit5",
        test_selector,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        cwd=str(working_dir) if working_dir else None,
    )
    try:
        out, _ = proc.communicate(timeout=(timeout_ms / 1000) if timeout_ms and timeout_ms > 0 else None)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
    return out or ""


def _run_junit5_console_probe(
    *,
    test_selector: str,
    classpath: str,
    timeout_ms: int | None,
    working_dir: Optional[Path] = None,
) -> str:
    java_args = list(JAVA_OPENS)
    if _classpath_has_class(classpath, "org/jboss/logmanager/LogManager.class"):
        java_args.append("-Djava.util.logging.manager=org.jboss.logmanager.LogManager")
    cmd = [
        "java",
        *java_args,
        "-cp",
        classpath,
        "org.junit.platform.console.ConsoleLauncher",
        "execute",
        "--details=tree",
        f"--select-class={test_selector}",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        cwd=str(working_dir) if working_dir else None,
    )
    try:
        out, _ = proc.communicate(timeout=(timeout_ms / 1000) if timeout_ms and timeout_ms > 0 else None)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
    return out or ""


def _run_junit4_probe(
    *,
    test_selector: str,
    classpath: str,
    timeout_ms: int | None,
    working_dir: Optional[Path] = None,
) -> str:
    cmd = [
        "java",
        *JAVA_OPENS,
        "-cp",
        classpath,
        "org.junit.runner.JUnitCore",
        test_selector,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        cwd=str(working_dir) if working_dir else None,
    )
    try:
        out, _ = proc.communicate(timeout=(timeout_ms / 1000) if timeout_ms and timeout_ms > 0 else None)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
    return out or ""


def _parse_junit4_failed_methods(output: str, test_fqcn: str) -> List[str]:
    methods: List[str] = []
    seen: Set[str] = set()
    pattern = re.compile(r"^\d+\)\s+([A-Za-z0-9_$]+)\(([^)]+)\)$")
    for raw_line in output.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        method_name = match.group(1)
        owner = match.group(2)
        if owner != test_fqcn or method_name in seen:
            continue
        seen.add(method_name)
        methods.append(method_name)
    return methods


def _extract_junit_failure_types(output: str) -> List[str]:
    if not output:
        return []
    failure_types: List[str] = []
    seen = set()
    pattern = re.compile(r"^\s*(?:Caused by:\s+)?([A-Za-z0-9_$.]+(?:Error|Exception))(?::|\s|$)")
    for raw_line in output.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        failure_type = match.group(1)
        if failure_type in seen:
            continue
        seen.add(failure_type)
        failure_types.append(failure_type)
    return failure_types


def _method_span(lines: Sequence[str], method_name: str) -> Optional[tuple[int, int]]:
    method_pattern = re.compile(
        rf"^\s*(?:public|protected|private)\s+\S+\s+{re.escape(method_name)}\s*\("
    )
    start = None
    brace_depth = 0
    saw_open = False
    for idx, line in enumerate(lines):
        if start is None:
            if not method_pattern.match(line):
                continue
            start = idx
        brace_depth += line.count("{")
        if line.count("{") > 0:
            saw_open = True
        brace_depth -= line.count("}")
        if saw_open and brace_depth <= 0:
            return start, idx
    return None


def _method_has_generated_expected_exception_pattern(test_src: Path, method_name: str) -> bool:
    try:
        lines = test_src.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False
    span = _method_span(lines, method_name)
    if span is None:
        return False
    start, end = span
    body = "\n".join(lines[start : end + 1])
    return 'fail("Expecting exception:' in body and "catch(" in body


def _looks_like_safe_generated_assertion_failure(
    output: str,
    *,
    test_src: Optional[Path] = None,
    method_names: Optional[Sequence[str]] = None,
) -> bool:
    if not output:
        return False
    failure_types = _extract_junit_failure_types(output)
    hard_runtime_types = (
        "NoClassDefFoundError",
        "ClassNotFoundException",
        "NoSuchMethodError",
        "NoSuchFieldError",
        "IllegalAccessError",
        "IncompatibleClassChangeError",
        "ExceptionInInitializerError",
        "StackOverflowError",
        "OutOfMemoryError",
    )
    if any(failure_type.endswith(hard_runtime_types) for failure_type in failure_types):
        return False
    if any(
        token in output
        for token in (
            "AssertionError",
            "AssertionFailedError",
            "ComparisonFailure",
            "expected:<",
        )
    ):
        return True
    if test_src is None or not method_names:
        return False
    return any(
        _method_has_generated_expected_exception_pattern(test_src, method_name)
        for method_name in method_names
    )


def _strip_agt_remediation_ignores(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    kept: List[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if (
            stripped.startswith("@org.junit.Ignore")
            or stripped.startswith("@Ignore")
        ) and AGT_REMEDIATION_IGNORE in stripped:
            changed = True
            continue
        kept.append(line)
    return "".join(kept), changed


def _generated_runtime_source_dir(coverage_tmp_dir: Path, test_fqcn: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", test_fqcn).replace(".", "_")
    return coverage_tmp_dir / "runtime-generated-sources" / safe_name


def _copy_scaffolding_for_runtime_source(original_test_src: Path, runtime_test_src: Path) -> Optional[Path]:
    original_scaffolding = original_test_src.with_name(
        original_test_src.name.replace("_ESTest.java", "_ESTest_scaffolding.java")
    )
    if not original_scaffolding.exists():
        return None
    runtime_scaffolding = runtime_test_src.with_name(original_scaffolding.name)
    try:
        runtime_scaffolding.write_text(
            original_scaffolding.read_text(encoding="utf-8", errors="ignore"),
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return None
    return runtime_scaffolding


def _runtime_generated_sources(test_src: Path) -> List[Path]:
    sources = [test_src]
    direct_scaffolding = test_src.with_name(test_src.name.replace("_ESTest.java", "_ESTest_scaffolding.java"))
    if direct_scaffolding.exists():
        sources.append(direct_scaffolding)
    return sources


def _materialize_runtime_generated_source(
    *,
    test_src: Path,
    test_fqcn: str,
    coverage_tmp_dir: Path,
    strip_agt_ignores: bool = False,
) -> Optional[Path]:
    try:
        text = test_src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    if strip_agt_ignores:
        text, _changed = _strip_agt_remediation_ignores(text)

    scaffolding_name = test_src.name.replace("_ESTest.java", "_ESTest_scaffolding.java")
    scaffolding_text: Optional[str] = None
    original_scaffolding = test_src.with_name(scaffolding_name)
    if original_scaffolding.exists():
        try:
            scaffolding_text = original_scaffolding.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            scaffolding_text = None

    runtime_dir = _generated_runtime_source_dir(coverage_tmp_dir, test_fqcn)
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    ensure_dir(runtime_dir)
    runtime_src = runtime_dir / test_src.name
    try:
        runtime_src.write_text(text, encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if scaffolding_text is not None:
        try:
            runtime_src.with_name(scaffolding_name).write_text(scaffolding_text, encoding="utf-8", errors="ignore")
        except OSError:
            return None
    else:
        _copy_scaffolding_for_runtime_source(test_src, runtime_src)
    return runtime_src


def _prepare_existing_remediated_generated_source(
    *,
    test_src: Path,
    test_fqcn: str,
    compiled_tests_dir: Path,
    coverage_tmp_dir: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    run_log: Path,
    repo_root_for_deps: Optional[Path],
    module_rel: str,
    build_tool: str,
) -> tuple[Path, Optional[TestExecutionResult]]:
    if not test_src.name.endswith("_ESTest.java"):
        return test_src, None
    try:
        text = test_src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return test_src, None
    if AGT_REMEDIATION_IGNORE not in text:
        return test_src, None

    runtime_src = _materialize_runtime_generated_source(
        test_src=test_src,
        test_fqcn=test_fqcn,
        coverage_tmp_dir=coverage_tmp_dir,
        strip_agt_ignores=True,
    )
    if runtime_src is None:
        return test_src, TestExecutionResult(
            status="failed",
            error_category="runtime_source_prepare_failed",
            error_detail=f"could not materialize runtime copy for {test_src}",
        )

    compile_log = run_log.with_name(f"{run_log.stem}.runtime-source.compile.log")
    ok_runtime, tail_runtime, _compiled_runtime = compile_test_set_smart(
        java_files=_runtime_generated_sources(runtime_src),
        build_dir=compiled_tests_dir,
        libs_glob_cp=libs_glob_cp,
        sut_jar=sut_jar,
        log_file=compile_log,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
        build_tool=build_tool,
        max_rounds=3,
    )
    if not ok_runtime:
        return runtime_src, TestExecutionResult(
            status="failed",
            error_category="runtime_source_compile_failed",
            error_detail=tail_runtime,
        )
    return runtime_src, None


def _ignore_generated_junit4_methods(test_src: Path, method_names: Sequence[str]) -> bool:
    if not method_names:
        return False
    try:
        lines = test_src.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except OSError:
        return False

    targets = set(method_names)
    changed = False
    method_pattern = re.compile(r"^(\s*)(?:public|protected|private)\s+\S+\s+([A-Za-z0-9_$]+)\s*\(")
    idx = 0
    while idx < len(lines):
        match = method_pattern.match(lines[idx])
        if not match:
            idx += 1
            continue
        method_name = match.group(2)
        if method_name not in targets:
            idx += 1
            continue

        annotation_start = idx
        scan = idx - 1
        while scan >= 0 and lines[scan].strip().startswith("@"):
            annotation_start = scan
            scan -= 1
        if annotation_start < idx:
            filtered_annotations = []
            removed_agt_ignore = False
            for pos in range(annotation_start, idx):
                stripped = lines[pos].strip()
                if (
                    stripped.startswith("@org.junit.Ignore")
                    or stripped.startswith("@Ignore")
                ) and AGT_REMEDIATION_IGNORE in stripped:
                    removed_agt_ignore = True
                    changed = True
                    continue
                filtered_annotations.append(lines[pos])
            if removed_agt_ignore:
                lines[annotation_start:idx] = filtered_annotations
                idx = annotation_start + len(filtered_annotations)

        brace_depth = 0
        saw_open = False
        end_idx: Optional[int] = None
        probe = idx
        while probe < len(lines):
            line = lines[probe]
            brace_depth += line.count("{")
            if line.count("{") > 0:
                saw_open = True
            brace_depth -= line.count("}")
            if saw_open and brace_depth <= 0:
                end_idx = probe
                break
            probe += 1
        if end_idx is None:
            idx += 1
            continue

        fail_stmt = f'org.junit.Assert.fail("{AGT_REMEDIATION_IGNORE}");'
        method_text = "".join(lines[idx : end_idx + 1])
        if fail_stmt not in method_text:
            close_indent_match = re.match(r"^(\s*)", lines[end_idx])
            close_indent = close_indent_match.group(1) if close_indent_match else ""
            lines.insert(end_idx, f"{close_indent}    {fail_stmt}\n")
            changed = True
            idx = end_idx + 2
            continue
        idx = end_idx + 1

    if not changed:
        return False

    try:
        test_src.write_text("".join(lines), encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return True


def _materialize_generated_junit4_ignored_methods(
    *,
    test_src: Path,
    method_names: Sequence[str],
    test_fqcn: str,
    coverage_tmp_dir: Path,
) -> Optional[Path]:
    runtime_src = _materialize_runtime_generated_source(
        test_src=test_src,
        test_fqcn=test_fqcn,
        coverage_tmp_dir=coverage_tmp_dir,
    )
    if runtime_src is None:
        return None
    if not _ignore_generated_junit4_methods(runtime_src, method_names):
        return None
    return runtime_src


def _junit4_test_ignore_counts(test_src: Path) -> tuple[int, int]:
    try:
        lines = test_src.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return 0, 0

    total = 0
    ignored = 0
    annotations: List[str] = []
    method_pattern = re.compile(r"^\s*(?:public|protected|private)\s+\S+\s+[A-Za-z0-9_$]+\s*\(")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("@"):
            annotations.append(stripped)
            continue
        if method_pattern.match(line):
            has_test = any("@Test" in annotation or "@org.junit.Test" in annotation for annotation in annotations)
            if has_test:
                total += 1
                if any("@Ignore" in annotation or "@org.junit.Ignore" in annotation for annotation in annotations):
                    ignored += 1
            annotations = []
            continue
        if stripped and not stripped.startswith("//") and not stripped.startswith("*"):
            annotations = []
    return total, ignored


def _filter_repo_test_output_entries(classpath: str, *, keep_paths: Optional[Sequence[Path]] = None) -> str:
    if not classpath:
        return ""

    filtered: List[str] = []
    seen = set()
    keep_paths = list(keep_paths or [])
    test_markers = (
        "/target/test-classes",
        "/build/classes/java/test",
        "/build/classes/kotlin/test",
        "/build/resources/test",
    )
    for entry in classpath.split(":"):
        candidate = entry.strip()
        if not candidate:
            continue
        normalized = candidate.replace("\\", "/")
        if any(marker in normalized for marker in test_markers):
            candidate_path = Path(candidate)
            if not any(_path_is_within(candidate_path, keep_path) for keep_path in keep_paths):
                continue
        if candidate in seen:
            continue
        seen.add(candidate)
        filtered.append(candidate)
    return ":".join(filtered)


def _is_test_output_path(path: Path) -> bool:
    normalized = str(path).replace("\\", "/")
    return any(
        marker in normalized
        for marker in (
            "/target/test-classes",
            "/build/classes/java/test",
            "/build/classes/kotlin/test",
            "/build/resources/test",
        )
    )


def _sanitize_runtime_cp(classpath: str) -> str:
    if not classpath:
        return ""

    entries = [entry.strip() for entry in classpath.split(":") if entry.strip()]
    entries = _align_logback_core(entries)
    has_logback = any("logback-classic" in Path(entry).name for entry in entries)
    filtered: List[str] = []
    seen = set()
    for entry in entries:
        name = Path(entry).name
        if has_logback and "slf4j-simple" in name:
            continue
        if entry in seen:
            continue
        seen.add(entry)
        filtered.append(entry)
    return ":".join(filtered)


def _classpath_has_class(classpath: str, class_resource: str) -> bool:
    entries = [entry.strip() for entry in classpath.split(":") if entry.strip()]
    if class_resource == "org/jboss/logmanager/LogManager.class":
        # The normal runtime classpath includes the user's entire Maven cache.
        # JBoss LogManager is either in its own Maven artifact or in the target's
        # assembled jar; opening every unrelated Maven jar here can take minutes.
        entries = [
            entry
            for entry in entries
            if "/.m2/repository/" not in Path(entry).as_posix()
            or "/org/jboss/logmanager/" in Path(entry).as_posix()
            or "jboss-logmanager" in Path(entry).name.lower()
        ]

    for entry in entries:
        candidate = entry.strip()
        if not candidate or "*" in candidate:
            continue
        path = Path(candidate)
        try:
            if path.is_dir():
                if (path / Path(class_resource)).exists():
                    return True
                continue
            if path.is_file() and path.suffix == ".jar":
                try:
                    with zipfile.ZipFile(path) as jar_file:
                        jar_file.getinfo(class_resource)
                        return True
                except (KeyError, OSError, zipfile.BadZipFile):
                    continue
        except OSError:
            continue
    return False


def _merge_classpath_strings(*parts: str) -> str:
    merged: List[str] = []
    seen = set()
    for part in parts:
        if not part:
            continue
        for entry in part.split(":"):
            candidate = entry.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            merged.append(candidate)
    return ":".join(merged)


def _result_from_runner_output(
    *,
    output: str,
    test_selector: str,
    class_origin: str,
    class_origin_detail: str,
) -> Optional[TestExecutionResult]:
    for line in output.splitlines():
        if line.startswith("[JUnit5TestRunner] started="):
            parts = line.strip().split()
            started = 0
            skipped = 0
            failed = 0
            for part in parts:
                if part.startswith("started="):
                    try:
                        started = int(part.split("=", 1)[1])
                    except ValueError:
                        started = 0
                if part.startswith("skipped="):
                    try:
                        skipped = int(part.split("=", 1)[1])
                    except ValueError:
                        skipped = 0
                if part.startswith("failed="):
                    try:
                        failed = int(part.split("=", 1)[1])
                    except ValueError:
                        failed = 0
            if failed > 0:
                failure_category, failure_detail = _categorize_run_failure(output, test_selector)
                if failure_category not in {"assertion_failure", "test_process_failed"}:
                    return TestExecutionResult(
                        status="failed",
                        error_category=failure_category,
                        error_detail=failure_detail or line.strip(),
                        class_origin=class_origin,
                        class_origin_detail=class_origin_detail,
                    )
                return TestExecutionResult(
                    status="failed",
                    error_category="junit_test_failure",
                    error_detail=line.strip(),
                    class_origin=class_origin,
                    class_origin_detail=class_origin_detail,
                )
            if started == 0 and failed == 0 and skipped == 0:
                return TestExecutionResult(
                    status="skipped",
                    error_category="no_tests_discovered",
                    error_detail=line.strip(),
                    class_origin=class_origin,
                    class_origin_detail=class_origin_detail,
                )
            if skipped > 0 and failed == 0:
                return TestExecutionResult(
                    status="skipped",
                    error_category="junit_skipped",
                    error_detail=line.strip(),
                    class_origin=class_origin,
                    class_origin_detail=class_origin_detail,
                )
            return TestExecutionResult(
                status="passed",
                class_origin=class_origin,
                class_origin_detail=class_origin_detail,
            )
        if line.startswith("[JUnit4TestRunner] run="):
            parts = line.strip().split()
            run_count = 0
            failed = 0
            for part in parts:
                if part.startswith("run="):
                    try:
                        run_count = int(part.split("=", 1)[1])
                    except ValueError:
                        run_count = 0
                if part.startswith("failed="):
                    try:
                        failed = int(part.split("=", 1)[1])
                    except ValueError:
                        failed = 0
            if failed > 0:
                failure_category, failure_detail = _categorize_run_failure(output, test_selector)
                if failure_category not in {"assertion_failure", "test_process_failed"}:
                    return TestExecutionResult(
                        status="failed",
                        error_category=failure_category,
                        error_detail=failure_detail or line.strip(),
                        class_origin=class_origin,
                        class_origin_detail=class_origin_detail,
                    )
                return TestExecutionResult(
                    status="failed",
                    error_category="junit_test_failure",
                    error_detail=line.strip(),
                    class_origin=class_origin,
                    class_origin_detail=class_origin_detail,
                )
            if run_count == 0 and failed == 0:
                return TestExecutionResult(
                    status="skipped",
                    error_category="no_tests_discovered",
                    error_detail=line.strip(),
                    class_origin=class_origin,
                    class_origin_detail=class_origin_detail,
                )
            return TestExecutionResult(
                status="passed",
                class_origin=class_origin,
                class_origin_detail=class_origin_detail,
            )
    return None


def _has_signed_jar_manifest_error(output: str) -> bool:
    return "Invalid signature file digest for Manifest main attributes" in output


def _extract_run_error_detail(output: str) -> str:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(
            token in line
            for token in (
                "Error opening zip file",
                "agent library failed to init",
                "AssertionError",
                "AssertionFailedError",
                "ComparisonFailure",
                "NoClassDefFoundError",
                "ClassNotFoundException",
                "NoSuchMethodError",
                "NoSuchFieldError",
                "IllegalAccessError",
                "ExceptionInInitializerError",
                "Exception:",
                "Error:",
            )
        ):
            return line
    return ""


def _categorize_run_failure(output: str, test_selector: str) -> tuple[str, str]:
    if re.search(rf"ClassNotFoundException:\s+{re.escape(test_selector)}\b", output):
        return "not_compiled", _extract_run_error_detail(output)
    if "Error opening zip file" in output or "agent library failed to init" in output:
        return "coverage_agent_error", _extract_run_error_detail(output)
    if any(token in output for token in ("AssertionError", "AssertionFailedError", "ComparisonFailure")):
        return "assertion_failure", _extract_run_error_detail(output)
    if any(token in output for token in ("NoClassDefFoundError", "ClassNotFoundException")):
        return "missing_runtime_dependency", _extract_run_error_detail(output)
    if "ServiceConfigurationError" in output and "Provider" in output and "not found" in output:
        return "missing_runtime_dependency", _extract_run_error_detail(output)
    if any(
        token in output
        for token in (
            "NoSuchMethodError",
            "NoSuchFieldError",
            "IllegalAccessError",
            "IncompatibleClassChangeError",
            "AbstractMethodError",
        )
    ):
        return "linkage_error", _extract_run_error_detail(output)
    if "ExceptionInInitializerError" in output:
        return "initialization_error", _extract_run_error_detail(output)
    if "Exception:" in output or "Error:" in output:
        return "runtime_error", _extract_run_error_detail(output)
    return "test_process_failed", _extract_run_error_detail(output)


def _merge_sources(existing: List[Path], additions: List[Path]) -> List[Path]:
    merged: List[Path] = []
    seen = set()
    for source in [*existing, *additions]:
        if source in seen:
            continue
        seen.add(source)
        merged.append(source)
    return merged


def _source_identity(source: Path) -> tuple[str, str]:
    pkg, cls = parse_package_and_class(source)
    return pkg, cls


def _compiled_variant_sources(ctx: "TargetContext") -> tuple[List[Path], List[Path]]:
    manual_identities = {
        _source_identity(source)
        for source in ctx.manual_sources
        if _source_identity(source)[1]
    }
    compiled_manual_sources = [
        source
        for source in ctx.final_sources
        if _source_identity(source) in manual_identities
    ]
    compiled_generated_sources = [
        source
        for source in ctx.final_sources
        if _source_identity(source) not in manual_identities
    ]
    return compiled_manual_sources, compiled_generated_sources


def _compile_sources_for_variant(ctx: "TargetContext", *, variant: str, test_src: Path) -> List[Path]:
    if variant == "manual":
        manual_sources = list(ctx.manual_sources)
        if not manual_sources:
            manual_primary = prefer_repo_manual_sources(ctx.repo_root_for_deps, [test_src])
            manual_sources = expand_manual_sources(manual_primary)
        sources = expand_same_package_support_sources(ctx.repo_root_for_deps, manual_sources)
        return _expand_same_module_imported_test_sources(ctx.repo_root_for_deps, sources)

    generated_sources = [source for source in ctx.sources if source not in set(ctx.manual_sources)]
    if not generated_sources:
        generated_sources = [test_src]
    sources = list(generated_sources)
    if test_src not in sources:
        sources.append(test_src)
    scaffolding = find_scaffolding_source(test_src, list(ctx.sources))
    if scaffolding is not None:
        sources.append(scaffolding)
    return _merge_sources([], sources)


def _compile_stamp_path(compiled_tests_dir: Path, test_fqcn: str) -> Path:
    safe_name = test_fqcn.replace(".", "_")
    return compiled_tests_dir / ".agt-source-stamps" / f"{safe_name}.sha256"


def _source_set_fingerprint(java_files: Sequence[Path]) -> str:
    hasher = hashlib.sha256()
    seen: Set[Path] = set()
    ordered_sources: List[Path] = []
    for source in sorted(java_files, key=lambda item: str(item)):
        if source in seen:
            continue
        seen.add(source)
        ordered_sources.append(source)

    for source in ordered_sources:
        try:
            resolved = source.resolve()
        except OSError:
            resolved = source
        hasher.update(str(resolved).encode("utf-8", errors="ignore"))
        hasher.update(b"\0")
        try:
            hasher.update(resolved.read_bytes())
        except OSError:
            hasher.update(b"<missing>")
        hasher.update(b"\0")
    return hasher.hexdigest()


def _has_matching_compile_stamp(
    *,
    compiled_tests_dir: Path,
    test_fqcn: str,
    java_files: Sequence[Path],
) -> bool:
    stamp_path = _compile_stamp_path(compiled_tests_dir, test_fqcn)
    try:
        recorded = stamp_path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return False
    return bool(recorded) and recorded == _source_set_fingerprint(java_files)


def _write_compile_stamp(
    *,
    compiled_tests_dir: Path,
    test_fqcn: str,
    java_files: Sequence[Path],
) -> None:
    stamp_path = _compile_stamp_path(compiled_tests_dir, test_fqcn)
    ensure_dir(stamp_path.parent)
    stamp_path.write_text(_source_set_fingerprint(java_files) + "\n", encoding="utf-8")


def _ensure_run_build_fresh(
    *,
    ctx: "TargetContext",
    variant: str,
    test_src: Path,
    test_fqcn: str,
    compile_sources: Sequence[Path],
    logs_dir: Path,
    libs_glob_cp: str,
    dep_rounds: int,
) -> None:
    sources = list(compile_sources) or _compile_sources_for_variant(ctx, variant=variant, test_src=test_src)
    if not sources:
        return

    prep_compile_log = logs_dir / f"{ctx.target_id}__{test_src.stem}.{variant}.runprep.compile.log"
    if ctx.target_build.exists():
        shutil.rmtree(ctx.target_build, ignore_errors=True)
    ensure_dir(ctx.target_build)
    ok_prep, _tail_prep, _compiled_prep = compile_test_set_smart(
        java_files=sources,
        build_dir=ctx.target_build,
        libs_glob_cp=libs_glob_cp,
        sut_jar=ctx.sut_jar,
        log_file=prep_compile_log,
        repo_root_for_deps=ctx.repo_root_for_deps,
        module_rel=ctx.module_rel,
        build_tool=ctx.build_tool,
        max_rounds=dep_rounds,
    )
    if not ok_prep and variant == "manual" and ctx.repo_root_for_deps and ctx.repo_root_for_deps.exists():
        repo_manual_primary = prefer_repo_manual_sources(ctx.repo_root_for_deps, [test_src])
        if repo_manual_primary and list(repo_manual_primary) != [test_src]:
            repo_sources = expand_manual_sources(repo_manual_primary)
            repo_sources = expand_same_package_support_sources(ctx.repo_root_for_deps, repo_sources)
            repo_sources = _expand_same_module_imported_test_sources(ctx.repo_root_for_deps, repo_sources)
            if ctx.target_build.exists():
                shutil.rmtree(ctx.target_build, ignore_errors=True)
            ensure_dir(ctx.target_build)
            ok_prep, _tail_prep, _compiled_prep = compile_test_set_smart(
                java_files=repo_sources,
                build_dir=ctx.target_build,
                libs_glob_cp=libs_glob_cp,
                sut_jar=ctx.sut_jar,
                log_file=prep_compile_log,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
                max_rounds=dep_rounds,
            )
            if ok_prep:
                sources = repo_sources
    if not ok_prep:
        return

    ctx.final_sources = _merge_sources(ctx.final_sources, sources)
    _write_compile_stamp(
        compiled_tests_dir=ctx.target_build,
        test_fqcn=test_fqcn,
        java_files=sources,
    )


def _attempt_run_error_fix(
    *,
    ctx: "TargetContext",
    variant: str,
    test_src: Path,
    test_fqcn: str,
    exec_file: Path,
    run_log: Path,
    jacoco_cli: Path,
    logs_dir: Path,
    libs_glob_cp: str,
    jacoco_agent: Path,
    tool_jar: Path,
    timeout_ms: int | None,
    dep_rounds: int,
    coverage_tmp_dir: Path,
    compile_sources: Optional[List[Path]] = None,
) -> Tuple[Optional[TestExecutionResult], Tuple[int, int, int, int], CoverageObservation]:
    sources = compile_sources or _compile_sources_for_variant(ctx, variant=variant, test_src=test_src)

    add_throws_exception_to_tests(str(test_src), False)
    fix_compile_log = logs_dir / f"{ctx.target_id}__{test_src.stem}.{variant}.runfix.compile.log"
    if ctx.target_build.exists():
        shutil.rmtree(ctx.target_build, ignore_errors=True)
    ensure_dir(ctx.target_build)
    ok_fix, _tail_fix, _compiled_fix = compile_test_set_smart(
        java_files=sources,
        build_dir=ctx.target_build,
        libs_glob_cp=libs_glob_cp,
        sut_jar=ctx.sut_jar,
        log_file=fix_compile_log,
        repo_root_for_deps=ctx.repo_root_for_deps,
        module_rel=ctx.module_rel,
        build_tool=ctx.build_tool,
        max_rounds=dep_rounds,
    )
    if not ok_fix:
        compile_output = fix_compile_log.read_text(encoding="utf-8", errors="ignore")
        pruned_generated = prune_generated_methods_from_compile_output(sources, compile_output)
        changed = False
        for source in sources:
            changed = add_throws_exception_to_error_methods(str(source), compile_output, False) or changed
        if not changed and not pruned_generated:
            return None, (0, 0, 0, 0), CoverageObservation()
        ok_fix, _tail_fix, _compiled_fix = compile_test_set_smart(
            java_files=sources,
            build_dir=ctx.target_build,
            libs_glob_cp=libs_glob_cp,
            sut_jar=ctx.sut_jar,
            log_file=fix_compile_log,
            repo_root_for_deps=ctx.repo_root_for_deps,
            module_rel=ctx.module_rel,
            build_tool=ctx.build_tool,
            max_rounds=dep_rounds,
        )
        if not ok_fix and pruned_generated:
            second_output = fix_compile_log.read_text(encoding="utf-8", errors="ignore")
            if prune_generated_methods_from_compile_output(sources, second_output):
                ok_fix, _tail_fix, _compiled_fix = compile_test_set_smart(
                    java_files=sources,
                    build_dir=ctx.target_build,
                    libs_glob_cp=libs_glob_cp,
                    sut_jar=ctx.sut_jar,
                    log_file=fix_compile_log,
                    repo_root_for_deps=ctx.repo_root_for_deps,
                    module_rel=ctx.module_rel,
                    build_tool=ctx.build_tool,
                    max_rounds=dep_rounds,
                )
    if not ok_fix:
        return None, (0, 0, 0, 0), CoverageObservation()

    ctx.final_sources = _merge_sources(ctx.final_sources, sources)
    _write_compile_stamp(
        compiled_tests_dir=ctx.target_build,
        test_fqcn=test_fqcn,
        java_files=sources,
    )
    result, stats, _, observation = run_test_with_coverage(
        test_src=test_src,
        test_fqcn=test_fqcn,
        compiled_tests_dir=ctx.target_build,
        exec_file=exec_file,
        run_log=run_log,
        sut_jar=ctx.sut_jar,
        libs_glob_cp=libs_glob_cp,
        jacoco_agent=jacoco_agent,
        tool_jar=tool_jar,
        timeout_ms=timeout_ms,
        jacoco_cli=jacoco_cli,
        target_fqcn=ctx.fqcn,
        coverage_tmp_dir=coverage_tmp_dir,
        repo_root_for_deps=ctx.repo_root_for_deps,
        module_rel=ctx.module_rel,
        build_tool=ctx.build_tool,
    )
    return result, stats, observation


def _write_coverage_observation_if_needed(
    *,
    csv_zero_hit: Path,
    csv_report_issues: Path,
    repo: str,
    fqcn: str,
    variant: str,
    test_fqcn: str,
    result: TestExecutionResult,
    stats: Tuple[int, int, int, int],
    observation: CoverageObservation,
    log_file: Path,
) -> None:
    if result.status != "passed" or not observation.category:
        return

    csv_path = csv_zero_hit if observation.category == "no_target_hit" else csv_report_issues
    write_coverage_diagnostic_row(
        csv_path=csv_path,
        repo=repo,
        fqcn=fqcn,
        variant=variant,
        test_fqcn=test_fqcn,
        status=result.status,
        coverage_category=observation.category,
        coverage_detail=observation.detail,
        line_covered=stats[0],
        line_total=stats[1],
        branch_covered=stats[2],
        branch_total=stats[3],
        class_origin=result.class_origin,
        class_origin_detail=result.class_origin_detail,
        log_file=str(log_file),
    )


class JacocoTestRunner:
    def __init__(
            self,
            *,
            libs_glob_cp: str,
            compiled_tests_dir: Path,
            sut_jar: Path,
            jacoco_agent_jar: Path,
            tool_jar: Path,
            timeout_ms: int | None,
            java_opens: List[str] | None = None,
            fallback_test_class_dirs: Optional[List[Path]] = None,
            extra_runtime_cp: str = "",
            working_dir: Optional[Path] = None,
            resource_roots: Optional[Sequence[Path]] = None,
            allowed_test_output_dirs: Optional[Sequence[Path]] = None,
            include_sut_jar: bool = True,
    ) -> None:
        self.libs_glob_cp = libs_glob_cp
        self.compiled_tests_dir = compiled_tests_dir
        self.sut_jar = sut_jar
        self.jacoco_agent_jar = jacoco_agent_jar
        self.tool_jar = tool_jar
        self.timeout_ms = timeout_ms
        self.java_opens = list(java_opens) if java_opens else list(JAVA_OPENS)
        self.fallback_test_class_dirs = list(fallback_test_class_dirs or [])
        self.extra_runtime_cp = (extra_runtime_cp or "").strip()
        self.working_dir = working_dir
        self.resource_roots = list(resource_roots or [])
        self.allowed_test_output_dirs = list(allowed_test_output_dirs or [])
        self.include_sut_jar = include_sut_jar

    def run_test(self, *, test_selector: str, jacoco_exec_file: Path, log_file: Path) -> TestExecutionResult:
        cp, selected_test_root, expected_test_class, checked_locations = _build_runtime_cp(
            compiled_tests_dir=self.compiled_tests_dir,
            fallback_test_class_dirs=self.fallback_test_class_dirs,
            libs_glob_cp=self.libs_glob_cp,
            sut_jar=self.sut_jar if self.include_sut_jar else None,
            extra_runtime_cp=self.extra_runtime_cp,
            tool_jar=self.tool_jar,
            test_selector=test_selector,
            resource_roots=self.resource_roots,
            allowed_test_output_dirs=self.allowed_test_output_dirs,
        )

        # Some inventory entries point to tests that don't compile for a given target.
        # If the class file isn't present, treat this as skipped rather than a hard failure.
        if selected_test_root is None:
            checked_text = "\n".join(str(p) for p in checked_locations)
            log_file.write_text(
                (
                    f"[agt] run skipped:\n"
                    f'test class "{test_selector}" is not present under compiled test classes.\n'
                    f"expected class file: {expected_test_class}\n"
                    f"checked class files:\n{checked_text}\n"
                    f"classpath would have been:\n{cp}\n"
                ),
                encoding="utf-8",
                errors="ignore",
            )
            return TestExecutionResult(
                status="skipped",
                error_category="not_compiled",
                error_detail='test class is not present under compiled or fallback class dirs',
            )

        class_origin = "build_output"
        class_origin_detail = str(selected_test_root)
        if selected_test_root != self.compiled_tests_dir:
            class_origin = "repo_class_fallback"

        java_args = list(self.java_opens)
        if _classpath_has_class(cp, "org/jboss/logmanager/LogManager.class"):
            java_args.append("-Djava.util.logging.manager=org.jboss.logmanager.LogManager")

        cmd = [
            "java", *java_args,
            f"-javaagent:{Path(self.jacoco_agent_jar).resolve()}=destfile={Path(jacoco_exec_file).resolve()}",
            "-cp",
            cp,
            "app.RunOne",
        ]
        if self.timeout_ms and self.timeout_ms > 0:
            cmd.extend(["--timeout-ms", str(self.timeout_ms)])
        cmd.append(test_selector)

        # JaCoCo appends sessions when destfile already exists. A pipeline run
        # represents one execution attempt, so stale sessions from an earlier
        # run (or a retry with a different CUT build) must not be retained.
        jacoco_exec_file.unlink(missing_ok=True)
        timed_out = False
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            cwd=str(self.working_dir) if self.working_dir else None,
        )
        deadline = (
            time.monotonic() + (self.timeout_ms / 1000)
            if self.timeout_ms and self.timeout_ms > 0
            else None
        )
        summary_seen = False
        summary_termination_deadline: float | None = None
        chunks: List[str] = []
        try:
            while True:
                if proc.stdout is None:
                    break
                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if ready:
                    line = proc.stdout.readline()
                    if line:
                        chunks.append(line)
                        if not summary_seen:
                            current_output = "".join(chunks)
                            if _result_from_runner_output(
                                output=current_output,
                                test_selector=test_selector,
                                class_origin=class_origin,
                                class_origin_detail=class_origin_detail,
                            ) is not None:
                                summary_seen = True
                                summary_termination_deadline = time.monotonic() + 5.0
                    elif proc.poll() is not None:
                        break
                elif proc.poll() is not None:
                    break

                if summary_seen and summary_termination_deadline is not None and time.monotonic() >= summary_termination_deadline:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        tail_out, _ = proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        tail_out, _ = proc.communicate()
                    if tail_out:
                        chunks.append(tail_out)
                    break

                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(cmd=cmd, timeout=self.timeout_ms / 1000)

            out = "".join(chunks)
            tail_out, _ = proc.communicate(timeout=0)
            if tail_out:
                out += tail_out
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                out, _ = proc.communicate()
            if chunks:
                out = "".join(chunks) + (out or "")

        out = out or ""
        log_file.write_text(
            f"[agt] run cmd:\n{shlex_join(cmd)}\n\n[agt] timed_out={timed_out}\n\n[agt] output:\n{out}\n",
            encoding="utf-8",
            errors="ignore",
        )
        completed_result = _result_from_runner_output(
            output=out,
            test_selector=test_selector,
            class_origin=class_origin,
            class_origin_detail=class_origin_detail,
        )
        if timed_out:
            if completed_result is not None:
                return completed_result
            error_category, error_detail = _categorize_run_failure(out, test_selector)
            if error_category not in {"test_process_failed", "timeout"} and error_detail:
                return TestExecutionResult(
                    status="failed",
                    error_category=error_category,
                    error_detail=error_detail,
                    class_origin=class_origin,
                    class_origin_detail=class_origin_detail,
                )
            return TestExecutionResult(
                status="timeout",
                error_category="timeout",
                class_origin=class_origin,
                class_origin_detail=class_origin_detail,
            )
        if completed_result is not None:
            return completed_result
        if proc.returncode == 0:
            return TestExecutionResult(
                status="passed",
                class_origin=class_origin,
                class_origin_detail=class_origin_detail,
            )
        m = re.search(r"ClassNotFoundException:\s+([A-Za-z0-9_.$]+)", out)
        if m and m.group(1) == test_selector:
            return TestExecutionResult(
                status="skipped",
                error_category="not_compiled",
                error_detail=_extract_run_error_detail(out),
                class_origin=class_origin,
                class_origin_detail=class_origin_detail,
            )
        error_category, error_detail = _categorize_run_failure(out, test_selector)
        return TestExecutionResult(
            status="failed",
            error_category=error_category,
            error_detail=error_detail,
            class_origin=class_origin,
            class_origin_detail=class_origin_detail,
        )


def run_one_test_with_jacoco(
        *,
        junit_version: int,
        test_fqcn: str,
        sut_jar: Path,
        libs_glob_cp: str,
        compiled_tests_dir: Path,
        jacoco_agent_jar: Path,
        jacoco_exec_file: Path,
        log_file: Path,
        tool_jar: Path,
        timeout_ms: int | None,
        fallback_test_class_dirs: Optional[List[Path]] = None,
        extra_runtime_cp: str = "",
        working_dir: Optional[Path] = None,
        resource_roots: Optional[Sequence[Path]] = None,
        allowed_test_output_dirs: Optional[Sequence[Path]] = None,
        include_sut_jar: bool = True,
) -> TestExecutionResult:
    """
    Run a single test class with JaCoCo using the Java tool's RunOne.
    junit_version: 4 or 5 (RunOne detects it automatically, but we keep the parameter for compatibility)
    """
    return JacocoTestRunner(
        libs_glob_cp=libs_glob_cp,
        compiled_tests_dir=compiled_tests_dir,
        sut_jar=sut_jar,
        jacoco_agent_jar=jacoco_agent_jar,
        tool_jar=tool_jar,
        timeout_ms=timeout_ms,
        fallback_test_class_dirs=fallback_test_class_dirs,
        extra_runtime_cp=extra_runtime_cp,
        working_dir=working_dir,
        resource_roots=resource_roots,
        allowed_test_output_dirs=allowed_test_output_dirs,
        include_sut_jar=include_sut_jar,
    ).run_test(
        test_selector=test_fqcn,
        jacoco_exec_file=jacoco_exec_file,
        log_file=log_file,
    )


def run_test_with_coverage(
    *,
    test_src: Path,
    compiled_tests_dir: Path,
    exec_file: Path,
    run_log: Path,
    sut_jar: Path,
    libs_glob_cp: str,
    jacoco_agent: Path,
    tool_jar: Path,
    timeout_ms: int | None,
    jacoco_cli: Path,
    target_fqcn: str,
    coverage_tmp_dir: Path,
    test_fqcn: str | None = None,
    repo_root_for_deps: Optional[Path] = None,
    module_rel: str = "",
    build_tool: str = "",
) -> Tuple[TestExecutionResult, Tuple[int, int, int, int], str | None, CoverageObservation]:
    if not test_fqcn:
        pkg, cls = parse_package_and_class(test_src)
        if not cls:
            return TestExecutionResult(status="skipped", error_category="parse_error"), (0, 0, 0, 0), None, CoverageObservation()
        test_fqcn = f"{pkg}.{cls}" if pkg else cls

    test_src, prepare_error = _prepare_existing_remediated_generated_source(
        test_src=test_src,
        test_fqcn=test_fqcn,
        compiled_tests_dir=compiled_tests_dir,
        coverage_tmp_dir=coverage_tmp_dir,
        libs_glob_cp=libs_glob_cp,
        sut_jar=sut_jar,
        run_log=run_log,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
        build_tool=build_tool,
    )
    if prepare_error is not None:
        return prepare_error, (0, 0, 0, 0), test_fqcn, CoverageObservation()

    junit_ver = detect_junit_version(test_src)
    fallback_test_class_dirs = candidate_repo_class_dirs(repo_root_for_deps, module_rel)
    working_dir, resource_roots = _runtime_workdir_and_resources(
        test_src=test_src,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
    )
    allowed_test_output_dirs = [
        path for path in fallback_test_class_dirs
        if working_dir is not None and _path_is_within(path, working_dir)
    ]
    extra_runtime_cp = ""
    if repo_root_for_deps and repo_root_for_deps.exists():
        extra_runtime_cp = resolve_repo_runtime_classpath(
            repo_root_for_deps,
            module_rel,
            build_tool,
            source_files=[test_src],
        )
    framework_runtime_cp = _test_framework_runtime_cp(test_src)
    try:
        test_text = test_src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        test_text = ""
    if _uses_regular_mockito_evosuite_runtime(test_text):
        extra_runtime_cp = _filter_evosuite_standalone_runtime(extra_runtime_cp)
        framework_runtime_cp = _filter_evosuite_standalone_runtime(framework_runtime_cp)
    if "org.evosuite.runtime" in test_text:
        extra_runtime_cp = _merge_classpath_strings(
            framework_runtime_cp,
            extra_runtime_cp,
        )
    else:
        extra_runtime_cp = _merge_classpath_strings(
            extra_runtime_cp,
            framework_runtime_cp,
        )
    extra_runtime_cp = _ensure_junit_jupiter_engine(extra_runtime_cp, test_text=test_text)
    extra_runtime_cp = _with_matching_junit_platform_jars(extra_runtime_cp)
    include_sut_jar = True
    attempted_service_providers: Set[str] = set()

    def classify_discovery_result(raw_result: TestExecutionResult) -> TestExecutionResult:
        if raw_result.error_category != "no_tests_discovered" or junit_ver != 4:
            return raw_result
        total_tests, ignored_tests = _junit4_test_ignore_counts(test_src)
        if total_tests > 0 and ignored_tests >= total_tests:
            return TestExecutionResult(
                status="skipped",
                error_category="all_tests_ignored",
                error_detail=f"all {total_tests} JUnit4 tests are annotated with @Ignore",
                class_origin=raw_result.class_origin,
                class_origin_detail=raw_result.class_origin_detail,
            )
        return raw_result

    def run_with_cp(runtime_cp: str, *, include_target_jar: bool) -> TestExecutionResult:
        return classify_discovery_result(run_one_test_with_jacoco(
            junit_version=junit_ver,
            test_fqcn=test_fqcn,
            sut_jar=sut_jar,
            libs_glob_cp=libs_glob_cp,
            compiled_tests_dir=compiled_tests_dir,
            jacoco_agent_jar=jacoco_agent,
            jacoco_exec_file=exec_file,
            log_file=run_log,
            tool_jar=tool_jar,
            timeout_ms=timeout_ms,
            fallback_test_class_dirs=fallback_test_class_dirs,
            extra_runtime_cp=runtime_cp,
            working_dir=working_dir,
            resource_roots=resource_roots,
            allowed_test_output_dirs=allowed_test_output_dirs,
            include_sut_jar=include_target_jar,
        ))

    result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
    for _ in range(8):
        run_output = run_log.read_text(encoding="utf-8", errors="ignore")
        if include_sut_jar and _has_signed_jar_manifest_error(run_output):
            include_sut_jar = False
            result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
            continue

        if result.error_category in {"missing_runtime_dependency", "initialization_error", "linkage_error"}:
            missing_providers = [
                provider
                for provider in _extract_missing_service_provider_classes(run_output)
                if provider not in attempted_service_providers
            ]
            if missing_providers:
                attempted_service_providers.update(missing_providers)
                provider_sources: List[Path] = []
                seen_provider_sources = set()
                for provider in missing_providers:
                    source_path = _source_path_for_provider_fqcn(
                        provider_fqcn=provider,
                        module_dir=working_dir,
                        repo_root_for_deps=repo_root_for_deps,
                        module_rel=module_rel,
                    )
                    if source_path is None or source_path in seen_provider_sources:
                        continue
                    seen_provider_sources.add(source_path)
                    provider_sources.append(source_path)

                if provider_sources:
                    service_compile_log = run_log.with_name(f"{run_log.stem}.service-provider.compile.log")
                    if _compile_service_provider_sources(
                        provider_sources=provider_sources,
                        compiled_tests_dir=compiled_tests_dir,
                        libs_glob_cp=libs_glob_cp,
                        sut_jar=sut_jar,
                        log_file=service_compile_log,
                        repo_root_for_deps=repo_root_for_deps,
                        module_rel=module_rel,
                        build_tool=build_tool,
                    ):
                        result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                        continue

            if include_sut_jar and result.error_category in {"initialization_error", "linkage_error"}:
                include_sut_jar = False
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue
            external_runtime_cp = resolve_external_runtime_classpath_from_output(run_output)
            retried_runtime_cp = _merge_classpath_strings(extra_runtime_cp, external_runtime_cp)
            if retried_runtime_cp and retried_runtime_cp != extra_runtime_cp:
                extra_runtime_cp = retried_runtime_cp
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue
            break

        if result.error_category == "junit_test_failure" and junit_ver == 5:
            probe_cp, _, _, _ = _build_runtime_cp(
                compiled_tests_dir=compiled_tests_dir,
                fallback_test_class_dirs=fallback_test_class_dirs,
                libs_glob_cp=libs_glob_cp,
                sut_jar=sut_jar if include_sut_jar else None,
                extra_runtime_cp=extra_runtime_cp,
                tool_jar=tool_jar,
                test_selector=test_fqcn,
                resource_roots=resource_roots,
                allowed_test_output_dirs=allowed_test_output_dirs,
            )
            verbose_output = _run_verbose_junit5_probe(
                test_selector=test_fqcn,
                classpath=probe_cp,
                timeout_ms=timeout_ms,
                working_dir=working_dir,
            )
            if not verbose_output:
                break
            with run_log.open("a", encoding="utf-8", errors="ignore") as handle:
                handle.write("\n[agt] junit5 verbose probe:\n")
                handle.write(verbose_output)
                if not verbose_output.endswith("\n"):
                    handle.write("\n")
            if include_sut_jar and _has_signed_jar_manifest_error(verbose_output):
                include_sut_jar = False
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue
            external_runtime_cp = resolve_external_runtime_classpath_from_output(verbose_output)
            retried_runtime_cp = _merge_classpath_strings(extra_runtime_cp, external_runtime_cp)
            if retried_runtime_cp and retried_runtime_cp != extra_runtime_cp:
                extra_runtime_cp = retried_runtime_cp
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue
            if any(token in verbose_output for token in ("NoClassDefFoundError", "ClassNotFoundException")):
                result = TestExecutionResult(
                    status="failed",
                    error_category="missing_runtime_dependency",
                    error_detail=_extract_run_error_detail(verbose_output),
                    class_origin=result.class_origin,
                    class_origin_detail=result.class_origin_detail,
                )
            break

        if result.error_category == "no_tests_discovered" and junit_ver == 5:
            probe_cp, _, _, _ = _build_runtime_cp(
                compiled_tests_dir=compiled_tests_dir,
                fallback_test_class_dirs=fallback_test_class_dirs,
                libs_glob_cp=libs_glob_cp,
                sut_jar=sut_jar if include_sut_jar else None,
                extra_runtime_cp=extra_runtime_cp,
                tool_jar=tool_jar,
                test_selector=test_fqcn,
                resource_roots=resource_roots,
                allowed_test_output_dirs=allowed_test_output_dirs,
            )
            console_output = _run_junit5_console_probe(
                test_selector=test_fqcn,
                classpath=probe_cp,
                timeout_ms=timeout_ms,
                working_dir=working_dir,
            )
            if not console_output:
                break
            with run_log.open("a", encoding="utf-8", errors="ignore") as handle:
                handle.write("\n[agt] junit5 console probe:\n")
                handle.write(console_output)
                if not console_output.endswith("\n"):
                    handle.write("\n")
            if include_sut_jar and _has_signed_jar_manifest_error(console_output):
                include_sut_jar = False
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue
            external_runtime_cp = resolve_external_runtime_classpath_from_output(console_output)
            retried_runtime_cp = _merge_classpath_strings(extra_runtime_cp, external_runtime_cp)
            if retried_runtime_cp and retried_runtime_cp != extra_runtime_cp:
                extra_runtime_cp = retried_runtime_cp
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue
            if any(token in console_output for token in ("NoClassDefFoundError", "ClassNotFoundException")):
                result = TestExecutionResult(
                    status="failed",
                    error_category="missing_runtime_dependency",
                    error_detail=_extract_run_error_detail(console_output),
                    class_origin=result.class_origin,
                    class_origin_detail=result.class_origin_detail,
                )
            break

        if result.error_category == "junit_test_failure" and junit_ver == 4:
            probe_cp, _, _, _ = _build_runtime_cp(
                compiled_tests_dir=compiled_tests_dir,
                fallback_test_class_dirs=fallback_test_class_dirs,
                libs_glob_cp=libs_glob_cp,
                sut_jar=sut_jar if include_sut_jar else None,
                extra_runtime_cp=extra_runtime_cp,
                tool_jar=tool_jar,
                test_selector=test_fqcn,
                resource_roots=resource_roots,
            )
            junit4_output = _run_junit4_probe(
                test_selector=test_fqcn,
                classpath=probe_cp,
                timeout_ms=timeout_ms,
                working_dir=working_dir,
            )
            if not junit4_output:
                break
            with run_log.open("a", encoding="utf-8", errors="ignore") as handle:
                handle.write("\n[agt] junit4 probe:\n")
                handle.write(junit4_output)
                if not junit4_output.endswith("\n"):
                    handle.write("\n")
            if include_sut_jar and _has_signed_jar_manifest_error(junit4_output):
                include_sut_jar = False
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue
            external_runtime_cp = resolve_external_runtime_classpath_from_output(junit4_output)
            retried_runtime_cp = _merge_classpath_strings(extra_runtime_cp, external_runtime_cp)
            if retried_runtime_cp and retried_runtime_cp != extra_runtime_cp:
                extra_runtime_cp = retried_runtime_cp
                result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
                continue

            if not test_src.name.endswith("_ESTest.java"):
                if any(token in junit4_output for token in ("NoClassDefFoundError", "ClassNotFoundException")):
                    result = TestExecutionResult(
                        status="failed",
                        error_category="missing_runtime_dependency",
                        error_detail=_extract_run_error_detail(junit4_output),
                        class_origin=result.class_origin,
                        class_origin_detail=result.class_origin_detail,
                    )
                break

            failed_methods = _parse_junit4_failed_methods(junit4_output, test_fqcn)
            if not failed_methods or not _looks_like_safe_generated_assertion_failure(
                junit4_output,
                test_src=test_src,
                method_names=failed_methods,
            ):
                break
            remediated_src = _materialize_generated_junit4_ignored_methods(
                test_src=test_src,
                method_names=failed_methods,
                test_fqcn=test_fqcn,
                coverage_tmp_dir=coverage_tmp_dir,
            )
            if remediated_src is None:
                break
            test_src = remediated_src

            generated_sources = _runtime_generated_sources(test_src)
            fix_compile_log = run_log.with_name(f"{run_log.stem}.autofix.compile.log")
            ok_fix, _tail_fix, _compiled_fix = compile_test_set_smart(
                java_files=generated_sources,
                build_dir=compiled_tests_dir,
                libs_glob_cp=libs_glob_cp,
                sut_jar=sut_jar,
                log_file=fix_compile_log,
                repo_root_for_deps=repo_root_for_deps,
                module_rel=module_rel,
                build_tool=build_tool,
                max_rounds=3,
            )
            if not ok_fix:
                break
            result = run_with_cp(extra_runtime_cp, include_target_jar=include_sut_jar)
            continue

        break

    stats = (0, 0, 0, 0)
    observation = CoverageObservation()
    if result.status in {"passed", "failed"}:
        if jacoco_cli.exists():
            stats, observation = get_coverage_stats(
                jacoco_cli,
                exec_file,
                sut_jar,
                target_fqcn,
                coverage_tmp_dir,
                preferred_classfiles_paths=[
                    Path(result.class_origin_detail)
                    for detail in [result.class_origin_detail]
                    if detail
                ] + candidate_repo_class_dirs(repo_root_for_deps, module_rel),
            )
            if (
                not observation.category
                and stats[1] > 0
                and stats[0] == 0
            ):
                observation = CoverageObservation(
                    category="no_target_hit",
                    detail=f"JaCoCo found the target class {target_fqcn}, but this test covered 0 of {stats[1]} lines",
                )
        else:
            observation = CoverageObservation(
                category="jacoco_cli_missing",
                detail=f"JaCoCo CLI jar is missing: {jacoco_cli}",
            )

    return result, stats, test_fqcn, observation


def get_coverage_stats(
    jacoco_cli_jar: Path,
    jacoco_exec_file: Path,
    sut_jar: Path,
    target_fqcn: str,
    temp_dir: Path,
    preferred_classfiles_paths: Sequence[Path] | None = None,
) -> Tuple[Tuple[int, int, int, int], CoverageObservation]:
    """
    Returns (line_covered, line_total, branch_covered, branch_total)
    """
    if not jacoco_exec_file.exists():
        return (0, 0, 0, 0), CoverageObservation(
            category="jacoco_exec_missing",
            detail=f"JaCoCo exec file was not produced: {jacoco_exec_file}",
        )

    csv_report = temp_dir / "jacoco_report.csv"

    def target_fqcn_aliases(fqcn: str) -> list[str]:
        aliases = [fqcn]
        if fqcn.startswith("io.seata."):
            aliases.append(f"org.apache.seata.{fqcn[len('io.seata.'):]}")
        elif fqcn.startswith("org.apache.seata."):
            aliases.append(f"io.seata.{fqcn[len('org.apache.seata.'):]}")
        return aliases

    def run_report(classfiles_path: Path) -> bool:
        if csv_report.exists():
            try:
                csv_report.unlink()
            except Exception:
                pass
        cmd = [
            "java", "-jar", str(jacoco_cli_jar),
            "report", str(jacoco_exec_file),
            "--classfiles", str(classfiles_path),
            "--csv", str(csv_report),
            "--quiet"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode == 0 and csv_report.exists()

    target_rel_class = Path(*target_fqcn.split(".")).with_suffix(".class")

    def has_target_class(classfiles_path: Path) -> bool:
        if not classfiles_path.exists():
            return False
        if classfiles_path.is_dir():
            return (classfiles_path / target_rel_class).exists()
        if classfiles_path.is_file() and classfiles_path.suffix == ".jar":
            try:
                with zipfile.ZipFile(classfiles_path) as z:
                    return target_rel_class.as_posix() in z.namelist()
            except OSError:
                return False
        return False

    report_inputs: list[Path] = []
    for path in preferred_classfiles_paths or ():
        if has_target_class(path) and path not in report_inputs:
            report_inputs.append(path)
    if has_target_class(sut_jar) and sut_jar not in report_inputs:
        report_inputs.append(sut_jar)
    if not report_inputs:
        report_inputs.append(sut_jar)

    def parse_current_report() -> Tuple[Tuple[int, int, int, int], CoverageObservation]:
        stats_by_fqcn: dict[str, list[int]] = {}

        try:
            with csv_report.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # JaCoCo CSV columns: PACKAGE, CLASS, ... LINE_MISSED, LINE_COVERED, BRANCH_MISSED, BRANCH_COVERED
                    pkg = row.get("PACKAGE", "")
                    cls = row.get("CLASS", "")
                    row_fqcn = f"{pkg}.{cls}" if pkg else cls
                    bucket = stats_by_fqcn.setdefault(row_fqcn, [0, 0, 0, 0])
                    lm = int(row.get("LINE_MISSED", 0))
                    lc = int(row.get("LINE_COVERED", 0))
                    bm = int(row.get("BRANCH_MISSED", 0))
                    bc = int(row.get("BRANCH_COVERED", 0))
                    bucket[0] += lc
                    bucket[1] += (lc + lm)
                    bucket[2] += bc
                    bucket[3] += (bc + bm)
        except Exception:
            return (0, 0, 0, 0), CoverageObservation(
                category="report_parse_failed",
                detail=f"Failed to parse JaCoCo CSV report {csv_report}",
            )

        target_stats = tuple(stats_by_fqcn.get(target_fqcn, [0, 0, 0, 0]))
        alias_fqcns = target_fqcn_aliases(target_fqcn)
        alias_stats = [
            (alias_fqcn, tuple(stats_by_fqcn.get(alias_fqcn, [0, 0, 0, 0])))
            for alias_fqcn in alias_fqcns
            if alias_fqcn != target_fqcn
        ]

        matched_rows = bool(target_stats[1] or target_stats[3]) or any(
            stats[1] or stats[3] for _alias, stats in alias_stats
        )
        if not matched_rows:
            return (0, 0, 0, 0), CoverageObservation(
                category="target_not_found_in_report",
                detail=f"JaCoCo report did not contain an entry for target class {target_fqcn}",
            )

        # Compatibility remap: if legacy wrapper class has no hit, attribute coverage
        # to its Apache package counterpart when that class is actually exercised.
        if target_stats[0] == 0 and target_stats[2] == 0:
            for _alias_fqcn, stats in alias_stats:
                if stats[0] > 0 or stats[2] > 0:
                    target_stats = stats
                    break

        line_cov, line_tot, branch_cov, branch_tot = target_stats
        if line_tot == 0 and branch_tot == 0:
            return (line_cov, line_tot, branch_cov, branch_tot), CoverageObservation(
                category="target_has_no_counters",
                detail=f"JaCoCo reported target class {target_fqcn}, but it had no line or branch counters",
            )

        return (line_cov, line_tot, branch_cov, branch_tot), CoverageObservation()

    report_generated = False
    best_zero_coverage: Optional[Tuple[Tuple[int, int, int, int], CoverageObservation]] = None
    first_report_issue: Optional[Tuple[Tuple[int, int, int, int], CoverageObservation]] = None
    for classfiles_path in report_inputs:
        if run_report(classfiles_path):
            report_generated = True
            candidate_stats, candidate_observation = parse_current_report()
            if not candidate_observation.category and candidate_stats[0] > 0:
                return candidate_stats, candidate_observation
            if not candidate_observation.category and candidate_stats[1] > 0 and best_zero_coverage is None:
                best_zero_coverage = (candidate_stats, candidate_observation)
            elif candidate_observation.category and first_report_issue is None:
                first_report_issue = (candidate_stats, candidate_observation)

    def run_report_with_extracted_target_from_sut() -> Optional[Tuple[Tuple[int, int, int, int], CoverageObservation]]:
        if not (sut_jar.is_file() and sut_jar.suffix == ".jar"):
            return None
        safe = target_fqcn.replace(".", "_")
        tmp_classes = temp_dir / f"jacoco_classfiles_{safe}"
        if tmp_classes.exists():
            shutil.rmtree(tmp_classes, ignore_errors=True)
        tmp_classes.mkdir(parents=True, exist_ok=True)
        prefix = target_fqcn.replace(".", "/")
        try:
            with zipfile.ZipFile(sut_jar) as z:
                for info in z.infolist():
                    name = info.filename
                    if not name.endswith(".class"):
                        continue
                    if not name.startswith(prefix):
                        continue
                    z.extract(info, tmp_classes)
            if not any(tmp_classes.rglob("*.class")):
                return (0, 0, 0, 0), CoverageObservation(
                    category="target_classfiles_missing",
                    detail=f"Target class {target_fqcn} was not found inside {sut_jar}",
                )
            if not run_report(tmp_classes):
                return (0, 0, 0, 0), CoverageObservation(
                    category="report_generation_failed",
                    detail=f"JaCoCo report generation failed for extracted classfiles of {target_fqcn}",
                )
            return parse_current_report()
        finally:
            shutil.rmtree(tmp_classes, ignore_errors=True)

    if best_zero_coverage is not None:
        zero_stats, _zero_obs = best_zero_coverage
        if zero_stats[1] > 0 and zero_stats[0] == 0:
            extracted_result = run_report_with_extracted_target_from_sut()
            if extracted_result is not None:
                extracted_stats, extracted_observation = extracted_result
                if not extracted_observation.category and extracted_stats[0] > 0:
                    return extracted_stats, extracted_observation
        return best_zero_coverage
    if report_generated and first_report_issue is not None:
        return first_report_issue

    if not report_generated:
        extracted_result = run_report_with_extracted_target_from_sut()
        if extracted_result is not None:
            return extracted_result
        return (0, 0, 0, 0), CoverageObservation(
            category="report_generation_failed",
            detail=f"JaCoCo report generation failed for {sut_jar}",
        )

    return (0, 0, 0, 0), CoverageObservation(
        category="report_generation_failed",
        detail=f"JaCoCo report generation failed for {target_fqcn}",
    )


class RunStep(Step):
    step_names = ("run",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        auto_variant = self.pipeline.args.auto_variant
        if self.pipeline.args.skip_empty_tests:
            generated_candidates = [
                source for source in (ctx.final_sources or ctx.sources) if source not in set(ctx.manual_sources)
            ]
            generated_test_src = first_test_source_for_fqcn(generated_candidates, ctx.generated_test_fqcn)
            if generated_test_src is None and generated_candidates:
                generated_test_src = generated_candidates[0]
            if is_empty_generated_test_source(generated_test_src):
                print(f'[agt] run: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                return True
        jacoco_cli = self.pipeline.jacoco_agent.parent / "org.jacoco.cli-run-0.8.14.jar"
        tool_jar = Path(self.pipeline.args.tool_jar)
        wrote_manual = False
        wrote_auto = False
        selected_tests = {t for t in (ctx.manual_test_fqcn, ctx.generated_test_fqcn) if t}
        compiled_manual_sources, compiled_generated_sources = _compiled_variant_sources(ctx)

        for src in ctx.final_sources:
            pkg, cls = parse_package_and_class(src)
            if not cls:
                continue
            if "scaffolding" in cls.lower():
                continue

            test_fqcn = f"{pkg}.{cls}" if pkg else cls
            if selected_tests and test_fqcn not in selected_tests:
                continue
            variant = "manual" if test_fqcn == ctx.manual_test_fqcn else auto_variant
            effective_libs_cp = _effective_libs_cp_for_test(
                libs_glob_cp=self.pipeline.args.libs_cp,
                test_src=src,
            )
            compile_sources = _merge_sources(
                compiled_manual_sources if variant == "manual" else compiled_generated_sources,
                _compile_sources_for_variant(ctx, variant=variant, test_src=src),
            )
            _ensure_run_build_fresh(
                ctx=ctx,
                variant=variant,
                test_src=src,
                test_fqcn=test_fqcn,
                compile_sources=compile_sources,
                logs_dir=self.pipeline.logs_dir,
                libs_glob_cp=effective_libs_cp,
                dep_rounds=self.pipeline.args.dep_rounds,
            )
            exec_file = self.pipeline.out_dir / f"{ctx.target_id}__{cls}.exec"
            run_log = self.pipeline.logs_dir / f"{ctx.target_id}__{cls}.run.log"

            print(f"[agt] Running: {test_fqcn}")
            result, stats, _, observation = run_test_with_coverage(
                test_src=src,
                test_fqcn=test_fqcn,
                compiled_tests_dir=ctx.target_build,
                exec_file=exec_file,
                run_log=run_log,
                sut_jar=ctx.sut_jar,
                libs_glob_cp=effective_libs_cp,
                jacoco_agent=self.pipeline.jacoco_agent,
                tool_jar=tool_jar,
                timeout_ms=self.pipeline.args.timeout_ms,
                jacoco_cli=jacoco_cli,
                target_fqcn=ctx.fqcn,
                coverage_tmp_dir=self.pipeline.build_dir,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
            )
            if result.error_category == "not_compiled":
                fixed_result, fixed_stats, fixed_observation = _attempt_run_error_fix(
                    ctx=ctx,
                    variant=variant,
                    test_src=src,
                    test_fqcn=test_fqcn,
                    exec_file=exec_file,
                    run_log=run_log,
                    jacoco_cli=jacoco_cli,
                    logs_dir=self.pipeline.logs_dir,
                    libs_glob_cp=effective_libs_cp,
                    jacoco_agent=self.pipeline.jacoco_agent,
                    tool_jar=tool_jar,
                    timeout_ms=self.pipeline.args.timeout_ms,
                    dep_rounds=self.pipeline.args.dep_rounds,
                    coverage_tmp_dir=self.pipeline.build_dir,
                    compile_sources=list(compile_sources),
                )
                if fixed_result is not None:
                    result, stats = fixed_result, fixed_stats
                    observation = fixed_observation
            if result.status == "failed":
                print(f"[agt] Test failed: {test_fqcn} (see {run_log})")
            elif result.status == "timeout":
                print(f"[agt] Test timed out: {test_fqcn} (see {run_log})")
            elif result.status == "skipped":
                print(f"[agt] Test skipped: {test_fqcn} (see {run_log})")
            if result.status != "passed":
                write_coverage_error_row(
                    csv_path=self.pipeline.coverage_errors_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    test_fqcn=test_fqcn,
                    status=result.status,
                    error_category=result.error_category,
                    error_detail=result.error_detail,
                    class_origin=result.class_origin,
                    class_origin_detail=result.class_origin_detail,
                    log_file=str(run_log),
                )
            _write_coverage_observation_if_needed(
                csv_zero_hit=self.pipeline.coverage_zero_hit_csv,
                csv_report_issues=self.pipeline.coverage_report_issues_csv,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variant=variant,
                test_fqcn=test_fqcn,
                result=result,
                stats=stats,
                observation=observation,
                log_file=run_log,
            )

            if test_fqcn == ctx.manual_test_fqcn and not wrote_manual:
                write_coverage_row(
                    csv_path=self.pipeline.summary_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant="manual",
                    line_covered=stats[0],
                    line_total=stats[1],
                    branch_covered=stats[2],
                    branch_total=stats[3],
                    status=result.status,
                )
                wrote_manual = True

            if test_fqcn == ctx.generated_test_fqcn and not wrote_auto:
                write_coverage_row(
                    csv_path=self.pipeline.summary_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=auto_variant,
                    line_covered=stats[0],
                    line_total=stats[1],
                    branch_covered=stats[2],
                    branch_total=stats[3],
                    status=result.status,
                )
                wrote_auto = True

            self.pipeline.ran += 1

        if not wrote_manual:
            manual_candidates = compiled_manual_sources or ctx.manual_sources or ctx.sources
            manual_src = first_test_source_for_fqcn(manual_candidates, ctx.manual_test_fqcn)
            if manual_src is None and manual_candidates:
                fallback_manual_fqcn = first_test_fqcn_from_sources(manual_candidates, prefer_estest=False)
                manual_src = first_test_source_for_fqcn(manual_candidates, fallback_manual_fqcn)
            missing_manual = ctx.manual_test_fqcn or first_test_fqcn_from_sources(manual_candidates, prefer_estest=False) or ""
            if manual_src is not None:
                manual_libs_cp = _effective_libs_cp_for_test(
                    libs_glob_cp=self.pipeline.args.libs_cp,
                    test_src=manual_src,
                )
                manual_pkg, manual_cls = parse_package_and_class(manual_src)
                manual_fqcn = missing_manual or (f"{manual_pkg}.{manual_cls}" if manual_pkg else manual_cls)
                exec_file = self.pipeline.out_dir / f"{ctx.target_id}__{manual_cls}.exec"
                run_log = self.pipeline.logs_dir / f"{ctx.target_id}__{manual_cls}.run.log"
                fixed_result, fixed_stats, fixed_observation = _attempt_run_error_fix(
                    ctx=ctx,
                    variant="manual",
                    test_src=manual_src,
                    test_fqcn=manual_fqcn,
                    exec_file=exec_file,
                    run_log=run_log,
                    jacoco_cli=jacoco_cli,
                    logs_dir=self.pipeline.logs_dir,
                    libs_glob_cp=manual_libs_cp,
                    jacoco_agent=self.pipeline.jacoco_agent,
                    tool_jar=tool_jar,
                    timeout_ms=self.pipeline.args.timeout_ms,
                    dep_rounds=self.pipeline.args.dep_rounds,
                    coverage_tmp_dir=self.pipeline.build_dir,
                )
                if fixed_result is not None:
                    write_coverage_row(
                        csv_path=self.pipeline.summary_csv,
                        repo=ctx.repo,
                        fqcn=ctx.fqcn,
                        variant="manual",
                        line_covered=fixed_stats[0],
                        line_total=fixed_stats[1],
                        branch_covered=fixed_stats[2],
                        branch_total=fixed_stats[3],
                        status=fixed_result.status,
                    )
                    _write_coverage_observation_if_needed(
                        csv_zero_hit=self.pipeline.coverage_zero_hit_csv,
                        csv_report_issues=self.pipeline.coverage_report_issues_csv,
                        repo=ctx.repo,
                        fqcn=ctx.fqcn,
                        variant="manual",
                        test_fqcn=manual_fqcn,
                        result=fixed_result,
                        stats=fixed_stats,
                        observation=fixed_observation,
                        log_file=run_log,
                    )
                    if fixed_result.status != "passed":
                        write_coverage_error_row(
                            csv_path=self.pipeline.coverage_errors_csv,
                            repo=ctx.repo,
                            fqcn=ctx.fqcn,
                            variant="manual",
                            test_fqcn=manual_fqcn,
                            status=fixed_result.status,
                            error_category=fixed_result.error_category,
                            error_detail=fixed_result.error_detail,
                            class_origin=fixed_result.class_origin,
                            class_origin_detail=fixed_result.class_origin_detail,
                            log_file=str(run_log),
                        )
                    wrote_manual = True
                    self.pipeline.ran += 1
            if not wrote_manual:
                write_coverage_row(
                    csv_path=self.pipeline.summary_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant="manual",
                    line_covered=0,
                    line_total=0,
                    branch_covered=0,
                    branch_total=0,
                    status="skipped",
                )
                write_coverage_error_row(
                    csv_path=self.pipeline.coverage_errors_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant="manual",
                    test_fqcn=missing_manual,
                    status="skipped",
                    error_category="no_compiled_test_class",
                    error_detail="no compiled manual test class available for run",
                    class_origin="",
                    class_origin_detail="",
                    log_file="",
                )
        if not wrote_auto:
            generated_candidates = compiled_generated_sources or [s for s in ctx.sources if s not in set(ctx.manual_sources)]
            generated_src = first_test_source_for_fqcn(generated_candidates, ctx.generated_test_fqcn)
            if generated_src is None and generated_candidates:
                fallback_auto_fqcn = first_test_fqcn_from_sources(generated_candidates, prefer_estest=True)
                generated_src = first_test_source_for_fqcn(generated_candidates, fallback_auto_fqcn)
            missing_auto = ctx.generated_test_fqcn or first_test_fqcn_from_sources(generated_candidates, prefer_estest=True) or ""
            if generated_src is not None:
                generated_libs_cp = _effective_libs_cp_for_test(
                    libs_glob_cp=self.pipeline.args.libs_cp,
                    test_src=generated_src,
                )
                auto_pkg, auto_cls = parse_package_and_class(generated_src)
                auto_fqcn = missing_auto or (f"{auto_pkg}.{auto_cls}" if auto_pkg else auto_cls)
                exec_file = self.pipeline.out_dir / f"{ctx.target_id}__{auto_cls}.exec"
                run_log = self.pipeline.logs_dir / f"{ctx.target_id}__{auto_cls}.run.log"
                fixed_result, fixed_stats, fixed_observation = _attempt_run_error_fix(
                    ctx=ctx,
                    variant=auto_variant,
                    test_src=generated_src,
                    test_fqcn=auto_fqcn,
                    exec_file=exec_file,
                    run_log=run_log,
                    jacoco_cli=jacoco_cli,
                    logs_dir=self.pipeline.logs_dir,
                    libs_glob_cp=generated_libs_cp,
                    jacoco_agent=self.pipeline.jacoco_agent,
                    tool_jar=tool_jar,
                    timeout_ms=self.pipeline.args.timeout_ms,
                    dep_rounds=self.pipeline.args.dep_rounds,
                    coverage_tmp_dir=self.pipeline.build_dir,
                )
                if fixed_result is not None:
                    write_coverage_row(
                        csv_path=self.pipeline.summary_csv,
                        repo=ctx.repo,
                        fqcn=ctx.fqcn,
                        variant=auto_variant,
                        line_covered=fixed_stats[0],
                        line_total=fixed_stats[1],
                        branch_covered=fixed_stats[2],
                        branch_total=fixed_stats[3],
                        status=fixed_result.status,
                    )
                    _write_coverage_observation_if_needed(
                        csv_zero_hit=self.pipeline.coverage_zero_hit_csv,
                        csv_report_issues=self.pipeline.coverage_report_issues_csv,
                        repo=ctx.repo,
                        fqcn=ctx.fqcn,
                        variant=auto_variant,
                        test_fqcn=auto_fqcn,
                        result=fixed_result,
                        stats=fixed_stats,
                        observation=fixed_observation,
                        log_file=run_log,
                    )
                    if fixed_result.status != "passed":
                        write_coverage_error_row(
                            csv_path=self.pipeline.coverage_errors_csv,
                            repo=ctx.repo,
                            fqcn=ctx.fqcn,
                            variant=auto_variant,
                            test_fqcn=auto_fqcn,
                            status=fixed_result.status,
                            error_category=fixed_result.error_category,
                            error_detail=fixed_result.error_detail,
                            class_origin=fixed_result.class_origin,
                            class_origin_detail=fixed_result.class_origin_detail,
                            log_file=str(run_log),
                        )
                    wrote_auto = True
                    self.pipeline.ran += 1
            if not wrote_auto:
                write_coverage_row(
                    csv_path=self.pipeline.summary_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=auto_variant,
                    line_covered=0,
                    line_total=0,
                    branch_covered=0,
                    branch_total=0,
                    status="skipped",
                )
                write_coverage_error_row(
                    csv_path=self.pipeline.coverage_errors_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=auto_variant,
                    test_fqcn=missing_auto,
                    status="skipped",
                    error_category="no_compiled_test_class",
                    error_detail="no compiled generated test class available for run",
                    class_origin="",
                    class_origin_detail="",
                    log_file="",
                )
        return True


def _selected_adopted_run_variants(variants_csv: str) -> set[str]:
    requested = {variant.strip().lower() for variant in (variants_csv or "").split(",") if variant.strip()}
    if requested:
        return requested & {"auto-100", "improved", "adopted", "agentic", "pr-tests"}
    return set(selected_adopted_variants(variants_csv))


class AdoptedRunStep(Step):
    step_names = ("adopted-run",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True
        selected = _selected_adopted_run_variants(self.pipeline.args.adopted_filter_variants)
        if self.pipeline.args.skip_empty_tests and selected != {"pr-tests"}:
            generated_candidates = [
                source for source in (ctx.final_sources or ctx.sources) if source not in set(ctx.manual_sources)
            ]
            generated_test_src = first_test_source_for_fqcn(generated_candidates, ctx.generated_test_fqcn)
            if generated_test_src is None and generated_candidates:
                generated_test_src = generated_candidates[0]
            if is_empty_generated_test_source(generated_test_src):
                print(f'[agt] adopted-run: Skip (empty generated tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                return True
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] adopted-run: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
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
            if variant in selected
        ]
        if "improved" in selected:
            improved_src = improved_test_path(self.pipeline.adopted_root, ctx.target_id, ctx.fqcn)
            if improved_src is not None:
                variants.append(("improved", improved_src))
        if "auto-100" in selected:
            generated_candidates = [source for source in ctx.sources if source not in set(ctx.manual_sources)]
            generated_src = first_test_source_for_fqcn(generated_candidates, ctx.generated_test_fqcn)
            if generated_src is None and generated_candidates:
                generated_src = generated_candidates[0]
            if generated_src is not None:
                auto_100_src = reduced_test_path(
                    Path(self.pipeline.args.reduced_out),
                    ctx.target_id,
                    generated_src,
                    100,
                    preferred_variants=(self.pipeline.args.auto_variant, "auto"),
                )
                if auto_100_src is not None:
                    variants.append(("auto-100", auto_100_src))
        if not variants:
            print(f'[agt] adopted-run: Skip (missing adopted tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            return True

        for variant, adopted_src in variants:
            if not adopted_src.exists():
                print(f'[agt] adopted-run: Skip (missing {variant} test): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                continue
            if variant == "pr-tests":
                reduced_src = reduced_variant_test_path(
                    self.pipeline.adopted_reduced_out_root,
                    variant,
                    ctx.target_id,
                    self.pipeline.args.adopted_reduce_max_tests,
                    allow_any_top_n=True,
                )
                if reduced_src is not None:
                    adopted_src = reduced_src
                else:
                    staged_sources, _staged_fqcn, allowed_methods, _output_fqcn = _materialize_pr_covfilter_sources(
                        ctx=ctx,
                        pr_source=adopted_src,
                        stage_root=self.pipeline.out_dir / "pr-tests-run" / ctx.target_id,
                    )
                    if not staged_sources or not allowed_methods:
                        print(f'[agt] adopted-run: Skip (no selectable adopted PR tests): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
                        continue
                    adopted_src = staged_sources[-1]
            effective_libs_cp = _effective_libs_cp_for_test(
                libs_glob_cp=self.pipeline.args.libs_cp,
                test_src=adopted_src,
            )
            compile_sources = [adopted_src]
            if variant == "auto-100":
                scaffolding = find_scaffolding_source(adopted_src, ctx.final_sources or ctx.sources)
                if scaffolding is not None and scaffolding.exists():
                    compile_sources.append(scaffolding)
            adopted_build = self.pipeline.build_dir / "adopted-classes" / variant / ctx.target_id
            ensure_dir(adopted_build)
            adopt_compile_log = self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.compile.log"
            ok_adopt, tail_adopt, _final_adopt_sources = compile_test_set_smart(
                java_files=compile_sources,
                build_dir=adopted_build,
                libs_glob_cp=effective_libs_cp,
                sut_jar=ctx.sut_jar,
                log_file=adopt_compile_log,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
                max_rounds=self.pipeline.args.dep_rounds,
            )
            if not ok_adopt:
                adopt_pkg, adopt_cls = parse_package_and_class(adopted_src)
                adopted_fqcn = f"{adopt_pkg}.{adopt_cls}" if adopt_pkg and adopt_cls else (adopt_cls or adopted_src.stem)
                print(
                    f'[agt] adopted-run: Skip (compile failed): repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}" (see {adopt_compile_log})'
                )
                print("[agt][ADOPTED-COMPILE-TAIL]\n" + tail_adopt)
                write_coverage_error_row(
                    csv_path=self.pipeline.coverage_errors_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    test_fqcn=adopted_fqcn,
                    status="not_compiled",
                    error_category=categorize_compile_problem(adopt_compile_log.read_text(encoding="utf-8", errors="ignore")),
                    error_detail=extract_compile_problem_detail(adopt_compile_log.read_text(encoding="utf-8", errors="ignore")),
                    class_origin="",
                    class_origin_detail="",
                    log_file=str(adopt_compile_log),
                )
                continue

            adopt_pkg, adopt_cls = parse_package_and_class(adopted_src)
            if not adopt_cls:
                print(f'[agt] adopted-run: Skip (cannot parse class): repo="{ctx.repo}" fqcn="{ctx.fqcn}" variant="{variant}"')
                continue

            exec_suffix = "auto_reduced" if variant == "auto-100" else variant
            exec_file = self.pipeline.out_dir / f"{ctx.target_id}__{exec_suffix}.exec"
            run_log = self.pipeline.logs_dir / f"{ctx.target_id}.adopted.{variant}.run.log"
            adopted_fqcn = f"{adopt_pkg}.{adopt_cls}" if adopt_pkg else adopt_cls
            print(f"[agt] Running adopted ({variant}): {adopted_fqcn}")
            result, adopted_cov, _, _ = run_test_with_coverage(
                test_src=adopted_src,
                test_fqcn=adopted_fqcn,
                compiled_tests_dir=adopted_build,
                exec_file=exec_file,
                run_log=run_log,
                sut_jar=ctx.sut_jar,
                libs_glob_cp=effective_libs_cp,
                jacoco_agent=self.pipeline.jacoco_agent,
                tool_jar=Path(self.pipeline.args.tool_jar),
                timeout_ms=self.pipeline.args.timeout_ms,
                jacoco_cli=self.pipeline.jacoco_agent.parent / "org.jacoco.cli-run-0.8.14.jar",
                target_fqcn=ctx.fqcn,
                coverage_tmp_dir=self.pipeline.build_dir,
                repo_root_for_deps=ctx.repo_root_for_deps,
                module_rel=ctx.module_rel,
                build_tool=ctx.build_tool,
            )
            if result.status != "passed":
                print(f"[agt] adopted test failed: {adopted_fqcn} (see {run_log})")
                write_coverage_error_row(
                    csv_path=self.pipeline.coverage_errors_csv,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                    test_fqcn=adopted_fqcn,
                    status=result.status,
                    error_category=result.error_category,
                    error_detail=result.error_detail,
                    class_origin=result.class_origin,
                    class_origin_detail=result.class_origin_detail,
                    log_file=str(run_log),
                )

            a_lc, a_lt, a_bc, a_bt = adopted_cov
            write_coverage_row(
                csv_path=self.pipeline.adopted_summary_csv,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variant=variant,
                line_covered=a_lc,
                line_total=a_lt,
                branch_covered=a_bc,
                branch_total=a_bt,
                status=result.status,
            )
        return True
