"""Convention-conformance metrics for RQ1.

These measure whether a candidate test follows the conventions of the existing
manually written test suite for the same class under test. Each measure is a
ratio in [0, 1] where 1 means exact agreement with the reference under the
metric definition. Naming additionally contains an absolute clarity component,
so the manually written reference is not forced to score 1 against itself. These
scores are not independently measured human-performance anchors.

The four measures replace the formatting-level style profile previously used by
``tri_compare_tests.style_distance``. Formatting features (indentation spread,
long-line ratio, camel-case ratio) do not vary between variants because all Java
under study is formatted conventionally; the conventions that do vary are
semantic: which assertion API, which fixture, which naming scheme, which
imports. See Robillard et al., "Understanding Test Convention Consistency as a
Dimension of Test Quality" (TOSEM 2025).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import javalang
except Exception:  # pragma: no cover - optional dependency
    javalang = None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

IMPORT_RE = re.compile(
    r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;?\s*$", re.M
)
NEW_RE = re.compile(r"\bnew\s+([A-Z]\w*)\s*[(<]")
CALL_RE = re.compile(r"(?<![\w.])([a-z]\w*)\s*\(")

METHOD_DECL_RE = re.compile(
    r"(?mx)^\s*"
    r"(?:(?:public|protected|private|static|final|synchronized|abstract|native|strictfp|default)\s+)*"
    r"(?:<[^>{};]+>\s+)?"
    r"[A-Za-z_$][\w$.\[\]<>?, @]*\s+"
    r"(?P<name>[A-Za-z_$][\w$]*)\s*"
    r"\([^;{}]*\)\s*"
    r"(?:throws\s+[^;{}]+\s*)?\{"
)
ANNOTATION_RE = re.compile(r"@([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)")

TEST_ANNOTATIONS = {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory"}
SETUP_ANNOTATIONS = {"Before", "BeforeEach", "BeforeAll", "BeforeClass",
                     "After", "AfterEach", "AfterAll", "AfterClass"}


def strip_noise(code: str) -> str:
    """Blank comments and literals without mistaking URL text for comments.

    The previous implementation removed ``//`` comments before string literals,
    so a value such as ``"https://example"`` could erase the rest of its line and
    make a later assertion disappear.  This small lexer handles comments, quoted
    strings, character literals, and Java text blocks in source order.  Newlines
    are retained so line-oriented fallbacks continue to work.
    """
    out: List[str] = []
    index = 0
    state = "code"
    while index < len(code):
        if state == "code":
            if code.startswith("//", index):
                out.extend("  ")
                index += 2
                state = "line_comment"
            elif code.startswith("/*", index):
                out.extend("  ")
                index += 2
                state = "block_comment"
            elif code.startswith('\"\"\"', index):
                out.extend("   ")
                index += 3
                state = "text_block"
            elif code[index] == '"':
                out.append(" ")
                index += 1
                state = "string"
            elif code[index] == "'":
                out.append(" ")
                index += 1
                state = "char"
            else:
                out.append(code[index])
                index += 1
            continue

        if state == "line_comment":
            if code[index] == "\n":
                out.append("\n")
                state = "code"
            else:
                out.append(" ")
            index += 1
            continue

        if state == "block_comment":
            if code.startswith("*/", index):
                out.extend("  ")
                index += 2
                state = "code"
            else:
                out.append("\n" if code[index] == "\n" else " ")
                index += 1
            continue

        if state == "text_block":
            if code.startswith('\"\"\"', index):
                out.extend("   ")
                index += 3
                state = "code"
            else:
                out.append("\n" if code[index] == "\n" else " ")
                index += 1
            continue

        # Ordinary string or character literal.
        if code[index] == "\\" and index + 1 < len(code):
            out.append(" ")
            out.append("\n" if code[index + 1] == "\n" else " ")
            index += 2
        elif (state == "string" and code[index] == '"') or (
            state == "char" and code[index] == "'"
        ):
            out.append(" ")
            index += 1
            state = "code"
        else:
            out.append("\n" if code[index] == "\n" else " ")
            index += 1
    return "".join(out)


@dataclass(frozen=True)
class MethodInfo:
    name: str
    annotations: Tuple[str, ...]

    @property
    def is_test(self) -> bool:
        return bool(set(self.annotations) & TEST_ANNOTATIONS) or self.name.startswith("test")

    @property
    def is_setup(self) -> bool:
        return bool(set(self.annotations) & SETUP_ANNOTATIONS)


def parse_methods(code: str) -> List[MethodInfo]:
    if javalang is not None:
        try:
            tree = javalang.parse.parse(code)
            out = []
            for _, node in tree.filter(javalang.tree.MethodDeclaration):
                names = tuple(
                    getattr(a, "name", "") for a in (getattr(node, "annotations", []) or [])
                )
                out.append(MethodInfo(node.name or "", names))
            if out:
                return out
        except Exception:
            pass
    # JavaParser can reject newer syntax in otherwise useful test files.  The
    # fallback is deliberately anchored to declaration lines and restricted to
    # class depth one, avoiding the old false positives for ``catch``, ``for``,
    # constructors in expressions, and anonymous-class methods.
    clean = strip_noise(code)
    depth = 0
    depths: List[int] = []
    for char in clean:
        depths.append(depth)
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)

    out = []
    previous_method_start = 0
    for match in METHOD_DECL_RE.finditer(clean):
        if depths[match.start()] != 1:
            continue
        first_word = match.group(0).lstrip().split(None, 1)[0]
        if first_word in {"return", "throw", "new", "case"}:
            continue
        annotations = tuple(
            annotation.group(1).rsplit(".", 1)[-1]
            for annotation in ANNOTATION_RE.finditer(
                clean, previous_method_start, match.start()
            )
            if depths[annotation.start()] == 1
        )
        out.append(MethodInfo(match.group("name"), annotations))
        previous_method_start = match.start()
    return out


def test_method_names(code: str) -> List[str]:
    return [m.name for m in parse_methods(code) if m.is_test]


def helper_method_names(code: str) -> Set[str]:
    """Non-test methods declared in a test file: fixtures, factories, assertions."""
    return {m.name for m in parse_methods(code) if not m.is_test and m.name}


def imports(code: str) -> Set[str]:
    return {m.group(1) for m in IMPORT_RE.finditer(code)}


# ---------------------------------------------------------------------------
# 1. Assertion-style conformance
# ---------------------------------------------------------------------------

JUNIT_ASSERT_RE = re.compile(
    r"\b(?:Assert(?:ions)?\.)?(?P<name>assert(?:Equals|NotEquals|True|False|Null|NotNull"
    r"|Same|NotSame|ArrayEquals|Throws|DoesNotThrow|All|Iterable\w*|LinesMatch"
    r"|InstanceOf|Timeout(?:Preemptively)?))\s*\(")
ASSERTTHAT_RE = re.compile(r"\b(?:Truth\.)?(?P<name>assertThat\w*)\s*\(")
TRUTH_MESSAGE_RE = re.compile(r"\b(?:Truth\.)?assertWithMessage\s*\(")
FAIL_RE = re.compile(r"(?<![\w.])(?P<name>fail)\s*\(")
MOCKMVC_EXPECT_RE = re.compile(r"\.andExpect\s*\(")


@dataclass(frozen=True)
class AssertionSite:
    """One assertion call, classified at two levels of specificity."""

    family: str
    idiom: str


def _matching_paren(text: str, opening: int) -> int:
    """Return the matching close parenthesis, or the end of ``text``."""
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


def _split_top_level_args(arguments: str) -> List[str]:
    """Split a lexically cleaned argument list without splitting nested calls."""
    if not arguments.strip():
        return []
    parts: List[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(arguments):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(arguments[start:index].strip())
            start = index + 1
    parts.append(arguments[start:].strip())
    return parts


def _boolean_argument_shape(arguments: Sequence[str]) -> str:
    # JUnit 4 places a message before the condition while JUnit 5 places it
    # after it, so examine every argument rather than assuming a position.
    expression = " ".join(arguments)
    if re.search(r"(?:==|!=|<=|>=|(?<![<>=!])<(?![=>])|(?<![<>=!])>(?![=]))", expression):
        return "comparison"
    if re.search(r"\b(?:contains|equals|is[A-Z]\w*|has[A-Z]\w*)\s*\(", expression):
        return "predicate"
    if "&&" in expression or "||" in expression:
        return "compound"
    return "condition"


def _junit_idiom(name: str, arguments: Sequence[str]) -> str:
    operation = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    if name in {"assertTrue", "assertFalse"}:
        shape = _boolean_argument_shape(arguments)
    elif name in {"assertEquals", "assertNotEquals", "assertArrayEquals"}:
        # Two operands are the ordinary equality idiom. Extra arguments encode
        # a message or a floating-point delta, both materially different forms.
        shape = {0: "empty", 1: "unary", 2: "binary"}.get(len(arguments), "extended")
    elif name in {"assertThrows", "assertDoesNotThrow"}:
        shape = "executable"
    else:
        shape = "unary" if len(arguments) == 1 else f"arity_{len(arguments)}"
    return f"junit:{operation}:{shape}"


def _fluent_terminal(body: str, closing: int) -> str:
    """Return the final fluent method in the assertion statement."""
    statement_end = body.find(";", closing)
    if statement_end < 0:
        statement_end = min(len(body), closing + 500)
    methods = re.findall(r"\.\s*([a-zA-Z_$][\w$]*)\s*\(", body[closing + 1:statement_end])
    return methods[-1] if methods else "assert_that"


def _first_call_name(expression: str) -> str:
    match = re.search(r"(?:^|[.(,\s])([a-zA-Z_$][\w$]*)\s*\(", expression)
    return match.group(1) if match else "expression"


def assertion_sites(code: str) -> List[AssertionSite]:
    """Extract assertion families and idioms from a Java test artifact.

    An idiom includes the assertion operation and a small structural shape. For
    example, ``assertTrue(a == b)`` and ``assertEquals(a, b)`` belong to the same
    JUnit family but to different idioms; AssertJ/Truth calls use their terminal
    fluent operation, and MockMvc expectations use the expectation type.
    """
    body = strip_noise(code)
    imp = imports(code)
    sites: List[AssertionSite] = []

    if any(i.startswith("org.assertj") for i in imp):
        that_family = "assertj"
    elif any(i.startswith("org.hamcrest") for i in imp) or any(
        i.startswith("org.junit.Assert.assertThat") for i in imp
    ):
        that_family = "hamcrest"
    elif any(i.startswith("com.google.common.truth") for i in imp):
        that_family = "truth"
    else:
        that_family = "assertj"

    for match in JUNIT_ASSERT_RE.finditer(body):
        opening = match.end() - 1
        closing = _matching_paren(body, opening)
        arguments = _split_top_level_args(body[opening + 1:closing])
        sites.append(AssertionSite("junit", _junit_idiom(match.group("name"), arguments)))

    for match in FAIL_RE.finditer(body):
        sites.append(AssertionSite("junit", "junit:fail"))

    for match in ASSERTTHAT_RE.finditer(body):
        opening = match.end() - 1
        closing = _matching_paren(body, opening)
        family = "truth" if match.group(0).lstrip().startswith("Truth.") else that_family
        if family == "hamcrest":
            arguments = _split_top_level_args(body[opening + 1:closing])
            matcher = _first_call_name(arguments[1]) if len(arguments) > 1 else "assert_that"
            idiom = f"hamcrest:{matcher}"
        else:
            idiom = f"{family}:{_fluent_terminal(body, closing)}"
        sites.append(AssertionSite(family, idiom))

    for match in TRUTH_MESSAGE_RE.finditer(body):
        opening = match.end() - 1
        closing = _matching_paren(body, opening)
        sites.append(AssertionSite("truth", f"truth:{_fluent_terminal(body, closing)}"))

    for match in MOCKMVC_EXPECT_RE.finditer(body):
        opening = match.end() - 1
        closing = _matching_paren(body, opening)
        expectation = _first_call_name(body[opening + 1:closing])
        sites.append(AssertionSite("spring_mockmvc", f"spring_mockmvc:{expectation}"))
    return sites


def assertion_families(code: str) -> Dict[str, int]:
    """Count assertion call sites per API family."""
    counts = {
        "junit": 0,
        "assertj": 0,
        "hamcrest": 0,
        "truth": 0,
        "spring_mockmvc": 0,
    }
    for site in assertion_sites(code):
        counts[site.family] += 1
    return counts


def assertion_idioms(code: str) -> Dict[str, int]:
    """Count operation-and-shape signatures, weighted by their occurrences."""
    counts: Dict[str, int] = {}
    for site in assertion_sites(code):
        counts[site.idiom] = counts.get(site.idiom, 0) + 1
    return counts


def _distribution_overlap(left: Dict[str, int], right: Dict[str, int]) -> float:
    """Histogram intersection after normalizing away artifact size."""
    left_total = sum(left.values())
    right_total = sum(right.values())
    if left_total == 0 or right_total == 0:
        return 0.0
    keys = set(left) | set(right)
    return sum(
        min(left.get(key, 0) / left_total, right.get(key, 0) / right_total)
        for key in keys
    )


def assertion_conformance_components(
    candidate: str, manual: str
) -> Optional[Dict[str, float]]:
    """Return the family and idiom distribution-overlap components."""
    manual_counts = assertion_families(manual)
    if sum(manual_counts.values()) == 0:
        return None
    candidate_counts = assertion_families(candidate)
    if sum(candidate_counts.values()) == 0:
        return {"family": 0.0, "idiom": 0.0}
    return {
        "family": _distribution_overlap(candidate_counts, manual_counts),
        "idiom": _distribution_overlap(
            assertion_idioms(candidate), assertion_idioms(manual)
        ),
    }


def assertion_conformance(candidate: str, manual: str) -> Optional[float]:
    """Agreement in assertion-family and assertion-idiom distributions.

    The two equal-weight components distinguish API choice from usage within an
    API. Histogram intersection weights every observed assertion and captures
    the reference suite's relative mix without rewarding a larger test file.
    """
    components = assertion_conformance_components(candidate, manual)
    if components is None:
        return None
    return sum(components.values()) / len(components)


# ---------------------------------------------------------------------------
# 2. Naming conformance
# ---------------------------------------------------------------------------

NAME_TOKEN_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|[A-Z]+|\d+"
)
MECHANICAL_NAME_RE = re.compile(r"(?i)(?:taking\d*arguments?|andcalls?)")


def _name_tokens(name: str) -> List[str]:
    """Split snake/camel names while preserving acronyms and numeric tokens."""
    return [token.lower() for token in NAME_TOKEN_RE.findall(name.replace("_", " "))]


def _name_shape(name: str) -> Tuple[str, str, int]:
    """Return ``(prefix, separator, lexical-content count)`` for compatibility."""
    tokens = _name_tokens(name)
    prefix = "bare"
    if tokens and tokens[0] in {"test", "should"}:
        prefix = tokens.pop(0)
    elif tokens and tokens[0] in {"given", "when"}:
        prefix = "scenario"
        tokens.pop(0)
    separator = "underscore" if "_" in name else "camel"
    return prefix, separator, sum(not token.isdigit() for token in tokens)


def _name_style_and_content(name: str) -> Tuple[Tuple[str, str], List[str]]:
    prefix, separator, _ = _name_shape(name)
    tokens = _name_tokens(name)
    if tokens and tokens[0] in {"test", "should", "given", "when"}:
        tokens = tokens[1:]
    return (prefix, separator), tokens


def _name_clarity(name: str, reference_segments: float) -> float:
    """Score lexical sufficiency, diversity, and non-mechanical phrasing."""
    _, tokens = _name_style_and_content(name)
    lexical = [token for token in tokens if not token.isdigit()]
    if not lexical:
        return 0.0

    sufficiency = min(1.0, len(lexical) / reference_segments)
    diversity = len(set(lexical)) / len(lexical)
    mechanical = bool(MECHANICAL_NAME_RE.search(name))
    numeric_suffix = bool(re.search(r"\d+$", name))
    naturalness = 0.25 if mechanical or numeric_suffix else 1.0
    return sufficiency * (1.0 + diversity + naturalness) / 3.0


def naming_conformance_components(candidate: str, manual: str) -> Optional[Dict[str, float]]:
    """Return equal-level style compatibility and name clarity components.

    Style uses the joint prefix/separator distribution, allowing suites with
    multiple established conventions without treating an unseen combination as
    conformant. Clarity is vocabulary-independent: it rewards sufficient,
    non-repetitive lexical content and penalizes placeholders and mechanical
    generator narration.
    """
    manual_names = test_method_names(manual)
    if not manual_names:
        return None

    style_counts: Dict[Tuple[str, str], int] = {}
    lexical_counts: List[int] = []
    for name in manual_names:
        style, tokens = _name_style_and_content(name)
        style_counts[style] = style_counts.get(style, 0) + 1
        lexical_counts.append(sum(not token.isdigit() for token in tokens))
    most_common_style = max(style_counts.values())
    reference_segments = max(
        1.0, min(3.0, float(statistics.median(lexical_counts)))
    )

    candidate_names = test_method_names(candidate)
    if not candidate_names:
        return {"style": 0.0, "clarity": 0.0}

    style_scores = []
    clarity_scores = []
    for name in candidate_names:
        style, _ = _name_style_and_content(name)
        style_scores.append(style_counts.get(style, 0) / most_common_style)
        clarity_scores.append(_name_clarity(name, reference_segments))
    return {
        "style": sum(style_scores) / len(style_scores),
        "clarity": sum(clarity_scores) / len(clarity_scores),
    }


def naming_conformance(candidate: str, manual: str) -> Optional[float]:
    """Equal-weight mean of style compatibility and clarity."""
    components = naming_conformance_components(candidate, manual)
    if components is None:
        return None
    return sum(components.values()) / len(components)


# ---------------------------------------------------------------------------
# 3. Fixture adoption
# ---------------------------------------------------------------------------

def fixture_reuse(candidate: str, manual: str,
                  extra_helpers: Optional[Set[str]] = None) -> Optional[float]:
    """Fixture-adoption ratio, retained under its old name for CSV compatibility.

    Denominator is every point at which the test obtains an object: a call to a
    helper name declared by the existing suite, or a direct ``new`` expression.
    RQ1 candidates are standalone files and can redeclare or copy those helpers,
    so this metric measures adoption of the suite's fixture vocabulary and does
    not establish cross-file helper reuse.
    """
    helpers = helper_method_names(manual)
    if extra_helpers:
        helpers = helpers | extra_helpers
    if not helpers:
        return None

    # A call counts as adoption whenever it uses the existing suite's fixture
    # vocabulary. We deliberately include helpers redeclared by the candidate,
    # because the standalone RQ1 artifacts often carry copied helper definitions.
    body = strip_noise(candidate)
    helper_calls = sum(1 for m in CALL_RE.finditer(body) if m.group(1) in helpers)
    constructions = len(NEW_RE.findall(body))

    denominator = helper_calls + constructions
    if denominator == 0:
        # The reference exposes reusable fixture vocabulary but this candidate
        # adopts none of it and performs no direct construction.  Zero is the
        # comparable "no adoption" value; returning None made eligibility depend
        # on the variant and produced incomparable sample sizes.
        return 0.0
    return helper_calls / denominator


# Preferred public name.  The old function and dataclass field remain available
# so existing result inventories and pipeline consumers continue to work.
fixture_adoption = fixture_reuse


# ---------------------------------------------------------------------------
# 4. Import conformance
# ---------------------------------------------------------------------------

def import_conformance(candidate: str, manual: str) -> Optional[float]:
    """Fraction of the candidate's imports already used by the existing suite."""
    candidate_imports = imports(candidate)
    if not candidate_imports:
        return None
    return len(candidate_imports & imports(manual)) / len(candidate_imports)


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

