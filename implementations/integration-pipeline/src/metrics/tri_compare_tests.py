from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional

try:
    import numpy as np
except Exception:
    np = None
try:
    import javalang
except Exception:
    javalang = None
try:
    import lizard
except Exception:
    lizard = None
try:
    import codebleu
except Exception:
    codebleu = None


def get_code_bleu_score(response_code, original_code):
    """
    A method to get the code bleu score for the generated code
    :param response_code: The response from the LLM without comments
    :param original_code: The original code without any improvements made
    :return: the score for the response
    """

    if codebleu is None:
        return None
    code_bleu_scores = codebleu.calc_codebleu([original_code], [response_code], lang="java",
                                              weights=(0.25, 0.25, 0.25, 0.25))
    return code_bleu_scores.get('codebleu')

# -----------------------------
# CodeBLEU
# -----------------------------
def codebleu_score(pred: str, ref: str, lang: str = "java") -> Optional[float]:
    try:
        return get_code_bleu_score(pred, ref)
    except Exception:
        return None


# -----------------------------
# Tokenization & similarity
# -----------------------------
JAVA_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.DOTALL | re.MULTILINE)
STRING_LIT_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"")
CHAR_LIT_RE = re.compile(r"'(?:\\.|[^'\\])'")

ASSERT_CALL_RE = re.compile(
    r"\b(assert(?:Equals|NotEquals|True|False|Null|NotNull|Same|NotSame|ArrayEquals|That)|fail)\s*\(",
    re.MULTILINE
)

IDENT_RE = re.compile(r"\b[A-Za-z_]\w*\b")


def strip_comments(code: str) -> str:
    return re.sub(JAVA_COMMENT_RE, "", code)


def normalize_literals(code: str) -> str:
    code = re.sub(STRING_LIT_RE, "\"<STR>\"", code)
    code = re.sub(CHAR_LIT_RE, "'<CHR>'", code)
    return code


def tokenize(code: str) -> List[str]:
    code = normalize_literals(strip_comments(code))
    return re.findall(r"[A-Za-z_]\w*|\d+|==|!=|<=|>=|\+\+|--|&&|\|\||[{}()\[\];,\.=+\-*/%<>!?:]", code)


def shingles(tokens: List[str], k: int = 5) -> Set[Tuple[str, ...]]:
    if len(tokens) < k:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i+k]) for i in range(len(tokens) - k + 1)}


def jaccard(a: Set, b: Set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a | b), 1)


def lcs_length(a: List[str], b: List[str]) -> int:
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 0
    dp = [0] * (m + 1)
    for i in range(1, n + 1):
        prev = 0
        for j in range(1, m + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev + 1
            else:
                dp[j] = max(dp[j], dp[j - 1])
            prev = temp
    return dp[m]


@dataclass
class Similarity:
    token_set_jaccard: float
    shingle_jaccard_k5: float
    lcs_token_ratio: float


def similarity(a: str, b: str) -> Similarity:
    ta, tb = tokenize(a), tokenize(b)
    s1 = jaccard(set(ta), set(tb))
    s2 = jaccard(shingles(ta, 5), shingles(tb, 5))
    lcs = lcs_length(ta, tb)
    denom = max(len(ta), len(tb), 1)
    s3 = lcs / denom
    return Similarity(float(s1), float(s2), float(s3))


# -----------------------------
# Style / readability proxies (compare to manual)
# -----------------------------
@dataclass
class StyleProfile:
    ident_count: int
    avg_ident_len: float
    camel_case_ratio: float
    screaming_snake_ratio: float
    test_method_count: int
    avg_test_name_len: float
    assert_calls: int
    assert_per_kloc: float
    long_line_ratio: float
    indentation_std: float


CAMEL_RE = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)+$")
SCREAMING_SNAKE_RE = re.compile(r"^[A-Z]+(?:_[A-Z0-9]+)+$")


def extract_test_method_names(code: str) -> List[str]:
    names: List[str] = []
    if javalang is None:
        return re.findall(r"\bvoid\s+(test\w+)\s*\(", code)
    try:
        tree = javalang.parse.parse(code)
        for _, m in tree.filter(javalang.tree.MethodDeclaration):
            ann = getattr(m, "annotations", []) or []
            if any(getattr(a, "name", "") == "Test" for a in ann) or (m.name or "").startswith("test"):
                names.append(m.name)
    except Exception:
        return re.findall(r"\bvoid\s+(test\w+)\s*\(", code)
    return names


