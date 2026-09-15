#!/usr/bin/env python3
"""Merge reduced PR tests into their corresponding agentic LLM outputs.

One-off experiment remediation, not a pipeline step. Two reasons it is not
promoted into src/steps/:

* It carries per-target source patches keyed on evaluation-target IDs. T09 gets
  an AssertJ import rewrite, T30 a ConfigurationFactory replacement, and T19,
  T30 and T32 are allowed to have their package rewritten. Those are repairs to
  four specific targets, not general behaviour.
* It rewrites files under pipeline-output/llm-out/ in place rather than producing new
  output. It is idempotent - the "// Added PR tests" marker guards against
  merging twice - but it still mutates recorded experiment results.

Run it with --check first; that reports the mappings without touching anything.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.core.layout import PIPELINE_OUTPUT_ROOT
from src.core.common import parse_package_and_class, repo_to_dir
from src.pipeline.helpers import agentic_test_path, mask_java_comments_and_strings, test_method_names


SUMMARY = ROOT / PIPELINE_OUTPUT_ROOT / "aggregate" / "reduce" / "baseline" / "pr-tests" / "reduce_summary.csv"
RAW_PR_TESTS = ROOT / PIPELINE_OUTPUT_ROOT / "pull-requests" / "tests"
TARGET_AUDIT = ROOT / PIPELINE_OUTPUT_ROOT / "aggregate" / "annotation" / "baseline" / "target_rater_audit.csv"
TARGETS_CSV = ROOT.parent.parent / "experiments" / "targets.csv"
LLM_OUT = ROOT / PIPELINE_OUTPUT_ROOT / "llm-out"
MARKER = "// Added PR tests"
_SIMILARITY_THRESHOLD = 0.40
_HIGH_CONFIDENCE_THRESHOLD = 0.50
_MIN_BEST_MATCH_MARGIN = 0.05
_TOKEN_STOPWORDS = {
    "test", "tests", "should", "when", "then", "given", "returns", "return",
    "with", "without", "default", "expected", "assert", "asserts", "actual",
    "true", "false", "null", "new", "void", "public", "private", "protected",
    "static", "throws", "exception", "no", "and", "or", "the", "a", "an",
}
_CALL_STOPWORDS = {
    "assertEquals", "assertNotEquals", "assertSame", "assertNotSame", "assertTrue",
    "assertFalse", "assertNull", "assertNotNull", "assertThrows", "assertThat",
    "fail", "verifyException", "when", "thenReturn", "thenThrow", "get", "set",
    "add", "remove", "create", "build", "start", "stop", "close", "run",
    "toString", "hashCode", "equals", "getMessage", "of",
}


@dataclass(frozen=True)
class Member:
    source: str
    key: str
    start: int
    end: int


def _matching_brace(masked: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("unbalanced Java class body")


def _class_body(text: str) -> tuple[int, int]:
    masked = mask_java_comments_and_strings(text)
    declaration = re.search(r"\b(?:class|interface|record|enum)\s+[A-Za-z_$][\w$]*[^;{]*\{", masked)
    if not declaration:
        raise ValueError("Java class declaration not found")
    opening = masked.find("{", declaration.start())
    return opening, _matching_brace(masked, opening)


def _parameter_arity(header: str) -> int:
    opening = header.rfind("(")
    if opening < 0:
        return 0
    depth = 0
    commas = 0
    has_content = False
    for char in header[opening + 1 :]:
        if char in "(<[{":
            depth += 1
        elif char in ")>]}":
            if char == ")" and depth == 0:
                break
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            commas += 1
        elif not char.isspace() and depth == 0:
            has_content = True
    return commas + 1 if has_content else 0


def _parameter_signature(header: str) -> str:
    opening = header.rfind("(")
    if opening < 0:
        return ""
    depth = 0
    closing = len(header)
    for index in range(opening + 1, len(header)):
        char = header[index]
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                closing = index
                break
            depth -= 1
    return re.sub(r"\s+", " ", header[opening + 1 : closing]).strip()


def _member_key(source: str) -> str:
    masked = mask_java_comments_and_strings(source)
    paren = bracket = 0
    header_end = len(masked)
    for index, char in enumerate(masked):
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char in "{;" and paren == 0 and bracket == 0:
            header_end = index
            break
    header = masked[:header_end]
    type_match = re.search(r"\b(?:class|interface|record|enum)\s+([A-Za-z_$][\w$]*)", header)
    if type_match:
        return f"type:{type_match.group(1)}"
    paren = bracket = 0
    assignment = -1
    for index, char in enumerate(header):
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "=" and paren == 0 and bracket == 0:
            assignment = index
            break
    if assignment >= 0:
        declaration = re.sub(r"@[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\s*\([^)]*\))?", " ", header[:assignment])
        identifiers = re.findall(r"[A-Za-z_$][\w$]*", declaration)
        if identifiers:
            return f"field:{identifiers[-1]}"
    method_matches = list(re.finditer(r"([A-Za-z_$][\w$]*)\s*\(", header))
    if method_matches:
        name = method_matches[-1].group(1)
        return f"method:{name}({_parameter_signature(header)})"
    declaration = re.sub(r"@[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:\s*\([^)]*\))?", " ", header)
    declaration = declaration.split("=", 1)[0]
    identifiers = re.findall(r"[A-Za-z_$][\w$]*", declaration)
    if identifiers:
        return f"field:{identifiers[-1]}"
    return "block:" + re.sub(r"\s+", "", masked)[:120]


def _members(text: str) -> list[Member]:
    masked = mask_java_comments_and_strings(text)
    opening, closing = _class_body(text)
    members: list[Member] = []
    cursor = opening + 1
    while cursor < closing:
        while cursor < closing and masked[cursor].isspace():
            cursor += 1
        if cursor >= closing:
            break
        start = cursor
        paren = bracket = 0
        index = cursor
        while index < closing:
            char = masked[index]
            if char == "(":
                paren += 1
            elif char == ")":
                paren = max(0, paren - 1)
            elif char == "[":
                bracket += 1
            elif char == "]":
                bracket = max(0, bracket - 1)
            elif char == "{" and paren == 0 and bracket == 0:
                end = _matching_brace(masked, index) + 1
                while end < closing and masked[end].isspace() and masked[end] != "\n":
                    end += 1
                if end < closing and masked[end] == ";":
                    end += 1
                break
            elif char == ";" and paren == 0 and bracket == 0:
                end = index + 1
                break
            index += 1
        else:
            break
        source = text[start:end].rstrip()
        if source:
            members.append(Member(source=source, key=_member_key(source), start=start, end=end))
        cursor = end
    return members


def _all_members(text: str) -> list[Member]:
    """Return members declared directly in a class or in named nested types."""
    members = _members(text)
    descendants: list[Member] = []
    for member in members:
        if not member.key.startswith("type:"):
            continue
        for child in _all_members(member.source):
            descendants.append(
                Member(
                    source=child.source,
                    key=child.key,
                    start=member.start + child.start,
                    end=member.start + child.end,
                )
            )
    return members + descendants


def _method_name(member: Member) -> str:
    if not member.key.startswith("method:"):
        return ""
    return member.key.removeprefix("method:").split("(", 1)[0]


def _declared_name(member: Member) -> str:
    kind, _, value = member.key.partition(":")
    if not value:
        return ""
    return value.split("(", 1)[0] if kind == "method" else value


def _is_test_member(member: Member) -> bool:
    if re.search(r"@(?:[A-Za-z_$][\w$]*\.)*(?:Test|ParameterizedTest)\b", member.source):
        return True
    name = _method_name(member)
    return name.startswith("test") and bool(re.search(r"\bpublic\b", member.source))


def _word_tokens(value: str) -> set[str]:
    words = re.findall(
        r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+",
        value.replace("_", " "),
    )
    return {word.lower() for word in words if word.lower() not in _TOKEN_STOPWORDS and len(word) > 1}


def _method_calls(member: Member) -> set[str]:
    masked = mask_java_comments_and_strings(member.source)
    calls = {
        match.group(1)
        for match in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\(", masked)
        if match.group(1) not in _CALL_STOPWORDS and match.group(1)[:1].islower()
    }
    calls.discard(_method_name(member))
    return calls


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _semantic_similarity(left: Member, right: Member) -> float:
    left_name = _word_tokens(_method_name(left))
    right_name = _word_tokens(_method_name(right))
    left_calls = _method_calls(left)
    right_calls = _method_calls(right)
    left_call_tokens = set().union(*(_word_tokens(call) for call in left_calls)) if left_calls else set()
    right_call_tokens = set().union(*(_word_tokens(call) for call in right_calls)) if right_calls else set()
    shared_anchor = (left_name & right_name) | (left_calls & right_calls) | (left_call_tokens & right_call_tokens)
    if not shared_anchor:
        return 0.0
    left_body = _word_tokens(mask_java_comments_and_strings(left.source))
    right_body = _word_tokens(mask_java_comments_and_strings(right.source))
    score = (
        0.40 * _jaccard(left_name, right_name)
        + 0.35 * max(_jaccard(left_calls, right_calls), _jaccard(left_call_tokens, right_call_tokens))
        + 0.25 * _jaccard(left_body, right_body)
    )
    # Require name-level agreement before rewarding a shared production call.
    # This recognizes differently named tests of the same behavior without
    # matching unrelated tests that merely use the same fixture utilities.
    if left_name & right_name and left_calls & right_calls:
        score += 0.27
    return min(score, 1.0)


def _semantic_replacement_matches(
    selected_members: dict[str, Member],
    agentic_text: str,
) -> dict[str, tuple[str, float]]:
    """Return a conservative one-to-one PR-name -> existing-name replacement plan."""
    existing = [member for member in _all_members(agentic_text) if _is_test_member(member)]
    existing_names = {_method_name(member) for member in existing}
    exact = set(selected_members) & existing_names
    candidates: list[tuple[float, str, str]] = []
    for selected_name, selected in selected_members.items():
        if selected_name in exact:
            continue
        ranked: list[tuple[float, str]] = []
        for current in existing:
            current_name = _method_name(current)
            if not current_name or current_name in exact:
                continue
            score = _semantic_similarity(selected, current)
            ranked.append((score, current_name))
        ranked.sort(reverse=True)
        if not ranked:
            continue
        best_score, best_name = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        best_member = next(member for member in existing if _method_name(member) == best_name)
        shared_name_tokens = _word_tokens(selected_name) & _word_tokens(best_name)
        shared_call_tokens = set().union(
            *(_word_tokens(call) for call in (_method_calls(selected) & _method_calls(best_member)))
        ) if (_method_calls(selected) & _method_calls(best_member)) else set()
        has_specific_anchor = (
            _jaccard(_word_tokens(selected_name), _word_tokens(best_name)) >= 0.50
            or bool(shared_name_tokens & shared_call_tokens)
        )
        if best_score >= _HIGH_CONFIDENCE_THRESHOLD or (
            best_score >= _SIMILARITY_THRESHOLD
            and best_score - runner_up >= _MIN_BEST_MATCH_MARGIN
            and has_specific_anchor
        ):
            # Only the closest candidate may replace. If another PR test wins
            # that same Agentic method, append this test rather than falling
            # through to a weaker second-best match.
            candidates.append((best_score, selected_name, best_name))
    matched_selected: set[str] = set()
    matched_existing: set[str] = set()
    matches: dict[str, tuple[str, float]] = {
        name: (name, 1.0) for name in sorted(exact)
    }
    for score, selected_name, current_name in sorted(candidates, reverse=True):
        if selected_name in matched_selected or current_name in matched_existing:
            continue
        matches[selected_name] = (current_name, score)
        matched_selected.add(selected_name)
        matched_existing.add(current_name)
    return matches


def _referenced_support_members(
    candidates: list[Member],
    roots: list[Member],
    existing_keys: set[str],
) -> list[Member]:
    """Find missing pre-marker fixtures/helpers referenced by adopted members."""
    selected: list[Member] = []
    referenced_source = "\n".join(member.source for member in roots)
    remaining = [
        member
        for member in candidates
        if member.key not in existing_keys and not _is_test_member(member)
    ]
    changed = True
    while changed:
        changed = False
        for member in list(remaining):
            if member not in remaining:
                continue
            name = _declared_name(member)
            if not name:
                continue
            if member.key.startswith("method:"):
                # TestNG data providers are linked through a string-valued
                # annotation rather than a Java method call.  Treat that as a
                # real dependency so parameterized PR tests remain runnable.
                pattern = (
                    rf"(?:\b{re.escape(name)}\s*\(|"
                    rf"\bdataProvider\s*=\s*\"{re.escape(name)}\")"
                )
            else:
                pattern = rf"\b{re.escape(name)}\b"
            if not re.search(pattern, referenced_source):
                continue
            selected.append(member)
            existing_keys.add(member.key)
            referenced_source += "\n" + member.source
            remaining.remove(member)
            if member.key.startswith("type:"):
                remaining = [
                    candidate
                    for candidate in remaining
                    if not (member.start < candidate.start and candidate.end < member.end)
                ]
            changed = True
    return selected


def _rewrite_member(member: Member, old_class: str, new_class: str) -> Member:
    source = re.sub(rf"\b{re.escape(old_class)}\b", new_class, member.source)
    return Member(source=source, key=member.key, start=member.start, end=member.end)


def _member_indent(text: str) -> str:
    prefixes: list[str] = []
    marker = text.find(MARKER)
    for member in _members(text):
        if marker >= 0 and member.start > marker:
            continue
        line_start = text.rfind("\n", 0, member.start) + 1
        prefix = text[line_start:member.start]
        if prefix and not prefix.strip():
            prefixes.append(prefix)
    return Counter(prefixes).most_common(1)[0][0] if prefixes else "    "


def _normalize_added_section(text: str) -> str:
    """Match the surrounding class's top-level indentation and annotation style."""
    marker = text.find(MARKER)
    if marker < 0:
        return text
    indent = _member_indent(text)

    edits: list[tuple[int, int, str]] = []
    for member in _members(text):
        if member.start <= marker:
            continue
        line_start = text.rfind("\n", 0, member.start) + 1
        edits.append((line_start, member.start, indent))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]

    text = re.sub(
        rf"(?m)^[ \t]*{re.escape(MARKER)}$",
        indent + MARKER,
        text,
        count=1,
    )
    if re.search(r"(?m)^import\s+org\.junit\.Test;\s*$", text):
        marker = text.find(MARKER)
        text = text[:marker] + text[marker:].replace("@org.junit.Test", "@Test")
    return text


