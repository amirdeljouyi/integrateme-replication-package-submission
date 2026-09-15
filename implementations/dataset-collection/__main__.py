import base64
from math import nan

import pandas as pd
import requests
from tqdm import tqdm

from github_tokens import load_github_tokens

tqdm.pandas()

# ---------------------------------------------------------------------
# 1. GitHub tokens & simple round-robin rotation
# ---------------------------------------------------------------------
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


# ---------------------------------------------------------------------
# 2. GraphQL query for repo metadata
# ---------------------------------------------------------------------
GRAPHQL_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
# Popularity
    stargazerCount
    forkCount
    watchers {
      totalCount
    }

    # Issues
    issues(states: OPEN) {
      totalCount
    }
    closedIssues: issues(states: CLOSED) {
      totalCount
    }

    # Pull Requests
    pullRequests(states: OPEN) {
      totalCount
    }
    closedPRs: pullRequests(states: CLOSED) {
      totalCount
    }
    mergedPRs: pullRequests(states: MERGED) {
      totalCount
    }

    # Releases
    releases {
      totalCount
    }

    # Size / language
    diskUsage
    primaryLanguage {
      name
    }

    # Repo status
    isPrivate
    isFork
    isArchived
    hasIssuesEnabled

    # Activity
    createdAt
    updatedAt
    pushedAt

    # Default branch & commit history
    defaultBranchRef {
      name
      target {
        ... on Commit {
          history {
            totalCount
          }
        }
      }
    }

    # Community / contributors
    mentionableUsers {
      totalCount
    }

    # Workflows (.github/workflows)
    workflowsDir: object(expression: "HEAD:.github/workflows") {
      ... on Tree {
        entries {
          name
        }
      }
    }
  }
}
"""


# ============================================================
# 3. Repo tree fetch (for counting src/main & src/test Java files)
#    IMPORTANT for "testing heaviness" and code size
# ============================================================
def fetch_repo_tree(full_name: str):
    """
    Fetch the full Git tree for HEAD (default branch HEAD ref) recursively.

    Uses REST: GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1

    Returns (tree_list, truncated_flag) or (None, None) on error.
    """
    if "/" not in full_name:
        print(f"[WARN] Invalid repo name (expected owner/repo): {full_name}")
        return None, None

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
            truncated = bool(data.get("truncated"))
            if truncated:
                print(f"[INFO] Tree for {full_name} is truncated; counts may be underestimated.")
            return tree, truncated

        elif resp.status_code == 404:
            print(f"[WARN] Tree not found for {full_name} (404).")
            return None, None

        else:
            print(
                f"[WARN] REST tree HTTP {resp.status_code} for {full_name}, "
                "trying next token if available."
            )
            continue

    print(f"[ERROR] All tokens failed for REST tree of {full_name}")
    return None, None


def count_java_tests_and_sources_from_tree(tree):
    """
    Count:
      - Java test files: any .java under a path whose directory component
        clearly looks like a test folder (test/, tests/, unit-tests/, etc.)
      - Java source files: any other .java under some src/ path.

    This works for:
      - Maven/Gradle: src/main/java, src/test/java
      - CoreNLP-style: src/..., test/src/...
      - Multi-module: moduleA/src/..., moduleA/test/src/...
    """
    if tree is None:
        return nan, nan

    test_count = 0
    src_count = 0

    for entry in tree:
        if entry.get("type") != "blob":
            continue

        path = entry.get("path", "")
        if not path.endswith(".java"):
            continue

        parts = path.split("/")
        lower_parts = [p.lower() for p in parts]

        # ----- decide if this is a TEST file -----
        return (
                part == "test"
                or part == "tests"
                or part == "javatests"
                or part.startswith("test-")
                or part.startswith("src.test")
                or part.startswith("src-tests")
                or part.startswith("src_tests")
                or part.endswith("-test")
                or part.endswith("-tests")
                or part in ("integration-tests", "unit-tests")
        )
        # More precise than "any 'test' substring" to avoid 'contest', etc.
        def is_test_folder(part: str) -> bool:
            pass

        is_test = any(is_test_folder(p) for p in lower_parts)

        if is_test:
            test_count += 1
            continue  # don't double-count as src

        # ----- otherwise, if it's under some src/ folder, treat as source -----
        # This covers:
        #   src/edu/stanford/nlp/...        (CoreNLP-style)
        #   moduleA/src/main/java/...
        #   moduleB/src/...
        if "src" in lower_parts:
            src_count += 1

    return test_count, src_count


# ============================================================
# 4. Workflow analysis
#    IMPORTANT: detect whether CI workflows are actually running tests
# ============================================================
def analyze_workflows(full_name: str, workflow_entries):
    """
    Given list of workflow entries (filenames from .github/workflows),
    fetch their contents and detect whether any workflow appears to run tests.

    Returns:
       has_test_in_name          -> any workflow filename contains 'test'
       content_mentions_test     -> workflow YAML text mentions test-related stuff
       workflow_runs_tests       -> OR of the above two
    """
    if not workflow_entries:
        return False, False, False

    owner, repo = full_name.split("/", 1)

    has_test_in_name = False
    content_mentions_test = False

    # filenames like test.yml, unit-tests.yml, ci-test.yaml, etc.
    name_keywords = ["test"]

    # text-level heuristics in workflow YAML
    content_keywords = [
        " test", "tests", "unit test", "integration test",
        "mvn test", "mvn verify",
        "gradle test", "./gradlew test", "./gradlew check",
        "pytest", "npm test", "yarn test"
    ]

    for entry in workflow_entries:
        filename = entry.get("name", "")
        if not filename:
            continue

        filename_lower = filename.lower()

        # --- filename heuristic ---
        if any(k in filename_lower for k in name_keywords):
            has_test_in_name = True

        # Only bother with typical workflow files
        if not (filename_lower.endswith(".yml") or filename_lower.endswith(".yaml")):
            continue

        path = f".github/workflows/{filename}"

        # fetch each workflow file via REST
        for _ in range(len(GITHUB_TOKENS)):
            token = get_next_token()
            headers = {"Authorization": f"Bearer {token}"}
            try:
                resp = requests.get(
                    f"{REST_URL_BASE}/repos/{owner}/{repo}/contents/{path}",
                    headers=headers,
                    timeout=30,
                )
            except Exception as e:
                print(f"[ERROR] Workflow contents request failed for {full_name}:{path}: {e}")
                break

            if resp.status_code != 200:
                # try next token
                continue

            try:
                data = resp.json()
            except Exception as e:
                print(f"[WARN] Could not parse JSON for workflow {full_name}:{path}: {e}")
                break

            # If this is a directory, GitHub returns a list -> skip it
            if isinstance(data, list):
                # directory listing, not a single file; ignore for now
                break

            # Expecting a file object here
            content_b64 = data.get("content")
            if not content_b64:
                break

            try:
                decoded = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
            except Exception as e:
                print(f"[WARN] Could not decode workflow {full_name}:{path}: {e}")
                break

            text_lower = decoded.lower()
            if any(k in text_lower for k in content_keywords):
                content_mentions_test = True

            # we successfully processed this workflow file, go to next entry
            break

    workflow_runs_tests = has_test_in_name or content_mentions_test
    return has_test_in_name, content_mentions_test, workflow_runs_tests


# ============================================================
# 5. CONTRIBUTING.md detection
# ============================================================
def has_contributing_file(full_name: str):
    """
    Check whether the repository has a CONTRIBUTING.md file.
    We search several common locations:
      - CONTRIBUTING.md
      - contributing.md
      - .github/CONTRIBUTING.md
      - .github/contributing.md
      - docs/CONTRIBUTING.md
      - docs/contributing.md
    """
    if "/" not in full_name:
        return False

    owner, repo = full_name.split("/", 1)

    candidate_paths = [
        "CONTRIBUTING.md",
        "contributing.md",
        ".github/CONTRIBUTING.md",
        ".github/contributing.md",
        "docs/CONTRIBUTING.md",
        "docs/contributing.md",
    ]

    for path in candidate_paths:
        for _ in range(len(GITHUB_TOKENS)):
            token = get_next_token()
            headers = {"Authorization": f"Bearer {token}"}

            try:
                resp = requests.get(
                    f"{REST_URL_BASE}/repos/{owner}/{repo}/contents/{path}",
                    headers=headers,
                    timeout=20,
                )
            except Exception as e:
                print(f"[ERROR] CONTRIBUTING request failed for {full_name}:{path}: {e}")
                break

            if resp.status_code == 200:
                try:
                    if resp.json().get("type") == "file":
                        return True
                except Exception:
                    continue

            if resp.status_code == 404:
                # file doesn't exist in this location, try next path
                break

            # otherwise, try next token
            continue

    return False


# ============================================================
# 6. Unique PR authors via paginated GraphQL
# ============================================================
def get_unique_pr_authors(full_name: str):
    """
    Count unique pull request authors for a repository via GraphQL.
    Fetches PR data in pages of 100.
    Returns an integer count.
    """
    if "/" not in full_name:
        return nan

    owner, repo = full_name.split("/", 1)

    query = """
    query($owner: String!, $repo: String!, $cursor: String) {
      repository(owner: $owner, name: $repo) {
        pullRequests(first: 100, after: $cursor) {
          nodes {
            author {
              login
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    unique_authors = set()
    cursor = None

    while True:
        # one paging step; may rotate tokens if needed
        page_fetched = False

        for _ in range(len(GITHUB_TOKENS)):
            token = get_next_token()
            headers = {"Authorization": f"Bearer {token}"}

            try:
                resp = requests.post(
                    GRAPHQL_URL,
                    json={
                        "query": query,
                        "variables": {"owner": owner, "repo": repo, "cursor": cursor},
                    },
                    headers=headers,
                    timeout=30,
                )
            except Exception as e:
                print(f"[ERROR] PR authors request failed for {full_name}: {e}")
                return len(unique_authors)

            if resp.status_code != 200:
                # try next token
                continue

            data = resp.json()
            if "errors" in data:
                # could be rate limit or inaccessible repo
                print(f"[WARN] PR authors GraphQL error for {full_name}: {data['errors'][0].get('message', '')}")
                continue

            pr_data = data.get("data", {}).get("repository", {}).get("pullRequests")
            if pr_data is None:
                return len(unique_authors)

            for node in pr_data.get("nodes", []):
                author = node.get("author")
                if author and "login" in author:
                    unique_authors.add(author["login"])

            page = pr_data.get("pageInfo", {})
            has_next = page.get("hasNextPage")
            cursor = page.get("endCursor")
            page_fetched = True
            break  # break token-rotation loop; go to next page or finish

        if not page_fetched:
            # all tokens failed for this page
            return len(unique_authors)

        if not has_next:
            return len(unique_authors)


# ============================================================
# 7. GraphQL + REST: full metadata fetcher for a repo
# ============================================================
def fetch_repo_metadata(full_name: str):
    """
    Fetch metadata for a repo using GitHub GraphQL and count Java test & source files.

    full_name: 'owner/repo'

    Returns a dict with:
      - Popularity: stars, forks, watchers
      - Issues: open_issues, closed_issues, total_issues
      - PRs: open_pull_requests, closed_pull_requests, merged_pull_requests, total_pull_requests
      - Releases: releases_count
      - Size & language: size_kb, language
      - Status: is_private, is_fork, is_archived, has_issues
      - Activity: created_at, updated_at, pushed_at
      - History: default_branch, commit_count
      - Community: mentionable_users, unique_pr_authors
      - CI/Tests: has_actions, workflow_has_test_in_name, workflow_content_mentions_test, workflow_runs_tests
      - Maturity: has_contributing
      - Testing heaviness: test_files (tests), src_files (source)
    """
    default_result = {
        "stars": nan,
        "forks": nan,
        "watchers": nan,

        "open_issues": nan,
        "closed_issues": nan,
        "total_issues": nan,

        "open_pull_requests": nan,
        "closed_pull_requests": nan,
        "merged_pull_requests": nan,
        "total_pull_requests": nan,

        "releases_count": nan,

        "size_kb": nan,
        "language": None,
        "is_private": nan,
        "is_fork": nan,
        "is_archived": nan,
        "has_issues": nan,

        "created_at": None,
        "updated_at": None,
        "pushed_at": None,

        "default_branch": None,
        "commit_count": nan,

        "mentionable_users": nan,
        "unique_pr_authors": nan,

        "has_actions": nan,
        "workflow_has_test_in_name": nan,
        "workflow_content_mentions_test": nan,
        "workflow_runs_tests": nan,

        "has_contributing": nan,

        "test_files": nan,
        "src_files": nan,
    }

    if "/" not in full_name:
        print(f"[WARN] Invalid repo name (expected owner/repo): {full_name}")
        return default_result

    owner, name = full_name.split("/", 1)
    variables = {"owner": owner, "name": name}

    # ---------- GraphQL: core repo metadata ----------
    repo = None
    for _ in range(len(GITHUB_TOKENS)):
        token = get_next_token()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            resp = requests.post(
                GRAPHQL_URL,
                json={"query": GRAPHQL_QUERY, "variables": variables},
                headers=headers,
                timeout=30,
            )
        except Exception as e:
            print(f"[ERROR] GraphQL request failed for {full_name} with token: {e}")
            continue

        if resp.status_code != 200:
            print(
                f"[WARN] GraphQL HTTP {resp.status_code} for {full_name}, "
                "trying next token if available."
            )
            continue

        data = resp.json()

        if "errors" in data:
            msg = data["errors"][0].get("message", "")
            print(f"[WARN] GraphQL error for {full_name}: {msg}")
            if "rate limit" in msg.lower():
                # try next token
                continue
            return default_result

        repo = data.get("data", {}).get("repository")
        break

    if repo is None:
        print(f"[WARN] Repository not found or inaccessible: {full_name}")
        return default_result

    # ---------- Extract fields ----------
    # Popularity
    stars = repo.get("stargazerCount")
    forks = repo.get("forkCount")
    watchers = repo.get("watchers", {}).get("totalCount")

    # Issues
    open_issues = repo.get("issues", {}).get("totalCount")
    closed_issues = repo.get("closedIssues", {}).get("totalCount")
    total_issues = (open_issues or 0) + (closed_issues or 0)

    # PRs
    open_prs = repo.get("pullRequests", {}).get("totalCount")
    closed_prs = repo.get("closedPRs", {}).get("totalCount")
    merged_prs = repo.get("mergedPRs", {}).get("totalCount")
    total_prs = (open_prs or 0) + (closed_prs or 0) + (merged_prs or 0)

    # Releases
    releases_count = repo.get("releases", {}).get("totalCount")

    # Size & language
    size_kb = repo.get("diskUsage")
    language = (repo.get("primaryLanguage") or {}).get("name")

    # Repo status
    is_private = repo.get("isPrivate")
    is_fork = repo.get("isFork")
    is_archived = repo.get("isArchived")
    has_issues = repo.get("hasIssuesEnabled")

    # Activity
    created_at = repo.get("createdAt")
    updated_at = repo.get("updatedAt")
    pushed_at = repo.get("pushedAt")

    # Default branch & commit history
    default_branch_ref = repo.get("defaultBranchRef") or {}
    default_branch = default_branch_ref.get("name")

    commit_count = nan
    target = default_branch_ref.get("target")
    if target and isinstance(target, dict):
        history = target.get("history") or {}
        commit_count = history.get("totalCount", nan)

    # Community
    mentionable_users = repo.get("mentionableUsers", {}).get("totalCount")

    # Workflows
    workflows_dir = repo.get("workflowsDir")
    workflow_entries = workflows_dir.get("entries") if workflows_dir else None
    has_actions = bool(workflow_entries)

    workflow_has_test_in_name, workflow_content_mentions_test, workflow_runs_tests = \
        analyze_workflows(full_name, workflow_entries)

    # REST: count Java test & source files
    tree, _truncated = fetch_repo_tree(full_name)
    test_files, src_files = count_java_tests_and_sources_from_tree(tree)

    # CONTRIBUTING
    has_contributing = has_contributing_file(full_name)

    # Unique PR authors
    unique_pr_authors = get_unique_pr_authors(full_name)

    return {
        "stars": stars,
        "forks": forks,
        "watchers": watchers,

        "open_issues": open_issues,
        "closed_issues": closed_issues,
        "total_issues": total_issues,

        "open_pull_requests": open_prs,
        "closed_pull_requests": closed_prs,
        "merged_pull_requests": merged_prs,
        "total_pull_requests": total_prs,

        "releases_count": releases_count,

        "size_kb": size_kb,
        "language": language,
        "is_private": is_private,
        "is_fork": is_fork,
        "is_archived": is_archived,
        "has_issues": has_issues,

        "created_at": created_at,
        "updated_at": updated_at,
        "pushed_at": pushed_at,

        "default_branch": default_branch,
        "commit_count": commit_count,

        "mentionable_users": mentionable_users,
        "unique_pr_authors": unique_pr_authors,

        "has_actions": has_actions,
        "workflow_has_test_in_name": workflow_has_test_in_name,
        "workflow_content_mentions_test": workflow_content_mentions_test,
        "workflow_runs_tests": workflow_runs_tests,

        "has_contributing": has_contributing,

        "test_files": test_files,
        "src_files": src_files,
    }


# ============================================================
# 8. Main script
# ============================================================
if __name__ == "__main__":
    # expects a column 'Name' with values like 'apache/accumulo'
    INPUT_CSV = "./data/output.csv"
    OUTPUT_CSV = "./data/selected_projects_add.csv"

    df = pd.read_csv(INPUT_CSV)

    if "Name" not in df.columns:
        raise ValueError("Expected a 'Name' column with values like 'owner/repo'.")

    meta_series = df["Name"].progress_apply(fetch_repo_metadata)
    meta_df = pd.DataFrame(list(meta_series))

    df_merged = pd.concat([df, meta_df], axis=1)
    df_merged.to_csv(OUTPUT_CSV, index=False)
    print(f"Done. Saved enriched data to {OUTPUT_CSV}")
