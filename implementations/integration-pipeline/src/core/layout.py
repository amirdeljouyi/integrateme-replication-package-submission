"""Repository layout anchors.

The pipeline is one tool among several under ``implementations/``. It does not
own the corpus it reads or the results it writes: those live in sibling
top-level trees (``data/``, ``pipeline-output/``, ``workspace/``). Every path that
crosses out of the pipeline directory is derived from one of the roots below,
so moving the pipeline or pointing it at a different corpus is a configuration
change rather than a code change.

Paths are relative to the pipeline root, which is the working directory the
pipeline runs from. Each root can be overridden by environment variable::

    ITL_PIPELINE_OUTPUT=/scratch/run-42/out python -m src.cli.app covfilter ...

Repository layout these defaults assume::

    implementations/integration-pipeline/   <- pipeline root (cwd)
    data/collected-tests/                   <- DATA_ROOT/collected-tests
    data/selected_cut_classes.csv           <- DATA_ROOT (the CUT list)
    pipeline-output/                        <- PIPELINE_OUTPUT_ROOT
    pipeline-output/runs/<target>/          <- PIPELINE_OUTPUT_ROOT/runs
    pipeline-output/aggregate/              <- derived, per-stage
    pipeline-output/generation/             <- raw AGT generator output
    pipeline-output/rating/{packets,filled} <- RQ3 human evaluation
    pipeline-output/pull-requests/          <- RQ4 drafts, tests, backups
    workspace/repos/                        <- WORKSPACE_ROOT/repos
    workspace/out/                          <- WORKSPACE_ROOT/out (fat jars)
    workspace/pipeline/build/               <- WORKSPACE_ROOT (build_dir)
    workspace/pipeline/tmp/                 <- WORKSPACE_ROOT (out_dir)

Only the pipeline's own assets stay inside the pipeline directory:

    src/        source only
    tests/      the regression suite
    scripts/    helpers it shells out to, including scripts/run-agt/
    resources/  runtime prompts, skill, and the PR template it fills in
    vendor/     third-party binaries it did not write:
                  vendor/libs/     test compile/run classpath (JUnit, mockito, ...)
                  vendor/jacoco/   coverage agent and report CLI
                  vendor/pmd/, vendor/checkstyle/, vendor/tsdetect/  analysers

Everything it reads or writes lives under one of the roots above, so those are
plain pipeline-relative paths and need no anchor.
"""

from __future__ import annotations

import os
from pathlib import Path

# Intermediate pipeline output: covfilter, reduce, annotation, compare,
# coverage and the per-run variants. These are stage artefacts, not the final
# numbers - curated per-RQ results live in experiments/<rq>/results/.
PIPELINE_OUTPUT_ROOT = os.environ.get("ITL_PIPELINE_OUTPUT", "../../pipeline-output")

# Versioned input corpora: the collected EvoSuite and manual test suites.
DATA_ROOT = os.environ.get("ITL_DATA_ROOT", "../../data")

# Regenerable scratch: cloned repositories, built fat jars, temp output.
WORKSPACE_ROOT = os.environ.get("ITL_WORKSPACE_ROOT", "../../workspace")

# Identifies one pipeline invocation. Every target a run touches shares it, so a whole
# run stays greppable. Override with ITL_RUN_LABEL to say why a rerun happened.
_RUN_ID: str | None = None


def current_run_id() -> str:
    """`20260805-1042-baseline` - computed once per process."""
    global _RUN_ID
    if _RUN_ID is None:
        explicit = os.environ.get("ITL_RUN_ID", "").strip()
        if explicit:
            import re as _re
            if not _re.fullmatch(r"\d{8}-\d{4}-[a-z0-9][a-z0-9-]*", explicit):
                raise ValueError(
                    "ITL_RUN_ID must match YYYYMMDD-HHMM-label, got "
                    f"{explicit!r}"
                )
            _RUN_ID = explicit
            return _RUN_ID
        import datetime as _dt
        label = os.environ.get("ITL_RUN_LABEL", "baseline").strip() or "baseline"
        _RUN_ID = f"{_dt.datetime.now():%Y%m%d-%H%M}-{label}"
    return _RUN_ID


# The evaluation-target ids (T01_...) live with the experiments, while the pipeline
# identifies a target by repo-and-class. runs/ is keyed by the former so a target has one
# directory; without the translation the pipeline writes a second tree under the raw key.
EXPERIMENTS_ROOT = os.environ.get("ITL_EXPERIMENTS_ROOT", "../../experiments")

_TARGET_IDS: dict[str, str] | None = None


def target_id_for_key(target_key: str) -> str:
    """`alibaba_druid_com_..._DruidDataSource` -> `T01_DruidDataSource`.

    Falls back to the key unchanged when the mapping is unavailable, so the pipeline
    still runs from a checkout without experiments/.
    """
    global _TARGET_IDS
    if _TARGET_IDS is None:
        _TARGET_IDS = {}
        csv_path = Path(EXPERIMENTS_ROOT) / "targets.csv"
        if csv_path.exists():
            import csv as _csv

            with csv_path.open(encoding="utf-8", newline="") as handle:
                for row in _csv.DictReader(handle):
                    key = f"{row['repo'].replace('/', '_')}_{row['fqcn'].replace('.', '_')}"
                    _TARGET_IDS[key] = row["target_id"]
    return _TARGET_IDS.get(target_key, target_key)


def target_run_dir(target_key: str, stage: str, variant: str) -> str:
    """Where this run's output for one target goes, relative to the pipeline root."""
    return (
        f"{PIPELINE_OUTPUT_ROOT}/runs/{target_id_for_key(target_key)}"
        f"/{stage}/{variant}/{current_run_id()}"
    )


__all__ = [
    "PIPELINE_OUTPUT_ROOT", "DATA_ROOT", "WORKSPACE_ROOT",
    "current_run_id", "target_run_dir", "target_id_for_key",
]