def _replace_named_methods(
    text: str,
    replacements: dict[str, Member],
    target_names: dict[str, str] | None = None,
) -> tuple[str, set[str]]:
    """Replace matched definitions with one canonical PR definition."""
    target_names = target_names or {name: name for name in replacements}
    selected_by_target = {target_names[name]: name for name in replacements if name in target_names}
    existing_by_name: dict[str, list[Member]] = {}
    for member in _all_members(text):
        name = _method_name(member)
        if name in selected_by_target:
            existing_by_name.setdefault(name, []).append(member)

    edits: list[tuple[int, int, str]] = []
    replaced: set[str] = set()
    for target_name, matches in existing_by_name.items():
        selected_name = selected_by_target[target_name]
        canonical = replacements[selected_name].source
        line_start = text.rfind("\n", 0, matches[0].start) + 1
        indent = text[line_start:matches[0].start]
        formatted = _format_member(canonical, indent)
        edits.append((matches[0].start, matches[0].end, formatted[len(indent) :]))
        edits.extend((duplicate.start, duplicate.end, "") for duplicate in matches[1:])
        replaced.add(selected_name)

    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, replaced


def _imports(text: str) -> list[str]:
    return re.findall(r"(?m)^import\s+(?:static\s+)?[^;\r\n]+;[ \t]*$", text)


