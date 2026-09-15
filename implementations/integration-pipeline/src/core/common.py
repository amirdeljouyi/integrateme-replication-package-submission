from __future__ import annotations

import csv
import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


def write_text(p: Path, s: str) -> None:
    ensure_dir(p.parent)
    p.write_text(s, encoding="utf-8", errors="replace")

def shlex_join(args: Sequence[str]) -> str:
    """
    Python 3.8-compatible shlex.join().
    """
    return " ".join(shlex.quote(a) for a in args)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def repo_to_dir(repo: str) -> str:
    return repo.replace("/", "_").strip()


def split_list_field(s: str) -> List[str]:
    """
    Parse list-like fields that may be:
      - "A.java;B.java"
      - "A.java|B.java"
      - CSV-escaped with quotes
    """
    if s is None:
        return []
    s = str(s).strip().strip('"').strip()
    if not s or s.lower() == "null":
        return []
    parts = re.split(r"[;|]", s)
    return [p.strip() for p in parts if p and p.strip()]


def looks_like_scaffolding(name: str) -> bool:
    n = name.lower()
    return "scaffolding" in n


def is_probably_test_filename(name: str) -> bool:
    """
    Conservative filter: treat these as tests
    """
    base = name.lower()
    if base.endswith("test.java") or base.endswith("tests.java"):
        return True
    if base.endswith("it.java") or base.endswith("its.java"):
        return True
    if base.endswith("testcase.java") or base.endswith("testcases.java"):
        return True
    if base.endswith("_test.java") or base.endswith("_tests.java"):
        return True
    if base.endswith("_it.java") or base.endswith("_its.java"):
        return True
    return False


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def parse_package_and_class(java_file: Path) -> Tuple[str, str]:
    """
    Very small parser: reads "package x;" and "public class Y" etc.
    Works for typical JUnit tests.
    """
    pkg = ""
    cls = ""
    try:
        text = java_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "", ""

    m = re.search(r"^\s*package\s+([a-zA-Z0-9_.]+)\s*;", text, flags=re.MULTILINE)
    if m:
        pkg = m.group(1).strip()

    # class name = file basename minus .java is often fine,
    # but prefer an actual class declaration if possible.
    declarations = re.findall(
        r"^\s*(?:public\s+)?(?:final\s+)?(?:abstract\s+)?(?:static\s+)?(?:sealed\s+)?(?:non-sealed\s+)?"
        r"(class|interface|enum|record|@interface)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
        text,
        flags=re.MULTILINE,
    )
    stem = java_file.stem
    for _kind, declared_name in declarations:
        if declared_name == stem:
            cls = declared_name
            break
    if not cls and declarations:
        cls = declarations[0][1].strip()
    if not cls:
        cls = stem

    return pkg, cls


def detect_junit_version(java_file: Path) -> int:
    """
    Return 5 if JUnit5, else 4 (default).
    """
    try:
        text = java_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 4

    if "org.junit.jupiter.api" in text or "org.junit.jupiter" in text:
        return 5
    if "org.junit.Test" in text or "org.junit.Assert" in text:
        return 4
    # fallback: JUnit4
    return 4


@dataclass(frozen=True)
class CutToFatjarRow:
    repo: str
    fqcn: str
    fatjar_path: str
    module_rel: str = ""
    build_tool: str = ""
    class_path: str = ""


def load_cut_to_fatjar_map(csv_path: Path) -> Dict[Tuple[str, str], CutToFatjarRow]:
    rows = read_csv_rows(csv_path)
    out: Dict[Tuple[str, str], CutToFatjarRow] = {}

    for r in rows:
        repo = (r.get("repo", "") or "").strip().strip('"')
        fqcn = (r.get("fqcn", "") or "").strip().strip('"')
        fatjar = (r.get("fatjar", "") or r.get("fatjar_path", "") or "").strip().strip('"')
        module_rel = (r.get("module_rel", "") or "").strip().strip('"')
        build_tool = (r.get("build_tool", "") or "").strip().strip('"')
        class_path = (r.get("class_path", "") or "").strip().strip('"')
        if not repo or not fqcn:
            continue
        out[(repo, fqcn)] = CutToFatjarRow(
            repo=repo,
            fqcn=fqcn,
            fatjar_path=fatjar,
            module_rel=module_rel,
            build_tool=build_tool,
            class_path=class_path,
        )

    return out


