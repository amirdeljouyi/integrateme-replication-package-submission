from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .common import (
    candidate_repo_class_dirs,
    ensure_dir,
    extract_missing_symbols_from_javac_log,
    file_declares_type,
    is_probably_test_filename,
    looks_like_scaffolding,
    parse_package_and_class,
    shlex_join,
)

_REPO_JAVA_INDEX_CACHE: dict[str, List[Path]] = {}
_REPO_JAVA_STEM_INDEX_CACHE: dict[str, Dict[str, List[Path]]] = {}
_M2_JAR_PATHS_CACHE: Optional[List[Path]] = None
_GRADLE_JAR_PATHS_CACHE: Optional[List[Path]] = None
_M2_CLASS_JARS_CACHE: dict[str, List[str]] = {}
_RUNTIME_DEP_CP_CACHE: dict[tuple[str, int], str] = {}
_MAVEN_MODULE_BUILD_CACHE: dict[tuple[str, str], bool] = {}
_JAVAC_INTERNAL_EXPORTS = [
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
]


def add_throws_exception_to_tests(path: str, dry_run: bool) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return False

    changed = False
    new_lines = []
    pending_test = False
    test_annotation = re.compile(
        r"^\s*@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b"
    )

    for line in lines:
        stripped = line.strip()
        if test_annotation.match(stripped):
            pending_test = True
            new_lines.append(line)
            continue

        if pending_test:
            if stripped.startswith("@"):
                new_lines.append(line)
                continue

            if "throws" not in line and ")" in line:
                updated = re.sub(r"\)\s*\{", ") throws Exception {", line)
                if updated == line:
                    updated = re.sub(r"\)\s*$", ") throws Exception", line)
                if updated != line:
                    changed = True
                    new_lines.append(updated)
                    pending_test = False
                    continue

            pending_test = False

        new_lines.append(line)

    if not changed:
        return False

    if dry_run:
        return True

    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(new_lines)
    except OSError:
        return False

    return True