def _add_imports(text: str, imports: list[str]) -> str:
    existing = set(_imports(text))
    existing_simple_names = {
        re.sub(r"^import\s+", "", item).rstrip("; \t\r\n").rsplit(".", 1)[-1]
        for item in existing
        if not item.startswith("import static ") and not item.rstrip().endswith(".*;")
    }
    additions = []
    for item in imports:
        if item in existing:
            continue
        if not item.startswith("import static "):
            simple_name = re.sub(r"^import\s+", "", item).rstrip("; \t\r\n").rsplit(".", 1)[-1]
            if simple_name != "*" and simple_name in existing_simple_names:
                continue
            if simple_name != "*":
                existing_simple_names.add(simple_name)
        additions.append(item)
    if not additions:
        return text
    matches = list(re.finditer(r"(?m)^import\s+(?:static\s+)?[^;\r\n]+;[ \t]*$", text))
    if matches:
        insertion = matches[-1].end()
    else:
        package = re.search(r"(?m)^package\s+[^;]+;\s*$", text)
        insertion = package.end() if package else 0
    return text[:insertion] + "\n" + "\n".join(additions) + text[insertion:]


def _required_imports(imports: list[str], members: list[Member]) -> list[str]:
    """Keep imports referenced by the PR members that will enter the target."""
    source = "\n".join(member.source for member in members)
    required: list[str] = []
    for item in imports:
        imported = re.sub(r"^import\s+(?:static\s+)?", "", item).rstrip("; \t\r\n")
        if imported.endswith(".*"):
            required.append(item)
            continue
        simple_name = imported.rsplit(".", 1)[-1]
        if re.search(rf"\b{re.escape(simple_name)}\b", source):
            required.append(item)
    return required


