from __future__ import annotations
import os
import re
import shutil
from pathlib import Path


def which_or_fail(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required tool not found on PATH: {name}")


def safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)


def ensure_executable(p: Path) -> None:
    if p.exists():
        try:
            p.chmod(p.stat().st_mode | 0o111)
        except Exception:
            pass


def set_java(env: dict, home: str) -> dict:
    """Return a copy of env with JAVA_HOME/PATH updated."""
    if not home:
        return env
    env = dict(env)
    env["JAVA_HOME"] = home
    env["PATH"] = str(Path(home) / "bin") + os.pathsep + env.get("PATH", "")
    return env