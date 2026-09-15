#!/usr/bin/env python3
import csv
import re
import sys
from pathlib import Path

def safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)

def main():
    if len(sys.argv) < 4:
        print("Usage: make_repo_roots.py <cut_to_fatjar_map.csv> <repos_dir> <out_repo_roots.csv>", file=sys.stderr)
        sys.exit(2)

    cut_map_csv = Path(sys.argv[1]).resolve()
    repos_dir = Path(sys.argv[2]).resolve()
    out_csv = Path(sys.argv[3]).resolve()

    if not cut_map_csv.exists():
        raise FileNotFoundError(cut_map_csv)
    if not repos_dir.exists():
        raise FileNotFoundError(repos_dir)

    repos = set()
    with cut_map_csv.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if "repo" not in (r.fieldnames or []):
            raise RuntimeError(f"{cut_map_csv} does not have a 'repo' column.")
        for row in r:
            repo = (row.get("repo") or "").strip()
            if repo:
                repos.add(repo)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "repo_root"])
        w.writeheader()
        for repo in sorted(repos):
            repo_root = repos_dir / safe_name(repo)
            w.writerow({"repo": repo, "repo_root": str(repo_root)})

    print(f"[OK] Wrote {out_csv} with {len(repos)} repos.")

if __name__ == "__main__":
    main()