def _remove_unneeded_source_imports(text: str, source_imports: list[str]) -> str:
    """Remove conflicting or unused non-static imports introduced by the PR source."""
    source_set = set(source_imports)
    body = re.sub(r"(?m)^import\s+(?:static\s+)?[^;\r\n]+;[ \t]*$", "", text)
    seen_simple_names: set[str] = set()
    removals: list[str] = []
    for item in _imports(text):
        if item.startswith("import static "):
            continue
        simple_name = re.sub(r"^import\s+", "", item).rstrip("; \t").rsplit(".", 1)[-1]
        if simple_name == "*":
            continue
        if simple_name in seen_simple_names:
            if item in source_set:
                removals.append(item)
            continue
        seen_simple_names.add(simple_name)
        if item in source_set and not re.search(rf"\b{re.escape(simple_name)}\b", body):
            removals.append(item)
    for item in removals:
        text = text.replace(item, "", 1)
    return re.sub(
        r"(?m)(^import[^\r\n]+;[ \t]*\r?\n)(?:[ \t]*\r?\n)+(?=(?:public\s+)?(?:class|interface|record|enum)\b)",
        r"\1\n",
        text,
    )


def _format_member(source: str, indent: str) -> str:
    """Reindent a copied member using the target class's indentation unit."""
    width = len(indent.expandtabs(4)) or 4
    lines = source.expandtabs(width).splitlines()
    positive_indents = [
        len(line) - len(line.lstrip())
        for line in lines[1:]
        if line.strip() and len(line) != len(line.lstrip())
    ]
    source_indent = min(positive_indents, default=0)
    formatted = [lines[0].lstrip()]
    formatted.extend(line[source_indent:] if line.strip() else "" for line in lines[1:])
    return "\n".join(indent + line if line else "" for line in formatted)


