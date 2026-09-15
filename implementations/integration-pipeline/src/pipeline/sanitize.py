from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

from ..core.common import ensure_dir, parse_package_and_class, repo_to_dir, write_text

AGT_REMEDIATION_IGNORE = "AGT remediation: failing generated assertion"


def _includes_filter_active(includes: str) -> bool:
    normalized = (includes or "").strip()
    return bool(normalized) and normalized != "*"


def _includes_csv_path(path: Path, includes: str) -> Path:
    if not _includes_filter_active(includes) or path.suffix.lower() != ".csv":
        return path
    return path.with_name(f"{path.stem}.includes{path.suffix}")


@dataclass(frozen=True)
class EvoSuitePair:
    test_src: Path
    scaffolding_src: Optional[Path]
    test_fqcn: str


def sanitized_es_bucket(root: Path, repo: str, fqcn: str) -> Path:
    return root / repo_to_dir(repo) / fqcn


def sanitize_summary_csv(root: Path, includes: str) -> Path:
    return _includes_csv_path(root / "sanitize_summary.csv", includes)


def sanitize_compare_summary_csv(covfilter_out_root: Path, includes: str) -> Path:
    return _includes_csv_path(covfilter_out_root / "auto" / "sanitize_compare.csv", includes)


def sanitize_compare_target_dir(root: Path, target_id: str, label: str) -> Path:
    return root / "auto" / target_id / label


def estest_base_name(class_name: str) -> str:
    for suffix in ("_Sanitized_ESTest", "_ESTest"):
        if class_name.endswith(suffix):
            return class_name[: -len(suffix)]
    return class_name


def variant_test_class_name(class_name: str, variant: str) -> str:
    base = estest_base_name(class_name)
    if variant == "baseline":
        return f"{base}_ESTest"
    if variant == "sanitized":
        return f"{base}_Sanitized_ESTest"
    raise ValueError(f"Unsupported EvoSuite variant: {variant}")


def variant_scaffolding_class_name(class_name: str, variant: str) -> str:
    return f"{variant_test_class_name(class_name, variant)}_scaffolding"


def variant_test_fqcn(test_fqcn: str, variant: str) -> str:
    pkg, cls = _split_fqcn(test_fqcn)
    updated_cls = variant_test_class_name(cls, variant)
    return f"{pkg}.{updated_cls}" if pkg else updated_cls


def baseline_pair(root: Path, repo: str, fqcn: str, test_fqcn: str) -> Optional[EvoSuitePair]:
    pkg, cls = _split_fqcn(test_fqcn)
    bucket = sanitized_es_bucket(root, repo, fqcn)
    test_src = bucket / f"{cls}.java"
    scaffolding_src = bucket / f"{cls}_scaffolding.java"
    if not test_src.exists():
        return None
    return EvoSuitePair(
        test_src=test_src,
        scaffolding_src=scaffolding_src if scaffolding_src.exists() else None,
        test_fqcn=f"{pkg}.{cls}" if pkg else cls,
    )


def variant_pair(root: Path, repo: str, fqcn: str, test_fqcn: str, variant: str) -> Optional[EvoSuitePair]:
    pkg, cls = _split_fqcn(test_fqcn)
    target_cls = variant_test_class_name(cls, variant)
    bucket = sanitized_es_bucket(root, repo, fqcn)
    test_src = bucket / f"{target_cls}.java"
    scaffolding_src = bucket / f"{target_cls}_scaffolding.java"
    if not test_src.exists():
        return None
    return EvoSuitePair(
        test_src=test_src,
        scaffolding_src=scaffolding_src if scaffolding_src.exists() else None,
        test_fqcn=f"{pkg}.{target_cls}" if pkg else target_cls,
    )


def materialize_sanitized_pair(
    *,
    source_root: Path,
    sanitized_root: Path,
    repo: str,
    fqcn: str,
    test_fqcn: str,
) -> Optional[EvoSuitePair]:
    source_pair = baseline_pair(source_root, repo, fqcn, test_fqcn)
    if source_pair is None:
        return None
    return _materialize_variant_pair(source_pair, sanitized_root, repo, fqcn, "sanitized", sanitize=True)


def clear_pair_root(root: Path, repo: str, fqcn: str) -> None:
    bucket = sanitized_es_bucket(root, repo, fqcn)
    if bucket.exists():
        shutil.rmtree(bucket, ignore_errors=True)


def ensure_pair_root(root: Path, repo: str, fqcn: str) -> Path:
    bucket = sanitized_es_bucket(root, repo, fqcn)
    ensure_dir(bucket)
    return bucket