CONFORMANCE_FIELDS = ("assertion_conformance", "naming_conformance",
                      "fixture_reuse", "import_conformance")


@dataclass
class ConventionProfile:
    assertion_family_conformance: Optional[float]
    assertion_idiom_conformance: Optional[float]
    assertion_conformance: Optional[float]
    naming_style_conformance: Optional[float]
    naming_clarity: Optional[float]
    naming_conformance: Optional[float]
    fixture_reuse: Optional[float]
    import_conformance: Optional[float]
    conformance_score: Optional[float]
    style_distance: Optional[float]

    def as_dict(self) -> Dict[str, Optional[float]]:
        return asdict(self)


def convention_profile(candidate: str, manual: str,
                       extra_helpers: Optional[Set[str]] = None) -> ConventionProfile:
    """Compute the four conformance measures plus their aggregate.

    ``conformance_score`` is the unweighted mean of the available measures. It is
    the reported aggregate and keeps the orientation of its components (higher is
    better). Because naming includes absolute clarity, a score of 1 denotes both
    convention agreement and maximum measured clarity; it is not an independently
    measured human-performance ceiling. ``style_distance`` is retained only as
    its complement for continuity with earlier revisions of the analysis.
    """
    assertion_components = assertion_conformance_components(candidate, manual)
    naming_components = naming_conformance_components(candidate, manual)
    values = {
        "assertion_conformance": (
            None if assertion_components is None
            else sum(assertion_components.values()) / len(assertion_components)
        ),
        "naming_conformance": (
            None if naming_components is None
            else sum(naming_components.values()) / len(naming_components)
        ),
        "fixture_reuse": fixture_reuse(candidate, manual, extra_helpers),
        "import_conformance": import_conformance(candidate, manual),
    }
    available: Sequence[float] = [v for v in values.values() if v is not None]
    score = sum(available) / len(available) if available else None
    return ConventionProfile(
        assertion_family_conformance=(
            None if assertion_components is None else assertion_components["family"]
        ),
        assertion_idiom_conformance=(
            None if assertion_components is None else assertion_components["idiom"]
        ),
        naming_style_conformance=(
            None if naming_components is None else naming_components["style"]
        ),
        naming_clarity=(
            None if naming_components is None else naming_components["clarity"]
        ),
        **values,
        conformance_score=score,
        style_distance=None if score is None else 1.0 - score,
    )