def _remove_added_section(text: str) -> str:
    marker = text.find(MARKER)
    if marker < 0:
        return text
    _, closing = _class_body(text)
    line_start = text.rfind("\n", 0, marker) + 1
    return text[:line_start].rstrip() + "\n" + text[closing:]


def _merge(
    reduced: Path,
    agentic: Path,
    *,
    selected_tests: list[str] | None = None,
    replacement_overrides: dict[str, str | None] | None = None,
    include_pre_marker_support: bool = True,
    allow_package_rewrite: bool = False,
    source_replacements: tuple[tuple[str, str], ...] = (),
) -> tuple[bool, list[str]]:
    reduced_text = reduced.read_text(encoding="utf-8", errors="ignore")
    original_agentic_text = agentic.read_text(encoding="utf-8", errors="ignore")
    agentic_text_without_added_section = _remove_added_section(original_agentic_text)
    selected_tests = selected_tests or test_method_names(reduced, after_adopted_marker=True)
    if not selected_tests:
        raise ValueError(f"no selected PR tests found in {reduced}")
    reduced_package, reduced_class = parse_package_and_class(reduced)
    agentic_package, agentic_class = parse_package_and_class(agentic)
    if reduced_package != agentic_package:
        if not allow_package_rewrite:
            raise ValueError(
                f"package mismatch: reduced PR source uses {reduced_package}, "
                f"agentic source uses {agentic_package}"
            )
        reduced_text = re.sub(
            rf"(?m)^package\s+{re.escape(reduced_package)}\s*;",
            f"package {agentic_package};",
            reduced_text,
            count=1,
        )
        reduced_text = reduced_text.replace(reduced_package + ".", agentic_package + ".")
    for old, new in source_replacements:
        reduced_text = reduced_text.replace(old, new)

    reduced_members = _all_members(reduced_text)
    adopted_marker = reduced_text.find("// Adopted Tests")
    selected_names = set(selected_tests)
    selected_members = {
        name: _rewrite_member(member, reduced_class, agentic_class)
        for member in reduced_members
        if (name := _method_name(member)) in selected_names
    }
    missing_pr_bodies = selected_names - set(selected_members)
    if missing_pr_bodies:
        raise ValueError(f"could not extract PR method bodies: {sorted(missing_pr_bodies)}")

    # Replace exact-name tests and conservative one-to-one semantic matches in
    # place. Only append a PR test when no suitable Agentic test exists.
    replacement_matches = _semantic_replacement_matches(
        selected_members,
        agentic_text_without_added_section,
    )
    for selected_name, existing_name in (replacement_overrides or {}).items():
        if existing_name is None:
            replacement_matches.pop(selected_name, None)
        else:
            replacement_matches[selected_name] = (existing_name, 1.0)
    agentic_text, replaced_names = _replace_named_methods(
        agentic_text_without_added_section,
        selected_members,
        {selected: existing for selected, (existing, _score) in replacement_matches.items()},
    )
    existing_keys = {member.key for member in _all_members(agentic_text)}
    additions: list[Member] = []
    for member in reduced_members:
        member_name = _method_name(member)
        is_selected_test = member_name in selected_names
        is_after_marker = adopted_marker >= 0 and member.start > adopted_marker
        # Reduced PR sources retain only selected tests, their support code, and
        # non-test fixtures. Add unique fixtures as well as the adopted section.
        should_add_test = is_selected_test and member_name not in replaced_names
        should_add_support = include_pre_marker_support and not is_selected_test and (
            is_after_marker or member.start < adopted_marker
        )
        if member.key not in existing_keys and (should_add_test or should_add_support):
            rewritten = _rewrite_member(member, reduced_class, agentic_class)
            additions.append(rewritten)
            existing_keys.add(member.key)

    if not include_pre_marker_support:
        support_candidates = [
            _rewrite_member(member, reduced_class, agentic_class)
            for member in reduced_members
            if _method_name(member) not in selected_names
        ]
        additions.extend(
            _referenced_support_members(
                support_candidates,
                _all_members(agentic_text) + list(selected_members.values()) + additions,
                existing_keys,
            )
        )

    entering_members = list(selected_members.values()) + additions
    merged = _add_imports(agentic_text, _required_imports(_imports(reduced_text), entering_members))
    if additions:
        _, closing = _class_body(merged)
        indent = _member_indent(merged)
        marker = "" if MARKER in merged else f"{indent}{MARKER}\n"
        block = "\n\n" + marker + "\n\n".join(_format_member(member.source, indent) for member in additions) + "\n"
        merged = merged[:closing].rstrip() + block + merged[closing:]
    merged = _normalize_added_section(merged)
    merged = _remove_unneeded_source_imports(merged, _imports(reduced_text))

    merged_names = [_method_name(member) for member in _all_members(merged)]
    missing = selected_names - set(merged_names)
    if missing:
        raise ValueError(f"merged source is missing PR tests: {sorted(missing)}")
    duplicates = {name: merged_names.count(name) for name in selected_names if merged_names.count(name) != 1}
    if duplicates:
        raise ValueError(f"merged source has duplicate PR tests: {duplicates}")

    changed = merged != original_agentic_text
    if changed:
        agentic.write_text(merged, encoding="utf-8")
    return changed, selected_tests