def normalize_duplicate_throws_clauses(path: str, dry_run: bool) -> bool:
    java_file = Path(path)
    try:
        lines = java_file.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except OSError:
        return False

    changed = False
    updated_lines: List[str] = []
    for line in lines:
        updated = line
        if " throws " in line and line.count(" throws ") > 1:
            head, tail = line.split(" throws ", 1)
            first_clause, remaining = tail.split(" throws ", 1)
            first_clause = first_clause.strip()
            remaining = remaining.strip()
            if first_clause == "Exception":
                merged_clause = remaining
            elif not first_clause:
                merged_clause = remaining
            elif not remaining:
                merged_clause = first_clause
            else:
                merged_clause = f"{first_clause}, {remaining}"
            updated = f"{head} throws {merged_clause}"
            if line.endswith("\n") and not updated.endswith("\n"):
                updated += "\n"
        if updated != line:
            changed = True
        updated_lines.append(updated)

    if not changed:
        return False
    if dry_run:
        return True

    try:
        java_file.write_text("".join(updated_lines), encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return True


def _parse_unreported_exception_lines(output: str, java_file: Path) -> List[int]:
    resolved = java_file.resolve()
    lines: List[int] = []
    pattern = re.compile(
        r"^(.*?):(\d+): error: unreported exception .+ must be caught or declared to be thrown$"
    )
    for raw_line in output.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        source = Path(match.group(1))
        same_file = False
        try:
            same_file = source.resolve() == resolved
        except Exception:
            same_file = source.name == java_file.name
        if not same_file:
            continue
        try:
            lines.append(int(match.group(2)))
        except ValueError:
            continue
    return sorted(set(lines))


def _looks_like_method_signature(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith(("//", "@", "*")):
        return False
    if any(
        stripped.startswith(prefix)
        for prefix in ("if ", "if(", "for ", "for(", "while ", "while(", "switch ", "switch(", "catch ", "catch(")
    ):
        return False
    if any(token in stripped for token in (" class ", " interface ", " enum ", " record ")):
        return False
    return "(" in stripped and ")" in stripped and ("{" in stripped or stripped.endswith(")"))


def add_throws_exception_to_error_methods(path: str, javac_output: str, dry_run: bool) -> bool:
    java_file = Path(path)
    error_lines = _parse_unreported_exception_lines(javac_output, java_file)
    if not error_lines:
        return False

    try:
        lines = java_file.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except OSError:
        return False

    signature_indexes: Set[int] = set()
    for line_number in error_lines:
        idx = max(0, line_number - 1)
        while idx >= 0:
            if _looks_like_method_signature(lines[idx]):
                signature_indexes.add(idx)
                break
            idx -= 1

    changed = False
    for idx in sorted(signature_indexes):
        line = lines[idx]
        if "throws" in line or ")" not in line:
            continue
        updated = re.sub(r"\)\s*\{", ") throws Exception {", line)
        if updated == line:
            updated = re.sub(r"\)\s*$", ") throws Exception", line)
        if updated != line:
            changed = True
            lines[idx] = updated

    if not changed:
        return False
    if dry_run:
        return True

    try:
        java_file.write_text("".join(lines), encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return True


def index_candidate_java_files(repo_root: Path) -> List[Path]:
    if not repo_root.exists():
        return []
    bad_parts = {"target", "build", ".gradle", ".mvn", ".git", "out", ".idea"}
    out: List[Path] = []
    for java_file in repo_root.rglob("*.java"):
        parts = set(java_file.parts)
        if parts & bad_parts:
            continue
        out.append(java_file)
    return out


def _repo_cache_key(repo_root: Path) -> str:
    return str(repo_root.resolve())


def _repo_java_candidates(repo_root: Path) -> List[Path]:
    key = _repo_cache_key(repo_root)
    cached = _REPO_JAVA_INDEX_CACHE.get(key)
    if cached is not None:
        return cached

    candidates = sorted(index_candidate_java_files(repo_root), key=lambda path: (path.name, str(path)))
    stem_index: Dict[str, List[Path]] = {}
    for candidate in candidates:
        stem_index.setdefault(candidate.stem, []).append(candidate)

    _REPO_JAVA_INDEX_CACHE[key] = candidates
    _REPO_JAVA_STEM_INDEX_CACHE[key] = stem_index
    return candidates


def _repo_java_stem_index(repo_root: Path) -> Dict[str, List[Path]]:
    key = _repo_cache_key(repo_root)
    if key not in _REPO_JAVA_STEM_INDEX_CACHE:
        _repo_java_candidates(repo_root)
    return _REPO_JAVA_STEM_INDEX_CACHE.get(key, {})


def _candidate_java_identity(java_file: Path) -> Tuple[str, str]:
    return parse_package_and_class(java_file)


def _existing_source_identities(java_files: Sequence[Path]) -> Set[Tuple[str, str]]:
    return {_candidate_java_identity(java_file) for java_file in java_files}


def find_declaring_sources(
    candidates: Sequence[Path],
    missing: Set[str],
    *,
    preferred_packages: Optional[Dict[str, Set[str]]] = None,
) -> List[Path]:
    found: List[Path] = []
    by_name: dict[str, List[Path]] = {}
    for candidate in candidates:
        by_name.setdefault(candidate.stem, []).append(candidate)

    for symbol in sorted(missing):
        symbol_candidates = sorted(by_name.get(symbol, []))
        preferred = preferred_packages.get(symbol, set()) if preferred_packages else set()
        if preferred:
            preferred_matches = [
                candidate
                for candidate in symbol_candidates
                if parse_package_and_class(candidate)[0] in preferred
            ]
            symbol_candidates = [*preferred_matches, *[c for c in symbol_candidates if c not in preferred_matches]]

        for candidate in symbol_candidates:
            if file_declares_type(candidate, symbol):
                found.append(candidate)
                break
        else:
            for candidate in candidates:
                if file_declares_type(candidate, symbol):
                    found.append(candidate)
                    break

    unique: List[Path] = []
    seen: Set[Path] = set()
    for candidate in found:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _build_marker_exists(path: Path) -> bool:
    return any((path / marker).exists() for marker in ("pom.xml", "build.gradle", "build.gradle.kts"))


def _nearest_build_module_dir(java_path: Path, repo_root: Path) -> Optional[Path]:
    try:
        resolved_root = repo_root.resolve()
        current = java_path.resolve()
    except OSError:
        return None

    if current.is_file():
        current = current.parent

    fallback = resolved_root if _build_marker_exists(resolved_root) else None
    while current == resolved_root or resolved_root in current.parents:
        if _build_marker_exists(current):
            return current
        # Some Gradle multi-project builds configure every subproject from the
        # root build.gradle, so a real module may not have its own build file.
        if (current / "src").is_dir() and any((current / output).exists() for output in ("build", "target")):
            return current
        if current == resolved_root:
            break
        current = current.parent
    return fallback


def _module_rel_from_dir(repo_root: Path, module_dir: Optional[Path]) -> str:
    if module_dir is None:
        return ""
    try:
        rel = module_dir.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return ""
    rel_str = rel.as_posix()
    return "" if rel_str == "." else rel_str


def _append_unique_module(module_rels: List[str], seen: Set[str], module_rel: str) -> None:
    normalized = _norm_module_rel(module_rel)
    if normalized in seen:
        return
    seen.add(normalized)
    module_rels.append(normalized)


def _append_unique_path(paths: List[Path], seen: Set[Path], path: Path) -> None:
    if path in seen:
        return
    seen.add(path)
    paths.append(path)


def _match_repo_source_for_collected_file(repo_root: Path, java_file: Path) -> Optional[Path]:
    pkg, cls = parse_package_and_class(java_file)
    if not cls:
        return None

    candidates = _repo_java_stem_index(repo_root).get(cls, [])
    for candidate in candidates:
        cand_pkg, cand_cls = parse_package_and_class(candidate)
        if cand_cls != cls:
            continue
        if pkg and cand_pkg != pkg:
            continue
        return candidate
    if len(candidates) == 1:
        return candidates[0]
    return None


def _match_repo_source_for_generated_test(repo_root: Path, java_file: Path) -> Optional[Path]:
    pkg, cls = parse_package_and_class(java_file)
    if not cls or "_" not in cls:
        return None

    base_cls = cls.split("_", 1)[0].strip()
    if not base_cls or base_cls == cls:
        return None

    preferred: List[Path] = []
    fallback: List[Path] = []
    for candidate in _repo_java_stem_index(repo_root).get(base_cls, []):
        cand_pkg, cand_cls = parse_package_and_class(candidate)
        if cand_cls != base_cls:
            continue
        if pkg and cand_pkg != pkg:
            continue
        if is_probably_test_filename(candidate.name):
            fallback.append(candidate)
        else:
            preferred.append(candidate)

    matches = preferred or fallback
    if len(matches) == 1:
        return matches[0]
    return None


def prefer_repo_manual_sources(repo_root: Optional[Path], java_files: Sequence[Path]) -> List[Path]:
    if repo_root is None or not repo_root.exists():
        return list(java_files)

    preferred: List[Path] = []
    seen: Set[Path] = set()
    for java_file in java_files:
        candidate = java_file
        if "/collected-tests/manual/" in java_file.as_posix():
            counterpart = _match_repo_source_for_collected_file(repo_root, java_file)
            if counterpart is not None and counterpart.exists():
                candidate = counterpart
        if candidate in seen:
            continue
        seen.add(candidate)
        preferred.append(candidate)
    return preferred


def _source_import_packages_for_symbol(java_file: Path, symbol: str) -> Set[str]:
    packages: Set[str] = set()
    try:
        lines = java_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return packages

    for line in lines:
        match = re.match(
            r"^\s*import\s+(?:static\s+)?([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)+)\s*;\s*$",
            line,
        )
        if not match:
            continue
        target = match.group(1)
        parts = [part for part in target.split(".") if part]
        if not parts:
            continue
        if parts[-1] == symbol:
            packages.add(".".join(parts[:-1]))
            continue
        if parts[-1] and parts[-1][0].islower() and len(parts) >= 2 and parts[-2] == symbol:
            packages.add(".".join(parts[:-2]))
    return packages


def _extract_missing_symbol_package_hints(log_text: str) -> Dict[str, Set[str]]:
    hints: Dict[str, Set[str]] = {}
    current_source: Optional[Path] = None
    current_symbol: Optional[str] = None

    for raw_line in log_text.splitlines():
        source_match = re.match(r"^(.*?\.java):\d+: error: cannot find symbol$", raw_line.strip())
        if source_match:
            current_source = Path(source_match.group(1))
            current_symbol = None
            continue

        symbol_match = re.match(
            r"^\s*symbol:\s+(?:class|interface|enum|record|annotation type|variable)\s+([A-Za-z_][A-Za-z0-9_$]*)\s*$",
            raw_line,
        )
        if symbol_match:
            current_symbol = symbol_match.group(1)
            continue

        if not current_source or not current_symbol:
            continue

        source_pkg, _ = parse_package_and_class(current_source)
        if source_pkg:
            hints.setdefault(current_symbol, set()).add(source_pkg)
        hints.setdefault(current_symbol, set()).update(
            _source_import_packages_for_symbol(current_source, current_symbol)
        )

        if not raw_line.strip():
            current_source = None
            current_symbol = None

    return hints


def _extract_missing_import_targets(log_text: str) -> List[str]:
    targets: List[str] = []
    seen: Set[str] = set()
    lines = log_text.splitlines()
    token_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)+")
    import_pattern = re.compile(
        r"^\s*import\s+(?:static\s+)?([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$*]*)+)\s*;\s*$"
    )

    for raw_line in lines:
        import_match = import_pattern.match(raw_line)
        if not import_match:
            continue
        target = import_match.group(1)
        if target.endswith(".*"):
            target = target[:-2]
        normalized = _normalize_import_target_class(target)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        targets.append(normalized)

    for idx, raw_line in enumerate(lines):
        match = re.search(r"error:\s+package\s+([A-Za-z0-9_.$]+)\s+does not exist", raw_line)
        if not match:
            continue
        missing_pkg = match.group(1)
        for candidate_line in lines[idx + 1 : idx + 4]:
            stripped = candidate_line.strip()
            if not stripped or stripped == "^":
                continue
            import_match = re.search(
                r"\bimport\s+(?:static\s+)?([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)+)(?:\.\*)?;",
                stripped,
            )
            if import_match:
                target = _normalize_import_target_class(import_match.group(1))
                if target and target not in seen:
                    seen.add(target)
                    targets.append(target)
                break

            found_token = False
            for token in token_pattern.findall(stripped):
                if not token.startswith(missing_pkg + "."):
                    continue
                simple_name = token.rsplit(".", 1)[-1]
                if not simple_name or not simple_name[0].isupper():
                    continue
                normalized = _normalize_import_target_class(token)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                targets.append(normalized)
                found_token = True
            if found_token:
                break

    for raw_line in lines:
        match = re.search(r"class file for\s+([A-Za-z0-9_.$]+)\s+not found", raw_line)
        if not match:
            continue
        normalized = _normalize_import_target_class(match.group(1))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        targets.append(normalized)
    return targets


def _find_generated_source_candidates(repo_root: Path, package_name: str, class_name: str) -> List[Path]:
    if not package_name or not class_name:
        return []

    rel = Path(*package_name.split(".")) / f"{class_name}.java"
    patterns = [
        Path("**") / "target" / "generated-sources" / "**" / rel,
        Path("**") / "target" / "generated-test-sources" / "**" / rel,
        Path("**") / "build" / "generated" / "**" / rel,
    ]

    found: List[Path] = []
    seen: Set[Path] = set()
    for pattern in patterns:
        for candidate in sorted(repo_root.glob(str(pattern))):
            if not candidate.is_file() or candidate in seen:
                continue
            seen.add(candidate)
            found.append(candidate)
    return found


def _find_declaring_sources_for_import_targets(
    repo_root: Path,
    import_targets: Sequence[str],
    *,
    existing_identities: Optional[Set[Tuple[str, str]]] = None,
) -> List[Path]:
    if not import_targets:
        return []

    found: List[Path] = []
    seen: Set[Path] = set()
    stem_index = _repo_java_stem_index(repo_root)
    existing_identities = existing_identities or set()
    for target in import_targets:
        pkg, _, cls = target.rpartition(".")
        if not cls:
            continue
        candidates = list(stem_index.get(cls, []))
        candidates.extend(_find_generated_source_candidates(repo_root, pkg, cls))
        for candidate in candidates:
            cand_pkg, cand_cls = parse_package_and_class(candidate)
            if cand_cls != cls:
                continue
            if pkg and cand_pkg != pkg:
                continue
            if (cand_pkg, cand_cls) in existing_identities:
                continue
            _append_unique_path(found, seen, candidate)
            break
    return found


def _normalize_import_target_class(target: str) -> str:
    parts = [part for part in target.split(".") if part]
    if len(parts) < 2:
        return ""
    if parts[-1] and parts[-1][0].islower():
        parts = parts[:-1]
    if not parts or not parts[-1] or not parts[-1][0].isupper():
        return ""
    return ".".join(parts)


def _local_maven_jars() -> List[Path]:
    global _M2_JAR_PATHS_CACHE
    if _M2_JAR_PATHS_CACHE is not None:
        return _M2_JAR_PATHS_CACHE

    repo = _local_maven_repo()
    if not repo.exists():
        _M2_JAR_PATHS_CACHE = []
        return _M2_JAR_PATHS_CACHE

    _M2_JAR_PATHS_CACHE = sorted(repo.rglob("*.jar"))
    return _M2_JAR_PATHS_CACHE


def _local_gradle_jars() -> List[Path]:
    global _GRADLE_JAR_PATHS_CACHE
    if _GRADLE_JAR_PATHS_CACHE is not None:
        return _GRADLE_JAR_PATHS_CACHE

    repo = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1"
    if not repo.exists():
        _GRADLE_JAR_PATHS_CACHE = []
        return _GRADLE_JAR_PATHS_CACHE

    _GRADLE_JAR_PATHS_CACHE = sorted(repo.rglob("*.jar"))
    return _GRADLE_JAR_PATHS_CACHE


def _local_dependency_jars() -> List[Path]:
    jars: List[Path] = []
    seen: Set[str] = set()
    for jar_path in [*_local_maven_jars(), *_local_gradle_jars()]:
        jar_str = str(jar_path)
        if jar_str in seen:
            continue
        seen.add(jar_str)
        jars.append(jar_path)
    return jars


def _jar_score_for_class_target(jar_path: Path, class_fqcn: str) -> int:
    lowered = str(jar_path).lower()
    collapsed = re.sub(r"[^a-z0-9]+", "", lowered)
    tokens = [part.lower() for part in class_fqcn.split(".") if len(part) > 2]
    namespace_tokens = {"com", "org", "net", "io", "java", "javax", "jakarta", "apache"}
    distinctive_package_tokens = [token for token in tokens[:-1] if token not in namespace_tokens]
    if distinctive_package_tokens and not any(
        token in lowered or token in collapsed for token in distinctive_package_tokens
    ):
        return 0
    score = 0
    for token in tokens[:-1]:
        if token in lowered or token in collapsed:
            score += 2
    if tokens and (tokens[-1] in lowered or tokens[-1] in collapsed):
        score += 1
    return score


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


def _dependency_jar_version_for_sort(jar_path: Path) -> str:
    try:
        resolved = jar_path.resolve()
    except OSError:
        resolved = jar_path

    parts = resolved.parts
    if ".m2" in parts and "repository" in parts:
        return resolved.parent.name
    if ".gradle" in parts and "files-2.1" in parts:
        return resolved.parent.parent.name

    match = re.match(r"^.+-(?P<version>\d[^/]*)$", jar_path.stem)
    return match.group("version") if match else ""


def _jar_contains_class_entry(jar_path: Path, entry_name: str) -> bool:
    try:
        with zipfile.ZipFile(jar_path) as handle:
            handle.getinfo(entry_name)
            return True
    except (KeyError, OSError, zipfile.BadZipFile):
        return False


def _resolve_m2_jars_for_import_targets(import_targets: Sequence[str]) -> str:
    jar_paths: List[str] = []
    seen_jars: Set[str] = set()
    for target in import_targets:
        class_target = _normalize_import_target_class(target)
        if not class_target:
            continue
        cached = _M2_CLASS_JARS_CACHE.get(class_target)
        if cached is None:
            entry_name = class_target.replace(".", "/") + ".class"
            ranked_matches: List[tuple[int, tuple, str]] = []
            for jar_path in _local_dependency_jars():
                score = _jar_score_for_class_target(jar_path, class_target)
                if score <= 0:
                    continue
                if not _jar_contains_class_entry(jar_path, entry_name):
                    continue
                ranked_matches.append((score, _version_sort_key(_dependency_jar_version_for_sort(jar_path)), str(jar_path)))
            ranked_matches.sort(reverse=True)
            cached = [ranked_matches[0][2]] if ranked_matches else []
            _M2_CLASS_JARS_CACHE[class_target] = cached
        for jar_path in cached:
            if jar_path in seen_jars:
                continue
            seen_jars.add(jar_path)
            jar_paths.append(jar_path)
    return ":".join(jar_paths)


def _resolve_external_classpath_from_log(log_text: str) -> str:
    return _resolve_m2_jars_for_import_targets(_extract_missing_import_targets(log_text))


def resolve_external_classpath_from_imports(java_files: Sequence[Path]) -> str:
    targets: List[str] = []
    seen: Set[str] = set()
    import_pattern = re.compile(
        r"^\s*import\s+(?:static\s+)?([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$*]*)+)\s*;\s*$"
    )

    for java_file in java_files:
        try:
            text = java_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw_line in text.splitlines():
            match = import_pattern.match(raw_line)
            if not match:
                continue
            target = match.group(1)
            if target.endswith(".*"):
                target = target[:-2]
            normalized = _normalize_import_target_class(target)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            targets.append(normalized)

    direct_cp = _resolve_m2_jars_for_import_targets(targets)
    transitive_cp = _expand_runtime_dependency_cp(direct_cp)
    return _merge_classpath_parts([direct_cp, transitive_cp])


def resolve_external_runtime_classpath_from_output(output: str) -> str:
    targets: List[str] = []
    seen: Set[str] = set()
    service_provider_pattern = re.compile(
        r"ServiceConfigurationError:\s+[A-Za-z0-9_.$]+:\s+Provider\s+([A-Za-z0-9_.$]+)\s+not found"
    )
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.search(r"(?:NoClassDefFoundError|ClassNotFoundException):\s+([A-Za-z0-9_.$/]+)", line)
        if match:
            target = match.group(1).replace("/", ".")
            normalized = _normalize_import_target_class(target)
            if normalized and normalized not in seen:
                seen.add(normalized)
                targets.append(normalized)
        provider_match = service_provider_pattern.search(line)
        if provider_match:
            provider = provider_match.group(1).strip()
            normalized_provider = _normalize_import_target_class(provider)
            if normalized_provider and normalized_provider not in seen:
                seen.add(normalized_provider)
                targets.append(normalized_provider)
    direct_cp = _resolve_m2_jars_for_import_targets(targets)
    transitive_cp = _expand_runtime_dependency_cp(direct_cp)
    return _merge_classpath_parts([direct_cp, transitive_cp])


def _java_source_root(java_file: Path) -> Optional[Path]:
    parts = list(java_file.parts)
    for marker in (
        ("src", "main", "java"),
        ("src", "test", "java"),
        ("src", "it", "java"),
        ("target", "generated-sources", "java"),
        ("target", "generated-test-sources", "java"),
        ("build", "generated", "sources"),
    ):
        for idx in range(len(parts) - len(marker) + 1):
            if tuple(parts[idx : idx + len(marker)]) == marker:
                return Path(*parts[: idx + len(marker)])
    return None


def _related_import_target_sources(java_file: Path, package_name: str) -> List[Path]:
    source_root = _java_source_root(java_file)
    if source_root is None:
        return []

    package_parts = [part for part in package_name.split(".") if part]
    related: List[Path] = []
    package_dir = source_root / Path(*package_parts) if package_parts else java_file.parent
    if package_dir.exists():
        related.extend(sorted(package_dir.glob("*.java")))

    if package_parts and package_parts[-1] == "api":
        parent_dir = package_dir.parent
        for sibling_name in ("error", "util"):
            sibling_dir = parent_dir / sibling_name
            if sibling_dir.exists():
                related.extend(sorted(sibling_dir.glob("*.java")))
    return related


def _same_package_java_sources(java_file: Path) -> List[Path]:
    pkg, _cls = parse_package_and_class(java_file)
    source_root = _java_source_root(java_file)
    if source_root is None:
        return [java_file]

    package_dir = source_root / Path(*pkg.split(".")) if pkg else java_file.parent
    if not package_dir.exists():
        return [java_file]
    related: List[Path] = []
    for candidate in sorted(package_dir.glob("*.java")):
        if candidate == java_file:
            related.append(candidate)
            continue
        if is_probably_test_filename(candidate.name):
            continue
        related.append(candidate)
    return related


def _extract_error_source_paths(log_text: str) -> List[Path]:
    paths: List[Path] = []
    seen: Set[Path] = set()
    for raw_line in log_text.splitlines():
        match = re.match(r"^(.*?\.java):\d+: error:", raw_line.strip())
        if not match:
            continue
        java_path = Path(match.group(1))
        if java_path in seen:
            continue
        seen.add(java_path)
        paths.append(java_path)
    return paths


def _same_package_sources_from_log(
    repo_root: Path,
    log_text: str,
    *,
    existing_identities: Optional[Set[Tuple[str, str]]] = None,
    allowed_packages: Optional[Set[str]] = None,
) -> List[Path]:
    found: List[Path] = []
    seen: Set[Path] = set()
    existing_identities = existing_identities or set()

    for source_path in _extract_error_source_paths(log_text):
        candidate_root = source_path
        counterpart = _match_repo_source_for_collected_file(repo_root, source_path)
        if counterpart is not None and counterpart.exists():
            candidate_root = counterpart
        elif not source_path.exists():
            continue

        pkg, _cls = parse_package_and_class(candidate_root)
        if allowed_packages is not None and pkg not in allowed_packages:
            continue

        for related in _same_package_java_sources(candidate_root):
            if _candidate_java_identity(related) in existing_identities:
                continue
            _append_unique_path(found, seen, related)

    return found


def _related_module_rels(
    repo_root: Path,
    base_module_rel: str,
    source_files: Sequence[Path],
    log_text: str = "",
) -> List[str]:
    module_rels: List[str] = []
    seen: Set[str] = set()
    _append_unique_module(module_rels, seen, base_module_rel)

    try:
        resolved_root = repo_root.resolve()
    except OSError:
        resolved_root = repo_root

    for source in source_files:
        module_dir: Optional[Path] = None
        try:
            resolved_source = source.resolve()
        except OSError:
            resolved_source = source
        if resolved_source == resolved_root or resolved_root in resolved_source.parents:
            module_dir = _nearest_build_module_dir(resolved_source, repo_root)
        else:
            counterpart = _match_repo_source_for_collected_file(repo_root, source)
            if counterpart is None:
                counterpart = _match_repo_source_for_generated_test(repo_root, source)
            if counterpart is not None:
                module_dir = _nearest_build_module_dir(counterpart, repo_root)
        _append_unique_module(module_rels, seen, _module_rel_from_dir(repo_root, module_dir))

    for extra_source in _find_declaring_sources_for_import_targets(repo_root, _extract_missing_import_targets(log_text)):
        _append_unique_module(
            module_rels,
            seen,
            _module_rel_from_dir(repo_root, _nearest_build_module_dir(extra_source, repo_root)),
        )

    return module_rels


def _merge_classpath_parts(parts: Sequence[str]) -> str:
    merged: List[str] = []
    seen: Set[str] = set()
    for part in parts:
        if not part:
            continue
        for entry in part.split(":"):
            entry = entry.strip()
            if not entry or entry in seen:
                continue
            seen.add(entry)
            merged.append(entry)
    return ":".join(merged)


def _ensure_maven_module_outputs(repo_root: Path, module_rel: str) -> bool:
    normalized = _norm_module_rel(module_rel)
    key = (str(repo_root.resolve()), normalized)
    cached = _MAVEN_MODULE_BUILD_CACHE.get(key)
    if cached is not None:
        return cached

    if candidate_repo_class_dirs(repo_root, normalized):
        _MAVEN_MODULE_BUILD_CACHE[key] = True
        return True

    module_dir = _module_dir(repo_root, normalized)
    if not (module_dir / "pom.xml").exists():
        _MAVEN_MODULE_BUILD_CACHE[key] = False
        return False

    cmd = [
        _maven_cmd(repo_root),
        "-q",
        "-pl",
        normalized or ".",
        "-am",
        "-DskipTests",
        "test-compile",
    ]
    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    ok = proc.returncode == 0 and bool(candidate_repo_class_dirs(repo_root, normalized))
    _MAVEN_MODULE_BUILD_CACHE[key] = ok
    return ok


def _looks_like_lombok_postcompiler_failure(log_text: str) -> bool:
    lowered = log_text.lower()
    return "post-compiler 'lombok.bytecode.sneakythrowsremover' caused an exception" in lowered


def _looks_like_annotation_processor_failure(log_text: str) -> bool:
    lowered = log_text.lower()
    return (
        "an annotation processor threw an uncaught exception" in lowered
        or "bad service configuration file" in lowered and "processor object" in lowered
        or "exception thrown while constructing processor object" in lowered
        or "javax.annotation.processing.processor:" in lowered and "could not be instantiated" in lowered
        or "java.lang.noclassdeffounderror:" in lowered and "processor" in lowered
    )


def _find_lombok_jar_in_classpath(classpath: str) -> Optional[Path]:
    for entry in classpath.split(":"):
        path = Path(entry.strip())
        if not path.name:
            continue
        lowered = path.name.lower()
        if lowered == "lombok.jar" or (lowered.startswith("lombok-") and lowered.endswith(".jar")):
            if path.exists():
                return path
    return None


def _delombok_sources(java_files: Sequence[Path], classpath: str) -> List[Path]:
    lombok_jar = _find_lombok_jar_in_classpath(classpath)
    if lombok_jar is None:
        return []

    temp_root = Path(tempfile.mkdtemp(prefix="agt-delombok-"))
    out_files: List[Path] = []
    for index, java_file in enumerate(java_files):
        out_dir = temp_root / f"src_{index}"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "java",
            "-jar",
            str(lombok_jar),
            "delombok",
            "--quiet",
            "--encoding",
            "UTF-8",
            "--classpath",
            classpath,
            "--target",
            str(out_dir),
            str(java_file),
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        candidates = sorted(out_dir.rglob("*.java"))
        if not candidates:
            out_files.append(java_file)
            continue

        exact_name = [candidate for candidate in candidates if candidate.name == java_file.name]
        out_files.append(exact_name[0] if exact_name else candidates[0])

    return out_files


class JavacCompiler:
    def __init__(self, *, libs_glob_cp: str, sut_jar: Path, extra_cp: str = "") -> None:
        self.libs_glob_cp = libs_glob_cp
        self.sut_jar = sut_jar
        self.extra_cp = extra_cp
        self.use_processorpath = True
        self.disable_processing = False

    def _classpath(self) -> str:
        cp_parts = [self.libs_glob_cp, str(self.sut_jar)]
        if self.extra_cp.strip():
            cp_parts.append(self.extra_cp.strip())
        return ":".join(cp_parts)

    def _javac_cmd(self, java_files: Sequence[Path], build_dir: Path) -> List[str]:
        cp = self._classpath()
        cmd = ["javac", "-g", "-cp", cp]
        if self.disable_processing:
            cmd.append("-proc:none")
        elif self.use_processorpath:
            # Since JDK 23, merely placing processors on the processor path no
            # longer enables annotation processing.  Generated test helpers
            # in projects such as Micronaut need the same processors as the
            # native project build, otherwise compilation succeeds but the
            # test later fails during framework initialization.
            cmd.extend(["-proc:full", "-processorpath", cp])
        cmd.extend(["-d", str(build_dir)])
        cmd.extend(_JAVAC_INTERNAL_EXPORTS)
        cmd.extend(str(java_file) for java_file in java_files)
        return cmd

    def compile_set(
        self,
        *,
        java_files: Sequence[Path],
        build_dir: Path,
        log_file: Path,
    ) -> Tuple[bool, str]:
        ensure_dir(build_dir.parent if build_dir.name else build_dir)
        ensure_dir(log_file.parent)

        cmd = self._javac_cmd(java_files, build_dir)
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        out = proc.stdout or ""
        log_file.write_text(
            f"[agt] javac cmd:\n{shlex_join(cmd)}\n\n[agt] output:\n{out}\n",
            encoding="utf-8",
            errors="ignore",
        )
        tail = "\n".join(out.splitlines()[-60:])
        return proc.returncode == 0, tail

    def compile_smart(
        self,
        *,
        java_files: Sequence[Path],
        build_dir: Path,
        log_file: Path,
        repo_root_for_deps: Optional[Path] = None,
        module_rel: str = "",
        build_tool: str = "",
        max_rounds: int = 20,
    ) -> Tuple[bool, str, List[Path]]:
        requested_sources: List[Path] = list(java_files)
        sources: List[Path] = list(requested_sources)
        requested_packages = {
            pkg
            for pkg, _cls in (_candidate_java_identity(source) for source in sources)
            if pkg
        }
        if repo_root_for_deps and repo_root_for_deps.exists():
            initial_repo_cp = resolve_repo_runtime_classpath(
                repo_root=repo_root_for_deps,
                module_rel=module_rel,
                build_tool=build_tool,
                source_files=sources,
                log_text="",
            )
            initial_extra_cp = _merge_classpath_parts([self.extra_cp, initial_repo_cp])
            if initial_extra_cp:
                self.extra_cp = initial_extra_cp

        ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
        if ok:
            return True, tail, sources

        if not repo_root_for_deps or not repo_root_for_deps.exists():
            return False, tail, sources

        full_log = read_compile_log(log_file)
        if _looks_like_annotation_processor_failure(full_log):
            self.disable_processing = True
            self.use_processorpath = False
            ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
            if ok:
                return True, tail, sources
            full_log = read_compile_log(log_file)
        if _looks_like_lombok_postcompiler_failure(full_log):
            delomboked_sources = _delombok_sources(sources, self._classpath())
            if delomboked_sources and delomboked_sources != sources:
                sources = delomboked_sources
                self.disable_processing = True
                self.use_processorpath = False
                ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                if ok:
                    return True, tail, sources
                full_log = read_compile_log(log_file)
        repo_cp = resolve_repo_runtime_classpath(
            repo_root=repo_root_for_deps,
            module_rel=module_rel,
            build_tool=build_tool,
            source_files=sources,
            log_text=full_log,
        )
        external_cp = _resolve_external_classpath_from_log(full_log)
        extra_cp = _merge_classpath_parts([self.extra_cp, repo_cp, external_cp])
        if extra_cp and extra_cp != self.extra_cp:
            self.extra_cp = extra_cp
            ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
            if ok:
                return True, tail, sources
            full_log = read_compile_log(log_file)
            if _looks_like_annotation_processor_failure(full_log):
                self.disable_processing = True
                self.use_processorpath = False
                ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                if ok:
                    return True, tail, sources
                full_log = read_compile_log(log_file)
            if _looks_like_lombok_postcompiler_failure(full_log):
                delomboked_sources = _delombok_sources(sources, self._classpath())
                if delomboked_sources and delomboked_sources != sources:
                    sources = delomboked_sources
                    self.disable_processing = True
                    self.use_processorpath = False
                    ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                    if ok:
                        return True, tail, sources
                    full_log = read_compile_log(log_file)

        candidate_root = _module_dir(repo_root_for_deps, module_rel)
        if not candidate_root.exists():
            candidate_root = repo_root_for_deps
        candidates = _repo_java_candidates(candidate_root)
        for _round in range(max_rounds):
            missing = extract_missing_symbols_from_javac_log(full_log)
            existing_identities = _existing_source_identities(sources)
            same_package_sources = _same_package_sources_from_log(
                repo_root_for_deps,
                full_log,
                existing_identities=existing_identities,
                allowed_packages=requested_packages,
            )
            same_package_added = False
            for java_file in same_package_sources:
                if java_file in sources:
                    continue
                sources.append(java_file)
                same_package_added = True
            if same_package_added:
                ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                if ok:
                    return True, tail, sources
                full_log = read_compile_log(log_file)
                if _looks_like_annotation_processor_failure(full_log):
                    self.disable_processing = True
                    self.use_processorpath = False
                    ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                    if ok:
                        return True, tail, sources
                    full_log = read_compile_log(log_file)
                if _looks_like_lombok_postcompiler_failure(full_log):
                    delomboked_sources = _delombok_sources(sources, self._classpath())
                    if delomboked_sources and delomboked_sources != sources:
                        sources = delomboked_sources
                        self.disable_processing = True
                        self.use_processorpath = False
                        ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                        if ok:
                            return True, tail, sources
                        full_log = read_compile_log(log_file)
                missing = extract_missing_symbols_from_javac_log(full_log)
                existing_identities = _existing_source_identities(sources)
            repo_cp = resolve_repo_runtime_classpath(
                repo_root=repo_root_for_deps,
                module_rel=module_rel,
                build_tool=build_tool,
                source_files=sources,
                log_text=full_log,
            )
            external_cp = _resolve_external_classpath_from_log(full_log)
            new_extra_cp = _merge_classpath_parts([self.extra_cp, repo_cp, external_cp])
            cp_changed = bool(new_extra_cp and new_extra_cp != self.extra_cp)
            if cp_changed:
                self.extra_cp = new_extra_cp
                ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                if ok:
                    return True, tail, sources
                full_log = read_compile_log(log_file)
                if _looks_like_annotation_processor_failure(full_log):
                    self.disable_processing = True
                    self.use_processorpath = False
                    ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                    if ok:
                        return True, tail, sources
                    full_log = read_compile_log(log_file)
                if _looks_like_lombok_postcompiler_failure(full_log):
                    delomboked_sources = _delombok_sources(sources, self._classpath())
                    if delomboked_sources and delomboked_sources != sources:
                        sources = delomboked_sources
                        self.disable_processing = True
                        self.use_processorpath = False
                        ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                        if ok:
                            return True, tail, sources
                        full_log = read_compile_log(log_file)
                missing = extract_missing_symbols_from_javac_log(full_log)
                existing_identities = _existing_source_identities(sources)

            new_sources = find_declaring_sources(
                candidates,
                missing,
                preferred_packages=_extract_missing_symbol_package_hints(full_log),
            )
            filtered_new_sources: List[Path] = []
            seen_filtered: Set[Path] = set()
            for java_file in new_sources:
                if java_file in seen_filtered:
                    continue
                seen_filtered.add(java_file)
                if _candidate_java_identity(java_file) in existing_identities:
                    continue
                filtered_new_sources.append(java_file)
            import_sources = _find_declaring_sources_for_import_targets(
                repo_root_for_deps,
                _extract_missing_import_targets(full_log),
                existing_identities=existing_identities,
            )

            add_count = 0
            for java_file in [*filtered_new_sources, *import_sources]:
                if java_file in sources:
                    continue
                sources.append(java_file)
                add_count += 1

            if add_count == 0 and not cp_changed:
                break

            ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
            if ok:
                return True, tail, sources
            full_log = read_compile_log(log_file)
            if _looks_like_annotation_processor_failure(full_log):
                self.disable_processing = True
                self.use_processorpath = False
                ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                if ok:
                    return True, tail, sources
                full_log = read_compile_log(log_file)
            if _looks_like_lombok_postcompiler_failure(full_log):
                delomboked_sources = _delombok_sources(sources, self._classpath())
                if delomboked_sources and delomboked_sources != sources:
                    sources = delomboked_sources
                    self.disable_processing = True
                    self.use_processorpath = False
                    ok, tail = self.compile_set(java_files=sources, build_dir=build_dir, log_file=log_file)
                    if ok:
                        return True, tail, sources
                    full_log = read_compile_log(log_file)

        if sources != requested_sources:
            ok, tail = self.compile_set(
                java_files=requested_sources,
                build_dir=build_dir,
                log_file=log_file,
            )
            if ok:
                return True, tail, requested_sources

        return False, tail, sources


def compile_test_set(
    java_files: Sequence[Path],
    build_dir: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    log_file: Path,
) -> Tuple[bool, str]:
    return JavacCompiler(libs_glob_cp=libs_glob_cp, sut_jar=sut_jar).compile_set(
        java_files=java_files,
        build_dir=build_dir,
        log_file=log_file,
    )


def compile_test_set_smart(
    java_files: Sequence[Path],
    build_dir: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    log_file: Path,
    repo_root_for_deps: Optional[Path] = None,
    module_rel: str = "",
    build_tool: str = "",
    max_rounds: int = 20,
) -> Tuple[bool, str, List[Path]]:
    return JavacCompiler(libs_glob_cp=libs_glob_cp, sut_jar=sut_jar).compile_smart(
        java_files=java_files,
        build_dir=build_dir,
        log_file=log_file,
        repo_root_for_deps=repo_root_for_deps,
        module_rel=module_rel,
        build_tool=build_tool,
        max_rounds=max_rounds,
    )


_MODULE_RUNTIME_CP_CACHE: dict[tuple[str, str, str], str] = {}
_RUNTIME_CP_CACHE: dict[tuple[str, tuple[str, ...], str], str] = {}
_MAVEN_CLASSPATH_CACHE: dict[tuple[tuple[str, ...], str], str] = {}


def _norm_module_rel(module_rel: str) -> str:
    normalized = (module_rel or "").strip()
    if not normalized or normalized in {".", "root"}:
        return ""
    return normalized


def _detect_build_tool(repo_root: Path) -> str:
    if (repo_root / "pom.xml").exists():
        return "maven"
    if (repo_root / "build.gradle").exists() or (repo_root / "build.gradle.kts").exists() or (repo_root / "gradlew").exists():
        return "gradle"
    return ""


def _module_dir(repo_root: Path, module_rel: str) -> Path:
    rel = _norm_module_rel(module_rel)
    if not rel:
        return repo_root
    return repo_root / rel


def _maven_cmd(repo_root: Path) -> str:
    mvnw = repo_root / "mvnw"
    return str(mvnw.resolve()) if mvnw.exists() else "mvn"


def _run_maven_classpath(cmd: Sequence[str], cwd: Path) -> str:
    key = (tuple(cmd), str(cwd.resolve()))
    cached = _MAVEN_CLASSPATH_CACHE.get(key)
    if cached is not None:
        return cached

    with tempfile.NamedTemporaryFile(prefix="agt-mvn-cp-", delete=False) as handle:
        outcp = Path(handle.name)
    try:
        full_cmd = [*cmd, f"-Dmdep.outputFile={outcp}"]
        proc = subprocess.run(full_cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cp = ""
        if outcp.exists():
            cp = outcp.read_text(encoding="utf-8", errors="ignore").strip()
        if proc.returncode == 0 or cp:
            _MAVEN_CLASSPATH_CACHE[key] = cp
            return cp
        _MAVEN_CLASSPATH_CACHE[key] = ""
        return ""
    finally:
        outcp.unlink(missing_ok=True)


def _pom_tag(tag: str) -> str:
    return f"{{http://maven.apache.org/POM/4.0.0}}{tag}"


def _resolve_props(raw: str, props: Dict[str, str]) -> str:
    value = raw or ""
    for _ in range(8):
        updated = re.sub(r"\$\{([^}]+)\}", lambda m: props.get(m.group(1), m.group(0)), value)
        if updated == value:
            break
        value = updated
    return value


def _pom_direct_child_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(_pom_tag(tag))
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _pom_children(parent: ET.Element, tag: str) -> List[ET.Element]:
    container = parent.find(_pom_tag(tag))
    if container is None:
        return []
    return list(container)


def _normalize_pom_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.is_dir():
        return resolved / "pom.xml"
    return resolved


def _maven_pom_chain(module_dir: Path) -> List[Path]:
    return _maven_pom_chain_from_pom(_normalize_pom_path(module_dir / "pom.xml"))


def _maven_pom_chain_from_pom(pom_path: Path) -> List[Path]:
    current = _normalize_pom_path(pom_path)
    if not current.exists():
        return []

    chain: List[Path] = []
    seen: Set[Path] = set()
    while current.exists() and current not in seen:
        seen.add(current)
        chain.append(current)
        try:
            root = ET.parse(current).getroot()
        except ET.ParseError:
            break
        parent = root.find(_pom_tag("parent"))
        if parent is None:
            break
        relative_path = _pom_direct_child_text(parent, "relativePath") or "../pom.xml"
        parent_pom = _normalize_pom_path(current.parent / relative_path)
        if not parent_pom.exists():
            parent_group_id = _pom_direct_child_text(parent, "groupId")
            parent_artifact_id = _pom_direct_child_text(parent, "artifactId")
            parent_version = _pom_direct_child_text(parent, "version")
            if parent_group_id and parent_artifact_id and parent_version:
                parent_pom = _pom_path_from_coords(parent_group_id, parent_artifact_id, parent_version)
        if not parent_pom.exists():
            break
        current = parent_pom
    chain.reverse()
    return chain


def _local_maven_repo() -> Path:
    return Path.home() / ".m2" / "repository"


def _jar_path_from_coords(group_id: str, artifact_id: str, version: str) -> Path:
    return _local_maven_repo() / Path(*group_id.split(".")) / artifact_id / version / f"{artifact_id}-{version}.jar"


def _pom_path_from_coords(group_id: str, artifact_id: str, version: str) -> Path:
    return _local_maven_repo() / Path(*group_id.split(".")) / artifact_id / version / f"{artifact_id}-{version}.pom"


def _gradle_jar_path_from_coords(group_id: str, artifact_id: str, version: str) -> Optional[Path]:
    gradle_root = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1" / group_id / artifact_id / version
    if not gradle_root.exists():
        return None
    matches = sorted(gradle_root.glob(f"*/{artifact_id}-{version}*.jar"))
    if not matches:
        return None
    exact = [match for match in matches if match.name == f"{artifact_id}-{version}.jar"]
    return exact[0] if exact else matches[0]


def _local_jar_path_from_coords(group_id: str, artifact_id: str, version: str) -> Optional[Path]:
    m2_path = _jar_path_from_coords(group_id, artifact_id, version)
    if m2_path.exists():
        return m2_path
    return _gradle_jar_path_from_coords(group_id, artifact_id, version)


def _jar_coords_from_path(jar_path: Path) -> Optional[Tuple[str, str, str]]:
    resolved = jar_path.resolve()
    parts = resolved.parts
    if ".m2" in parts and "repository" in parts:
        repo_idx = parts.index("repository")
        if len(parts) - repo_idx >= 4:
            group_id = ".".join(parts[repo_idx + 1 : -3])
            artifact_id = parts[-3]
            version = parts[-2]
            if group_id and artifact_id and version:
                return group_id, artifact_id, version
    if ".gradle" in parts and "files-2.1" in parts:
        gradle_idx = parts.index("files-2.1")
        if len(parts) - gradle_idx >= 6:
            group_id = parts[gradle_idx + 1]
            artifact_id = parts[gradle_idx + 2]
            version = parts[gradle_idx + 3]
            if group_id and artifact_id and version:
                return group_id, artifact_id, version
    return None


def _embedded_pom_root(jar_path: Path, group_id: str, artifact_id: str) -> Optional[ET.Element]:
    preferred = f"META-INF/maven/{group_id}/{artifact_id}/pom.xml"
    try:
        with zipfile.ZipFile(jar_path) as handle:
            names = handle.namelist()
            if preferred in names:
                return ET.fromstring(handle.read(preferred))
            for name in names:
                if name.endswith("/pom.xml") and name.startswith("META-INF/maven/"):
                    return ET.fromstring(handle.read(name))
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return None
    return None


def _pom_roots_for_coords(
    group_id: str,
    artifact_id: str,
    version: str,
    *,
    jar_path: Optional[Path] = None,
) -> List[ET.Element]:
    pom_path = _pom_path_from_coords(group_id, artifact_id, version)
    if pom_path.exists():
        roots: List[ET.Element] = []
        for current_pom in _maven_pom_chain_from_pom(pom_path):
            try:
                roots.append(ET.parse(current_pom).getroot())
            except ET.ParseError:
                continue
        if roots:
            return roots
    if jar_path is not None:
        embedded_root = _embedded_pom_root(jar_path, group_id, artifact_id)
        if embedded_root is not None:
            return [embedded_root]
    return []


def _is_probably_test_dependency(group_id: str, artifact_id: str) -> bool:
    lowered_group = group_id.lower()
    lowered_artifact = artifact_id.lower()
    test_markers = (
        "junit",
        "testng",
        "assertj",
        "hamcrest",
        "mockito",
        "kotest",
        "awaitility",
        "arquillian",
        "evosuite",
        "rest-assured",
    )
    return any(marker in lowered_group or marker in lowered_artifact for marker in test_markers)


def _runtime_dependency_cp_for_coords(
    group_id: str,
    artifact_id: str,
    version: str,
    *,
    jar_path: Optional[Path] = None,
    max_depth: int = 2,
    seen_coords: Optional[Set[Tuple[str, str, str]]] = None,
) -> str:
    if max_depth <= 0:
        return ""
    if seen_coords is None:
        seen_coords = set()
    coord_key = (group_id, artifact_id, version)
    if coord_key in seen_coords:
        return ""
    seen_coords.add(coord_key)

    cache_key = (f"{group_id}:{artifact_id}:{version}", max_depth)
    if jar_path is None and cache_key in _RUNTIME_DEP_CP_CACHE:
        return _RUNTIME_DEP_CP_CACHE[cache_key]

    parsed_roots = _pom_roots_for_coords(group_id, artifact_id, version, jar_path=jar_path)
    if not parsed_roots:
        return ""

    props: Dict[str, str] = {}
    dependency_management: Dict[Tuple[str, str], str] = {}
    for root in parsed_roots:
        current_group_id = _pom_direct_child_text(root, "groupId")
        current_version = _pom_direct_child_text(root, "version")
        current_artifact_id = _pom_direct_child_text(root, "artifactId")
        parent = root.find(_pom_tag("parent"))
        if parent is not None:
            if not current_group_id:
                current_group_id = _resolve_props(_pom_direct_child_text(parent, "groupId"), props)
            if not current_version:
                current_version = _resolve_props(_pom_direct_child_text(parent, "version"), props)
        current_group_id = _resolve_props(current_group_id, props)
        current_version = _resolve_props(current_version, props)
        current_artifact_id = _resolve_props(current_artifact_id, props)
        props.update(
            {
                "project.groupId": current_group_id,
                "project.version": current_version,
                "project.artifactId": current_artifact_id,
                "groupId": current_group_id,
                "version": current_version,
                "artifactId": current_artifact_id,
            }
        )

        properties = root.find(_pom_tag("properties"))
        if properties is not None:
            for child in list(properties):
                tag = child.tag.rsplit("}", 1)[-1]
                value = _resolve_props((child.text or "").strip(), props)
                if value:
                    props[tag] = value

    for root in parsed_roots:
        dep_mgmt = root.find(_pom_tag("dependencyManagement"))
        if dep_mgmt is None:
            continue
        dependencies = dep_mgmt.find(_pom_tag("dependencies"))
        if dependencies is None:
            continue
        for dep in dependencies.findall(_pom_tag("dependency")):
            dep_group_id = _resolve_props(_pom_direct_child_text(dep, "groupId"), props)
            dep_artifact_id = _resolve_props(_pom_direct_child_text(dep, "artifactId"), props)
            dep_version = _resolve_props(_pom_direct_child_text(dep, "version"), props)
            dep_type = _resolve_props(_pom_direct_child_text(dep, "type"), props) or "jar"
            dep_scope = _resolve_props(_pom_direct_child_text(dep, "scope"), props) or "compile"
            if dep_type == "pom" and dep_scope == "import" and dep_group_id and dep_artifact_id and dep_version:
                dependency_management.update(
                    _collect_imported_bom_versions(_pom_path_from_coords(dep_group_id, dep_artifact_id, dep_version))
                )
                continue
            if dep_group_id and dep_artifact_id and dep_version:
                dependency_management[(dep_group_id, dep_artifact_id)] = dep_version

    jar_paths: List[str] = []
    seen_jars: Set[str] = set()
    for root in parsed_roots:
        dependencies = root.find(_pom_tag("dependencies"))
        if dependencies is None:
            continue
        for dep in dependencies.findall(_pom_tag("dependency")):
            dep_group_id = _resolve_props(_pom_direct_child_text(dep, "groupId"), props)
            dep_artifact_id = _resolve_props(_pom_direct_child_text(dep, "artifactId"), props)
            dep_version = _resolve_props(_pom_direct_child_text(dep, "version"), props) or dependency_management.get(
                (dep_group_id, dep_artifact_id),
                "",
            )
            dep_scope = _resolve_props(_pom_direct_child_text(dep, "scope"), props) or "compile"
            dep_type = _resolve_props(_pom_direct_child_text(dep, "type"), props) or "jar"
            dep_optional = _resolve_props(_pom_direct_child_text(dep, "optional"), props).lower() == "true"
            if not dep_group_id or not dep_artifact_id or not dep_version:
                continue
            if dep_optional or dep_type == "pom" or dep_scope in {"test", "provided", "system", "import"}:
                continue
            if _is_probably_test_dependency(dep_group_id, dep_artifact_id):
                continue
            dep_jar_path = _local_jar_path_from_coords(dep_group_id, dep_artifact_id, dep_version)
            if dep_jar_path is None or not dep_jar_path.exists():
                continue
            dep_jar_str = str(dep_jar_path)
            if dep_jar_str not in seen_jars:
                seen_jars.add(dep_jar_str)
                jar_paths.append(dep_jar_str)
            nested_cp = _runtime_dependency_cp_for_coords(
                dep_group_id,
                dep_artifact_id,
                dep_version,
                jar_path=dep_jar_path,
                max_depth=max_depth - 1,
                seen_coords=seen_coords,
            )
            if not nested_cp:
                continue
            for entry in nested_cp.split(":"):
                nested_entry = entry.strip()
                if not nested_entry or nested_entry in seen_jars:
                    continue
                seen_jars.add(nested_entry)
                jar_paths.append(nested_entry)

    merged = _merge_classpath_parts(jar_paths)
    if jar_path is None:
        _RUNTIME_DEP_CP_CACHE[cache_key] = merged
    return merged


def _expand_runtime_dependency_cp(classpath: str, *, max_depth: int = 2) -> str:
    cache_key = (classpath, max_depth)
    cached = _RUNTIME_DEP_CP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    extras: List[str] = []
    seen_entries: Set[str] = set()
    seen_coords: Set[Tuple[str, str, str]] = set()
    for raw_entry in classpath.split(":"):
        entry = raw_entry.strip()
        if not entry:
            continue
        jar_path = Path(entry)
        if not jar_path.exists() or not jar_path.is_file() or jar_path.suffix != ".jar":
            continue
        coords = _jar_coords_from_path(jar_path)
        if coords is None:
            continue
        dep_cp = _runtime_dependency_cp_for_coords(
            coords[0],
            coords[1],
            coords[2],
            jar_path=jar_path,
            max_depth=max_depth,
            seen_coords=seen_coords,
        )
        if not dep_cp:
            continue
        for dep_entry in dep_cp.split(":"):
            candidate = dep_entry.strip()
            if not candidate or candidate in seen_entries or candidate == entry:
                continue
            seen_entries.add(candidate)
            extras.append(candidate)

    merged = _merge_classpath_parts(extras)
    _RUNTIME_DEP_CP_CACHE[cache_key] = merged
    return merged


def _collect_imported_bom_versions(
    pom_path: Path,
    seen: Optional[Set[Path]] = None,
) -> Dict[Tuple[str, str], str]:
    if seen is None:
        seen = set()
    if pom_path in seen or not pom_path.exists():
        return {}
    seen.add(pom_path)

    dep_mgmt: Dict[Tuple[str, str], str] = {}
    props: Dict[str, str] = {}
    parsed_roots: List[ET.Element] = []
    for current_pom in _maven_pom_chain_from_pom(pom_path):
        try:
            root = ET.parse(current_pom).getroot()
        except ET.ParseError:
            continue
        parsed_roots.append(root)
        group_id = _pom_direct_child_text(root, "groupId")
        version = _pom_direct_child_text(root, "version")
        artifact_id = _pom_direct_child_text(root, "artifactId")
        parent = root.find(_pom_tag("parent"))
        if parent is not None:
            if not group_id:
                group_id = _resolve_props(_pom_direct_child_text(parent, "groupId"), props)
            if not version:
                version = _resolve_props(_pom_direct_child_text(parent, "version"), props)
        group_id = _resolve_props(group_id, props)
        version = _resolve_props(version, props)
        artifact_id = _resolve_props(artifact_id, props)
        props.update(
            {
                "project.groupId": group_id,
                "project.version": version,
                "project.artifactId": artifact_id,
                "groupId": group_id,
                "version": version,
                "artifactId": artifact_id,
            }
        )
        properties = root.find(_pom_tag("properties"))
        if properties is not None:
            for child in list(properties):
                tag = child.tag.rsplit("}", 1)[-1]
                value = _resolve_props((child.text or "").strip(), props)
                if value:
                    props[tag] = value

    for root in parsed_roots:
        dep_mgmt_container = root.find(_pom_tag("dependencyManagement"))
        if dep_mgmt_container is None:
            continue
        dependencies = dep_mgmt_container.find(_pom_tag("dependencies"))
        if dependencies is None:
            continue
        for dep in dependencies.findall(_pom_tag("dependency")):
            group_id = _resolve_props(_pom_direct_child_text(dep, "groupId"), props)
            artifact_id = _resolve_props(_pom_direct_child_text(dep, "artifactId"), props)
            version = _resolve_props(_pom_direct_child_text(dep, "version"), props)
            dep_type = _resolve_props(_pom_direct_child_text(dep, "type"), props) or "jar"
            scope = _resolve_props(_pom_direct_child_text(dep, "scope"), props) or "compile"
            if dep_type == "pom" and scope == "import" and group_id and artifact_id and version:
                dep_mgmt.update(_collect_imported_bom_versions(_pom_path_from_coords(group_id, artifact_id, version), seen))
                continue
            if group_id and artifact_id and version:
                dep_mgmt[(group_id, artifact_id)] = version
    return dep_mgmt


def _maven_declared_dependency_cp(repo_root: Path, module_rel: str) -> str:
    module_dir = _module_dir(repo_root, module_rel)
    pom_chain = _maven_pom_chain(module_dir)
    if not pom_chain:
        return ""

    props: Dict[str, str] = {}
    root_group_id = ""

    parsed_roots: List[ET.Element] = []
    for pom_path in pom_chain:
        try:
            root = ET.parse(pom_path).getroot()
        except ET.ParseError:
            continue
        parsed_roots.append(root)
        group_id = _pom_direct_child_text(root, "groupId")
        version = _pom_direct_child_text(root, "version")
        artifact_id = _pom_direct_child_text(root, "artifactId")
        parent = root.find(_pom_tag("parent"))
        if parent is not None:
            if not group_id:
                group_id = _resolve_props(_pom_direct_child_text(parent, "groupId"), props)
            if not version:
                version = _resolve_props(_pom_direct_child_text(parent, "version"), props)

        group_id = _resolve_props(group_id, props)
        version = _resolve_props(version, props)
        artifact_id = _resolve_props(artifact_id, props)
        if group_id and not root_group_id:
            root_group_id = group_id

        props.update(
            {
                "project.groupId": group_id,
                "project.version": version,
                "project.artifactId": artifact_id,
                "groupId": group_id,
                "version": version,
                "artifactId": artifact_id,
            }
        )

        properties = root.find(_pom_tag("properties"))
        if properties is not None:
            for child in list(properties):
                tag = child.tag.rsplit("}", 1)[-1]
                value = _resolve_props((child.text or "").strip(), props)
                if value:
                    props[tag] = value

    dependency_management: Dict[Tuple[str, str], str] = {}
    for root in parsed_roots:
        dep_mgmt = root.find(_pom_tag("dependencyManagement"))
        if dep_mgmt is not None:
            dependencies = dep_mgmt.find(_pom_tag("dependencies"))
            if dependencies is not None:
                for dep in dependencies.findall(_pom_tag("dependency")):
                    group_id = _resolve_props(_pom_direct_child_text(dep, "groupId"), props)
                    artifact_id = _resolve_props(_pom_direct_child_text(dep, "artifactId"), props)
                    version = _resolve_props(_pom_direct_child_text(dep, "version"), props)
                    dep_type = _resolve_props(_pom_direct_child_text(dep, "type"), props) or "jar"
                    scope = _resolve_props(_pom_direct_child_text(dep, "scope"), props) or "compile"
                    if dep_type == "pom" and scope == "import" and group_id and artifact_id and version:
                        dependency_management.update(
                            _collect_imported_bom_versions(_pom_path_from_coords(group_id, artifact_id, version))
                        )
                        continue
                    if group_id and artifact_id and version:
                        dependency_management[(group_id, artifact_id)] = version

    jars: List[str] = []
    seen_jars: Set[str] = set()
    for root in parsed_roots:
        dependencies = root.find(_pom_tag("dependencies"))
        if dependencies is None:
            continue
        for dep in dependencies.findall(_pom_tag("dependency")):
            group_id = _resolve_props(_pom_direct_child_text(dep, "groupId"), props)
            artifact_id = _resolve_props(_pom_direct_child_text(dep, "artifactId"), props)
            version = _resolve_props(_pom_direct_child_text(dep, "version"), props) or dependency_management.get((group_id, artifact_id), "")
            scope = _resolve_props(_pom_direct_child_text(dep, "scope"), props) or "compile"
            dep_type = _resolve_props(_pom_direct_child_text(dep, "type"), props) or "jar"
            optional = _resolve_props(_pom_direct_child_text(dep, "optional"), props).lower() == "true"
            if not group_id or not artifact_id or not version:
                continue
            if optional or dep_type == "pom" or scope == "import" or scope == "system":
                continue
            if root_group_id and group_id.startswith(root_group_id):
                continue
            jar_path = _jar_path_from_coords(group_id, artifact_id, version)
            if not jar_path.exists():
                continue
            jar_str = str(jar_path)
            if jar_str in seen_jars:
                continue
            seen_jars.add(jar_str)
            jars.append(jar_str)
    return ":".join(jars)


def _maven_runtime_cp(repo_root: Path, module_rel: str) -> str:
    module_dir = _module_dir(repo_root, module_rel)
    if not module_dir.exists():
        return ""

    cp_parts: List[str] = []
    scope_fallbacks = ("test", "compile", "runtime", "provided")

    def first_nonempty_cp(cwd: Path, cmd_factory, *, fallback_scopes: Sequence[str]) -> str:
        for scope in fallback_scopes:
            cp = _run_maven_classpath(cmd_factory(scope), cwd)
            if cp:
                return cp
        return ""

    def base_cmd_for_scope(scope: str) -> List[str]:
        return [
            _maven_cmd(repo_root),
            "-q",
            "-DskipTests",
            "dependency:build-classpath",
            "-Dmdep.pathSeparator=:",
            f"-Dmdep.includeScope={scope}",
        ]

    def root_cmd_for_scope(scope: str) -> List[str]:
        return [
            _maven_cmd(repo_root),
            "-q",
            "-pl",
            _norm_module_rel(module_rel) or ".",
            "-am",
            "-DskipTests",
            "dependency:build-classpath",
            "-Dmdep.pathSeparator=:",
            f"-Dmdep.includeScope={scope}",
        ]

    module_cp = first_nonempty_cp(module_dir, base_cmd_for_scope, fallback_scopes=("test",))
    if module_cp:
        cp_parts.append(module_cp)

    root_cp = first_nonempty_cp(repo_root, root_cmd_for_scope, fallback_scopes=("test",))
    if root_cp:
        cp_parts.append(root_cp)
    else:
        if not module_cp:
            module_cp = first_nonempty_cp(module_dir, base_cmd_for_scope, fallback_scopes=scope_fallbacks[1:])
            if module_cp:
                cp_parts.append(module_cp)
        root_cp = first_nonempty_cp(repo_root, root_cmd_for_scope, fallback_scopes=scope_fallbacks[1:])
        if root_cp:
            cp_parts.append(root_cp)

    declared_cp = _maven_declared_dependency_cp(repo_root, module_rel)
    if declared_cp:
        cp_parts.append(declared_cp)
        expanded_declared_cp = _expand_runtime_dependency_cp(declared_cp)
        if expanded_declared_cp:
            cp_parts.append(expanded_declared_cp)

    return _merge_classpath_parts(cp_parts)


def _gradle_runtime_cp(repo_root: Path, module_rel: str) -> str:
    module_dir = _module_dir(repo_root, module_rel)
    if not module_dir.exists():
        return ""

    with tempfile.NamedTemporaryFile(prefix="agt-gradle-init-", suffix=".gradle", mode="w", delete=False) as handle:
        handle.write(
            """
allprojects { p ->
  p.tasks.register("printAgtClasspath") {
    doLast {
      def names = [
        "testAnnotationProcessor",
        "annotationProcessor",
        "testCompileClasspath",
        "testRuntimeClasspath",
        "compileClasspath",
        "runtimeClasspath",
        "testImplementation",
        "implementation",
        "testRuntimeOnly",
        "runtimeOnly"
      ]
      def files = [] as LinkedHashSet<File>
      names.each { n ->
        def cfg = p.configurations.findByName(n)
        if (cfg != null && cfg.canBeResolved) {
          try {
            cfg.resolve()
            files.addAll(cfg.files)
          } catch (Throwable ignored) {
          }
        }
      }
      print(files.collect { it.absolutePath }.join(":"))
    }
  }
}
"""
        )
        init_script = Path(handle.name)

    try:
        gradlew = repo_root / "gradlew"
        gradle_cmd = str(gradlew.resolve()) if gradlew.exists() else "gradle"
        normalized_module = _norm_module_rel(module_rel).replace("/", ":")
        classpath_tasks = [
            f":{normalized_module}:printAgtClasspath"
            if normalized_module
            else ":printAgtClasspath"
        ]
        # Some builds rename included projects while retaining their directory
        # names (for example Micronaut maps /inject to :micronaut-inject).
        # Try that conventional mapping before falling back to no classpath.
        if normalized_module and ":" not in normalized_module:
            classpath_tasks.append(f":micronaut-{normalized_module}:printAgtClasspath")
        for classpath_task in classpath_tasks:
            cmd = [
                gradle_cmd, "--no-daemon", "--no-configuration-cache", "-q", "-I", str(init_script),
                classpath_task,
            ]
            proc = subprocess.run(
                cmd, cwd=module_dir, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
            if proc.returncode == 0:
                return (proc.stdout or "").strip()
        return ""
    finally:
        init_script.unlink(missing_ok=True)


def _resolve_single_module_runtime_classpath(repo_root: Path, module_rel: str, build_tool: str) -> str:
    tool = (build_tool or "").strip().lower()
    if tool not in {"maven", "gradle"}:
        tool = _detect_build_tool(repo_root)

    key = (str(repo_root.resolve()), _norm_module_rel(module_rel), tool)
    if key in _MODULE_RUNTIME_CP_CACHE:
        return _MODULE_RUNTIME_CP_CACHE[key]

    cp = ""
    if tool == "maven":
        cp = _maven_runtime_cp(repo_root, module_rel)
    elif tool == "gradle":
        cp = _gradle_runtime_cp(repo_root, module_rel)

    _MODULE_RUNTIME_CP_CACHE[key] = cp
    return cp


def resolve_repo_runtime_classpath(
    repo_root: Path,
    module_rel: str,
    build_tool: str,
    *,
    source_files: Sequence[Path] = (),
    log_text: str = "",
) -> str:
    tool = (build_tool or "").strip().lower()
    if tool not in {"maven", "gradle"}:
        tool = _detect_build_tool(repo_root)

    module_rels_list = _related_module_rels(repo_root, module_rel, source_files, log_text)
    normalized_module_rels = [_norm_module_rel(rel) for rel in module_rels_list]
    if any(normalized_module_rels):
        module_rels_list = [rel for rel in module_rels_list if _norm_module_rel(rel)]
    module_rels = tuple(module_rels_list)
    key = (str(repo_root.resolve()), module_rels, tool)
    if key in _RUNTIME_CP_CACHE:
        return _RUNTIME_CP_CACHE[key]

    if tool == "maven":
        for related_module_rel in module_rels:
            _ensure_maven_module_outputs(repo_root, related_module_rel)

    cp_parts: List[str] = []
    class_dir_parts: List[str] = []
    seen_class_dirs: Set[str] = set()
    for related_module_rel in module_rels:
        cp_parts.append(_resolve_single_module_runtime_classpath(repo_root, related_module_rel, tool))
        for path in candidate_repo_class_dirs(repo_root, related_module_rel):
            path_str = str(path)
            if path_str in seen_class_dirs:
                continue
            seen_class_dirs.add(path_str)
            class_dir_parts.append(path_str)

    cp = _merge_classpath_parts([*cp_parts, *class_dir_parts])
    _RUNTIME_CP_CACHE[key] = cp
    return cp


def read_compile_log(log_file: Path) -> str:
    try:
        return log_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def categorize_compile_problem(log_text: str) -> str:
    lowered = log_text.lower()
    if not lowered.strip():
        return "no_javac_output"

    has_missing_symbol = "cannot find symbol" in lowered
    has_missing_package = re.search(r"package\s+[a-z0-9_.$]+\s+does not exist", lowered) is not None
    if has_missing_symbol and has_missing_package:
        return "missing_symbol_and_package"
    if has_missing_symbol:
        return "missing_symbol"
    if has_missing_package:
        return "missing_package"
    if "class file for" in lowered and "not found" in lowered:
        return "missing_class_file"
    if "duplicate class:" in lowered:
        return "duplicate_class"
    if "incompatible types:" in lowered:
        return "incompatible_types"
    if "cannot be applied to given types" in lowered:
        return "argument_mismatch"
    if (
        "has private access in" in lowered
        or "has protected access in" in lowered
        or "is not public in" in lowered
    ):
        return "access_error"
    if any(
        marker in lowered
        for marker in (
            "reached end of file while parsing",
            "';' expected",
            "<identifier> expected",
            "illegal start of expression",
            "not a statement",
        )
    ):
        return "syntax_error"
    return "other_compile_error"


def extract_compile_problem_detail(log_text: str) -> str:
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "error:" in line or "cannot find symbol" in line or "does not exist" in line or "not found" in line:
            return line
    return ""


def is_structurally_sane_java_source(text: str) -> bool:
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    code = re.sub(r"//.*", "", code)
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    code = re.sub(r"'(?:\\.|[^'\\])+'", "''", code)
    if not re.search(r"\b(class|interface|enum|record)\b", code):
        return False
    depth = 0
    saw_open = False
    for ch in code:
        if ch == "{":
            depth += 1
            saw_open = True
            continue
        if ch == "}":
            depth -= 1
            if depth < 0:
                return False
    if not saw_open or depth != 0:
        return False
    return code.rstrip().endswith("}")


def _parse_javac_error_lines_by_source(log_text: str) -> Dict[Path, Set[int]]:
    errors: Dict[Path, Set[int]] = {}
    pattern = re.compile(r"^(.+?\.java):(\d+):\s+error:")
    for raw_line in log_text.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        source_path = Path(match.group(1)).resolve()
        line_no = int(match.group(2))
        errors.setdefault(source_path, set()).add(line_no)
    return errors


def _method_spans_with_annotations(lines: Sequence[str]) -> List[Tuple[int, int, int]]:
    spans: List[Tuple[int, int, int]] = []
    method_pattern = re.compile(
        r"^\s*(?:public|protected|private)\s+(?:static\s+)?(?:<[^>]+>\s+)?[\w\[\]<>?,.$\s]+\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\("
    )
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not method_pattern.match(line):
            idx += 1
            continue
        decl_start = idx
        end = idx
        brace_depth = line.count("{") - line.count("}")
        saw_open = line.count("{") > 0
        while end + 1 < len(lines):
            if saw_open and brace_depth <= 0:
                break
            end += 1
            candidate = lines[end]
            opens = candidate.count("{")
            closes = candidate.count("}")
            if opens > 0:
                saw_open = True
            brace_depth += opens - closes
        if not saw_open:
            idx += 1
            continue
        anno_start = decl_start
        while anno_start > 0 and lines[anno_start - 1].strip().startswith("@"):
            anno_start -= 1
        spans.append((anno_start, decl_start, end))
        idx = end + 1
    return spans


def _drop_generated_methods_containing_errors(java_file: Path, error_lines: Set[int]) -> bool:
    if not error_lines:
        return False
    try:
        lines = java_file.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except OSError:
        return False
    if not lines:
        return False

    spans = _method_spans_with_annotations(lines)
    if not spans:
        return False

    remove_indexes: Set[int] = set()
    for anno_start, decl_start, end in spans:
        start_line = decl_start + 1
        end_line = end + 1
        if any(start_line <= line_no <= end_line for line_no in error_lines):
            remove_indexes.update(range(anno_start, end + 1))
    if not remove_indexes:
        return False

    updated = [line for idx, line in enumerate(lines) if idx not in remove_indexes]
    if updated == lines:
        return False

    updated_text = "".join(updated)
    if not is_structurally_sane_java_source(updated_text):
        return False
    try:
        java_file.write_text(updated_text, encoding="utf-8")
    except OSError:
        return False
    return True


def prune_generated_methods_from_compile_output(java_files: Sequence[Path], compile_output: str) -> bool:
    if not compile_output:
        return False

    generated_candidates = {
        source.resolve(): source
        for source in java_files
        if "_ESTest" in source.name and not source.name.endswith("_ESTest_scaffolding.java")
    }
    if not generated_candidates:
        return False

    errors_by_source = _parse_javac_error_lines_by_source(compile_output)
    changed = False
    for source_path, error_lines in errors_by_source.items():
        candidate = generated_candidates.get(source_path)
        if candidate is None:
            continue
        changed = _drop_generated_methods_containing_errors(candidate, error_lines) or changed
    return changed


def looks_like_fixable_test_source(java_file: Path) -> bool:
    if looks_like_scaffolding(java_file.name):
        return False
    if java_file.name.endswith("_ESTest.java"):
        return True
    return is_probably_test_filename(java_file.name)


def remove_unused_imports(path: str, dry_run: bool) -> bool:
    java_file = Path(path)
    try:
        raw = java_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    lines = raw.splitlines(keepends=True)
    if not lines:
        return False

    import_pattern = re.compile(
        r"^\s*import\s+(static\s+)?([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$*]*)+)\s*;\s*$"
    )
    import_entries: List[Tuple[int, bool, str]] = []
    body_lines: List[str] = []
    for index, line in enumerate(lines):
        match = import_pattern.match(line)
        if match:
            import_entries.append((index, bool(match.group(1)), match.group(2)))
            continue
        if line.lstrip().startswith("package "):
            continue
        body_lines.append(line)

    if not import_entries:
        return False

    body = "".join(body_lines)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    body = re.sub(r"//.*", "", body)
    body = re.sub(r'"(?:\\.|[^"\\])*"', '""', body)
    body = re.sub(r"'(?:\\.|[^'\\])+'", "''", body)

    keep_indexes: Set[int] = set()
    for index, is_static, target in import_entries:
        if target.endswith(".*"):
            keep_indexes.add(index)
            continue

        parts = [part for part in target.split(".") if part]
        if not parts:
            keep_indexes.add(index)
            continue

        token = parts[-1]
        if is_static and len(parts) >= 2:
            owner = parts[-2]
            if re.search(rf"\b{re.escape(token)}\b", body) or re.search(rf"\b{re.escape(owner)}\b", body):
                keep_indexes.add(index)
            continue

        if re.search(rf"\b{re.escape(token)}\b", body):
            keep_indexes.add(index)

    changed = False
    for index, _is_static, _target in import_entries:
        if index in keep_indexes:
            continue
        lines[index] = ""
        changed = True

    if not changed:
        return False
    if dry_run:
        return True

    try:
        java_file.write_text("".join(lines), encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return True


def expand_same_package_support_sources(repo_root: Optional[Path], java_files: Sequence[Path]) -> List[Path]:
    expanded: List[Path] = []
    seen: Set[Path] = set()
    seen_identities: Set[Tuple[str, str]] = set()

    def add(path: Path) -> None:
        if path in seen:
            return
        identity = _candidate_java_identity(path)
        if identity[1] and identity in seen_identities:
            return
        seen.add(path)
        if identity[1]:
            seen_identities.add(identity)
        expanded.append(path)

    for java_file in java_files:
        add(java_file)
        source = java_file
        if repo_root is not None and repo_root.exists():
            counterpart = _match_repo_source_for_collected_file(repo_root, java_file)
            if counterpart is not None and counterpart.exists():
                source = counterpart
        for related in _same_package_java_sources(source):
            add(related)
    return expanded


def merge_candidate_java_files(*groups: Sequence[Path]) -> List[Path]:
    merged: List[Path] = []
    seen: Set[Path] = set()
    for group in groups:
        for java_file in group:
            if java_file in seen:
                continue
            seen.add(java_file)
            merged.append(java_file)
    return merged


def _strip_string_literals(line: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', line)


def _brace_counts(line: str) -> Tuple[int, int]:
    sanitized = _strip_string_literals(line)
    for _ in range(2):
        sanitized = re.sub(r"\{[^{}]*\}", "", sanitized)
    return sanitized.count("{"), sanitized.count("}")


def _parse_javac_error_lines(output: str, test_file: Path) -> List[int]:
    pattern = re.compile(rf"^{re.escape(str(test_file))}:(\d+):")
    lines: List[int] = []
    for line in output.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        try:
            lines.append(int(match.group(1)))
        except ValueError:
            continue
    return sorted(set(lines))


def _comment_error_lines_in_file(test_file: Path, line_numbers: Sequence[int]) -> bool:
    try:
        raw = test_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    lines = raw.splitlines(keepends=True)
    if not lines:
        return False

    def _looks_like_top_level_type_declaration(line: str) -> bool:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            return False
        return re.match(
            r"^(?:public\s+|protected\s+|private\s+)?(?:abstract\s+|final\s+|static\s+|sealed\s+|non-sealed\s+)*"
            r"(?:class|interface|enum|record|@interface)\s+[A-Za-z_][A-Za-z0-9_]*\b",
            stripped,
        ) is not None

    def _is_live_code(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and not stripped.startswith("//")

    lines_to_comment: Set[int] = set()
    for line_number in line_numbers:
        idx = line_number - 1
        if idx < 0 or idx >= len(lines) or idx in lines_to_comment:
            continue
        if lines[idx].lstrip().startswith("//"):
            continue
        stripped_line = lines[idx].strip()
        if stripped_line == "}" and idx == len(lines) - 1:
            backtrack = idx - 1
            while backtrack >= 0:
                candidate = lines[backtrack]
                if candidate.strip() and not candidate.lstrip().startswith("//"):
                    break
                backtrack -= 1
            if backtrack < 0 or backtrack in lines_to_comment:
                continue
            idx = backtrack
            if lines[idx].lstrip().startswith("//"):
                continue
            stripped_line = lines[idx].strip()

        start_idx = idx
        while start_idx > 0 and lines[start_idx - 1].strip().startswith("@"):
            start_idx -= 1
        for anno_idx in range(start_idx, idx):
            if not lines[anno_idx].lstrip().startswith("//"):
                lines_to_comment.add(anno_idx)

        open_current, close_current = _brace_counts(lines[idx])
        lines_to_comment.add(idx)
        if open_current == close_current:
            continue

        open_count = open_current
        close_count = close_current
        next_index = idx + 1
        while next_index < len(lines):
            if next_index == len(lines) - 1 and lines[next_index].strip() == "}":
                break
            if not lines[next_index].lstrip().startswith("//"):
                open_delta, close_delta = _brace_counts(lines[next_index])
                open_count += open_delta
                close_count += close_delta
                lines_to_comment.add(next_index)
                if open_count > 0 and open_count == close_count:
                    break
            next_index += 1

    if not lines_to_comment:
        return False

    if len(lines) > 0 and (len(lines) - 1) in lines_to_comment and lines[-1].strip() == "}":
        lines_to_comment.remove(len(lines) - 1)
    if not lines_to_comment:
        return False

    live_code_indexes = [idx for idx, line in enumerate(lines) if _is_live_code(line)]
    live_code_total = len(live_code_indexes)
    lines_to_comment_live = {idx for idx in lines_to_comment if _is_live_code(lines[idx])}
    comment_live_count = len(lines_to_comment_live)
    if not comment_live_count:
        return False
    if comment_live_count > 120:
        return False
    if live_code_total > 0:
        ratio = comment_live_count / live_code_total
        if ratio > 0.60 and comment_live_count >= 12:
            return False

    if any(_looks_like_top_level_type_declaration(lines[idx]) for idx in lines_to_comment):
        return False

    for idx in sorted(lines_to_comment):
        if lines[idx].lstrip().startswith("//"):
            continue
        lines[idx] = f"// {lines[idx]}"

    test_file.write_text("".join(lines), encoding="utf-8", errors="ignore")
    return True


def _parse_missing_packages_from_javac_log(output: str) -> Set[str]:
    missing_packages: Set[str] = set()
    for match in re.finditer(r"package\s+([A-Za-z0-9_.$]+)\s+does not exist", output):
        missing_packages.add(match.group(1))
    return missing_packages


def _read_import_context(test_file: Path) -> tuple[str, Set[str], str]:
    try:
        text = test_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "", set(), ""

    package_name = ""
    imports: Set[str] = set()
    for line in text.splitlines():
        pkg_match = re.match(r"^\s*package\s+([A-Za-z0-9_.]+)\s*;", line)
        if pkg_match:
            package_name = pkg_match.group(1)
            continue
        imp_match = re.match(r"^\s*import\s+([A-Za-z0-9_.*]+)\s*;", line)
        if imp_match:
            imports.add(imp_match.group(1))
    return package_name, imports, text


def _has_import(imports: Set[str], package_name: str, class_name: str) -> bool:
    fqcn = f"{package_name}.{class_name}" if package_name else class_name
    return fqcn in imports or f"{package_name}.*" in imports


def _insert_imports(test_file: Path, import_fqcns: Sequence[str]) -> bool:
    unique_imports: List[str] = []
    seen: Set[str] = set()
    for fqcn in import_fqcns:
        if not fqcn or fqcn in seen:
            continue
        seen.add(fqcn)
        unique_imports.append(fqcn)
    if not unique_imports:
        return False

    try:
        lines = test_file.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except Exception:
        return False

    package_end = 0
    import_end = 0
    for idx, line in enumerate(lines):
        if re.match(r"^\s*package\s+[A-Za-z0-9_.]+\s*;", line):
            package_end = idx + 1
        if re.match(r"^\s*import\s+[A-Za-z0-9_.*]+\s*;", line):
            import_end = idx + 1
    insert_at = import_end or package_end

    block = [f"import {fqcn};\n" for fqcn in sorted(unique_imports)]
    if insert_at == package_end and package_end > 0 and (package_end == len(lines) or lines[package_end].strip()):
        block.insert(0, "\n")
    lines[insert_at:insert_at] = block
    test_file.write_text("".join(lines), encoding="utf-8", errors="ignore")
    return True


def _attempt_symbol_and_package_fixes(
    *,
    test_file: Path,
    output: str,
    candidate_java_files: Sequence[Path],
    additional_java_files: List[Path],
) -> bool:
    package_name, imports, text = _read_import_context(test_file)
    if not text:
        return False

    changed = False
    import_fqcns: List[str] = []
    additional_set = set(additional_java_files)
    candidate_list = [path for path in candidate_java_files if path != test_file]

    for symbol in sorted(extract_missing_symbols_from_javac_log(output)):
        declaring_sources = find_declaring_sources(candidate_list, {symbol})
        if not declaring_sources:
            continue
        declaring = declaring_sources[0]
        declaring_package, declaring_class = parse_package_and_class(declaring)
        if declaring not in additional_set:
            additional_java_files.append(declaring)
            additional_set.add(declaring)
            changed = True
        if (
            declaring_class
            and declaring_package
            and declaring_package != package_name
            and not _has_import(imports, declaring_package, declaring_class)
        ):
            import_fqcns.append(f"{declaring_package}.{declaring_class}")

    missing_packages = _parse_missing_packages_from_javac_log(output)
    if missing_packages:
        for line in text.splitlines():
            match = re.match(r"^\s*import\s+([A-Za-z0-9_.]+)\s*;", line)
            if not match:
                continue
            fqcn = match.group(1)
            package_name_part, _, class_name = fqcn.rpartition(".")
            if not class_name or package_name_part not in missing_packages:
                continue
            for declaring in find_declaring_sources(candidate_list, {class_name}):
                declaring_package, declaring_class = parse_package_and_class(declaring)
                if declaring_package != package_name_part or declaring_class != class_name:
                    continue
                if declaring not in additional_set:
                    additional_java_files.append(declaring)
                    additional_set.add(declaring)
                    changed = True
                break

    if import_fqcns:
        changed = _insert_imports(test_file, import_fqcns) or changed
    return changed


def comment_compile_errors(
    *,
    test_file: Path,
    build_dir: Path,
    log_file: Path,
    libs_glob_cp: str,
    sut_jar: Path,
    extra_cp: str = "",
    additional_java_files: Optional[Sequence[Path]] = None,
    candidate_java_files: Optional[Sequence[Path]] = None,
    max_iterations: int = 50,
) -> bool:
    ensure_dir(build_dir)
    ensure_dir(log_file.parent)
    test_file = test_file.resolve()

    cp_parts = [libs_glob_cp, str(sut_jar)]
    if extra_cp.strip():
        cp_parts.append(extra_cp.strip())
    cp = ":".join(cp_parts)
    extra_files = [path.resolve() for path in (additional_java_files or []) if path.resolve() != test_file]
    candidate_files = [path.resolve() for path in (candidate_java_files or additional_java_files or []) if path.resolve() != test_file]

    for iteration in range(max_iterations):
        cmd = [
            "javac",
            "-Xmaxerrs",
            "0",
            "-cp",
            cp,
            "-d",
            str(build_dir),
            str(test_file),
        ]
        cmd.extend(str(path) for path in extra_files)
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        out = proc.stdout or ""
        status = "success" if proc.returncode == 0 else "failure"
        log_file.write_text(
            f"[agt] javac cmd:\n{shlex.join(cmd)}\n\n[agt] returncode: {proc.returncode} ({status})\n\n[agt] output:\n{out}\n",
            encoding="utf-8",
            errors="ignore",
        )

        if proc.returncode == 0:
            return True

        if _attempt_symbol_and_package_fixes(
            test_file=test_file,
            output=out,
            candidate_java_files=candidate_files,
            additional_java_files=extra_files,
        ):
            continue

        error_lines = _parse_javac_error_lines(out, test_file)
        if not error_lines or not _comment_error_lines_in_file(test_file, error_lines):
            return False
        if iteration == max_iterations - 1:
            return False

    return False
