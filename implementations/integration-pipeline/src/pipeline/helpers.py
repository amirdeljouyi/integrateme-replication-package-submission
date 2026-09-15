from __future__ import annotations

import csv
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..core.common import is_probably_test_filename, looks_like_scaffolding, parse_package_and_class

_EMPTY_GENERATED_TEST_METHOD_PATTERN = re.compile(r"\bvoid\s+notGeneratedAnyTest\s*\(")
_EMPTY_GENERATED_TEST_COMMENT = "EvoSuite did not generate any tests"
_TEST_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|protected|private|final|static|synchronized|native|abstract|strictfp|default)\s+)*void\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\("
)
_VOID_METHOD_START_RE = re.compile(_TEST_METHOD_RE.pattern, re.MULTILINE)
_TEST_ANNOTATION_NAME_RE = re.compile(
    r"@\s*(?:[A-Za-z_][A-Za-z0-9_$]*\.)*"
    r"(?P<name>Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b"
)
_UNSUPPORTED_INDIVIDUAL_TEST_ANNOTATIONS = {"ParameterizedTest", "TestFactory", "TestTemplate"}
_ADOPTED_TESTS_MARKER_RE = re.compile(
    r"^\s*//\s*(?:the\s+)?(?:added|adopted)(?:\s+agt)?\s+tests?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def mask_java_comments_and_strings(source: str) -> str:
    chars = list(source)
    index = 0
    state = "code"
    while index < len(chars):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(chars) else ""
        triple = source[index : index + 3]

        if state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                chars[index] = " "
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                chars[index : index + 2] = [" ", " "]
                state = "code"
                index += 2
            else:
                if char != "\n":
                    chars[index] = " "
                index += 1
            continue
        if state == "text_block":
            if triple == '"""':
                chars[index : index + 3] = [" ", " ", " "]
                state = "code"
                index += 3
            else:
                if char != "\n":
                    chars[index] = " "
                index += 1
            continue
        if state in {"string", "character"}:
            chars[index] = " "
            if char == "\\" and index + 1 < len(chars):
                chars[index + 1] = " "
                index += 2
                continue
            expected = '"' if state == "string" else "'"
            if char == expected:
                state = "code"
            index += 1
            continue

        if char == "/" and next_char == "/":
            chars[index : index + 2] = [" ", " "]
            state = "line_comment"
            index += 2
        elif char == "/" and next_char == "*":
            chars[index : index + 2] = [" ", " "]
            state = "block_comment"
            index += 2
        elif triple == '"""':
            chars[index : index + 3] = [" ", " ", " "]
            state = "text_block"
            index += 3
        elif char == '"':
            chars[index] = " "
            state = "string"
            index += 1
        elif char == "'":
            chars[index] = " "
            state = "character"
            index += 1
        else:
            index += 1
    return "".join(chars)


def find_tests_in_bucket(bucket_root: Path, filenames: List[str]) -> List[Path]:
    """
    Resolve basenames by searching within a repo bucket directory.
    Works for both:
      - collected layout: ../manual/<repo_dir>/<fqcn>/SomeTest.java
      - repos layout:     ../repos/<repo_dir>/.../SomeTest.java (not recommended unless inventory is tight)
    """
    want = [f.strip() for f in filenames if f and f.strip() and f.strip().lower() != "null"]
    if not want or not bucket_root.exists():
        return []

    index: Dict[str, List[Path]] = {}
    for p in bucket_root.rglob("*.java"):
        index.setdefault(p.name, []).append(p)

    out: List[Path] = []
    for f in want:
        hits = index.get(f, [])
        if hits:
            out.append(sorted(hits)[0])
    return out


def expand_manual_sources(manual_test_files: List[Path]) -> List[Path]:
    """
    Keep scope tight:
      - Always include selected manual test file(s)
      - Include same-folder NON-test helpers (*.java that are not *Test.java / *IT.java etc)
    """
    out: List[Path] = []
    seen: Set[Path] = set()

    for tf in manual_test_files:
        if tf not in seen:
            seen.add(tf)
            out.append(tf)

        pkg_dir = tf.parent
        if not pkg_dir.exists():
            continue

        for p in pkg_dir.glob("*.java"):
            if p == tf:
                continue
            if looks_like_scaffolding(p.name):
                continue
            if is_probably_test_filename(p.name):
                continue
            if p not in seen:
                seen.add(p)
                out.append(p)

    return out


def looks_like_test_source(java_file: Path) -> bool:
    if is_probably_test_filename(java_file.name):
        return True

    try:
        text = java_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    markers = (
        "@Test",
        "@ParameterizedTest",
        "@RepeatedTest",
        "@TestFactory",
        "@TestTemplate",
        "@RunWith(",
        "@ExtendWith(",
        " extends TestCase",
        "org.junit.Test",
        "org.junit.jupiter.api.Test",
    )
    return any(marker in text for marker in markers)


def generated_source_matches_target(java_file: Path, target_fqcn: str) -> bool:
    target_fqcn = (target_fqcn or "").strip()
    if not target_fqcn or "." not in target_fqcn:
        return True

    target_pkg, target_cls = target_fqcn.rsplit(".", 1)
    pkg, cls = parse_package_and_class(java_file)
    if not cls:
        return False

    normalized_cls = cls
    if normalized_cls.endswith("_ESTest_scaffolding"):
        normalized_cls = normalized_cls[: -len("_ESTest_scaffolding")]
    elif normalized_cls.endswith("_ESTest"):
        normalized_cls = normalized_cls[: -len("_ESTest")]

    if normalized_cls != target_cls:
        return False
    if pkg and pkg != target_pkg:
        # Compatibility tests may deliberately live in the wrapper package
        # while exercising a legacy implementation selected as the CUT.
        # Accept only an explicit fully-qualified reference; a simple-name
        # collision is still rejected.
        try:
            return target_fqcn in java_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
    return True


def first_test_fqcn_from_sources(sources: List[Path], *, prefer_estest: bool) -> Optional[str]:
    """
    Pick a single "representative" test fqcn from compiled sources.
    - prefer_estest=True: prefer *_ESTest.java
    - prefer_estest=False: prefer non-scaffolding and not *_ESTest_scaffolding.java
    """
    if not sources:
        return None

    def score(p: Path) -> Tuple[int, str]:
        name = p.name
        s = 100
        if looks_like_scaffolding(name):
            s += 1000
        if not looks_like_test_source(p):
            s += 500
        if prefer_estest:
            if name.endswith("_ESTest.java"):
                s -= 50
            if name.endswith("_ESTest_scaffolding.java"):
                s += 500
        else:
            if name.endswith("_ESTest.java"):
                s += 200
        return (s, name)

    for p in sorted(sources, key=score):
        pkg, cls = parse_package_and_class(p)
        if not cls:
            continue
        if "scaffolding" in cls.lower():
            continue
        return f"{pkg}.{cls}" if pkg else cls

    return None


def first_test_source_for_fqcn(sources: List[Path], fqcn: Optional[str]) -> Optional[Path]:
    if not fqcn:
        return None
    for p in sources:
        pkg, cls = parse_package_and_class(p)
        if not cls:
            continue
        cand = f"{pkg}.{cls}" if pkg else cls
        if cand == fqcn:
            return p
    return None


def is_empty_generated_test_source(test_src: Optional[Path]) -> bool:
    if test_src is None or not test_src.exists():
        return False
    try:
        text = test_src.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if _EMPTY_GENERATED_TEST_COMMENT not in text:
        return False
    return _EMPTY_GENERATED_TEST_METHOD_PATTERN.search(text) is not None


def test_fqcn_from_source(src: Path) -> Optional[str]:
    pkg, cls = parse_package_and_class(src)
    if not cls:
        return None
    return f"{pkg}.{cls}" if pkg else cls


def is_test_annotation_line(line: str) -> bool:
    return _TEST_ANNOTATION_NAME_RE.search(line) is not None


def _test_method_entries(text: str, *, after_adopted_marker: bool) -> List[Tuple[str, Set[str], int]]:
    lines = text.splitlines()
    masked_lines = mask_java_comments_and_strings(text).splitlines()
    brace_depths: List[int] = []
    brace_depth = 0
    for masked_line in masked_lines:
        brace_depths.append(brace_depth)
        brace_depth += masked_line.count("{") - masked_line.count("}")

    methods: List[Tuple[str, Set[str], int]] = []
    seen: Set[str] = set()
    pending_test_annotations: Set[str] = set()
    annotation_parenthesis_depth = 0
    in_annotation = False
    marker_seen = not after_adopted_marker
    for line, masked_line, method_brace_depth in zip(lines, masked_lines, brace_depths):
        stripped = line.strip()
        masked_stripped = masked_line.strip()
        if not marker_seen and _ADOPTED_TESTS_MARKER_RE.search(stripped):
            marker_seen = True
            pending_test_annotations.clear()
            continue
        if not marker_seen:
            continue
        if stripped.startswith("@"):
            annotation_match = _TEST_ANNOTATION_NAME_RE.search(stripped)
            if annotation_match:
                pending_test_annotations.add(annotation_match.group("name"))
            annotation_parenthesis_depth = masked_stripped.count("(") - masked_stripped.count(")")
            in_annotation = annotation_parenthesis_depth > 0
            continue
        if in_annotation:
            annotation_parenthesis_depth += masked_stripped.count("(") - masked_stripped.count(")")
            in_annotation = annotation_parenthesis_depth > 0
            continue

        match = _TEST_METHOD_RE.match(masked_line)
        if match:
            method = match.group("name")
            is_junit3_test = method.startswith("test") and re.search(r"\bpublic\b", line) is not None
            if (pending_test_annotations or is_junit3_test) and method not in seen:
                seen.add(method)
                methods.append((method, set(pending_test_annotations), method_brace_depth))
            pending_test_annotations.clear()
            continue

        if stripped and not stripped.startswith(("//", "*", "/*", "*/")):
            pending_test_annotations.clear()
    return methods


def test_method_names(source: Path, *, after_adopted_marker: bool = False) -> List[str]:
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return [method for method, _, _ in _test_method_entries(text, after_adopted_marker=after_adopted_marker)]


def zero_arg_void_method_names(source: Path) -> Set[str]:
    try:
        masked = mask_java_comments_and_strings(source.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return set()

    methods: Set[str] = set()
    for match in _VOID_METHOD_START_RE.finditer(masked):
        open_parenthesis = match.end() - 1
        depth = 0
        close_parenthesis = None
        for index in range(open_parenthesis, len(masked)):
            char = masked[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_parenthesis = index
                    break
        if close_parenthesis is not None and not masked[open_parenthesis + 1 : close_parenthesis].strip():
            methods.add(match.group("name"))
    return methods


def individually_runnable_test_method_names(source: Path) -> List[str]:
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    zero_arg = zero_arg_void_method_names(source)
    uses_testng_test = "org.testng.annotations.Test" in text
    return [
        method
        for method, annotations, brace_depth in _test_method_entries(text, after_adopted_marker=False)
        if (
            method in zero_arg
            and brace_depth == 1
            and not annotations.intersection(_UNSUPPORTED_INDIVIDUAL_TEST_ANNOTATIONS)
            # CoverageFilterApp discovers/runs JUnit methods only.
            and not (uses_testng_test and "Test" in annotations)
        )
    ]


def annotated_test_method_names(source: Path) -> List[str]:
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return [
        method
        for method, annotations, _ in _test_method_entries(text, after_adopted_marker=False)
        if annotations
    ]


def adopted_test_method_names(pr_source: Path, manual_source: Optional[Path]) -> List[str]:
    try:
        pr_text = pr_source.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    if _ADOPTED_TESTS_MARKER_RE.search(pr_text):
        return test_method_names(pr_source, after_adopted_marker=True)

    pr_methods = test_method_names(pr_source)
    if manual_source is None:
        return pr_methods
    manual_methods = set(test_method_names(manual_source))
    return [method for method in pr_methods if method not in manual_methods]


def find_scaffolding_source(test_src: Path, candidate_sources: Optional[List[Path]] = None) -> Optional[Path]:
    stem = test_src.stem
    base = stem.split("_Top", 1)[0]
    if not base.endswith("_ESTest"):
        return None
    want_stem = f"{base}_scaffolding"
    direct = test_src.parent / f"{want_stem}.java"
    if direct.exists():
        return direct
    if candidate_sources:
        for p in candidate_sources:
            if p.stem == want_stem:
                return p
    return None


def fix_reduced_scaffolding_import(reduced_src: Path, scaffolding_src: Optional[Path]) -> bool:
    if not scaffolding_src or not reduced_src.exists():
        return False
    try:
        reduced_text = reduced_src.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    scaf_pkg, scaf_cls = parse_package_and_class(scaffolding_src)
    if not scaf_cls:
        return False

    import_line = f"import {scaf_cls};"
    replacement = f"import {scaf_pkg}.{scaf_cls};" if scaf_pkg else ""

    lines = reduced_text.splitlines()
    new_lines: List[str] = []
    replaced = False
    for line in lines:
        if line.strip() == import_line:
            if replacement:
                new_lines.append(replacement)
            replaced = True
            continue
        new_lines.append(line)

    if not replaced:
        return False

    try:
        reduced_src.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception:
        return False
    return True


def reduced_test_path(
    reduced_root: Path,
    target_id: str,
    generated_src: Path,
    top_n: int,
    preferred_variants: Optional[Sequence[str]] = None,
    *,
    allow_any_top_n: bool = False,
) -> Optional[Path]:
    pkg, cls = parse_package_and_class(generated_src)
    if not cls:
        return None
    reduced_name = f"{cls}_Top{top_n}.java"
    bases: List[Path] = []
    seen_bases: set[Path] = set()

    for variant in preferred_variants or ():
        base = reduced_root / variant / target_id
        if base not in seen_bases:
            seen_bases.add(base)
            bases.append(base)

    for base in (
        reduced_root / "auto" / target_id,
        reduced_root / "auto-original" / target_id,
        reduced_root / target_id,
    ):
        if base in seen_bases:
            continue
        seen_bases.add(base)
        bases.append(base)

    for base in bases:
        cand = base / reduced_name
        if cand.exists():
            return cand
        matches = list(base.rglob(reduced_name)) if base.exists() else []
        if matches:
            return matches[0]

    if allow_any_top_n:
        candidates: List[Tuple[int, bool, Path]] = []
        pattern = re.compile(r"_Top(?P<count>\d+)\.java$")
        for base in bases:
            if not base.exists():
                continue
            for candidate in base.rglob("*_Top*.java"):
                match = pattern.search(candidate.name)
                if match:
                    candidates.append(
                        (
                            int(match.group("count")),
                            candidate.name.startswith(f"{cls}_Top"),
                            candidate,
                        )
                    )
        if candidates:
            return max(candidates, key=lambda item: (item[0], item[1], str(item[2])))[2]
    return None


def reduced_variant_test_path(
    reduced_root: Path,
    variant: str,
    target_id: str,
    top_n: int,
    *,
    allow_any_top_n: bool = False,
) -> Optional[Path]:
    base = reduced_root / variant / target_id
    if not base.exists():
        return None
    matches = list(base.rglob(f"*_Top{top_n}.java"))
    if matches:
        return matches[0]
    if not allow_any_top_n:
        return None
    candidates: List[Tuple[int, Path]] = []
    pattern = re.compile(r"_Top(?P<count>\d+)\.java$")
    for candidate in base.rglob("*_Top*.java"):
        match = pattern.search(candidate.name)
        if match:
            candidates.append((int(match.group("count")), candidate))
    return min(candidates, key=lambda item: (item[0], str(item[1])))[1] if candidates else None


def _llm_output_candidate_score(path: Path, *, adopted_root: Path, target_id: str, expected_fqcn: Optional[str]) -> Tuple[int, int, str]:
    score = 0
    rel = path
    try:
        rel = path.relative_to(adopted_root)
    except ValueError:
        pass

    top_dir = rel.parts[0] if rel.parts else ""
    if top_dir != target_id:
        score += 1000

    pkg, cls = parse_package_and_class(path)
    if expected_fqcn and "." in expected_fqcn:
        expected_pkg, expected_cls = expected_fqcn.rsplit(".", 1)
        if pkg == expected_pkg:
            score -= 100
        elif pkg:
            score += 25

        expected_test_prefix = f"{expected_cls}_ESTest"
        if cls.startswith(expected_test_prefix):
            score -= 50
        elif cls:
            score += 10

    return (score, len(rel.parts), str(path))


def _find_llm_output_path(
    adopted_root: Path,
    target_id: str,
    pattern: str,
    *,
    expected_fqcn: Optional[str] = None,
) -> Optional[Path]:
    base = adopted_root / target_id
    if not base.exists():
        return None
    candidates = sorted(base.rglob(pattern))
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda p: _llm_output_candidate_score(
            p,
            adopted_root=adopted_root,
            target_id=target_id,
            expected_fqcn=expected_fqcn,
        ),
    )


def adopted_test_path(adopted_root: Path, target_id: str, expected_fqcn: Optional[str] = None) -> Optional[Path]:
    # Prefer sanitized adopted outputs for downstream covfilter/reduce/run phases.
    for pattern in ("*_Sanitized_ESTest_Adopted.java", "*_ESTest_Adopted.java", "*_Adopted.java"):
        candidate = _find_llm_output_path(adopted_root, target_id, pattern, expected_fqcn=expected_fqcn)
        if candidate is not None:
            return candidate
    return None


def improved_test_path(adopted_root: Path, target_id: str, expected_fqcn: Optional[str] = None) -> Optional[Path]:
    # Prefer sanitized improved outputs for downstream agent/integration phases.
    for pattern in ("*_Sanitized_ESTest_Improved.java", "*_ESTest_Improved.java", "*_Improved.java"):
        candidate = _find_llm_output_path(adopted_root, target_id, pattern, expected_fqcn=expected_fqcn)
        if candidate is not None:
            return candidate
    return None


def agentic_test_path(adopted_root: Path, target_id: str, expected_fqcn: Optional[str] = None) -> Optional[Path]:
    # Prefer sanitized agentic outputs for downstream covfilter/reduce/run phases.
    for pattern in ("*_Sanitized_ESTest_Adopted_Agentic.java", "*_ESTest_Adopted_Agentic.java", "*_Adopted_Agentic.java"):
        candidate = _find_llm_output_path(adopted_root, target_id, pattern, expected_fqcn=expected_fqcn)
        if candidate is not None:
            return candidate
    return None


def pr_test_path(pr_tests_root: Path, target_id: str, expected_fqcn: Optional[str] = None) -> Optional[Path]:
    if not pr_tests_root.exists():
        return None

    candidates = sorted((pr_tests_root / target_id).rglob("*.java")) if (pr_tests_root / target_id).exists() else []
    audit_csv = pr_tests_root.parent / "annotation" / "target_rater_audit.csv"
    audit_is_authoritative = False
    if not candidates and expected_fqcn and audit_csv.exists():
        audit_is_authoritative = True
        try:
            with audit_csv.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    if (row.get("fqcn", "") or "").strip() != expected_fqcn:
                        continue
                    case_id = (row.get("target_id", "") or "").strip()
                    case_prefix = case_id.split("_", 1)[0]
                    candidates = sorted(pr_tests_root.glob(f"{case_prefix}-*.java"))
                    break
        except OSError:
            candidates = []
    if audit_is_authoritative and not candidates:
        return None
    if not candidates:
        candidates = sorted(pr_tests_root.glob("*.java"))
    if not candidates:
        return None

    expected_pkg = ""
    expected_cls = ""
    if expected_fqcn:
        if "." in expected_fqcn:
            expected_pkg, expected_cls = expected_fqcn.rsplit(".", 1)
        else:
            expected_cls = expected_fqcn

    def score(path: Path) -> Tuple[int, str]:
        pkg, cls = parse_package_and_class(path)
        value = 0
        if expected_cls:
            class_matches = expected_cls.lower() in path.stem.lower() or expected_cls.lower() in cls.lower()
            if not class_matches:
                value += 1000
        if expected_pkg and pkg == expected_pkg:
            value -= 25
        if target_id.lower() not in path.as_posix().lower():
            value += 5
        return value, str(path)

    best = min(candidates, key=score)
    return best if score(best)[0] < 1000 else None


def materialize_pr_test_source(pr_src: Path, stage_root: Path, target_id: str) -> Path:
    pkg, cls = parse_package_and_class(pr_src)
    if not cls:
        return pr_src
    package_dir = Path(*pkg.split(".")) if pkg else Path()
    staged = stage_root / target_id / package_dir / f"{cls}.java"
    staged.parent.mkdir(parents=True, exist_ok=True)
    if not staged.exists() or staged.read_bytes() != pr_src.read_bytes():
        shutil.copy2(pr_src, staged)
    return staged


def step_by_step_test_path(adopted_root: Path, target_id: str, expected_fqcn: Optional[str] = None) -> Optional[Path]:
    return _find_llm_output_path(adopted_root, target_id, "*_Adopted_StepByStep.java", expected_fqcn=expected_fqcn)


def adopted_variants(
    adopted_root: Path,
    target_id: str,
    expected_fqcn: Optional[str] = None,
    pr_tests_root: Optional[Path] = None,
    pr_tests_stage_root: Optional[Path] = None,
) -> List[Tuple[str, Path]]:
    variants: List[Tuple[str, Path]] = []
    adopted_src = adopted_test_path(adopted_root, target_id, expected_fqcn)
    if adopted_src and adopted_src.exists():
        variants.append(("adopted", adopted_src))
    agentic_src = agentic_test_path(adopted_root, target_id, expected_fqcn)
    if agentic_src and agentic_src.exists():
        variants.append(("agentic", agentic_src))
    if pr_tests_root is not None:
        pr_src = pr_test_path(pr_tests_root, target_id, expected_fqcn)
        if pr_src and pr_src.exists():
            if pr_tests_stage_root is not None:
                pr_src = materialize_pr_test_source(pr_src, pr_tests_stage_root, target_id)
            variants.append(("pr-tests", pr_src))
    return variants


def libs_dir_from_glob(libs_glob_cp: str) -> Path:
    """
    CoverageFilterApp wants a libs DIRECTORY arg (e.g., 'vendor/libs'), not a glob.
    We'll infer it from the left-most component if possible.
    """
    s = libs_glob_cp.strip()
    if s.endswith("/*"):
        return Path(s[:-2])
    if s.endswith("*"):
        # e.g. libs*
        return Path(s.rstrip("*").rstrip("/"))
    # if it's a direct dir or classpath string, fall back to 'libs'
    return Path("libs")


def _load_dotenv_manual(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _load_dotenv_if_present() -> None:
    roots = [Path.cwd(), Path(__file__).resolve().parents[1]]
    checked = set()
    for root in roots:
        for parent in [root, *root.parents]:
            for name in ("local.env", "config.env"):
                dotenv_path = parent / name
                if dotenv_path in checked:
                    continue
                checked.add(dotenv_path)
                if not dotenv_path.exists():
                    continue
                try:
                    from dotenv import load_dotenv
                except ImportError:
                    _load_dotenv_manual(dotenv_path)
                    return
                load_dotenv(dotenv_path)
                return