def style_profile(code: str) -> StyleProfile:
    code_nc = normalize_literals(strip_comments(code))
    idents = re.findall(IDENT_RE, code_nc)

    camel = sum(1 for x in idents if CAMEL_RE.match(x or ""))
    scream = sum(1 for x in idents if SCREAMING_SNAKE_RE.match(x or ""))

    ident_count = len(idents)
    if np is not None:
        avg_ident_len = float(np.mean([len(x) for x in idents])) if idents else 0.0
    else:
        avg_ident_len = float(sum(len(x) for x in idents) / max(len(idents), 1))
    camel_ratio = camel / ident_count if ident_count else 0.0
    scream_ratio = scream / ident_count if ident_count else 0.0

    test_names = extract_test_method_names(code)
    test_method_count = len(test_names)
    if np is not None:
        avg_test_name_len = float(np.mean([len(n) for n in test_names])) if test_names else 0.0
    else:
        avg_test_name_len = float(sum(len(n) for n in test_names) / max(len(test_names), 1))

    assert_calls = len(re.findall(ASSERT_CALL_RE, code))

    lines = code.splitlines()
    non_empty = [l for l in lines if l.strip()]
    kloc = max(len(non_empty) / 1000.0, 1e-9)
    assert_per_kloc = assert_calls / kloc

    long_line_ratio = (sum(1 for l in lines if len(l) > 120) / max(len(lines), 1))

    indents = [len(re.match(r"^\s*", l).group(0).replace("\t", "    ")) for l in non_empty]
    if np is not None:
        indentation_std = float(np.std(indents)) if indents else 0.0
    else:
        if not indents:
            indentation_std = 0.0
        else:
            mean = sum(indents) / len(indents)
            var = sum((x - mean) ** 2 for x in indents) / len(indents)
            indentation_std = float(var ** 0.5)

    return StyleProfile(
        ident_count=ident_count,
        avg_ident_len=avg_ident_len,
        camel_case_ratio=float(camel_ratio),
        screaming_snake_ratio=float(scream_ratio),
        test_method_count=test_method_count,
        avg_test_name_len=avg_test_name_len,
        assert_calls=assert_calls,
        assert_per_kloc=float(assert_per_kloc),
        long_line_ratio=float(long_line_ratio),
        indentation_std=indentation_std,
    )


def style_distance(p: StyleProfile, m: StyleProfile) -> float:
    def rel(a: float, b: float) -> float:
        # A unit floor prevents a zero-valued reference feature (for example,
        # no detected test methods) from producing an arbitrarily large
        # distance while preserving relative scaling for larger features.
        return abs(a - b) / max(abs(b), 1.0)

    parts = [
        rel(p.avg_ident_len, m.avg_ident_len),
        abs(p.camel_case_ratio - m.camel_case_ratio),
        abs(p.screaming_snake_ratio - m.screaming_snake_ratio),
        rel(p.test_method_count, m.test_method_count),
        rel(p.avg_test_name_len, m.avg_test_name_len),
        rel(p.assert_per_kloc, m.assert_per_kloc),
        abs(p.long_line_ratio - m.long_line_ratio),
        rel(p.indentation_std, m.indentation_std),
    ]
    if np is not None:
        return float(np.mean(parts))
    return float(sum(parts) / max(len(parts), 1))


# -----------------------------
# Quality proxies (A vs B)
# -----------------------------
@dataclass
class Quality:
    loc: int
    methods: int
    cyclomatic_total: int
    cyclomatic_avg: float
    avg_method_loc: float


def quality(code: str, filename_hint: str = "Test.java") -> Quality:
    lines = code.splitlines()
    loc = len(lines)

    if lizard is None:
        methods = len(re.findall(r"\bvoid\s+\w+\s*\(", code))
        avg_len = 0.0
        cyclo_total = 0
        cyclo_avg = 0.0
    else:
        analysis = lizard.analyze_file.analyze_source_code(filename_hint, code)
        funcs = analysis.function_list or []
        methods = len(funcs)
        if np is not None:
            avg_len = float(np.mean([f.length for f in funcs])) if funcs else 0.0
        else:
            avg_len = float(sum(f.length for f in funcs) / max(len(funcs), 1)) if funcs else 0.0
        cyclo_total = int(sum(f.cyclomatic_complexity for f in funcs)) if funcs else 0
        cyclo_avg = cyclo_total / methods if methods else 0.0

    return Quality(loc=loc, methods=methods, cyclomatic_total=cyclo_total,
                   cyclomatic_avg=float(cyclo_avg), avg_method_loc=float(avg_len))


# -----------------------------
# Final report
# -----------------------------
try:  # conformance metrics live beside this module
    from convention_metrics import convention_profile as _convention_profile
