import base64
import re
from math import nan

import pandas as pd
from tqdm import tqdm
import requests

from github_tokens import load_github_tokens

tqdm.pandas()

# ============================================================
# CONFIG
# ============================================================

INPUT_CSV = "./data/selected_projects_add.csv"
OUTPUT_CSV = "./data/selected_projects_java.csv"

GITHUB_TOKENS = load_github_tokens()

REST_URL_BASE = "https://api.github.com"
_token_index = 0


def get_next_token():
    """Return the next token in a round-robin fashion."""
    global _token_index
    token = GITHUB_TOKENS[_token_index]
    _token_index = (_token_index + 1) % len(GITHUB_TOKENS)
    return token


# ============================================================
# Java version extraction helpers
# ============================================================

def extract_java_versions_from_text(text: str):
    """
    Extract Java source and target versions from:
      - maven.compiler.source / maven.compiler.target / maven.compiler.release
      - compiler.source / compiler.target (custom property names)
      - java.version
      - Gradle sourceCompatibility/targetCompatibility/JavaLanguageVersion.of
      - property references like ${java.version}

    Returns (source_version, target_version) or (None, None).
    """
    # 1. Extract <properties> block (optional)
    props = {}
    props_block = re.search(r"<properties>(.*?)</properties>", text, re.DOTALL)
    if props_block:
        for m in re.finditer(r"<([^/>]+)>([^<]+)</\1>", props_block.group(1)):
            key = m.group(1).strip()
            val = m.group(2).strip()
            props[key] = val

    def resolve_prop(key: str):
        val = props.get(key)
        if not val:
            return None
        if val.startswith("${") and val.endswith("}"):
            inner = val[2:-1]
            return props.get(inner, val)
        return val

    source_candidates = []
    target_candidates = []

    # From properties
    for k in ["maven.compiler.source", "compiler.source", "java.version", "maven.compiler.release"]:
        v = resolve_prop(k)
        if v:
            source_candidates.append(v)

    for k in ["maven.compiler.target", "compiler.target", "java.version", "maven.compiler.release"]:
        v = resolve_prop(k)
        if v:
            target_candidates.append(v)

    # Direct XML tags
    patterns_source = [
        r"<maven\.compiler\.source>([\d\.]+)</maven\.compiler\.source>",
        r"<compiler\.source>([\d\.]+)</compiler\.source>",
        r"<source>([\d\.]+)</source>",
        r"<java\.version>([\d\.]+)</java\.version>",
        r"<maven\.compiler\.release>([\d\.]+)</maven\.compiler\.release>",
        r"<release>([\d\.]+)</release>",
    ]
    patterns_target = [
        r"<maven\.compiler\.target>([\d\.]+)</maven\.compiler\.target>",
        r"<compiler\.target>([\d\.]+)</compiler\.target>",
        r"<target>([\d\.]+)</target>",
        r"<java\.version>([\d\.]+)</java\.version>",
        r"<maven\.compiler\.release>([\d\.]+)</maven\.compiler\.release>",
        r"<release>([\d\.]+)</release>",
    ]

    for pat in patterns_source:
        m = re.search(pat, text)
        if m:
            source_candidates.append(m.group(1))
    for pat in patterns_target:
        m = re.search(pat, text)
        if m:
            target_candidates.append(m.group(1))

    # Gradle-style
    gradle_source_patterns = [
        r"sourceCompatibility\s*=\s*['\"]?([\d\.]+)['\"]?",
        r"JavaLanguageVersion\.of\((\d+)\)",
        r"java-version:\s*['\"]?(\d+)['\"]?",
    ]
    gradle_target_patterns = [
        r"targetCompatibility\s*=\s*['\"]?([\d\.]+)['\"]?",
        r"JavaLanguageVersion\.of\((\d+)\)",
        r"java-version:\s*['\"]?(\d+)['\"]?",
    ]

    for pat in gradle_source_patterns:
        m = re.search(pat, text)
        if m:
            source_candidates.append(m.group(1))
    for pat in gradle_target_patterns:
        m = re.search(pat, text)
        if m:
            target_candidates.append(m.group(1))

    src = source_candidates[0] if source_candidates else None
    tgt = target_candidates[0] if target_candidates else None

    # If only one is known, mirror it
    if src is None and tgt is not None:
        src = tgt
    if tgt is None and src is not None:
        tgt = src

    return src, tgt


def detect_java_versions(full_name: str):
    """
    Try to detect project's Java source and target versions from:
      - pom.xml
      - build.gradle / build.gradle.kts
      - .java-version
      - GitHub workflow files (.github/workflows/*.yml/.yaml)

    Returns (java_source_version, java_target_version) or (None, None).
    """
    if "/" not in full_name:
        return None, None

    owner, repo = full_name.split("/", 1)

    # 1. Root-level files
    candidate_paths = [
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        ".java-version",
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
                print(f"[ERROR] Java version request failed for {full_name}:{path}: {e}")
                break

            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("type") == "file":
                    content_b64 = data.get("content")
                    if content_b64:
                        text = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
                        src, tgt = extract_java_versions_from_text(text)
                        if src or tgt:
                            return src, tgt
            if resp.status_code == 404:
                break

    # 2. GitHub workflows: actions/setup-java etc.
    for _ in range(len(GITHUB_TOKENS)):
        token = get_next_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(
                f"{REST_URL_BASE}/repos/{owner}/{repo}/contents/.github/workflows",
                headers=headers,
                timeout=20,
            )
        except Exception:
            break

        if resp.status_code != 200:
            break

        entries = resp.json()
        if not isinstance(entries, list):
            break

        for entry in entries:
            if entry.get("type") != "file":
                continue
            file_path = entry.get("path")
            resp2 = requests.get(
                f"{REST_URL_BASE}/repos/{owner}/{repo}/contents/{file_path}",
                headers=headers,
                timeout=20,
            )
            if resp2.status_code == 200:
                data = resp2.json()
                content_b64 = data.get("content", "")
                if not content_b64:
                    continue
                text = base64.b64decode(content_b64).decode("utf-8", errors="ignore")
                src, tgt = extract_java_versions_from_text(text)
                if src or tgt:
                    return src, tgt
        break

    return None, None


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    df = pd.read_csv(INPUT_CSV)

    if "Name" not in df.columns:
        raise ValueError("Expected a 'Name' column with values like 'owner/repo' in the CSV.")

    # If you re-run, don't recompute existing ones
    has_src_col = "java_source_version" in df.columns
    has_tgt_col = "java_target_version" in df.columns
    has_main_col = "java_version" in df.columns

    java_src = []
    java_tgt = []

    for full_name in tqdm(df["Name"], desc="Detecting Java versions"):
        src, tgt = detect_java_versions(full_name)
        java_src.append(src)
        java_tgt.append(tgt)

    # Add/overwrite columns
    df["java_source_version"] = java_src
    df["java_target_version"] = java_tgt

    # Convenience column: prefer target, fallback to source
    df["java_version"] = df["java_target_version"].fillna(df["java_source_version"])

    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Done. Java versions added to {OUTPUT_CSV}")