def find_test_source_root(repo_root: Path) -> Optional[Path]:
    """
    Best-effort: locate likely test root in a repo module.
    We do NOT use this to compile everything. Only for targeted dependency search.
    """
    # Common layouts:
    # - src/test/java
    # - <module>/src/test/java
    candidates = list(repo_root.rglob("src/test/java"))
    if not candidates:
        return None
    # choose shortest path deterministically
    candidates = sorted(candidates, key=lambda p: (len(str(p)), str(p)))
    return candidates[0]


def _looks_like_repo_class_dir(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    name = parts[-1] if parts else ""
    if "target" in parts and name.endswith("classes"):
        return True
    if len(parts) >= 4 and parts[-4:] in (
        ["build", "classes", "java", "test"],
        ["build", "classes", "java", "main"],
        ["build", "classes", "kotlin", "test"],
        ["build", "classes", "kotlin", "main"],
    ):
        return True
    if len(parts) >= 3 and parts[-3:] in (
        ["build", "resources", "test"],
        ["build", "resources", "main"],
    ):
        return True
    return False


def candidate_repo_class_dirs(repo_root_for_deps: Optional[Path], module_rel: str) -> List[Path]:
    if not repo_root_for_deps or not repo_root_for_deps.exists():
        return []

    rel = (module_rel or "").strip()
    module_dir = repo_root_for_deps if not rel or rel in {".", "root"} else (repo_root_for_deps / rel)
    roots: List[Path] = []
    if module_dir.exists():
        roots.append(module_dir)
    if repo_root_for_deps not in roots:
        roots.append(repo_root_for_deps)

    candidates: List[Path] = []
    rel_dirs = [
        "target/test-classes",
        "build/classes/java/test",
        "build/classes/kotlin/test",
        "build/resources/test",
        "target/classes",
        "build/classes/java/main",
        "build/classes/kotlin/main",
        "build/resources/main",
    ]
    seen: Set[Path] = set()
    for root in roots:
        for rel_dir in rel_dirs:
            path = root / rel_dir
            if not path.exists() or path in seen:
                continue
            seen.add(path)
            candidates.append(path)

    recursive_patterns = [
        "**/target/*classes",
        "**/build/classes/java/*",
        "**/build/classes/kotlin/*",
        "**/build/resources/*",
    ]
    # Recursive fallback discovery is only needed inside the target module.
    # Scanning every module in large repositories on every test run can take
    # minutes, while cross-module dependencies are supplied by the resolved
    # runtime classpath.
    recursive_roots = [module_dir] if module_dir.exists() else [repo_root_for_deps]
    for root in recursive_roots:
        for pattern in recursive_patterns:
            for path in sorted(root.glob(pattern)):
                if not path.is_dir() or path in seen or not _looks_like_repo_class_dir(path):
                    continue
                seen.add(path)
                candidates.append(path)
    return candidates


def extract_missing_symbols_from_javac_log(log_text: str) -> Set[str]:
    """
    Extract class names from common javac errors like:
      cannot find symbol
        symbol:   class Foo
    """
    missing: Set[str] = set()
    # class symbols
    for m in re.finditer(r"symbol:\s+class\s+([A-Za-z_][A-Za-z0-9_]*)", log_text):
        missing.add(m.group(1))
    # sometimes: symbol:   variable Foo   (could be a class used as static holder)
    for m in re.finditer(r"symbol:\s+variable\s+([A-Za-z_][A-Za-z0-9_]*)", log_text):
        missing.add(m.group(1))
    return missing


def file_declares_type(java_path: Path, type_name: str) -> bool:
    """
    Check if a java file declares the requested top-level type.
    Lightweight text scan.
    """
    try:
        txt = java_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    pat = (
        rf"^\s*"
        rf"(?:(?:public|protected|private|abstract|static|final|sealed|non-sealed)\s+)*"
        rf"(?:class|interface|enum|record|@interface)\s+{re.escape(type_name)}\b"
    )
    return re.search(pat, txt, flags=re.MULTILINE) is not None