except Exception:  # pragma: no cover
    try:
        from .convention_metrics import convention_profile as _convention_profile  # type: ignore
    except Exception:
        _convention_profile = None  # type: ignore


@dataclass
class TriReport:
    # closeness-to-manual (similarity family)
    auto_vs_manual: Similarity
    adopted_vs_manual: Similarity
    codebleu_auto_vs_manual: Optional[float]
    codebleu_adopted_vs_manual: Optional[float]

    # combined closeness score (higher = closer)
    closeness_score_auto: float
    closeness_score_adopted: float
    closer_to_manual_overall: str

    # style closeness (lower = closer)
    style_distance_auto_to_manual: float
    style_distance_adopted_to_manual: float
    closer_to_manual_by_style: str

    # diagnostics
    style_manual: StyleProfile
    style_auto: StyleProfile
    style_adopted: StyleProfile
    quality_auto: Quality
    quality_adopted: Quality

    # Convention conformance against the existing suite (see convention_metrics).
    # These supersede the formatting-level style profile above, which is retained
    # only as a diagnostic.
    conventions_auto: Optional[object] = None
    conventions_adopted: Optional[object] = None


def combined_closeness(sim: Similarity, cb: Optional[float], w: Dict[str, float]) -> float:
    """
    Higher = closer to manual.
    If CodeBLEU is unavailable, re-normalize weights among available components.
    """
    components = {
        "token": sim.token_set_jaccard,
        "shingle": sim.shingle_jaccard_k5,
        "lcs": sim.lcs_token_ratio,
        "codebleu": cb,
    }
    # select available
    avail = {k: v for k, v in components.items() if v is not None}
    if not avail:
        return 0.0
    # renormalize weights
    wsum = sum(w[k] for k in avail.keys())
    if wsum <= 0:
        if np is not None:
            return float(np.mean(list(avail.values())))
        vals = list(avail.values())
        return float(sum(vals) / max(len(vals), 1))
    score = sum((w[k] / wsum) * float(avail[k]) for k in avail.keys())
    return float(score)


def tri_compare(auto_path: str, adopted_path: str, manual_path: str, lang: str = "java") -> TriReport:
    auto = open(auto_path, "r", encoding="utf-8").read()
    adopted = open(adopted_path, "r", encoding="utf-8").read()
    manual = open(manual_path, "r", encoding="utf-8").read()

    sim_am = similarity(auto, manual)
    sim_bm = similarity(adopted, manual)

    cb_am = codebleu_score(auto, manual, lang=lang)
    cb_bm = codebleu_score(adopted, manual, lang=lang)

    # Combined closeness as reported in the paper: the unweighted mean of the two
    # retained similarity measures. Shingle and LCS overlap are still recorded in
    # the CSV as replication data but no longer contribute to the aggregate.
    weights = {"token": 0.50, "shingle": 0.0, "lcs": 0.0, "codebleu": 0.50}

    clos_a = combined_closeness(sim_am, cb_am, weights)
    clos_b = combined_closeness(sim_bm, cb_bm, weights)
    overall = "adopted" if clos_b > clos_a else ("auto" if clos_a > clos_b else "tie")

    sm = style_profile(manual)
    sa = style_profile(auto)
    sb = style_profile(adopted)

    dist_a = style_distance(sa, sm)
    dist_b = style_distance(sb, sm)
    closer_style = "adopted" if dist_b < dist_a else ("auto" if dist_a < dist_b else "tie")

    qa = quality(auto, auto_path)
    qb = quality(adopted, adopted_path)

    conv_a = _convention_profile(auto, manual) if _convention_profile else None
    conv_b = _convention_profile(adopted, manual) if _convention_profile else None

    return TriReport(
        auto_vs_manual=sim_am,
        adopted_vs_manual=sim_bm,
        codebleu_auto_vs_manual=cb_am,
        codebleu_adopted_vs_manual=cb_bm,
        closeness_score_auto=clos_a,
        closeness_score_adopted=clos_b,
        closer_to_manual_overall=overall,
        style_distance_auto_to_manual=float(dist_a),
        style_distance_adopted_to_manual=float(dist_b),
        closer_to_manual_by_style=closer_style,
        style_manual=sm,
        style_auto=sa,
        style_adopted=sb,
        quality_auto=qa,
        quality_adopted=qb,
        conventions_auto=conv_a,
        conventions_adopted=conv_b,
    )