def sanitize_evosuite_text(text: str) -> str:
    updated = text
    updated = re.sub(
        rf'^\s*@(?:org\.junit\.)?Ignore\s*\(\s*"{re.escape(AGT_REMEDIATION_IGNORE)}"\s*\)\s*$',
        "",
        updated,
        flags=re.MULTILINE,
    )
    updated = re.sub(r"^\s*import\s+org\.evosuite\.runtime\.EvoRunner\s*;\s*$", "", updated, flags=re.MULTILINE)
    updated = re.sub(
        r"^\s*import\s+org\.evosuite\.runtime\.EvoRunnerParameters\s*;\s*$",
        "",
        updated,
        flags=re.MULTILINE,
    )
    updated = re.sub(r"^\s*import\s+org\.junit\.runner\.RunWith\s*;\s*$", "", updated, flags=re.MULTILINE)
    updated = re.sub(
        r"@RunWith\s*\(\s*EvoRunner\.class\s*\)\s*",
        "",
        updated,
        flags=re.MULTILINE,
    )
    updated = re.sub(
        r"@EvoRunnerParameters\s*\((?:[^)(]+|\([^)(]*\))*\)\s*",
        "",
        updated,
        flags=re.MULTILINE,
    )
    updated = re.sub(
        r"^\s*import\s+static\s+org\.evosuite\.shaded\.org\.mockito\.Mockito\.\*\s*;\s*$",
        "import static org.mockito.Mockito.*;",
        updated,
        flags=re.MULTILINE,
    )
    updated = re.sub(
        r"\(org\.evosuite\.shaded\.org\.mockito\.stubbing\.Answer\)\s*new\s+ViolatedAssumptionAnswer\(\)",
        "new ViolatedAssumptionAnswer()",
        updated,
    )
    updated = re.sub(
        r"try\s*\{\s*initMocksToAvoidTimeoutsInTheTests\(\);\s*\}\s*catch\s*\(\s*ClassNotFoundException\s+\w+\s*\)\s*\{\s*\}",
        "try { initMocksToAvoidTimeoutsInTheTests(); } catch(Throwable t) {}",
        updated,
        flags=re.MULTILINE,
    )
    updated = _dedupe_imports(updated)
    updated = re.sub(r"\n{3,}", "\n\n", updated)
    return updated.strip() + "\n"


def _materialize_variant_pair(
    source_pair: EvoSuitePair,
    root: Path,
    repo: str,
    fqcn: str,
    variant: str,
    *,
    sanitize: bool,
) -> EvoSuitePair:
    bucket = ensure_pair_root(root, repo, fqcn)
    source_pkg, source_cls = _split_fqcn(source_pair.test_fqcn)
    source_scaf_cls = f"{source_cls}_scaffolding"
    target_cls = variant_test_class_name(source_cls, variant)
    target_scaf_cls = f"{target_cls}_scaffolding"
    replacements: Dict[str, str] = {
        source_cls: target_cls,
        source_scaf_cls: target_scaf_cls,
    }
    materialized_scaf: Optional[Path] = None
    for source_path in _iter_pair_files(source_pair):
        text = source_path.read_text(encoding="utf-8", errors="ignore")
        if sanitize:
            text = sanitize_evosuite_text(text)
        for old_name, new_name in replacements.items():
            text = re.sub(rf"\b{re.escape(old_name)}\b", new_name, text)
        text = _dedupe_imports(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
        source_name = source_path.stem
        target_name = replacements.get(source_name, source_name)
        dest_path = bucket / f"{target_name}.java"
        write_text(dest_path, text)
        if target_name == target_scaf_cls:
            materialized_scaf = dest_path
    target_fqcn = f"{source_pkg}.{target_cls}" if source_pkg else target_cls
    return EvoSuitePair(
        test_src=bucket / f"{target_cls}.java",
        scaffolding_src=materialized_scaf,
        test_fqcn=target_fqcn,
    )


def _iter_pair_files(pair: EvoSuitePair) -> Iterable[Path]:
    yield pair.test_src
    if pair.scaffolding_src is not None and pair.scaffolding_src.exists():
        yield pair.scaffolding_src


def _split_fqcn(fqcn: str) -> tuple[str, str]:
    normalized = (fqcn or "").strip()
    if "." not in normalized:
        return "", normalized
    return normalized.rsplit(".", 1)


def _insert_import_after_package(text: str, import_line: str) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("package "):
            lines.insert(idx + 1, "")
            lines.insert(idx + 2, import_line)
            return "\n".join(lines) + "\n"
    return import_line + "\n" + text


def _dedupe_imports(text: str) -> str:
    seen = set()
    new_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            if stripped in seen:
                continue
            seen.add(stripped)
        new_lines.append(line)
    return "\n".join(new_lines)
