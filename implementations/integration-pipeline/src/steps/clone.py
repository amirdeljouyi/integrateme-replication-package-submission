from __future__ import annotations

import csv
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


SKIP_REPOS = {
    "openjdk/jdk",
    "bazelbuild/bazel",
    "seleniumhq/selenium",
    "hibernate/hibernate-orm",
}


class CmdError(RuntimeError):
    def __init__(self, cmd: List[str], rc: int, out: str):
        super().__init__(f"Command failed ({rc}): {' '.join(cmd)}\n{out}")
        self.cmd = cmd
        self.rc = rc
        self.out = out


@dataclass(frozen=True)
class CloneConfig:
    cut_csv: Path
    mode: str = "local"
    base_dir: Path = Path(".")
    update_existing: bool = False
    log_dir: Optional[Path] = None
    out_repos_csv: Optional[Path] = None


def _which_or_fail(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required tool not found on PATH: {name}")


def _safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)


def _run(cmd: List[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> str:
    cmd_str = " ".join(cmd)
    print(f"\n>> {cmd_str}")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    combined = out + (("\n" if out and not out.endswith("\n") else "") + err if err else "")
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    if err:
        print(err, end="" if err.endswith("\n") else "\n")
    if proc.returncode != 0:
        raise CmdError(cmd, proc.returncode, combined)
    return combined


def _setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("integration_pipeline.clone")
    logger.setLevel(logging.DEBUG)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    info_fh = logging.FileHandler(log_dir / "info.log", encoding="utf-8")
    info_fh.setLevel(logging.INFO)
    info_fh.setFormatter(fmt)

    err_fh = logging.FileHandler(log_dir / "error.log", encoding="utf-8")
    err_fh.setLevel(logging.ERROR)
    err_fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(info_fh)
    logger.addHandler(err_fh)
    logger.addHandler(ch)
    return logger


def _git_clone_or_fetch(base_dir: Path, repo: str, update_existing: bool = False) -> Path:
    url = f"https://github.com/{repo}.git"
    local = base_dir / _safe_name(repo)

    if local.exists():
        print(f"[OK] Already cloned: {repo} -> {local}")
        if update_existing:
            _run(["git", "fetch", "--all", "--tags", "--prune"], cwd=local)
            try:
                _run(["git", "reset", "--hard", "origin/HEAD"], cwd=local)
            except CmdError:
                pass
        return local

    _run(["git", "clone", "--depth", "1", url, str(local)])
    return local


def run_clone(config: CloneConfig) -> int:
    mode = (config.mode or "local").strip()
    if mode not in {"local", "docker"}:
        print(f"[agt] clone: FAIL (unsupported mode: {mode})")
        return 1

    base = Path("/work") if mode == "docker" else config.base_dir.resolve()
    repos_dir = base / "repos"
    out_dir = base / "out"
    repos_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    log_dir = config.log_dir.resolve() if config.log_dir else (out_dir / "logs-clone")
    logger = _setup_logging(log_dir)
    logger.info("Starting integration pipeline clone step")
    logger.info("Config: %s", config)

    if mode == "local":
        try:
            _which_or_fail("git")
        except Exception as exc:
            logger.error("Missing required binary: %s", exc)
            print(f"[agt] clone: FAIL ({exc})")
            return 1

    cut_csv = config.cut_csv.resolve()
    if not cut_csv.exists():
        print(f"[agt] clone: FAIL (missing cut csv): {cut_csv}")
        return 1

    repos: List[str] = []
    seen = set()
    with cut_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            repo = (row.get("repo") or "").strip()
            if repo and repo not in seen:
                seen.add(repo)
                repos.append(repo)

    if not repos:
        print("[agt] clone: FAIL (no repos found in selected CUT csv)")
        return 1

    repo_roots: Dict[str, str] = {}
    for repo in repos:
        if repo in SKIP_REPOS:
            print(f"[SKIP-REPO] {repo}")
            repo_roots[repo] = "SKIP-REPO"
            continue
        try:
            root = _git_clone_or_fetch(repos_dir, repo, update_existing=config.update_existing)
            repo_roots[repo] = str(root)
            logger.info("[OK] %s -> %s", repo, root)
        except Exception as exc:
            repo_roots[repo] = "FAIL"
            logger.error("[FAIL] %s -> %s", repo, exc)

    out_csv = config.out_repos_csv.resolve() if config.out_repos_csv else (out_dir / "repo_roots.csv")
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["repo", "repo_root"])
        writer.writeheader()
        for repo in repos:
            writer.writerow({"repo": repo, "repo_root": repo_roots.get(repo, "FAIL")})

    print("\n===== DONE (CLONE) =====")
    print(f"Repos dir: {repos_dir}")
    print(f"Repo roots CSV: {out_csv}")
    print(f"Logs: {log_dir}")
    return 0