def _empty_report() -> TriReport:
    sim = Similarity(0.0, 0.0, 0.0)
    style = StyleProfile(
        ident_count=0,
        avg_ident_len=0.0,
        camel_case_ratio=0.0,
        screaming_snake_ratio=0.0,
        test_method_count=0,
        avg_test_name_len=0.0,
        assert_calls=0,
        assert_per_kloc=0.0,
        long_line_ratio=0.0,
        indentation_std=0.0,
    )
    qual = Quality(loc=0, methods=0, cyclomatic_total=0, cyclomatic_avg=0.0, avg_method_loc=0.0)
    return TriReport(
        auto_vs_manual=sim,
        adopted_vs_manual=sim,
        codebleu_auto_vs_manual=None,
        codebleu_adopted_vs_manual=None,
        closeness_score_auto=0.0,
        closeness_score_adopted=0.0,
        closer_to_manual_overall="tie",
        style_distance_auto_to_manual=0.0,
        style_distance_adopted_to_manual=0.0,
        closer_to_manual_by_style="tie",
        style_manual=style,
        style_auto=style,
        style_adopted=style,
        quality_auto=qual,
        quality_adopted=qual,
    )


def safe_tri_compare(auto_path: str, adopted_path: str, manual_path: str, lang: str = "java") -> TriReport:
    try:
        return tri_compare(auto_path, adopted_path, manual_path, lang=lang)
    except Exception:
        return _empty_report()


class TriComparator:
    @staticmethod
    def compare(auto_path: str, adopted_path: str, manual_path: str, lang: str = "java") -> TriReport:
        return tri_compare(auto_path, adopted_path, manual_path, lang=lang)

    @staticmethod
    def write_rows(
            *,
            csv_path: str,
            group_id: str,
            manual_path: str,
            auto_path: str,
            adopted_path: str,
            rep: TriReport,
            auto_variant: str = "auto",
            adopted_variant: str = "adopted",
            include_auto: bool = True,
    ) -> None:
        write_rows_csv(
            csv_path=csv_path,
            group_id=group_id,
            manual_path=manual_path,
            auto_path=auto_path,
            adopted_path=adopted_path,
            rep=rep,
            auto_variant=auto_variant,
            adopted_variant=adopted_variant,
            include_auto=include_auto,
        )


import csv

def write_rows_csv(
    *,
    csv_path: str,
    group_id: str,
    manual_path: str,
    auto_path: str,
    adopted_path: str,
    rep,
    auto_variant: str = "auto",
    adopted_variant: str = "adopted",
    include_auto: bool = True,
):
    """
    Writes TWO rows:
      - variant=<auto_variant>
      - variant=<adopted_variant>
    Each row contains metrics vs the same manual file.
    """
    header = [
        "group_id",
        "variant",
        "candidate_file",
        "manual_file",

        "token_jaccard",
        "shingle_jaccard_k5",
        "lcs_token_ratio",
        "codebleu",
        "closeness_score",

        "assertion_conformance",
        "naming_style_conformance",
        "naming_clarity",
        "naming_conformance",
        "fixture_reuse",
        "import_conformance",
        "conformance_score",

        "style_distance_to_manual",

        "cyclo_avg",
        "avg_method_loc",
        "loc",
        "methods",
    ]

    def row_for(variant: str):
        if variant == auto_variant:
            sim = rep.auto_vs_manual
            cb = rep.codebleu_auto_vs_manual
            clos = rep.closeness_score_auto
            style_dist = rep.style_distance_auto_to_manual
            qual = rep.quality_auto
            conv = rep.conventions_auto
            cand = auto_path
        else:
            sim = rep.adopted_vs_manual
            cb = rep.codebleu_adopted_vs_manual
            clos = rep.closeness_score_adopted
            style_dist = rep.style_distance_adopted_to_manual
            qual = rep.quality_adopted
            conv = rep.conventions_adopted
            cand = adopted_path

        return [
            group_id,
            variant,
            cand,
            manual_path,

            sim.token_set_jaccard,
            sim.shingle_jaccard_k5,
            sim.lcs_token_ratio,
            cb,
            clos,

            getattr(conv, "assertion_conformance", None),
            getattr(conv, "naming_style_conformance", None),
            getattr(conv, "naming_clarity", None),
            getattr(conv, "naming_conformance", None),
            getattr(conv, "fixture_reuse", None),
            getattr(conv, "import_conformance", None),
            getattr(conv, "conformance_score", None),

            style_dist,

            qual.cyclomatic_avg,
            qual.avg_method_loc,
            qual.loc,
            qual.methods,
        ]

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(header)

        if include_auto:
            writer.writerow(row_for(auto_variant))
        writer.writerow(row_for(adopted_variant))
