from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from .utils import safe_name


@dataclass
class Context:
    repo: str = "unknown_repo"
    module_rel: str = "unknown_module"


class LoggingContext:
    def __init__(self, log_base: Path):
        self.log_base = log_base
        self.logger: Optional[logging.Logger] = None
        self.ctx = Context()
        self.run_seq = 0

    def setup(self) -> logging.Logger:
        self.log_base.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("builder")
        logger.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        info_fh = logging.FileHandler(self.log_base / "info.log", encoding="utf-8")
        info_fh.setLevel(logging.INFO)
        info_fh.setFormatter(fmt)

        err_fh = logging.FileHandler(self.log_base / "error.log", encoding="utf-8")
        err_fh.setLevel(logging.ERROR)
        err_fh.setFormatter(fmt)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)

        if not logger.handlers:
            logger.addHandler(info_fh)
            logger.addHandler(err_fh)
            logger.addHandler(ch)

        self.logger = logger
        return logger

    def set_context(self, repo: str, module_rel: str) -> None:
        self.ctx.repo = repo or "unknown_repo"
        self.ctx.module_rel = module_rel or "root"

    def next_seq(self) -> int:
        self.run_seq += 1
        return self.run_seq

    def cmd_log_dir(self) -> Path:
        return self.log_base / safe_name(self.ctx.repo) / safe_name(self.ctx.module_rel)