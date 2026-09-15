from __future__ import annotations

import csv
import html
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from ..core.common import repo_to_dir
from ..pipeline.config import ADOPTED_LIKE_VARIANTS, covfilter_candidate_out_dirs
from ..pipeline.helpers import (
    first_test_source_for_fqcn,
    reduced_test_path,
    reduced_variant_test_path,
    test_fqcn_from_source,
)
from .base import Step

if TYPE_CHECKING:
    from ..pipeline.pipeline import TargetContext


_METHOD_RE = re.compile(
    r"^(?P<indent>\s*)(?:public|protected|private)?\s*(?:final\s+)?(?:static\s+)?void\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_CLASS_RE = re.compile(
    r"^(?P<indent>\s*)(?:public|protected|private)?\s*(?:final\s+)?(?:abstract\s+)?class\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)


@dataclass
class MethodDelta:
    added_lines: int
    added_methods: int
    added_branches: int
    added_instructions: int
    target_added_lines: int
    line_entries: List["LineEntry"]


@dataclass
class LineEntry:
    class_name: str
    newly_covered_lines: str


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _selector_method_name(selector: str) -> str:
    raw = (selector or "").strip()
    if "#" not in raw:
        return raw
    return raw.rsplit("#", 1)[1].strip()


def _safe_int(value: str) -> int:
    try:
        return int((value or "").strip())
    except Exception:
        return 0


def _parse_line_spec(spec: str) -> List[int]:
    out: List[int] = []
    for token in [t.strip() for t in (spec or "").split(";") if t.strip()]:
        if "-" in token:
            a, b = token.split("-", 1)
            try:
                start = int(a)
                end = int(b)
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            out.extend(range(start, end + 1))
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return sorted(set(n for n in out if n > 0))


def _parse_spans(spec: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    for token in [t.strip() for t in (spec or "").split(";") if t.strip()]:
        if "-" in token:
            a, b = token.split("-", 1)
            try:
                start = int(a)
                end = int(b)
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            if start > 0:
                spans.append((start, end))
            continue
        try:
            n = int(token)
        except ValueError:
            continue
        if n > 0:
            spans.append((n, n))

    uniq: List[Tuple[int, int]] = []
    seen = set()
    for span in spans:
        if span in seen:
            continue
        seen.add(span)
        uniq.append(span)
    return uniq


def _span_token(span: Tuple[int, int]) -> str:
    a, b = span
    return f"{a}" if a == b else f"{a}-{b}"


def _normalize_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    normalized = sorted(spans, key=lambda span: (span[0], span[1]))
    if not normalized:
        return []

    merged: List[Tuple[int, int]] = [normalized[0]]
    for start, end in normalized[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
            continue
        merged.append((start, end))
    return merged


def _line_has_code_outside_comments(line: str, *, in_block_comment: bool) -> Tuple[bool, bool]:
    has_code = False
    in_string = False
    string_delim = ""
    escaped = False
    i = 0
    n = len(line)

    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""

        if in_block_comment:
            end = line.find("*/", i)
            if end < 0:
                return has_code, True
            i = end + 2
            in_block_comment = False
            continue

        if in_string:
            has_code = True
            if escaped:
                escaped = False
                i += 1
                continue
            if ch == "\\":
                escaped = True
                i += 1
                continue
            if ch == string_delim:
                in_string = False
            i += 1
            continue

        if ch.isspace():
            i += 1
            continue

        if ch == "/" and nxt == "/":
            break
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue

        if ch == '"' or ch == "'":
            has_code = True
            in_string = True
            string_delim = ch
            i += 1
            continue

        has_code = True
        i += 1

    return has_code, in_block_comment


_CONTROL_FLOW_PREFIX_RE = re.compile(
    r"^(if|for|while|switch|try|catch|finally|do|else|return|throw|break|continue|case|default)\b"
)


def _is_soft_continuation_code_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if "{" in stripped or "}" in stripped:
        return False
    if _CONTROL_FLOW_PREFIX_RE.match(stripped):
        return False
    return True


def _line_has_continuation_hint(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.endswith(("(", ",", ".", "=", "||", "&&", "+", "-", "*", "/", "%", "?", ":")):
        return True
    if stripped.startswith(("||", "&&", ")", "]", ".", ",", "+", "-", "*", "/", "%", "?", ":")):
        return True
    return False


def _non_code_line_flags(src_lines: List[str]) -> List[bool]:
    flags: List[bool] = [False] * (len(src_lines) + 1)
    in_block_comment = False
    for idx, line in enumerate(src_lines, start=1):
        has_code, in_block_comment = _line_has_code_outside_comments(line, in_block_comment=in_block_comment)
        flags[idx] = not has_code
    return flags


def _gap_is_mergeable(src_lines: List[str], non_code_flags: List[bool], start: int, end: int) -> bool:
    if start > end:
        return True
    if start < 1 or end >= len(non_code_flags):
        return False

    all_non_code = True
    all_soft = True
    for idx in range(start, end + 1):
        if non_code_flags[idx]:
            continue
        all_non_code = False
        if not _is_soft_continuation_code_line(src_lines[idx - 1]):
            all_soft = False
            break

    if all_non_code:
        return True
    if not all_soft:
        return False

    # Merge only compact continuation-like code gaps.
    if end - start + 1 > 3:
        return False

    prev_line = src_lines[start - 2] if start - 2 >= 0 else ""
    next_line = src_lines[end] if end < len(src_lines) else ""
    if _line_has_continuation_hint(prev_line) or _line_has_continuation_hint(next_line):
        return True

    for idx in range(start, end + 1):
        if _line_has_continuation_hint(src_lines[idx - 1]):
            return True
    return False


def _merge_spans_with_non_code_gaps(spans: List[Tuple[int, int]], src_lines: List[str]) -> List[Tuple[int, int]]:
    normalized = _normalize_spans(spans)
    if not normalized:
        return []
    if not src_lines:
        return normalized

    non_code_flags = _non_code_line_flags(src_lines)
    merged: List[Tuple[int, int]] = [normalized[0]]
    for start, end in normalized[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
            continue
        if _gap_is_mergeable(src_lines, non_code_flags, prev_end + 1, start - 1):
            merged[-1] = (prev_start, max(prev_end, end))
            continue
        merged.append((start, end))
    return merged


def _pick_largest_span(spans: List[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    if not spans:
        return None
    return max(spans, key=lambda s: (s[1] - s[0] + 1, -s[0]))


def _find_existing_delta_csv(base_dir: Path, names: List[str]) -> Optional[Path]:
    for name in names:
        cand = base_dir / name
        if cand.exists():
            return cand
    return None


def _load_line_total_from_summaries(
    summary_csvs: List[Path],
    *,
    repo: str,
    fqcn: str,
    variant: str,
) -> int:
    repo = (repo or "").strip()
    fqcn = (fqcn or "").strip()
    variant = (variant or "").strip().lower()
    if not repo or not fqcn or not variant:
        return 0

    fallback_line_total = 0
    for csv_path in summary_csvs:
        if not csv_path.exists():
            continue
        for row in _read_csv_rows(csv_path):
            if (row.get("repo", "") or "").strip() != repo:
                continue
            if (row.get("fqcn", "") or "").strip() != fqcn:
                continue
            val = _safe_int(row.get("line_total", "0"))
            if val <= 0:
                continue

            row_variant = (row.get("variant", "") or "").strip().lower()
            if row_variant == variant:
                return val

            # Some variant summaries can carry line_total=0 due to upstream gaps.
            # Reuse any positive line_total for the same target class to keep
            # annotation denominators stable across variants.
            fallback_line_total = max(fallback_line_total, val)
    return fallback_line_total


def _summary_csv_candidates(summary_csvs: List[Path]) -> List[Path]:
    candidates: List[Path] = []
    seen: set[Path] = set()
    for csv_path in summary_csvs:
        for cand in (csv_path,):
            if cand not in seen:
                seen.add(cand)
                candidates.append(cand)
        name = csv_path.name
        if name.endswith(".includes.csv"):
            base = csv_path.with_name(name[: -len(".includes.csv")] + ".csv")
            if base not in seen:
                seen.add(base)
                candidates.append(base)
    return candidates


def _is_target_class(class_name: str, target_class: str) -> bool:
    cls = (class_name or "").strip()
    tgt = (target_class or "").strip()
    if not cls or not tgt:
        return False
    return cls == tgt or cls.startswith(tgt + "$")


def _load_method_deltas(
    test_deltas_csv: Path,
    line_deltas_csv: Path,
    *,
    target_class: str,
) -> Tuple[Dict[str, MethodDelta], int]:
    by_method: Dict[str, MethodDelta] = {}

    for row in _read_csv_rows(test_deltas_csv):
        method = _selector_method_name(row.get("test_selector", ""))
        if not method:
            continue
        by_method[method] = MethodDelta(
            added_lines=_safe_int(row.get("added_lines", "0")),
            added_methods=_safe_int(row.get("added_methods", "0")),
            added_branches=_safe_int(row.get("added_branches", "0")),
            added_instructions=_safe_int(row.get("added_instructions", "0")),
            target_added_lines=0,
            line_entries=[],
        )

    for row in _read_csv_rows(line_deltas_csv):
        method = _selector_method_name(row.get("test_selector", ""))
        if not method or method not in by_method:
            continue
        cls = (row.get("class_name", "") or "").strip()
        newly = (row.get("newly_covered_lines", "") or "").strip()
        if not cls or not newly:
            continue
        by_method[method].line_entries.append(LineEntry(class_name=cls, newly_covered_lines=newly))
        if _is_target_class(cls, target_class):
            by_method[method].target_added_lines += len(_parse_line_spec(newly))

    total_target_added_lines = sum(max(0, d.target_added_lines) for d in by_method.values())
    return by_method, total_target_added_lines


def _count_target_line_entries(line_deltas_csv: Path, *, target_class: str) -> int:
    count = 0
    for row in _read_csv_rows(line_deltas_csv):
        cls = (row.get("class_name", "") or "").strip()
        newly = (row.get("newly_covered_lines", "") or "").strip()
        if not cls or not newly:
            continue
        if _is_target_class(cls, target_class):
            count += 1
    return count


def _pick_best_delta_pair(
    candidate_dirs: List[Path],
    *,
    target_class: str,
    allow_all_tests: bool = False,
) -> Tuple[Optional[Path], Optional[Path]]:
    best_test: Optional[Path] = None
    best_line: Optional[Path] = None
    best_score = -1
    for cov_base in candidate_dirs:
        test_names = ["test_deltas_kept.csv", "tests_deltas_kept.csv"]
        line_names = ["line_deltas_kept.csv", "lines_deltas_kept.csv"]
        if allow_all_tests:
            test_names = ["test_deltas_selected.csv", "test_deltas_all.csv", *test_names]
            line_names = ["line_deltas_selected.csv", *line_names]
        test_deltas = _find_existing_delta_csv(cov_base, test_names)
        line_deltas = _find_existing_delta_csv(cov_base, line_names)
        if not test_deltas or not line_deltas:
            continue
        score = _count_target_line_entries(line_deltas, target_class=target_class)
        if score > best_score:
            best_score = score
            best_test = test_deltas
            best_line = line_deltas
    return best_test, best_line


def _resolve_source_for_class(
    repo_root: Path,
    class_name: str,
    cache: Dict[str, Optional[Path]],
) -> Optional[Path]:
    if class_name in cache:
        return cache[class_name]

    primary_name = class_name.split("$", 1)[0]
    rel = Path(*primary_name.split(".")).with_suffix(".java")
    direct = repo_root / rel
    if direct.exists():
        cache[class_name] = direct
        return direct

    matches = list(repo_root.rglob(rel.name))
    if matches:
        for cand in matches:
            if str(cand).endswith(str(rel)):
                cache[class_name] = cand
                return cand
        cache[class_name] = matches[0]
        return matches[0]

    cache[class_name] = None
    return None


def _git_output(repo_root: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except Exception:
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return None
    return out


def _github_ref(repo_root: Path) -> str:
    # Prefer immutable commit links so line anchors stay stable.
    head_sha = _git_output(repo_root, "rev-parse", "HEAD")
    if head_sha:
        return head_sha
    remote_head = _git_output(repo_root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if remote_head and "/" in remote_head:
        return remote_head.split("/", 1)[1]
    for cand in ("main", "master"):
        if (repo_root / ".git").exists() and _git_output(repo_root, "rev-parse", "--verify", cand):
            return cand
    return "main"


def _github_url(repo: str, repo_root: Path, src: Path, span: Tuple[int, int], ref: str) -> Optional[str]:
    try:
        rel = src.resolve().relative_to(repo_root.resolve())
    except Exception:
        return None
    a, b = span
    return f"https://github.com/{repo}/blob/{ref}/{rel.as_posix()}#L{a}-L{b}"


def _github_file_url(repo: str, repo_root: Path, src: Path, ref: str) -> Optional[str]:
    try:
        rel = src.resolve().relative_to(repo_root.resolve())
    except Exception:
        return None
    return f"https://github.com/{repo}/blob/{ref}/{rel.as_posix()}"


def _source_lines_for_ref(
    repo_root: Path,
    src: Path,
    ref: str,
    cache: Dict[Tuple[str, str], Optional[List[str]]],
) -> List[str]:
    try:
        rel = src.resolve().relative_to(repo_root.resolve())
    except Exception:
        rel = None

    if rel is not None:
        key = (ref, rel.as_posix())
        if key in cache:
            return cache[key] or []
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_root), "show", f"{ref}:{rel.as_posix()}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except Exception:
            proc = None
        if proc is not None and proc.returncode == 0:
            lines = (proc.stdout or "").splitlines()
            cache[key] = lines
            return lines
        cache[key] = None

    try:
        return src.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def _resolve_repo_source_for_fqcn(repo_root: Path, fqcn: str) -> Optional[Path]:
    fqcn = (fqcn or "").strip()
    if not fqcn:
        return None
    primary = fqcn.split("$", 1)[0]
    rel = Path(*primary.split(".")).with_suffix(".java")
    direct = repo_root / rel
    if direct.exists():
        return direct

    matches = list(repo_root.rglob(rel.name))
    if not matches:
        return None
    for cand in matches:
        if str(cand).endswith(str(rel)):
            return cand
    return matches[0]


def _render_line_block(
    indent: str,
    *,
    repo: str,
    repo_root: Path,
    repo_ref: str,
    line_entry: LineEntry,
    source_cache: Dict[str, Optional[Path]],
    source_lines_cache: Dict[Tuple[str, str], Optional[List[str]]],
) -> List[str]:
    def _format_code_line(raw: str) -> str:
        # Preserve indentation in rendered HTML blocks: expand tabs and keep spaces explicit.
        expanded = raw.expandtabs(4)
        return html.escape(expanded, quote=False)

    spans = _parse_spans(line_entry.newly_covered_lines)
    if not spans:
        return []

    src = _resolve_source_for_class(repo_root, line_entry.class_name, source_cache)
    simple_name = line_entry.class_name.split(".")[-1]
    label_name = simple_name.split("$", 1)[0] + ".java"

    block: List[str] = []
    if src and src.exists():
        src_lines = _source_lines_for_ref(repo_root, src, repo_ref, source_lines_cache)
        display_spans = _merge_spans_with_non_code_gaps(spans, src_lines) if src_lines else _normalize_spans(spans)
        span = _pick_largest_span(display_spans)
        if not span:
            return []

        url = _github_url(repo, repo_root, src, span, repo_ref)
        if url:
            block.append(
                f'{indent} * Full version of the covered block is here: '
                f'<a href="{url}">{label_name} (lines {span[0]}-{span[1]})</a>'
            )
        else:
            block.append(
                f"{indent} * Full version of the covered block is here: {line_entry.class_name} "
                f"(lines {span[0]}-{span[1]})"
            )

        if src_lines:
            start, end = span
            end = min(end, start + 24)
            block.append(f"{indent} * Covered Lines:")
            block.append(f"{indent} * <pre><code>")
            for n in range(start, min(end, len(src_lines)) + 1):
                code = _format_code_line(src_lines[n - 1])
                block.append(f"{indent} * {code}")
            block.append(f"{indent} * </code></pre>")
            other_spans = [s for s in display_spans if s != span]
            if other_spans:
                others = ";".join(_span_token(s) for s in other_spans)
                block.append(f"{indent} * Other newly covered ranges to check: {others}")
            return block

    display_spans = _normalize_spans(spans)
    span = _pick_largest_span(display_spans)
    if not span:
        return []
    block.append(
        f"{indent} * Full version of the covered block is here: {line_entry.class_name} "
        f"(lines {span[0]}-{span[1]})"
    )
    other_spans = [s for s in display_spans if s != span]
    if other_spans:
        others = ";".join(_span_token(s) for s in other_spans)
        block.append(f"{indent} * Other newly covered ranges to check: {others}")
    return block


def _build_annotation_comment(
    indent: str,
    *,
    repo: str,
    repo_root: Path,
    repo_ref: str,
    target_class: str,
    method_delta: MethodDelta,
    denominator_line_total: int,
    source_cache: Dict[str, Optional[Path]],
    source_lines_cache: Dict[Tuple[str, str], Optional[List[str]]],
) -> List[str]:
    pct = (100.0 * method_delta.target_added_lines / denominator_line_total) if denominator_line_total > 0 else 0.0
    out = [f"{indent}/**"]
    out.append(
        f"{indent} * Overall delta: +{method_delta.added_lines} lines, +{method_delta.added_methods} methods, "
        f"+{method_delta.added_branches} branches, +{method_delta.added_instructions} instructions."
    )
    out.append(
        f"{indent} * Target-class added line coverage: {pct:.2f}% for {target_class} "
        f"({method_delta.target_added_lines}/{denominator_line_total} lines)."
    )
    if method_delta.target_added_lines == 0 and any(
        (
            method_delta.added_lines,
            method_delta.added_methods,
            method_delta.added_branches,
            method_delta.added_instructions,
        )
    ):
        detail = (
            "the added line coverage is in the related classes listed below."
            if method_delta.line_entries
            else "the non-zero overall delta has no emitted per-class line range."
        )
        out.append(f"{indent} * No new target-class lines were observed; {detail}")

    ranked_entries = sorted(
        method_delta.line_entries,
        key=lambda e: (
            0 if _resolve_source_for_class(repo_root, e.class_name, source_cache) is not None else 1,
            -len(_parse_line_spec(e.newly_covered_lines)),
        ),
    )

    for line_entry in ranked_entries[:2]:
        out.extend(
            _render_line_block(
                indent,
                repo=repo,
                repo_root=repo_root,
                repo_ref=repo_ref,
                line_entry=line_entry,
                source_cache=source_cache,
                source_lines_cache=source_lines_cache,
            )
        )

    if len(method_delta.line_entries) > 2:
        out.append(f"{indent} * Additional covered classes omitted: {len(method_delta.line_entries) - 2}")

    out.append(f"{indent} */")
    return out


def _strip_existing_class_annotation_javadoc(text: str) -> str:
    return re.sub(
        r"/\*\*[\s\S]*?Corresponding manual test:[\s\S]*?\*/\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )


def _insert_class_annotation_javadoc(
    lines: List[str],
    *,
    manual_test_fqcn: str,
    manual_test_link_html: str,
) -> Tuple[List[str], bool]:
    class_idx = -1
    for idx, line in enumerate(lines):
        if _CLASS_RE.match(line):
            class_idx = idx
            break
    if class_idx < 0:
        return lines, False

    insert_at = class_idx
    while insert_at > 0 and lines[insert_at - 1].lstrip().startswith("@"):
        insert_at -= 1
    indent = _CLASS_RE.match(lines[class_idx]).group("indent") if _CLASS_RE.match(lines[class_idx]) else ""
    block = [
        f"{indent}/**",
        f"{indent} * Corresponding manual test: {{@link {manual_test_fqcn}}}.",
        f"{indent} * Manual test source on GitHub: {manual_test_link_html}.",
        f"{indent} * @see {manual_test_fqcn}",
        f"{indent} */",
    ]
    out = list(lines[:insert_at])
    if out and out[-1].strip() != "":
        out.append("")
    out.extend(block)
    out.extend(lines[insert_at:])
    return out, True


def _annotate_reduced_test(
    *,
    repo: str,
    repo_root: Path,
    repo_ref: str,
    target_class: str,
    denominator_line_total: int,
    manual_test_fqcn: str,
    manual_test_link_html: str,
    reduced_src: Path,
    annotated_out: Path,
    test_deltas_csv: Path,
    line_deltas_csv: Path,
) -> Tuple[str, str, str]:
    try:
        text = reduced_src.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ("failed", "read_reduced_source_error", "Failed to read reduced test source.")
    original_text = text
    # Rebuild annotation blocks on each run to avoid stale/incorrect links.
    text = re.sub(
        r"/\*\*[\s\S]*?This test added[\s\S]*?\*/\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = _strip_existing_class_annotation_javadoc(text)
    # Normalize legacy annotations: remove any span wrappers from prior runs.
    text = re.sub(r"</?span[^>]*>", "", text, flags=re.IGNORECASE)
    # Normalize legacy explicit-space entities from prior runs.
    text = text.replace("&#32;", " ")

    by_method, total_target_added_lines = _load_method_deltas(
        test_deltas_csv,
        line_deltas_csv,
        target_class=target_class,
    )
    if not by_method:
        try:
            annotated_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(reduced_src, annotated_out)
        except Exception:
            return ("failed", "copy_annotation_output_error", "Failed to write annotation output.")
        return ("skipped", "missing_method_deltas", "No matching method deltas found for reduced test methods.")

    lines = text.splitlines()
    if not lines:
        try:
            annotated_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(reduced_src, annotated_out)
        except Exception:
            return ("failed", "copy_annotation_output_error", "Failed to write annotation output.")
        return ("skipped", "empty_reduced_source", "Reduced test source has no lines to annotate.")

    source_cache: Dict[str, Optional[Path]] = {}
    source_lines_cache: Dict[Tuple[str, str], Optional[List[str]]] = {}
    new_lines: List[str] = []
    changed = text != original_text

    def _has_annotation_immediately_before(buf: List[str]) -> bool:
        idx = len(buf) - 1
        while idx >= 0 and not buf[idx].strip():
            idx -= 1
        if idx < 0 or buf[idx].strip() != "*/":
            return False
        start = idx
        while start >= 0 and not buf[start].lstrip().startswith("/**"):
            start -= 1
        if start < 0:
            return False
        block = "\n".join(buf[start : idx + 1]).lower()
        return "added coverage" in block

    def _relocate_existing_annotation_before_method_annotations(buf: List[str]) -> Tuple[List[str], bool]:
        idx = len(buf) - 1
        while idx >= 0 and not buf[idx].strip():
            idx -= 1
        if idx < 0 or buf[idx].strip() != "*/":
            return buf, False

        end = idx
        start = end
        while start >= 0 and not buf[start].lstrip().startswith("/**"):
            start -= 1
        if start < 0:
            return buf, False

        pre = buf[:start]
        comment_block = buf[start : end + 1]
        post = buf[end + 1 :]

        j = len(pre) - 1
        while j >= 0 and not pre[j].strip():
            j -= 1
        if j < 0 or not pre[j].lstrip().startswith("@"):
            return buf, False

        ann_end = j
        while j >= 0 and pre[j].lstrip().startswith("@"):
            j -= 1
        ann_start = j + 1

        before_ann = pre[:ann_start]
        ann_block = pre[ann_start : ann_end + 1]
        reordered = list(before_ann)
        if reordered and reordered[-1].strip() != "":
            reordered.append("")
        reordered.extend(comment_block)
        reordered.extend(ann_block)
        reordered.extend(post)
        return reordered, True

    for i, line in enumerate(lines):
        m = _METHOD_RE.match(line)
        if m:
            method_name = m.group("name")
            indent = m.group("indent")
            delta = by_method.get(method_name)
            if delta:
                relocated, moved = _relocate_existing_annotation_before_method_annotations(new_lines)
                if moved:
                    new_lines = relocated
                    changed = True

                ann_start = len(new_lines)
                while ann_start > 0 and new_lines[ann_start - 1].lstrip().startswith("@"):
                    ann_start -= 1
                prefix = new_lines[:ann_start]
                trailing_annotations = new_lines[ann_start:]

                if not _has_annotation_immediately_before(prefix):
                    comment_lines = _build_annotation_comment(
                        indent,
                        repo=repo,
                        repo_root=repo_root,
                        repo_ref=repo_ref,
                        target_class=target_class,
                        method_delta=delta,
                        denominator_line_total=(
                            denominator_line_total if denominator_line_total > 0 else total_target_added_lines
                        ),
                        source_cache=source_cache,
                        source_lines_cache=source_lines_cache,
                    )
                    if prefix and prefix[-1].strip() != "":
                        prefix.append("")
                    prefix.extend(comment_lines)
                    prefix.extend(trailing_annotations)
                    new_lines = prefix
                    changed = True
        new_lines.append(line)

    if manual_test_fqcn and manual_test_link_html:
        new_lines, added_class_doc = _insert_class_annotation_javadoc(
            new_lines,
            manual_test_fqcn=manual_test_fqcn,
            manual_test_link_html=manual_test_link_html,
        )
        if added_class_doc:
            changed = True

    annotated_out.parent.mkdir(parents=True, exist_ok=True)
    if changed:
        try:
            annotated_out.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception:
            return ("failed", "write_annotation_output_error", "Failed to write annotated test source.")
        return ("passed", "", "")

    try:
        shutil.copy2(reduced_src, annotated_out)
    except Exception:
        return ("failed", "copy_annotation_output_error", "Failed to write annotation output.")
    return ("passed", "", "")


def _find_any_reduced_file(base_dir: Path) -> Optional[Path]:
    if not base_dir.exists():
        return None
    matches = sorted(base_dir.rglob("*_Top*.java"))
    return matches[0] if matches else None


def _resolve_rel_path(src: Path, roots: List[Path]) -> Path:
    for root in roots:
        try:
            return src.resolve().relative_to(root.resolve())
        except Exception:
            continue
    return Path(src.name)


def _append_annotation_summary_row(
    *,
    summary_csv: Path,
    ctx: "TargetContext",
    variant: str,
    status: str,
    problem_category: str,
    problem_detail: str,
    input_test_source: Optional[Path],
    test_deltas_csv: Optional[Path],
    line_deltas_csv: Optional[Path],
    line_total: int,
    annotated_test_path: Optional[Path],
    out_dir: Path,
) -> None:
    with summary_csv.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                ctx.repo,
                ctx.fqcn,
                variant,
                status,
                problem_category,
                problem_detail,
                ctx.manual_test_fqcn or "",
                ctx.generated_test_fqcn or "",
                str(input_test_source) if input_test_source else "",
                str(test_deltas_csv) if test_deltas_csv else "",
                str(line_deltas_csv) if line_deltas_csv else "",
                line_total,
                str(annotated_test_path) if annotated_test_path else "",
                str(out_dir),
            ]
        )


def _enabled_annotation_variants(enabled: set[str], auto_variant: str) -> List[str]:
    variants: List[str] = []
    if "auto" in enabled or "auto-original" in enabled:
        variants.append(auto_variant)
    for variant in ADOPTED_LIKE_VARIANTS:
        if variant in enabled:
            variants.append(variant)
    return variants


class ReducedAnnotationStep(Step):
    step_names = ("annotation",)

    def run(self, ctx: "TargetContext") -> bool:
        if not self.should_run():
            return True

        enabled = {v.strip().lower() for v in self.pipeline.args.annotation_variants.split(",") if v.strip()}
        if not enabled:
            enabled = {"auto", *ADOPTED_LIKE_VARIANTS}
        auto_variant = self.pipeline.args.auto_variant
        annotation_root = Path(self.pipeline.args.annotation_out)
        summary_csv = self.pipeline.annotation_summary_csv
        if self.pipeline.covfilter_allow is not None and (ctx.repo, ctx.fqcn) not in self.pipeline.covfilter_allow:
            print(f'[agt] annotation: Skip (agt_line_covered=0): repo="{ctx.repo}" fqcn="{ctx.fqcn}"')
            for variant in _enabled_annotation_variants(enabled, auto_variant):
                _append_annotation_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category="excluded_by_agt_coverage",
                    problem_detail="Target excluded because AGT coverage summary reports zero covered lines.",
                    input_test_source=None,
                    test_deltas_csv=None,
                    line_deltas_csv=None,
                    line_total=0,
                    annotated_test_path=None,
                    out_dir=annotation_root / variant / ctx.target_id,
                )
            return True

        repo_root = self.pipeline.repos_dir / repo_to_dir(ctx.repo)
        repo_ref = _github_ref(repo_root)
        summary_csvs = _summary_csv_candidates([self.pipeline.summary_csv, self.pipeline.adopted_summary_csv])
        manual_fqcn = (ctx.manual_test_fqcn or "").strip()
        manual_src: Optional[Path] = None
        for src in ctx.manual_sources:
            fqcn = test_fqcn_from_source(src) or ""
            if fqcn and fqcn == manual_fqcn:
                manual_src = src
                break
        if not manual_fqcn and ctx.manual_sources:
            manual_src = ctx.manual_sources[0]
            manual_fqcn = test_fqcn_from_source(manual_src) or ""
        if manual_src is None and ctx.manual_sources:
            manual_src = ctx.manual_sources[0]
        manual_repo_src: Optional[Path] = None
        if manual_src is not None:
            manual_url = _github_file_url(ctx.repo, repo_root, manual_src, repo_ref)
            if manual_url:
                manual_repo_src = manual_src
            else:
                manual_repo_src = _resolve_repo_source_for_fqcn(repo_root, manual_fqcn)
        else:
            manual_repo_src = _resolve_repo_source_for_fqcn(repo_root, manual_fqcn)

        manual_url = _github_file_url(ctx.repo, repo_root, manual_repo_src, repo_ref) if manual_repo_src else None
        manual_label = (manual_fqcn.rsplit(".", 1)[-1] if manual_fqcn else "")
        if not manual_label and manual_repo_src is not None:
            manual_label = manual_repo_src.stem
        if not manual_label and manual_src is not None:
            manual_label = manual_src.stem
        manual_link_html = (
            f'<a href="{manual_url}">{manual_label}</a>'
            if manual_url and manual_label
            else (
                f"`{manual_src.resolve()}`"
                if manual_src is not None
                else (f"`{manual_repo_src.resolve()}`" if manual_repo_src is not None else f"`{manual_fqcn}`")
            )
        )

        if "auto" in enabled or "auto-original" in enabled:
            generated_test_src = first_test_source_for_fqcn(ctx.final_sources, ctx.generated_test_fqcn)
            reduced_root = Path(self.pipeline.args.reduced_out)
            top_n = max(1, min(self.pipeline.args.reduce_max_tests, 100))
            auto_out_dir = annotation_root / auto_variant / ctx.target_id
            if auto_out_dir.exists():
                shutil.rmtree(auto_out_dir, ignore_errors=True)
            reduced_src = (
                reduced_test_path(
                    reduced_root,
                    ctx.target_id,
                    generated_test_src,
                    top_n,
                    preferred_variants=[auto_variant],
                )
                if generated_test_src
                else None
            )
            if reduced_src is None:
                reduced_src = _find_any_reduced_file(reduced_root / auto_variant / ctx.target_id)
            if not reduced_src or not reduced_src.exists():
                line_total = _load_line_total_from_summaries(
                    summary_csvs,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=auto_variant,
                )
                _append_annotation_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=auto_variant,
                    status="skipped",
                    problem_category="missing_reduced_test_source",
                    problem_detail="Missing reduced test source.",
                    input_test_source=generated_test_src,
                    test_deltas_csv=None,
                    line_deltas_csv=None,
                    line_total=line_total,
                    annotated_test_path=None,
                    out_dir=auto_out_dir,
                )
            else:
                rel = _resolve_rel_path(
                    reduced_src,
                    [reduced_root / auto_variant / ctx.target_id, reduced_root / ctx.target_id],
                )
                annotated_out = auto_out_dir / rel
                test_deltas, line_deltas = _pick_best_delta_pair(
                    covfilter_candidate_out_dirs(
                        self.pipeline.covfilter_out_root,
                        self.pipeline.adopted_covfilter_out_root,
                        auto_variant,
                        ctx.target_id,
                        self.pipeline.agentic_covfilter_out_root,
                    ),
                    target_class=ctx.fqcn,
                )
                line_total = _load_line_total_from_summaries(
                    summary_csvs,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=auto_variant,
                )
                if not test_deltas or not line_deltas:
                    _append_annotation_summary_row(
                        summary_csv=summary_csv,
                        ctx=ctx,
                        variant=auto_variant,
                        status="skipped",
                        problem_category="missing_covfilter_deltas",
                        problem_detail="Missing test_deltas_kept.csv or line_deltas_kept.csv for annotation input.",
                        input_test_source=reduced_src,
                        test_deltas_csv=test_deltas,
                        line_deltas_csv=line_deltas,
                        line_total=line_total,
                        annotated_test_path=None,
                        out_dir=auto_out_dir,
                    )
                else:
                    status, category, detail = _annotate_reduced_test(
                        repo=ctx.repo,
                        repo_root=repo_root,
                        repo_ref=repo_ref,
                        target_class=ctx.fqcn,
                        denominator_line_total=line_total,
                        manual_test_fqcn=manual_fqcn,
                        manual_test_link_html=manual_link_html,
                        reduced_src=reduced_src,
                        annotated_out=annotated_out,
                        test_deltas_csv=test_deltas,
                        line_deltas_csv=line_deltas,
                    )
                    _append_annotation_summary_row(
                        summary_csv=summary_csv,
                        ctx=ctx,
                        variant=auto_variant,
                        status=status,
                        problem_category=category,
                        problem_detail=detail,
                        input_test_source=reduced_src,
                        test_deltas_csv=test_deltas,
                        line_deltas_csv=line_deltas,
                        line_total=line_total,
                        annotated_test_path=annotated_out if status == "passed" else None,
                        out_dir=auto_out_dir,
                    )
                    if status == "passed":
                        print(
                            f'[agt] annotation: wrote {auto_variant} annotated test for repo="{ctx.repo}" '
                            f'fqcn="{ctx.fqcn}" -> {annotated_out}'
                        )
                    else:
                        print(
                            f'[agt] annotation: {status} ({category}): repo="{ctx.repo}" '
                            f'fqcn="{ctx.fqcn}" variant="{auto_variant}"'
                        )

        for variant in ADOPTED_LIKE_VARIANTS:
            if variant not in enabled:
                continue
            top_n = max(1, min(self.pipeline.args.adopted_reduce_max_tests, 100))
            variant_out_dir = annotation_root / variant / ctx.target_id
            if variant_out_dir.exists():
                shutil.rmtree(variant_out_dir, ignore_errors=True)
            reduced_src = reduced_variant_test_path(self.pipeline.adopted_reduced_out_root, variant, ctx.target_id, top_n)
            if reduced_src is None:
                reduced_src = _find_any_reduced_file(self.pipeline.adopted_reduced_out_root / variant / ctx.target_id)
            if not reduced_src or not reduced_src.exists():
                line_total = _load_line_total_from_summaries(
                    summary_csvs,
                    repo=ctx.repo,
                    fqcn=ctx.fqcn,
                    variant=variant,
                )
                _append_annotation_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category="missing_reduced_test_source",
                    problem_detail="Missing reduced test source.",
                    input_test_source=None,
                    test_deltas_csv=None,
                    line_deltas_csv=None,
                    line_total=line_total,
                    annotated_test_path=None,
                    out_dir=variant_out_dir,
                )
                continue
            rel = _resolve_rel_path(
                reduced_src,
                [
                    self.pipeline.adopted_reduced_out_root / variant / ctx.target_id,
                    self.pipeline.adopted_reduced_out_root / ctx.target_id,
                ],
            )
            annotated_out = variant_out_dir / rel

            test_deltas, line_deltas = _pick_best_delta_pair(
                covfilter_candidate_out_dirs(
                    self.pipeline.covfilter_out_root,
                    self.pipeline.adopted_covfilter_out_root,
                    variant,
                    ctx.target_id,
                    self.pipeline.agentic_covfilter_out_root,
                ),
                target_class=ctx.fqcn,
                allow_all_tests=variant == "pr-tests",
            )
            line_total = _load_line_total_from_summaries(
                summary_csvs,
                repo=ctx.repo,
                fqcn=ctx.fqcn,
                variant=variant,
            )
            if not test_deltas or not line_deltas:
                _append_annotation_summary_row(
                    summary_csv=summary_csv,
                    ctx=ctx,
                    variant=variant,
                    status="skipped",
                    problem_category="missing_covfilter_deltas",
                    problem_detail="Missing test_deltas_kept.csv or line_deltas_kept.csv for annotation input.",
                    input_test_source=reduced_src,
                    test_deltas_csv=test_deltas,
                    line_deltas_csv=line_deltas,
                    line_total=line_total,
                    annotated_test_path=None,
                    out_dir=variant_out_dir,
                )
                continue

            status, category, detail = _annotate_reduced_test(
                repo=ctx.repo,
                repo_root=repo_root,
                repo_ref=repo_ref,
                target_class=ctx.fqcn,
                denominator_line_total=line_total,
                manual_test_fqcn=manual_fqcn,
                manual_test_link_html=manual_link_html,
                reduced_src=reduced_src,
                annotated_out=annotated_out,
                test_deltas_csv=test_deltas,
                line_deltas_csv=line_deltas,
            )
            _append_annotation_summary_row(
                summary_csv=summary_csv,
                ctx=ctx,
                variant=variant,
                status=status,
                problem_category=category,
                problem_detail=detail,
                input_test_source=reduced_src,
                test_deltas_csv=test_deltas,
                line_deltas_csv=line_deltas,
                line_total=line_total,
                annotated_test_path=annotated_out if status == "passed" else None,
                out_dir=variant_out_dir,
            )
            if status == "passed":
                print(
                    f'[agt] annotation: wrote {variant} annotated test for repo="{ctx.repo}" '
                    f'fqcn="{ctx.fqcn}" -> {annotated_out}'
                )
            else:
                print(
                    f'[agt] annotation: {status} ({category}): repo="{ctx.repo}" '
                    f'fqcn="{ctx.fqcn}" variant="{variant}"'
                )

        return True