def _raw_rows() -> list[dict[str, str]]:
    with TARGET_AUDIT.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    current_targets: dict[str, dict[str, str]] = {}
    if TARGETS_CSV.exists():
        with TARGETS_CSV.open(encoding="utf-8", newline="") as handle:
            current_targets = {row["target_id"]: row for row in csv.DictReader(handle)}
    for row in rows:
        current = current_targets.get(row["target_id"])
        if current is not None:
            row["repo"] = current["repo"]
            row["fqcn"] = current["fqcn"]
        match = re.match(r"T(\d+)_", row["target_id"])
        if not match:
            raise ValueError(f"invalid target id: {row['target_id']}")
        sources = sorted(RAW_PR_TESTS.glob(f"T{int(match.group(1)):02d}-*.java"))
        if len(sources) != 1:
            raise ValueError(f"expected one raw PR source for {row['target_id']}, found {len(sources)}")
        row["source_path"] = str(sources[0].relative_to(ROOT))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Report mappings without modifying files")
    parser.add_argument(
        "--all-raw",
        action="store_true",
        help="Merge every test after // Adopted Tests from raw T01-T32 PR sources",
    )
    parser.add_argument(
        "--target",
        help="Merge one target only (for example T04 or T04_SubscriptionState).",
    )
    args = parser.parse_args()

    if args.all_raw:
        rows = _raw_rows()
    else:
        with SUMMARY.open(encoding="utf-8", newline="") as handle:
            rows = [
                row
            for row in csv.DictReader(handle)
            if row.get("status") == "passed" and row.get("reduced_test_path")
        ]
    if args.target:
        prefix = args.target.split("_", 1)[0]
        rows = [row for row in rows if row["target_id"] == args.target or row["target_id"].startswith(prefix + "_")]
        if len(rows) != 1:
            raise ValueError(f"expected one target matching {args.target!r}, found {len(rows)}")

    changed = 0
    total_tests = 0
    for row in rows:
        target_id = f"{repo_to_dir(row['repo'])}_{row['fqcn'].replace('.', '_')}"
        reduced = ROOT / (row["source_path"] if args.all_raw else row["reduced_test_path"])
        agentic = agentic_test_path(LLM_OUT, target_id, row["fqcn"])
        if agentic is None:
            raise SystemExit(f"No agentic output found for {row['repo']} {row['fqcn']}")
        tests = test_method_names(reduced, after_adopted_marker=True)
        if row["target_id"].startswith("T18_"):
            # The authoritative T18 snapshot carries its two contributed
            # methods in the middle of the upstream test class rather than
            # after the legacy marker.  Select only the submitted methods;
            # surrounding upstream tests must never be merged into Agentic.
            submitted = {
                "operationListenersCanPropagateToDifferentInstrumenterEnd",
                "operationListenersFromNestedInstrumenterPropagateToDifferentInstrumenterEnd",
            }
            all_tests = test_method_names(reduced)
            tests = [name for name in all_tests if name in submitted]
        if row["target_id"].startswith("T19_"):
            # T19's authoritative snapshot ends with exactly these two
            # contributed processOpts regression tests.  The raw fixture used
            # previously contained unrelated tests from a different PR.
            submitted = {
                "processOptsConfiguresRxJavaOptions",
                "processOptsConvertsConfiguredSupportUrlQuery",
            }
            all_tests = test_method_names(reduced)
            tests = [name for name in all_tests if name in submitted]
        if row["target_id"].startswith("T22_"):
            # Like T18, T22's two contributed methods appear in the middle of
            # the authoritative upstream snapshot.  Select them explicitly so
            # unrelated surrounding tests cannot enter the Agentic suite.
            submitted = {
                "testValidateEmbeddedRejectsRecordIdValue",
                "testValidateEmbeddedAcceptsLinkedEmbeddedDocument",
            }
            all_tests = test_method_names(reduced)
            tests = [name for name in all_tests if name in submitted]
        reduced_package, _ = parse_package_and_class(reduced)
        agentic_package, _ = parse_package_and_class(agentic)
        allow_package_rewrite = args.all_raw and row["target_id"].startswith(("T19_", "T30_", "T32_"))
        replacement_overrides: dict[str, str | None] = {}
        if row["target_id"].startswith("T19_"):
            replacement_overrides = {
                "processOptsConfiguresRxJavaOptions": "setUseRxJava3_thenProcessOpts_keepsDefaults",
                "processOptsConvertsConfiguredSupportUrlQuery": None,
            }
        if row["target_id"].startswith("T27_"):
            # Both submitted tests exercise EmptySubscription parent behavior.
            # One already replaced scanEmptySubscription; replace the remaining
            # near-duplicate instead of appending the second PR test at the end.
            replacement_overrides = {
                "terminalEmptySubscriptionHasNoParent":
                    "parentsStreamIsNotNullForEmptySubscription",
                "discardQueueWithoutHookClearsQueue":
                    "discardQueueWithClearContinuesOnRawQueueElementNotDiscarded",
            }
        if reduced_package != agentic_package and not allow_package_rewrite:
            print(
                f"SKIPPED {target_id}: package mismatch: reduced PR source uses "
                f"{reduced_package}, agentic source uses {agentic_package}"
            )
            continue
        total_tests += len(tests)
        if args.check:
            reduced_text = reduced.read_text(encoding="utf-8", errors="ignore")
            reduced_members = _all_members(reduced_text)
            selected_members = {
                name: member
                for member in reduced_members
                if (name := _method_name(member)) in set(tests)
            }
            matches = _semantic_replacement_matches(
                selected_members,
                _remove_added_section(agentic.read_text(encoding="utf-8", errors="ignore")),
            )
            for selected_name, existing_name in replacement_overrides.items():
                if existing_name is None:
                    matches.pop(selected_name, None)
                else:
                    matches[selected_name] = (existing_name, 1.0)
            plans = [
                (
                    f"{name} -> {matches[name][0]} ({matches[name][1]:.3f})"
                    if name in matches
                    else f"{name} -> APPEND"
                )
                for name in tests
            ]
            print(
                f"CHECK {target_id}: {len(tests)} tests -> {agentic.relative_to(ROOT)} | "
                + "; ".join(plans)
            )
            continue
        try:
            replacements: tuple[tuple[str, str], ...] = ()
            if row["target_id"].startswith("T09_"):
                replacements = (("assertThat(", "org.assertj.core.api.Assertions.assertThat("),)
            if row["target_id"].startswith("T30_") and row["fqcn"].startswith("io.seata."):
                replacements = (("ConfigurationFactory.getInstance()", "new FileConfiguration()"),)
            did_change, tests = _merge(
                reduced,
                agentic,
                selected_tests=tests,
                replacement_overrides=replacement_overrides,
                include_pre_marker_support=not args.all_raw,
                allow_package_rewrite=allow_package_rewrite,
                source_replacements=replacements,
            )
        except ValueError as error:
            if str(error).startswith("package mismatch:"):
                total_tests -= len(tests)
                print(f"SKIPPED {target_id}: {error}")
                continue
            raise
        changed += int(did_change)
        print(f"{'MERGED' if did_change else 'UNCHANGED'} {target_id}: {', '.join(tests)}")

    print(f"targets={len(rows)} changed={changed} selected_pr_tests={total_tests}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
