import base64
import datetime as dt
from math import nan

import pandas as pd
import requests
from tqdm import tqdm

from github_tokens import load_github_tokens

tqdm.pandas()

# ============================================================
# 0. CONFIG
# ============================================================

TOP_PROJECTS_CSV = "./data/top_200_projects.csv"
OUTPUT_CSV = "./data/selected_cut_classes.csv"

NUM_TOP_PROJECTS = 100         # how many projects to consider
CUTS_PER_PROJECT = 2           # how many classes (CUTs) per project

MIN_COMMITS_PER_CLASS = 10     # "many commits" threshold
RECENT_DAYS_THRESHOLD = 730    # "recently updated": last commit <= 2 years ago

# ============================================================
# 1. GitHub tokens & simple round-robin rotation
# ============================================================

GITHUB_TOKENS = load_github_tokens()

GRAPHQL_URL = "https://api.github.com/graphql"
REST_URL_BASE = "https://api.github.com"
_token_index = 0


def get_next_token():
    """Return the next token in a round-robin fashion."""
    global _token_index
    token = GITHUB_TOKENS[_token_index]
    _token_index = (_token_index + 1) % len(GITHUB_TOKENS)
    return token


# ============================================================
# 2. Helper: fetch repo tree
# ============================================================

def fetch_repo_tree(full_name: str):
    """
    Fetch the full Git tree for HEAD (default branch HEAD ref) recursively.

    Uses REST: GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1

    Returns list of entries or None on error.
    """
    if "/" not in full_name:
        print(f"[WARN] Invalid repo name (expected owner/repo): {full_name}")
        return None

    owner, name = full_name.split("/", 1)
    path = f"/repos/{owner}/{name}/git/trees/HEAD"
    params = {"recursive": "1"}

    for _ in range(len(GITHUB_TOKENS)):
        token = get_next_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        try:
            resp = requests.get(
                REST_URL_BASE + path,
                headers=headers,
                params=params,
                timeout=60,
            )
        except Exception as e:
            print(f"[ERROR] REST tree request failed for {full_name}: {e}")
            continue

        if resp.status_code == 200:
            data = resp.json()
            tree = data.get("tree", [])
            if data.get("truncated"):
                print(f"[INFO] Tree for {full_name} is truncated; file-based stats may be underestimated.")
            return tree

        elif resp.status_code == 404:
            print(f"[WARN] Tree not found for {full_name} (404).")
            return None

        else:
            print(
                f"[WARN] REST tree HTTP {resp.status_code} for {full_name}, "
                "trying next token if available."
            )
            continue

    print(f"[ERROR] All tokens failed for REST tree of {full_name}")
    return None


# ============================================================
# 3. Helpers: classify source vs test files
#    (CoreNLP-style, BiglyBT-style, Maven/Gradle, etc.)
# ============================================================

def is_test_folder(part: str) -> bool:
    """
    Decide if a single path component represents a test folder.

    Examples treated as TEST:
      test, tests, unit-tests, integration-tests,
      src.test, src-tests, src_tests, test-core, core-tests, etc.
    """
    part = part.lower()
    if part in {"test", "tests", "integration-tests", "unit-tests"}:
        return True

    if part.startswith("test-") or part.startswith("tests-"):
        return True
    if part.endswith("-test") or part.endswith("-tests"):
        return True

    # BiglyBT-style and similar: src.test, src-tests, src_tests
    if part.startswith("src.test") or part.startswith("src-tests") or part.startswith("src_tests"):
        return True

    return False


def classify_java_files(tree):
    """
    From a Git tree, classify .java files into:
      - source_files: likely production classes
      - test_files: likely test classes

    Returns (source_files, test_files) as lists of paths.
    """
    if tree is None:
        return [], []

    source_files = []
    test_files = []

    for entry in tree:
        if entry.get("type") != "blob":
            continue

        path = entry.get("path", "")
        if not path.endswith(".java"):
            continue

        parts = path.split("/")
        lower_parts = [p.lower() for p in parts]

        # Test?
        if any(is_test_folder(p) for p in lower_parts):
            test_files.append(path)
            continue

        # Otherwise treat as source if under some 'src'
        if "src" in lower_parts:
            source_files.append(path)

    return source_files, test_files


# ============================================================
# 4. Match source classes to test classes by naming
# ============================================================

import os


