from __future__ import annotations

import csv
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import traceback


@dataclass
class PMDResult:
    violations: int
    by_rule: Dict[str, int]


@dataclass
class CPDResult:
    duplicate_blocks: int
    duplicate_lines_total: int


def _run_cmd(cmd: List[str], ok_codes: Tuple[int, ...]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode not in ok_codes:
        raise RuntimeError(
            f"Command failed ({p.returncode}): {' '.join(cmd)}\n"
            f"STDERR:\n{p.stderr}\nSTDOUT:\n{p.stdout}\n"
        )
    if not p.stdout.strip():
        raise RuntimeError(f"Tool produced no stdout.\nSTDERR:\n{p.stderr}")
    return p.stdout


def run_pmd(pmd_bin: Path, ruleset: Path, java_file: Path) -> PMDResult:
    xml_out = _run_cmd(
        [str(pmd_bin), "check", "-d", str(java_file), "-R", str(ruleset), "-f", "xml"],
        ok_codes=(0, 1, 4),
    )
    root = ET.fromstring(xml_out)

    violations = 0
    by_rule: Dict[str, int] = {}
    for v in root.findall(".//violation"):
        violations += 1
        rule = v.attrib.get("rule", "UNKNOWN")
        by_rule[rule] = by_rule.get(rule, 0) + 1

    return PMDResult(violations=violations, by_rule=by_rule)


def run_cpd(pmd_bin: Path, files_dir: Path, minimum_tokens: int = 50) -> CPDResult:
    file_list = files_dir / "files.txt"
    java_files = sorted(files_dir.glob("*.java"))
    file_list.write_text("\n".join(str(p.resolve()) for p in java_files), encoding="utf-8")
    xml_out = _run_cmd(
        [
            str(pmd_bin),
            "cpd",
            "--language", "java",
            "--minimum-tokens", str(minimum_tokens),
            "--file-list", str(file_list),
            "--format", "xml",
        ],
        ok_codes=(0, 1, 4),
    )
    root = ET.fromstring(xml_out)
    dups = root.findall(".//duplication")
    duplicate_blocks = len(dups)
    duplicate_lines_total = 0
    for d in dups:
        duplicate_lines_total += int(d.attrib.get("lines", "0"))
    return CPDResult(duplicate_blocks=duplicate_blocks, duplicate_lines_total=duplicate_lines_total)


class CompareRunner:
    def __init__(self, comparison_root: Path, vendor_root: Path | None = None) -> None:
        self.comparison_root = comparison_root
        # Third-party binaries live under vendor/ at the pipeline root, keeping
        # src/ to source. Pipeline-relative, like scripts/ and docs/.
        self.vendor_root = vendor_root or Path("vendor")
        self.pmd_bin = self.vendor_root / "pmd" / "pmd-bin-7.0.0" / "bin" / "pmd"
        self.ruleset = comparison_root / "configs" / "pmd-ruleset.xml"

    def _validate(self) -> None:
        if not self.pmd_bin.exists():
            raise FileNotFoundError(f"PMD bin not found: {self.pmd_bin}")
        if not self.ruleset.exists():
            raise FileNotFoundError(f"PMD ruleset not found: {self.ruleset}")

    def compare_metrics(
        self,
        agt_file: Path,
        adopted_file: Path,
        *,
        minimum_tokens: int = 50,
    ) -> Tuple[PMDResult, PMDResult, CPDResult]:
        self._validate()
        pmd_agt = run_pmd(self.pmd_bin, self.ruleset, agt_file)
        pmd_adopted = run_pmd(self.pmd_bin, self.ruleset, adopted_file)

        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            (tmp_dir / agt_file.name).write_text(agt_file.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            (tmp_dir / adopted_file.name).write_text(adopted_file.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
            cpd = run_cpd(self.pmd_bin, tmp_dir, minimum_tokens=minimum_tokens)

        return pmd_agt, pmd_adopted, cpd

    @staticmethod
    def run_tri_compare(
            *,
            tri_script: Path,
            group_id: str,
            auto_path: Path,
            adopted_path: Path,
            manual_path: Path,
            out_csv: Path,
            auto_variant: str = "auto",
            adopted_variant: str = "adopted",
            include_auto: bool = True,
    ) -> None:
        if not tri_script.exists():
            raise FileNotFoundError(f"tri_compare_tests.py not found: {tri_script}")
        spec = importlib.util.spec_from_file_location("tri_compare_tests", tri_script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Failed to load tri_compare_tests from {tri_script}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if hasattr(module, "safe_tri_compare"):
            rep = module.safe_tri_compare(str(auto_path), str(adopted_path), str(manual_path), lang="java")
        else:
            rep = module.tri_compare(str(auto_path), str(adopted_path), str(manual_path), lang="java")
        module.write_rows_csv(
            csv_path=str(out_csv),
            group_id=group_id,
            manual_path=str(manual_path),
            auto_path=str(auto_path),
            adopted_path=str(adopted_path),
            rep=rep,
            auto_variant=auto_variant,
            adopted_variant=adopted_variant,
            include_auto=include_auto,
        )


def run_tri_compare_with_log(
        *,
        tri_script: Path,
        group_id: str,
        auto_path: Path,
        adopted_path: Path,
        manual_path: Path,
        out_csv: Path,
        log_path: Path,
        auto_variant: str = "auto",
        adopted_variant: str = "adopted",
        include_auto: bool = True,
) -> None:
    try:
        CompareRunner.run_tri_compare(
            tri_script=tri_script,
            group_id=group_id,
            auto_path=auto_path,
            adopted_path=adopted_path,
            manual_path=manual_path,
            out_csv=out_csv,
            auto_variant=auto_variant,
            adopted_variant=adopted_variant,
            include_auto=include_auto,
        )
    except Exception:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(traceback.format_exc(), encoding="utf-8", errors="ignore")
        raise


def compare_tests(
        *,
        comparison_root: Path,
        agt_file: Path,
        adopted_file: Path,
        out_csv: Path,
        minimum_tokens: int = 50,
        auto_variant: str = "auto",
        candidate_variant: str = "adopted",
        include_auto: bool = True,
        repo: str = "",
        fqcn: str = "",
) -> None:
    runner = CompareRunner(comparison_root)
    pmd_agt, pmd_adopted, cpd = runner.compare_metrics(
        agt_file,
        adopted_file,
        minimum_tokens=minimum_tokens,
    )
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    write_compare_row(
        out_csv=out_csv,
        variant=auto_variant if include_auto else None,
        repo=repo,
        fqcn=fqcn,
        auto_pmd=pmd_agt.violations,
        candidate_pmd=None,
        cpd=None,
    )
    write_compare_row(
        out_csv=out_csv,
        variant=candidate_variant,
        repo=repo,
        fqcn=fqcn,
        auto_pmd=pmd_agt.violations,
        candidate_pmd=pmd_adopted.violations,
        cpd=cpd,
    )


def write_compare_row(
    *,
    out_csv: Path,
    variant: str | None,
    repo: str,
    fqcn: str,
    auto_pmd: int,
    candidate_pmd: Optional[int],
    cpd: Optional[CPDResult],
) -> None:
    if variant is None:
        return
    header = [
        "repo",
        "fqcn",
        "variant",
        "auto_pmd_violations",
        "candidate_pmd_violations",
        "cpd_duplicate_blocks",
        "cpd_duplicate_lines_total",
    ]
    rows = [
        [
            repo,
            fqcn,
            variant,
            auto_pmd,
            "" if candidate_pmd is None else candidate_pmd,
            "" if cpd is None else cpd.duplicate_blocks,
            "" if cpd is None else cpd.duplicate_lines_total,
        ]
    ]
    with out_csv.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(header)
        writer.writerows(rows)


def run_tri_compare(
        *,
        tri_script: Path,
        group_id: str,
        auto_path: Path,
        adopted_path: Path,
        manual_path: Path,
        out_csv: Path,
        adopted_variant: str = "adopted",
        include_auto: bool = True,
) -> None:
    CompareRunner.run_tri_compare(
        tri_script=tri_script,
        group_id=group_id,
        auto_path=auto_path,
        adopted_path=adopted_path,
        manual_path=manual_path,
        out_csv=out_csv,
        adopted_variant=adopted_variant,
        include_auto=include_auto,
    )
