import os
import re


def load_github_tokens():
    raw_tokens = os.environ.get("GITHUB_TOKENS", "")
    tokens = [token.strip() for token in re.split(r"[\n,]", raw_tokens) if token.strip()]

    single_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if single_token and single_token not in tokens:
        tokens.append(single_token)

    if not tokens:
        raise RuntimeError(
            "Set GITHUB_TOKENS (comma/newline-separated) or GITHUB_TOKEN before running this script."
        )

    return tokens
