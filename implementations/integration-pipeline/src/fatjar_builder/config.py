from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set


DEFAULT_SKIP_REPOS: Set[str] = {
    "openjdk/jdk",
    "bazelbuild/bazel",
    "seleniumhq/selenium",
    "hibernate/hibernate-orm",
}


@dataclass(frozen=True)
class BuildConfig:
    cut_csv: Path
    mode: str = "local"          # "local" | "docker"
    base_dir: Path = Path(".")   # used only for local
    java_home: str = ""
    java21_home: str = ""
    log_dir: Optional[Path] = None
    repos_csv: Optional[Path] = None
    out_map_csv: Optional[Path] = None
    failures_csv: Optional[Path] = None
    retry_only: bool = False
    skip_repos: Set[str] = field(default_factory=lambda: set(DEFAULT_SKIP_REPOS))

    def base(self) -> Path:
        return Path("/work") if self.mode == "docker" else self.base_dir.resolve()

    def repos_dir(self) -> Path:
        return self.base() / "repos"

    def out_dir(self) -> Path:
        return self.base() / "out"

    def cache_dir(self) -> Path:
        return self.base() / ".cache"

    def resolved_log_dir(self) -> Path:
        return self.log_dir.resolve() if self.log_dir else (self.out_dir() / "logs-build")

    def resolved_repos_csv(self) -> Path:
        return self.repos_csv.resolve() if self.repos_csv else (self.out_dir() / "repo_roots.csv")

    def resolved_out_map_csv(self) -> Path:
        return self.out_map_csv.resolve() if self.out_map_csv else (self.out_dir() / "cut_to_fatjar_map.csv")