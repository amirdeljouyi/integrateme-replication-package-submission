from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..fatjar_builder.build_root import BuildRootDetector
from ..fatjar_builder.command_runner import CommandRunner
from ..fatjar_builder.config import BuildConfig
from ..fatjar_builder.csvio import CsvIO
from ..fatjar_builder.gradle_fatjar import GradleFatJarBuilder
from ..fatjar_builder.jars import JarPicker
from ..fatjar_builder.logging_ctx import LoggingContext
from ..fatjar_builder.maven_fatjar import MavenFatJarBuilder
from ..fatjar_builder.orchestrator import BuildOrchestrator
from ..fatjar_builder.repo_scan import RepoScanner
from ..fatjar_builder.wrappers import WrapperSelector


@dataclass(frozen=True)
class FatjarConfig:
    cut_csv: Path
    mode: str = "local"
    base_dir: Path = Path(".")
    java_home: str = ""
    java21_home: str = ""
    log_dir: Optional[Path] = None
    repos_csv: Optional[Path] = None
    out_map_csv: Optional[Path] = None
    failures_csv: Optional[Path] = None
    retry_only: bool = False


def run_fatjar(config: FatjarConfig) -> int:
    mode = (config.mode or "local").strip()
    if mode not in {"local", "docker"}:
        print(f"[agt] fatjar: FAIL (unsupported mode: {mode})")
        return 1

    cut_csv = config.cut_csv.resolve()
    if not cut_csv.exists():
        print(f"[agt] fatjar: FAIL (missing cut csv): {cut_csv}")
        return 1

    cfg = BuildConfig(
        cut_csv=cut_csv,
        mode=mode,
        base_dir=config.base_dir.resolve(),
        java_home=config.java_home,
        java21_home=config.java21_home,
        log_dir=config.log_dir.resolve() if config.log_dir else None,
        repos_csv=config.repos_csv.resolve() if config.repos_csv else None,
        out_map_csv=config.out_map_csv.resolve() if config.out_map_csv else None,
        failures_csv=config.failures_csv.resolve() if config.failures_csv else None,
        retry_only=config.retry_only,
    )

    lc = LoggingContext(cfg.resolved_log_dir())
    logger = lc.setup()
    logger.info("Starting integration pipeline fatjar step")
    logger.info("Config: %s", config)

    scanner = RepoScanner()
    detector = BuildRootDetector(scanner)
    runner = CommandRunner(lc)
    wrappers = WrapperSelector()
    jar_picker = JarPicker()

    maven = MavenFatJarBuilder(runner, wrappers, detector, jar_picker, logger)
    gradle = GradleFatJarBuilder(runner, wrappers, detector, jar_picker)

    io = CsvIO()
    orchestrator = BuildOrchestrator(cfg, io, detector, maven, gradle, lc)

    try:
        orchestrator.run()
        return 0
    except Exception as exc:
        print(f"[agt] fatjar: FAIL ({exc})")
        return 1
