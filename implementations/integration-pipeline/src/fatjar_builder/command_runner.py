from __future__ import annotations
import subprocess
from pathlib import Path
from typing import List, Optional, Dict

from .exceptions import CmdError
from .logging_ctx import LoggingContext
from .utils import safe_name


class CommandRunner:
    def __init__(self, lc: LoggingContext):
        self.lc = lc

    @staticmethod
    def _write_text(p: Path, s: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(s or "", encoding="utf-8", errors="replace")

    def run(self, cmd: List[str], cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> str:
        seq = self.lc.next_seq()
        cmd_str = " ".join(cmd)
        print(f"\n>> {cmd_str}")

        if self.lc.logger:
            self.lc.logger.info("RUN[%04d]: %s (cwd=%s)", seq, cmd_str, str(cwd) if cwd else "")

        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        out = p.stdout or ""
        err = p.stderr or ""
        combined = out + (("\n" if out and not out.endswith("\n") else "") + err if err else "")

        if out:
            print(out, end="" if out.endswith("\n") else "\n")
        if err:
            print(err, end="" if err.endswith("\n") else "\n")

        base = self.lc.cmd_log_dir()
        first = safe_name(cmd[0]) if cmd else "cmd"
        last = safe_name(cmd[-1]) if cmd else "cmd"
        stem = f"{seq:04d}_{first}_{last}"
        self._write_text(base / f"{stem}.stdout.log", out)
        self._write_text(base / f"{stem}.stderr.log", err)
        self._write_text(base / f"{stem}.combined.log", combined)

        if p.returncode != 0:
            if self.lc.logger:
                tail = (err or combined)[-4000:]
                self.lc.logger.error("FAILED[%04d] rc=%d: %s", seq, p.returncode, cmd_str)
                self.lc.logger.error("---- tail ----\n%s\n------------", tail)
            raise CmdError(cmd, p.returncode, combined)

        if self.lc.logger:
            self.lc.logger.info("OK[%04d]: %s", seq, cmd_str)

        return combined