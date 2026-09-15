from __future__ import annotations
from pathlib import Path
from typing import List


class RepoScanner:
    @staticmethod
    def _is_ignored_path(p: Path) -> bool:
        s = str(p).replace("\\", "/")
        return (
            "/.git/" in s or "/target/" in s or "/build/" in s or "/out/" in s or
            "/node_modules/" in s or "/.gradle/" in s or "/.idea/" in s
        )

    def find_all_files(self, repo_root: Path, names: List[str]) -> List[Path]:
        out: List[Path] = []
        for n in names:
            for p in repo_root.rglob(n):
                if p.is_file() and not self._is_ignored_path(p):
                    out.append(p)
        return out