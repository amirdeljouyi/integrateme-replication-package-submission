from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModuleKey:
    repo: str
    tool: str
    module_rel: str


@dataclass(frozen=True)
class CutRow:
    repo: str
    class_path: str
    test_paths: str


@dataclass(frozen=True)
class CutRecord:
    repo: str
    class_path: str
    test_paths: str
    build_tool: str
    module_rel: str
    fqcn: str


@dataclass(frozen=True)
class RepoRoot:
    repo: str
    repo_root: Path