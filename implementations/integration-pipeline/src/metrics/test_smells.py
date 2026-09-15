"""Test-smell detection for RQ1.

A test-specific replacement for the general-purpose static analysis previously
used (PMD), which reported zero violations for every artifact in the study
because its default rulesets encode no test-specific quality concerns.

We detect seven smells from the standard catalog introduced by van Deursen et
al. and consolidated by Meszaros, restricted to those decidable from a single
test file without the production source. Prior work established that
automatically generated tests are substantially more smell-prone than manually
written ones (Palomba et al. 2016; Grano et al. 2019), which makes smell density
a direct measure of remaining generator residue in an integrated test.

Detection is lexical and deliberately conservative: it operates on test-method
bodies with comments and string literals stripped, and prefers a false negative
to a false positive. Reported values are therefore lower bounds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from convention_metrics import (  # type: ignore
    ASSERTTHAT_RE,
    FAIL_RE,
    JUNIT_ASSERT_RE,
    strip_noise,
)

SMELLS = (
    "assertion_roulette",
    "exception_handling",
    "conditional_test_logic",
    "magic_number_test",
    "duplicate_assert",
    "unknown_test",
    "sensitive_equality",
)

METHOD_SIG_RE = re.compile(
    r"(?:@\w+(?:\s*\([^)]*\))?\s*)*"          # annotations
    r"(?:public|protected|private)?\s*"
    r"(?:static\s+|final\s+)*"
    r"(?:[\w.<>\[\],?\s]+?)\s+"                # return type
    r"(\w+)\s*\([^;{)]*\)"                     # name + params
    r"(?:\s*throws\s+[\w.,\s]+)?\s*\{",
    re.M,
)

ANY_ASSERT_RE = re.compile(
    JUNIT_ASSERT_RE.pattern + "|" + ASSERTTHAT_RE.pattern + "|" + FAIL_RE.pattern)
CONDITIONAL_RE = re.compile(r"(?<![\w.])(?:if|for|while|switch|catch)\s*\(")
NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?[LlFfDd]?(?![\w.])")
TOSTRING_RE = re.compile(r"\.toString\s*\(\s*\)")
TRY_RE = re.compile(r"(?<![\w.])try\s*\{")
ALLOWED_NUMBERS = {"0", "1", "-1", "2", "0L", "1L", "0.0", "1.0"}


def _match_brace(code: str, open_index: int) -> int:
    """Index just past the block opened at ``open_index``."""
    depth = 0
    for i in range(open_index, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(code)


def test_method_bodies(code: str) -> List[Tuple[str, str]]:
    """(name, body) for each test method, on comment- and string-stripped source."""
    body_text = strip_noise(code)
    out: List[Tuple[str, str]] = []
    for match in METHOD_SIG_RE.finditer(body_text):
        name = match.group(1)
        if name in {"if", "for", "while", "switch", "catch", "synchronized", "try"}:
            continue
        open_index = body_text.index("{", match.end() - 1)
        # The signature pattern consumes any annotations, so look for the test
        # annotation inside the match itself as well as in the text before it.
        window = body_text[max(0, match.start() - 220):match.start()] + match.group(0)
        annotated = "@Test" in window or "@ParameterizedTest" in window
        if not (annotated or name.startswith("test")):
            continue
        out.append((name, body_text[open_index:_match_brace(body_text, open_index)]))
    return out


def _assertion_calls(body: str) -> List[str]:
    """Assertion statements in a method body, one string per call site."""
    calls = []
    for match in ANY_ASSERT_RE.finditer(body):
        end = body.find(";", match.start())
        calls.append(body[match.start(): end if end != -1 else match.end()])
    return calls


def smells_in_method(body: str) -> List[str]:
    found = []
    asserts = _assertion_calls(body)

    # Assertion Roulette: several unexplained assertions in one test.
    if len(asserts) > 1 and not any('""' in a for a in asserts):
        found.append("assertion_roulette")

    # Exception Handling: the test manages exceptions itself instead of
    # declaring the expectation. EvoSuite's try/fail idiom is the common case.
    if TRY_RE.search(body) or "catch" in body:
        found.append("exception_handling")

    # Conditional Test Logic: control flow inside a test obscures what is tested.
    conditional = CONDITIONAL_RE.sub(
        lambda m: "" if m.group(0).startswith("catch") else m.group(0), body)
    if CONDITIONAL_RE.search(conditional):
        found.append("conditional_test_logic")

    # Magic Number Test: unexplained numeric literals inside assertions.
    if any(n for a in asserts for n in NUMBER_RE.findall(a) if n not in ALLOWED_NUMBERS):
        found.append("magic_number_test")

    # Duplicate Assert: the same assertion repeated within one test.
    normalized = [re.sub(r"\s+", " ", a).strip() for a in asserts]
    if len(normalized) != len(set(normalized)):
        found.append("duplicate_assert")

    # Unknown Test: a test method that asserts nothing.
    if not asserts:
        found.append("unknown_test")

    # Sensitive Equality: assertions compared through toString().
    if any(TOSTRING_RE.search(a) for a in asserts):
        found.append("sensitive_equality")

    return found


@dataclass
class SmellReport:
    test_methods: int
    total_smells: int
    smell_density: Optional[float]      # distinct smells per test method
    smelly_test_ratio: Optional[float]  # fraction of tests with >=1 smell
    per_smell: Dict[str, int]

    def as_dict(self) -> Dict[str, Optional[float]]:
        out: Dict[str, Optional[float]] = {
            "test_methods": self.test_methods,
            "total_smells": self.total_smells,
            "smell_density": self.smell_density,
            "smelly_test_ratio": self.smelly_test_ratio,
        }
        out.update({f"smell_{k}": v for k, v in self.per_smell.items()})
        return out


def detect(code: str) -> SmellReport:
    bodies = test_method_bodies(code)
    per_smell = {name: 0 for name in SMELLS}
    total = 0
    smelly = 0
    for _, body in bodies:
        found = smells_in_method(body)
        if found:
            smelly += 1
        total += len(found)
        for name in found:
            per_smell[name] += 1
    count = len(bodies)
    return SmellReport(
        test_methods=count,
        total_smells=total,
        smell_density=total / count if count else None,
        smelly_test_ratio=smelly / count if count else None,
        per_smell=per_smell,
    )
