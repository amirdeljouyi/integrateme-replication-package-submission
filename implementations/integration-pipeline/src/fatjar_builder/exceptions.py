from __future__ import annotations
from typing import List


class CmdError(RuntimeError):
    def __init__(self, cmd: List[str], rc: int, out: str):
        super().__init__(f"Command failed ({rc}): {' '.join(cmd)}\n{out}")
        self.cmd = cmd
        self.rc = rc
        self.out = out


class SkipBuild(RuntimeError):
    """Signal: skip this module/repo build (unsupported build, requires newer JDK, etc.)."""
    pass