def find_tests_for_source(source_path: str, test_paths):
    """
    Given a source file path and list of test file paths,
    try to find matching tests by naming conventions.

    E.g., Foo.java -> FooTest.java, FooTests.java, TestFoo.java, FooIT.java
    """
    src_base = os.path.splitext(os.path.basename(source_path))[0]  # Foo
    candidate_names = {
        f"{src_base}Test",
        f"{src_base}Tests",
        f"{src_base}IT",
        f"Test{src_base}",
    }

    matches = []
    for tpath in test_paths:
        tbase = os.path.splitext(os.path.basename(tpath))[0]
        if tbase in candidate_names:
            matches.append(tpath)

    return matches


# ============================================================
# 5. Per-file commit stats (REST: /commits)
# ============================================================

def get_file_commit_stats(full_name: str, file_path: str):
    """
    Use REST API to fetch commits that touch file_path:

      GET /repos/{owner}/{repo}/commits?path=<file_path>&per_page=100

    Returns (commit_count, last_commit_date) where:
      - commit_count <= 100 (but ">=100" signals heavy dev)
      - last_commit_date: datetime of most recent commit touching this file
    """
    if "/" not in full_name:
        return 0, None

    owner, repo = full_name.split("/", 1)
    url = f"{REST_URL_BASE}/repos/{owner}/{repo}/commits"
    params = {
        "path": file_path,
        "per_page": 100,
    }

    for _ in range(len(GITHUB_TOKENS)):
        token = get_next_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=60)
        except Exception as e:
            print(f"[ERROR] Commits request failed for {full_name}:{file_path}: {e}")
            continue

        if resp.status_code != 200:
            # try next token
            continue

        commits = resp.json()
        if not isinstance(commits, list) or len(commits) == 0:
            return 0, None

        commit_count = len(commits)
        # commits are in reverse chrono order -> first is latest
        latest = commits[0]
        date_str = latest.get("commit", {}).get("author", {}).get("date")
        try:
            last_date = dt.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            last_date = None

        return commit_count, last_date

    # all tokens failed
    return 0, None


# ============================================================
# 6. Main per-project CUT selection logic
# ============================================================

def select_cuts_for_project(full_name: str):
    """
    For a single project (owner/repo), select up to CUTS_PER_PROJECT classes
    that:
      - are Java source files
      - have at least one matching test class
      - have many commits
      - have recent commits

    Returns a list of dicts with info for each selected CUT.
    """
    tree = fetch_repo_tree(full_name)
    source_files, test_files = classify_java_files(tree)

    if not source_files or not test_files:
        print(f"[INFO] No suitable source/test files for {full_name}")
        return []

    now = dt.datetime.now(dt.timezone.utc)
    cutoff_date = now - dt.timedelta(days=RECENT_DAYS_THRESHOLD)

    candidates = []

    for src_path in source_files:
        tests = find_tests_for_source(src_path, test_files)
        if not tests:
            continue  # no matching test class

        commit_count, last_date = get_file_commit_stats(full_name, src_path)
        if last_date is None:
            continue

        # Filter by activity: minimum commits & recent
        if commit_count < MIN_COMMITS_PER_CLASS:
            continue
        if last_date < cutoff_date:
            continue

        candidates.append({
            "repo": full_name,
            "class_path": src_path,
            "test_paths": ";".join(tests),
            "commit_count": commit_count,
            "last_commit_date": last_date.isoformat(),
        })

    if not candidates:
        print(f"[WARN] No active tested classes found for {full_name}")
        return []

    # Rank candidates: more commits, then more recent
    candidates.sort(
        key=lambda c: (c["commit_count"], c["last_commit_date"]),
        reverse=True,
    )

    # Pick top K
    return candidates[:CUTS_PER_PROJECT]


# ============================================================
# 7. Driver: apply to top N projects
# ============================================================

if __name__ == "__main__":
    # Load top projects (assumes Name column exists and it's sorted by overall_score)
    df_top = pd.read_csv(TOP_PROJECTS_CSV)

    if "Name" not in df_top.columns:
        raise ValueError("Expected 'Name' column in TOP_PROJECTS_CSV with values like 'owner/repo'.")

    # Sort by overall_score if present, then pick top N
    if "overall_score" in df_top.columns:
        df_top = df_top.sort_values("overall_score", ascending=False)

    df_top_n = df_top.head(NUM_TOP_PROJECTS)

    all_cuts = []

    for full_name in tqdm(df_top_n["Name"], desc="Selecting CUTs per project"):
        cuts = select_cuts_for_project(full_name)
        all_cuts.extend(cuts)

    df_cuts = pd.DataFrame(all_cuts)
    df_cuts.to_csv(OUTPUT_CSV, index=False)
    print(f"Done. Saved selected CUT classes to {OUTPUT_CSV}")
