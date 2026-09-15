from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Iterable, Optional

import click
import typer

from ..core.incremental_target_cut import build_incremental_target_cut_report
from ..core.layout import PIPELINE_OUTPUT_ROOT, WORKSPACE_ROOT
from ..pipeline.config import ADOPTED_LIKE_VARIANTS, PipelineArgs, build_pipeline_args, pipeline_defaults
from ..pipeline.pipeline import run_pipeline

COMMAND_HELP_ORDER = [
    "clone",
    "fatjar",
    "generate-auto",
    "compile",
    "run",
    "filter",
    "reduce",
    "llm",
    "repair",
    "sync",
    "coverage",
    "compare",
    "annotation",
    "pull-request-maker",
]


class OrderedHelpGroup(typer.core.TyperGroup):
    def list_commands(self, ctx: click.Context) -> list[str]:
        ordered = [name for name in COMMAND_HELP_ORDER if name in self.commands]
        remaining = [name for name in self.commands if name not in COMMAND_HELP_ORDER]
        return ordered + sorted(remaining)


cli = typer.Typer(no_args_is_help=True, add_completion=False, cls=OrderedHelpGroup)

llm_app = typer.Typer(no_args_is_help=True)
repair_app = typer.Typer(no_args_is_help=True)
coverage_app = typer.Typer(no_args_is_help=True)

HELP_PANEL_SETUP = "1) Repository Setup"
HELP_PANEL_GENERATE = "2) Test Generation"
HELP_PANEL_EXECUTION = "3) Build, Run, and Reduction"
HELP_PANEL_LLM = "4) LLM Workflows"
HELP_PANEL_REPAIR_SYNC = "5) Test Maintenance and Sync"
HELP_PANEL_COVERAGE_COMPARE = "6) Coverage and Comparison"
HELP_PANEL_ANNOTATION_PR = "7) Annotation and PR Artifacts"

cli.add_typer(llm_app, name="llm", help="LLM-related steps", rich_help_panel=HELP_PANEL_LLM)
cli.add_typer(
    repair_app,
    name="repair",
    help="Repair steps for adopted and agentic tests",
    rich_help_panel=HELP_PANEL_REPAIR_SYNC,
)
cli.add_typer(
    coverage_app,
    name="coverage",
    help="Coverage comparison steps",
    rich_help_panel=HELP_PANEL_COVERAGE_COMPARE,
)

DEFAULTS = pipeline_defaults()


def _base_args(ctx: typer.Context) -> PipelineArgs:
    try:
        return ctx.obj["base"]
    except Exception as exc:  # pragma: no cover
        raise typer.BadParameter("Internal error: base args not initialized.") from exc


def _run(ctx: typer.Context, step: str, **overrides: Any) -> None:
    _run_steps(ctx, [step], **overrides)


def _run_steps(ctx: typer.Context, steps: Iterable[str], **overrides: Any) -> None:
    base = _base_args(ctx)
    normalized_overrides = dict(overrides)
    normalized_overrides.pop("variants", None)
    merged = {**base.__dict__, **normalized_overrides}
    for step in steps:
        rc = run_pipeline(build_pipeline_args(**{**merged, "step": step}))
        if rc != 0:
            raise typer.Exit(code=rc)
    raise typer.Exit(code=0)


def _run_step_configs(ctx: typer.Context, step_configs: Iterable[tuple[str, dict[str, Any]]], **overrides: Any) -> None:
    base = _base_args(ctx)
    normalized_overrides = dict(overrides)
    normalized_overrides.pop("variants", None)
    merged = {**base.__dict__, **normalized_overrides}
    for step, extra_overrides in step_configs:
        rc = run_pipeline(build_pipeline_args(**{**merged, **extra_overrides, "step": step}))
        if rc != 0:
            raise typer.Exit(code=rc)
    raise typer.Exit(code=0)


def _parse_variants(variants: str) -> set[str]:
    items = {v.strip().lower() for v in variants.split(",") if v.strip()}
    if not items:
        return set()
    if "all" in items:
        return {"auto", "auto-original", "auto-100", "manual", "improved", *ADOPTED_LIKE_VARIANTS}
    allowed = {"auto", "auto-original", "auto-100", "manual", "improved", *ADOPTED_LIKE_VARIANTS}
    bad = sorted(items - allowed)
    if bad:
        raise typer.BadParameter(f"Unknown variants: {', '.join(bad)}")
    return items


def _step_configs_for_variants(
    *,
    variants: set[str],
    auto_step: str,
    adopted_step: str,
    allow_manual: bool,
    allow_auto: bool,
    allow_adopted: bool,
    allow_agentic: bool,
) -> list[tuple[str, dict[str, Any]]]:
    step_configs: list[tuple[str, dict[str, Any]]] = []
    if allow_auto:
        if "auto" in variants:
            step_configs.append((auto_step, {"auto_variant": "auto"}))
        if "auto-original" in variants:
            step_configs.append((auto_step, {"auto_variant": "auto-original"}))
        if "manual" in variants and not ({"auto", "auto-original"} & variants):
            step_configs.append((auto_step, {"auto_variant": "auto"}))
    adopted_like_variants = list(ADOPTED_LIKE_VARIANTS)
    if adopted_step == "adopted-run":
        adopted_like_variants.extend(("auto-100", "improved"))
    adopted_like = [variant for variant in adopted_like_variants if variant in variants]
    if adopted_like and allow_adopted:
        step_configs.append((adopted_step, {"adopted_filter_variants": ",".join(adopted_like)}))
    if "manual" in variants and not allow_manual:
        print("[agt] Warning: manual variant not supported for this command")
    if "auto" in variants and not allow_auto:
        print("[agt] Warning: auto variant not supported for this command")
    if "auto-original" in variants and not allow_auto:
        print("[agt] Warning: auto-original variant not supported for this command")
    if "adopted" in variants and not allow_adopted:
        print("[agt] Warning: adopted variant not supported for this command")
    if "agentic" in variants and not allow_agentic:
        print("[agt] Warning: agentic variant not supported for this command")
    if "pr-tests" in variants and not allow_adopted:
        print("[agt] Warning: pr-tests variant not supported for this command")
    return step_configs


def _auto_variant_step_configs(variants: set[str], *, step: str) -> list[tuple[str, dict[str, Any]]]:
    step_configs: list[tuple[str, dict[str, Any]]] = []
    if not variants:
        return [(step, {"auto_variant": "auto"})]
    if "auto" in variants:
        step_configs.append((step, {"auto_variant": "auto"}))
    if "auto-original" in variants:
        step_configs.append((step, {"auto_variant": "auto-original"}))
    if not step_configs:
        print("[agt] Warning: no supported auto variants selected; use `auto` or `auto-original`.")
    return step_configs


def _comparison_step_configs(variants: set[str], *, step: str) -> list[tuple[str, dict[str, Any]]]:
    if not variants:
        return [(step, {"auto_variant": "auto"})]
    auto_variants = [variant for variant in ("auto", "auto-original") if variant in variants] or ["auto"]
    adopted_like = [variant for variant in ADOPTED_LIKE_VARIANTS if variant in variants]
    configs: list[tuple[str, dict[str, Any]]] = []
    for auto_variant in auto_variants:
        overrides: dict[str, Any] = {"auto_variant": auto_variant}
        if adopted_like:
            overrides["adopted_filter_variants"] = ",".join(adopted_like)
        configs.append((step, overrides))
    return configs


def _annotation_step_configs(variants: set[str]) -> list[tuple[str, dict[str, Any]]]:
    selected = set(variants)
    selected.discard("manual")
    auto_variants = [variant for variant in ("auto", "auto-original") if variant in selected]
    adopted_variants = [variant for variant in ADOPTED_LIKE_VARIANTS if variant in selected]

    if not selected:
        return [("annotation", {"auto_variant": "auto", "annotation_variants": "auto,adopted,agentic,pr-tests"})]

    if not auto_variants:
        variant_csv = ",".join(adopted_variants)
        return [("annotation", {"auto_variant": "auto", "annotation_variants": variant_csv})] if variant_csv else []

    step_configs: list[tuple[str, dict[str, Any]]] = []
    for index, auto_variant in enumerate(auto_variants):
        enabled = [auto_variant]
        if index == 0:
            enabled.extend(adopted_variants)
        step_configs.append(
            (
                "annotation",
                {
                    "auto_variant": auto_variant,
                    "annotation_variants": ",".join(enabled),
                },
            )
        )
    return step_configs


@cli.callback()
def cli_main(
    ctx: typer.Context,
    tests_inventory_csv: Annotated[Path, typer.Option(help="Path to tests inventory CSV")] = Path(
        str(DEFAULTS["tests_inventory_csv"])
    ),
    cut_to_fatjar_map_csv: Annotated[Path, typer.Option(help="Path to CUT->fat-jar map CSV")] = Path(
        str(DEFAULTS["cut_to_fatjar_map_csv"])
    ),
    generated_dir: Annotated[Path, typer.Option(help="Generated tests root directory")] = Path(
        str(DEFAULTS["generated_dir"])
    ),
    manual_dir: Annotated[Path, typer.Option(help="Manual tests root directory")] = Path(
        str(DEFAULTS["manual_dir"])
    ),
    pr_tests_dir: Annotated[Path, typer.Option(help="Pull-request tests root directory")] = Path(
        str(DEFAULTS["pr_tests_dir"])
    ),
    repos_dir: Annotated[Path, typer.Option(help="Cloned repositories root directory")] = Path(
        str(DEFAULTS["repos_dir"])
    ),
    libs_cp: Annotated[str, typer.Option(help="Classpath glob/pattern for libs")] = str(DEFAULTS["libs_cp"]),
    jacoco_agent: Annotated[Path, typer.Option(help="Path to JaCoCo agent jar")] = Path(
        str(DEFAULTS["jacoco_agent"])
    ),
    out_dir: Annotated[Path, typer.Option(help="Working directory (temp outputs)")] = Path(
        str(DEFAULTS["out_dir"])
    ),
    build_dir: Annotated[Path, typer.Option(help="Build directory for compilation/run steps")] = Path(
        str(DEFAULTS["build_dir"])
    ),
    includes: Annotated[str, typer.Option(help="Glob for selecting tests/targets")] = str(
        DEFAULTS["includes"]
    ),
    dep_rounds: Annotated[int, typer.Option(help="Dependency resolution rounds")] = int(
        DEFAULTS["dep_rounds"]
    ),
    tool_jar: Annotated[Path, typer.Option(help="Coverage tool jar")] = Path(
        str(DEFAULTS["tool_jar"])
    ),
    timeout_ms: Annotated[int, typer.Option(help="Per-target timeout in ms")] = int(
        DEFAULTS["timeout_ms"]
    ),
    filter_only_agt_covered: Annotated[bool, typer.Option(help="Filter to AGT-covered targets only")] = bool(
        DEFAULTS["filter_only_agt_covered"]
    ),
    skip_empty_tests: Annotated[
        bool,
        typer.Option(
            "--skip-empty-tests/--no-skip-empty-tests",
            help="Skip targets whose generated test only contains notGeneratedAnyTest",
        ),
    ] = bool(DEFAULTS["skip_empty_tests"]),
    coverage_summary: Annotated[str, typer.Option(help="Coverage summary CSV path override")] = str(
        DEFAULTS["coverage_summary"]
    ),
) -> None:
    ctx.obj = {
        "base": build_pipeline_args(
            tests_inventory_csv=str(tests_inventory_csv),
            cut_to_fatjar_map_csv=str(cut_to_fatjar_map_csv),
            generated_dir=str(generated_dir),
            manual_dir=str(manual_dir),
            pr_tests_dir=str(pr_tests_dir),
            repos_dir=str(repos_dir),
            libs_cp=libs_cp,
            jacoco_agent=str(jacoco_agent),
            out_dir=str(out_dir),
            build_dir=str(build_dir),
            includes=includes,
            dep_rounds=dep_rounds,
            tool_jar=str(tool_jar),
            timeout_ms=timeout_ms,
            filter_only_agt_covered=filter_only_agt_covered,
            skip_empty_tests=skip_empty_tests,
            coverage_summary=coverage_summary,
        )
    }


@cli.command("clone", help="Clone repositories listed in selected_cut_classes.csv", rich_help_panel=HELP_PANEL_SETUP)
def clone(
    ctx: typer.Context,
    selected_cut_csv: Annotated[Path, typer.Option(help="Path to selected CUT CSV")] = Path(
        str(DEFAULTS["selected_cut_csv"])
    ),
    mode: Annotated[str, typer.Option(help="Clone mode: local or docker")] = str(DEFAULTS["clone_mode"]),
    base_dir: Annotated[Path, typer.Option(help="Base dir used by clone step in local mode")] = Path(
        str(DEFAULTS["clone_base_dir"])
    ),
    update_existing: Annotated[
        bool,
        typer.Option("--update-existing/--no-update-existing", help="Fetch/reset existing repositories"),
    ] = bool(DEFAULTS["clone_update_existing"]),
    log_dir: Annotated[
        str, typer.Option(help="Clone log directory (default: <base-dir>/logs-clone)")
    ] = str(DEFAULTS["clone_log_dir"]),
    out_repos_csv: Annotated[
        str, typer.Option(help="Where to write repo_roots.csv (default: <base-dir>/repo_roots.csv)")
    ] = str(DEFAULTS["clone_out_repos_csv"]),
) -> None:
    _run(
        ctx,
        "clone",
        selected_cut_csv=str(selected_cut_csv),
        clone_mode=mode,
        clone_base_dir=str(base_dir),
        clone_update_existing=update_existing,
        clone_log_dir=log_dir,
        clone_out_repos_csv=out_repos_csv,
    )


@cli.command("fatjar", help="Build CUT fat jars and write cut_to_fatjar_map.csv", rich_help_panel=HELP_PANEL_SETUP)
def fatjar(
    ctx: typer.Context,
    selected_cut_csv: Annotated[Path, typer.Option(help="Path to selected CUT CSV")] = Path(
        str(DEFAULTS["selected_cut_csv"])
    ),
    mode: Annotated[str, typer.Option(help="Build mode: local or docker")] = str(DEFAULTS["fatjar_mode"]),
    base_dir: Annotated[Path, typer.Option(help="Base dir used by fatjar step in local mode")] = Path(
        str(DEFAULTS["fatjar_base_dir"])
    ),
    java_home: Annotated[str, typer.Option(help="JAVA_HOME override for fatjar build")] = str(
        DEFAULTS["fatjar_java_home"]
    ),
    java21_home: Annotated[str, typer.Option(help="JDK 21 home override for Maven wrapper builds")] = str(
        DEFAULTS["fatjar_java21_home"]
    ),
    retry_only: Annotated[
        bool,
        typer.Option("--retry-only/--no-retry-only", help="Build only previously failed CUT rows"),
    ] = bool(DEFAULTS["fatjar_retry_only"]),
    log_dir: Annotated[
        str, typer.Option(help="Fatjar log directory (default: <base-dir>/logs-fatjar)")
    ] = str(DEFAULTS["fatjar_log_dir"]),
    repos_csv: Annotated[
        str, typer.Option(help="repo_roots.csv to read (default: <base-dir>/repo_roots.csv)")
    ] = str(DEFAULTS["fatjar_repos_csv"]),
    failures_csv: Annotated[
        str, typer.Option(help="Failures CSV consulted by --retry-only")
    ] = str(DEFAULTS["fatjar_failures_csv"]),
) -> None:
    _run(
        ctx,
        "fatjar",
        selected_cut_csv=str(selected_cut_csv),
        fatjar_mode=mode,
        fatjar_base_dir=str(base_dir),
        fatjar_java_home=java_home,
        fatjar_java21_home=java21_home,
        fatjar_retry_only=retry_only,
        fatjar_log_dir=log_dir,
        fatjar_repos_csv=repos_csv,
        fatjar_failures_csv=failures_csv,
    )


@cli.command(
    "generate-auto",
    help="Generate auto tests via the run-agt tool and refresh collected-tests",
    rich_help_panel=HELP_PANEL_GENERATE,
)
def generate_auto(
    ctx: typer.Context,
    skip_existing: Annotated[
        bool,
        typer.Option("--skip-existing/--no-skip-existing", help="Skip CUTs that already have generated tests"),
    ] = bool(DEFAULTS["generate_auto_skip_existing"]),
) -> None:
    _run(ctx, "generate-auto", generate_auto_skip_existing=skip_existing)


@cli.command("sync", help="Sync generated tests from run-agt output into collected-tests", rich_help_panel=HELP_PANEL_REPAIR_SYNC)
def sync(
    ctx: typer.Context,
    variants: Annotated[str, typer.Option(help="Comma-separated variants; only auto is supported right now")] = "auto",
    force: Annotated[
        bool,
        typer.Option("--force", help="Merge generated tests into an existing collected-tests tree without replacing it"),
    ] = bool(DEFAULTS["sync_force"]),
) -> None:
    selected = _parse_variants(variants)
    if "manual" in selected:
        print("[agt] Warning: manual variant not supported for this command")
    if "adopted" in selected:
        print("[agt] Warning: adopted variant not supported for this command")
    if "agentic" in selected:
        print("[agt] Warning: agentic variant not supported for this command")
    if "pr-tests" in selected:
        print("[agt] Warning: pr-tests variant not supported for this command")
    if {"auto", "auto-original"} & selected:
        _run(ctx, "sync", sync_variants="auto", sync_force=force)
        return
    raise typer.Exit(code=0)


@cli.command(help="Compile selected tests and dependencies", rich_help_panel=HELP_PANEL_EXECUTION)
def compile(
    ctx: typer.Context,
    skip_passed: Annotated[bool, typer.Option(help="Skip targets whose latest compile already passed")] = bool(
        DEFAULTS["skip_passed"]
    ),
    variants: Annotated[str, typer.Option(help="Comma-separated variants: auto,auto-original,manual or all")] = "",
) -> None:
    if variants:
        step_configs = _step_configs_for_variants(
            variants=_parse_variants(variants),
            auto_step="compile",
            adopted_step="compile",
            allow_manual=True,
            allow_auto=True,
            allow_adopted=False,
            allow_agentic=False,
        )
        if step_configs:
            _run_step_configs(ctx, step_configs, skip_passed=skip_passed)
        raise typer.Exit(code=0)
    _run(ctx, "compile", skip_passed=skip_passed)


@cli.command(help="Run compiled tests and collect coverage", rich_help_panel=HELP_PANEL_EXECUTION)
def run(
    ctx: typer.Context,
    variants: Annotated[str, typer.Option(help="Comma-separated variants: auto,auto-original,auto-100,manual,improved,adopted,agentic,pr-tests or all")] = "",
    auto_100_dir: Annotated[
        Path,
        typer.Option(help="Reduced-test root containing true Top-100 auto suites"),
    ] = Path(f"{PIPELINE_OUTPUT_ROOT}/reduced"),
) -> None:
    if variants:
        step_configs = _step_configs_for_variants(
            variants=_parse_variants(variants),
            auto_step="run",
            adopted_step="adopted-run",
            allow_manual=True,
            allow_auto=True,
            allow_adopted=True,
            allow_agentic=True,
        )
        if step_configs:
            _run_step_configs(ctx, step_configs, reduced_out=str(auto_100_dir))
        raise typer.Exit(code=0)
    _run(ctx, "run")


@cli.command(help="Run coverage filter for generated/manual tests", rich_help_panel=HELP_PANEL_EXECUTION)
def filter(
    ctx: typer.Context,
    covfilter_jar: Annotated[Path, typer.Option(help="Coverage filter tool jar")] = Path(
        str(DEFAULTS["covfilter_jar"])
    ),
    covfilter_out: Annotated[Path, typer.Option(help="Output directory for covfilter results")] = Path(
        str(DEFAULTS["covfilter_out"])
    ),
    adopted_covfilter_out: Annotated[Path, typer.Option(help="Output directory for adopted covfilter results")] = Path(
        str(DEFAULTS["adopted_covfilter_out"])
    ),
    agentic_covfilter_out: Annotated[Path, typer.Option(help="Output directory for agentic covfilter results")] = Path(
        str(DEFAULTS["agentic_covfilter_out"])
    ),
    sut_classes_dir: Annotated[str, typer.Option(help="Directory of SUT .class files (optional)")] = str(
        DEFAULTS["sut_classes_dir"]
    ),
    skip_exists: Annotated[bool, typer.Option(help="Skip targets that already have covfilter output")] = bool(
        DEFAULTS["skip_exists"]
    ),
    skip_passed: Annotated[bool, typer.Option(help="Skip targets that already have a passed covfilter output")] = bool(
        DEFAULTS["skip_passed"]
    ),
    skip_passed_by_status: Annotated[
        bool,
        typer.Option(help="Skip targets whose latest covfilter summary status is passed"),
    ] = bool(DEFAULTS["skip_passed_by_status"]),
    sanitized_es_dir: Annotated[Path, typer.Option(help="Output directory for sanitized EvoSuite variants")] = Path(
        str(DEFAULTS["sanitized_es_dir"])
    ),
    sanitize_compare_out: Annotated[Path, typer.Option(help="Output directory for baseline vs sanitized compare artifacts")] = Path(
        str(DEFAULTS["sanitize_compare_out"])
    ),
    sanitize_compare: Annotated[
        bool,
        typer.Option("--sanitize-compare/--no-sanitize-compare", help="Try sanitized EvoSuite tests first and fall back to baseline/original only if needed"),
    ] = bool(DEFAULTS["sanitize_compare"]),
    retry_zero_kept_tests: Annotated[
        bool,
        typer.Option(
            "--retry-zero-kept-tests/--no-retry-zero-kept-tests",
            help="For adopted/agentic variants, retry only targets marked as zero_kept_tests in reduced summaries",
        ),
    ] = bool(DEFAULTS["retry_zero_kept_tests"]),
    retry_missing_test_deltas_csv: Annotated[
        bool,
        typer.Option(
            "--retry-missing-test-deltas-csv/--no-retry-missing-test-deltas-csv",
            help="For selected variants, retry only targets marked as missing_test_deltas_csv in reduced summaries",
        ),
    ] = bool(DEFAULTS["retry_missing_test_deltas_csv"]),
    comment: Annotated[
        bool,
        typer.Option(
            "--comment/--no-comment",
            help="Comment generated test compile-error lines as a fallback during filter compile retries",
        ),
    ] = bool(DEFAULTS["covfilter_comment_compile_errors"]),
    variants: Annotated[str, typer.Option(help="Comma-separated variants: auto,auto-original,manual,adopted,agentic,pr-tests or all")] = "",
) -> None:
    if variants:
        selected_variants = _parse_variants(variants)
        selected_adopted_variants = ",".join(
            variant for variant in ADOPTED_LIKE_VARIANTS if variant in selected_variants
        )
        step_configs = _step_configs_for_variants(
            variants=selected_variants,
            auto_step="filter",
            adopted_step="adopted-filter",
            allow_manual=True,
            allow_auto=True,
            allow_adopted=True,
            allow_agentic=True,
        )
        if step_configs:
            _run_step_configs(
                ctx,
                step_configs,
                do_covfilter=True,
                covfilter_jar=str(covfilter_jar),
                covfilter_out=str(covfilter_out),
                adopted_covfilter_out=str(adopted_covfilter_out),
                agentic_covfilter_out=str(agentic_covfilter_out),
                sut_classes_dir=sut_classes_dir,
                skip_exists=skip_exists,
                skip_passed=skip_passed,
                skip_passed_by_status=skip_passed_by_status,
                sanitized_es_dir=str(sanitized_es_dir),
                sanitize_compare_out=str(sanitize_compare_out),
                sanitize_compare=sanitize_compare,
                retry_zero_kept_tests=retry_zero_kept_tests,
                retry_missing_test_deltas_csv=retry_missing_test_deltas_csv,
                covfilter_comment_compile_errors=comment,
                adopted_filter_variants=selected_adopted_variants,
            )
        raise typer.Exit(code=0)
    _run(
        ctx,
        "filter",
        do_covfilter=True,
        covfilter_jar=str(covfilter_jar),
        covfilter_out=str(covfilter_out),
        adopted_covfilter_out=str(adopted_covfilter_out),
        agentic_covfilter_out=str(agentic_covfilter_out),
        sut_classes_dir=sut_classes_dir,
        skip_exists=skip_exists,
        skip_passed=skip_passed,
        skip_passed_by_status=skip_passed_by_status,
        sanitized_es_dir=str(sanitized_es_dir),
        sanitize_compare_out=str(sanitize_compare_out),
        sanitize_compare=sanitize_compare,
        retry_zero_kept_tests=retry_zero_kept_tests,
        retry_missing_test_deltas_csv=retry_missing_test_deltas_csv,
        covfilter_comment_compile_errors=comment,
    )


@cli.command(help="Reduce AGT tests using covfilter output", rich_help_panel=HELP_PANEL_EXECUTION)
def reduce(
    ctx: typer.Context,
    reduced_out: Annotated[Path, typer.Option(help="Output directory for reduced tests")] = Path(
        str(DEFAULTS["reduced_out"])
    ),
    max_tests: Annotated[int, typer.Option("--max-tests", min=1, help="Maximum tests to keep")] = int(
        DEFAULTS["reduce_max_tests"]
    ),
    top_n: Annotated[Optional[int], typer.Option("--top-n", min=1, help="Alias for --max-tests")] = None,
    covfilter_jar: Annotated[Path, typer.Option(help="Coverage filter tool jar")] = Path(
        str(DEFAULTS["covfilter_jar"])
    ),
    covfilter_out: Annotated[Path, typer.Option(help="Covfilter output directory")]= Path(
        str(DEFAULTS["covfilter_out"])
    ),
    adopted_covfilter_out: Annotated[Path, typer.Option(help="Covfilter output directory for adopted tests")]= Path(
        str(DEFAULTS["adopted_covfilter_out"])
    ),
    agentic_covfilter_out: Annotated[Path, typer.Option(help="Covfilter output directory for agentic tests")]= Path(
        str(DEFAULTS["agentic_covfilter_out"])
    ),
    variants: Annotated[str, typer.Option(help="Comma-separated variants: auto,auto-original,manual,adopted,agentic,pr-tests or all")] = "",
) -> None:
    if top_n is not None:
        max_tests = top_n
    if variants:
        step_configs = _step_configs_for_variants(
            variants=_parse_variants(variants),
            auto_step="reduce",
            adopted_step="adopted-reduce",
            allow_manual=False,
            allow_auto=True,
            allow_adopted=True,
            allow_agentic=True,
        )
        if step_configs:
            _run_step_configs(
                ctx,
                step_configs,
                reduced_out=str(reduced_out),
                reduce_max_tests=max_tests,
                adopted_reduce_max_tests=max_tests,
                covfilter_jar=str(covfilter_jar),
                covfilter_out=str(covfilter_out),
                adopted_covfilter_out=str(adopted_covfilter_out),
                agentic_covfilter_out=str(agentic_covfilter_out),
            )
        raise typer.Exit(code=0)
    _run(
        ctx,
        "reduce",
        reduced_out=str(reduced_out),
        reduce_max_tests=max_tests,
        covfilter_jar=str(covfilter_jar),
        covfilter_out=str(covfilter_out),
        adopted_covfilter_out=str(adopted_covfilter_out),
        agentic_covfilter_out=str(agentic_covfilter_out),
    )


@cli.command(help="Annotate reduced tests with coverage deltas and covered lines", rich_help_panel=HELP_PANEL_ANNOTATION_PR)
def annotation(
    ctx: typer.Context,
    reduced_out: Annotated[Path, typer.Option(help="Output directory for reduced tests")] = Path(
        str(DEFAULTS["reduced_out"])
    ),
    adopted_reduced_out: Annotated[Path, typer.Option(help="Output directory for reduced adopted tests")] = Path(
        str(DEFAULTS["adopted_reduced_out"])
    ),
    covfilter_out: Annotated[Path, typer.Option(help="Covfilter output directory")] = Path(
        str(DEFAULTS["covfilter_out"])
    ),
    adopted_covfilter_out: Annotated[Path, typer.Option(help="Covfilter output for adopted tests")] = Path(
        str(DEFAULTS["adopted_covfilter_out"])
    ),
    agentic_covfilter_out: Annotated[Path, typer.Option(help="Covfilter output for agentic tests")] = Path(
        str(DEFAULTS["agentic_covfilter_out"])
    ),
    annotation_out: Annotated[Path, typer.Option(help="Output directory for annotated reduced tests")] = Path(
        str(DEFAULTS["annotation_out"])
    ),
    filtered_dataset: Annotated[
        bool,
        typer.Option(
            "--filtered-dataset/--no-filtered-dataset",
            help="Use results/annotation/dataset_filtered.csv (repo,fqcn) and write summary_filtered*.csv outputs",
        ),
    ] = False,
    variants: Annotated[
        str, typer.Option(help="Comma-separated variants: auto,auto-original,adopted,agentic,pr-tests or all")
    ] = str(DEFAULTS["annotation_variants"]),
) -> None:
    step_configs = _annotation_step_configs(_parse_variants(variants))
    if step_configs:
        _run_step_configs(
            ctx,
            step_configs,
            reduced_out=str(reduced_out),
            adopted_reduced_out=str(adopted_reduced_out),
            covfilter_out=str(covfilter_out),
            adopted_covfilter_out=str(adopted_covfilter_out),
            agentic_covfilter_out=str(agentic_covfilter_out),
            annotation_out=str(annotation_out),
            filtered_dataset=f"{PIPELINE_OUTPUT_ROOT}/annotation/dataset_filtered.csv" if filtered_dataset else "",
        )
    raise typer.Exit(code=0)


def _run_agent(
    ctx: typer.Context,
    *,
    adopted_dir: Path,
    skip_exists: bool,
    agent_model: str,
    max_context_files: int,
    max_context_chars: int,
    max_prompt_chars: int,
    retry_zero_kept_tests: bool,
) -> None:
    _run(
        ctx,
        "llm-agent",
        adopted_dir=str(adopted_dir),
        skip_exists=skip_exists,
        agent_model=agent_model,
        agent_max_context_files=max_context_files,
        agent_max_context_chars=max_context_chars,
        agent_max_prompt_chars=max_prompt_chars,
        retry_zero_kept_tests=retry_zero_kept_tests,
    )


@cli.command(help="Compare results (manual vs auto/reduced)", rich_help_panel=HELP_PANEL_COVERAGE_COMPARE)
def compare(
    ctx: typer.Context,
    compare_root: Annotated[Path, typer.Option(help="Path to comparison/metrics implementation")] = Path(
        str(DEFAULTS["compare_root"])
    ),
    compare_out: Annotated[Path, typer.Option(help="Output directory for comparison results")] = Path(
        str(DEFAULTS["compare_out"])
    ),
    min_tokens: Annotated[int, typer.Option("--min-tokens", min=0, help="Minimum token threshold")] = int(
        DEFAULTS["compare_min_tokens"]
    ),
    variants: Annotated[str, typer.Option(help="Comma-separated variants: auto,auto-original,adopted,agentic,pr-tests or all")] = "",
) -> None:
    step_configs = _comparison_step_configs(_parse_variants(variants) if variants else set(), step="compare")
    if step_configs:
        _run_step_configs(
            ctx,
            step_configs,
            compare_root=str(compare_root),
            compare_out=str(compare_out),
            compare_min_tokens=min_tokens,
        )
    raise typer.Exit(code=0)


@cli.command(name="pull-request-maker", help="Generate pull request artifacts", rich_help_panel=HELP_PANEL_ANNOTATION_PR)
def pull_request_maker(
    ctx: typer.Context,
    send_script: Annotated[str, typer.Option(help="Script used to send PRs (optional)")] = str(
        DEFAULTS["send_script"]
    ),
    send_api_url: Annotated[str, typer.Option(help="GraphQL API URL (optional)")] = str(
        DEFAULTS["send_api_url"]
    ),
    send_sleep_seconds: Annotated[int, typer.Option(help="Sleep seconds between sends")] = int(
        DEFAULTS["send_sleep_seconds"]
    ),
) -> None:
    _run(
        ctx,
        "pull-request-maker",
        send_script=send_script,
        send_api_url=send_api_url,
        send_sleep_seconds=send_sleep_seconds,
    )


@llm_app.command("improve", help="Improve AGT tests using LLM")
def llm_improve(
    ctx: typer.Context,
    adopted_dir: Annotated[Path, typer.Option(help="Directory to write improved outputs")] = Path(
        str(DEFAULTS["adopted_dir"])
    ),
    skip_exists: Annotated[bool, typer.Option(help="Skip targets that already have an improved output")] = bool(
        DEFAULTS["skip_exists"]
    ),
    retry_zero_kept_tests: Annotated[
        bool,
        typer.Option(
            "--retry-zero-kept-tests/--no-retry-zero-kept-tests",
            help="Retry only targets marked as zero_kept_tests in reduced adopted/agentic summaries",
        ),
    ] = bool(DEFAULTS["retry_zero_kept_tests"]),
) -> None:
    _run(
        ctx,
        "llm-agt-improvement",
        adopted_dir=str(adopted_dir),
        skip_exists=skip_exists,
        retry_zero_kept_tests=retry_zero_kept_tests,
    )


@llm_app.command("integrate", help="Integrate AGT with manual tests using LLM")
def llm_integrate(
    ctx: typer.Context,
    adopted_dir: Annotated[Path, typer.Option(help="Directory to write integrated outputs")] = Path(
        str(DEFAULTS["adopted_dir"])
    ),
    skip_exists: Annotated[bool, typer.Option(help="Skip targets that already have an integrated output")] = bool(
        DEFAULTS["skip_exists"]
    ),
    retry_zero_kept_tests: Annotated[
        bool,
        typer.Option(
            "--retry-zero-kept-tests/--no-retry-zero-kept-tests",
            help="Retry only targets marked as zero_kept_tests in reduced adopted summary",
        ),
    ] = bool(DEFAULTS["retry_zero_kept_tests"]),
) -> None:
    _run(
        ctx,
        "llm-integration",
        adopted_dir=str(adopted_dir),
        skip_exists=skip_exists,
        retry_zero_kept_tests=retry_zero_kept_tests,
    )


@llm_app.command("integrate-sbs", help="Integration step-by-step")
def llm_integrate_step_by_step(
    ctx: typer.Context,
    adopted_dir: Annotated[Path, typer.Option(help="Directory to write integrated outputs")] = Path(
        str(DEFAULTS["adopted_dir"])
    ),
    skip_exists: Annotated[bool, typer.Option(help="Skip targets that already have a step-by-step output")] = bool(
        DEFAULTS["skip_exists"]
    ),
    retry_zero_kept_tests: Annotated[
        bool,
        typer.Option(
            "--retry-zero-kept-tests/--no-retry-zero-kept-tests",
            help="Retry only targets marked as zero_kept_tests in reduced adopted summary",
        ),
    ] = bool(DEFAULTS["retry_zero_kept_tests"]),
) -> None:
    _run(
        ctx,
        "llm-integration-step-by-step",
        adopted_dir=str(adopted_dir),
        skip_exists=skip_exists,
        retry_zero_kept_tests=retry_zero_kept_tests,
    )


@llm_app.command("agent", help="Integrate AGT with manual tests using the agent")
def llm_agent(
    ctx: typer.Context,
    adopted_dir: Annotated[Path, typer.Option(help="Directory containing adopted tests")] = Path(
        str(DEFAULTS["adopted_dir"])
    ),
    skip_exists: Annotated[bool, typer.Option(help="Skip targets that already have an agent output")] = bool(
        DEFAULTS["skip_exists"]
    ),
    agent_model: Annotated[str, typer.Option(help="Model name for agent (optional)")] = str(
        DEFAULTS["agent_model"]
    ),
    max_context_files: Annotated[int, typer.Option(help="Max context files for agent")] = int(
        DEFAULTS["agent_max_context_files"]
    ),
    max_context_chars: Annotated[int, typer.Option(help="Max context characters for agent")] = int(
        DEFAULTS["agent_max_context_chars"]
    ),
    max_prompt_chars: Annotated[int, typer.Option(help="Max prompt characters for agent")] = int(
        DEFAULTS["agent_max_prompt_chars"]
    ),
    retry_zero_kept_tests: Annotated[
        bool,
        typer.Option(
            "--retry-zero-kept-tests/--no-retry-zero-kept-tests",
            help="Retry only targets marked as zero_kept_tests in reduced agentic summary",
        ),
    ] = bool(DEFAULTS["retry_zero_kept_tests"]),
) -> None:
    _run_agent(
        ctx,
        adopted_dir=adopted_dir,
        skip_exists=skip_exists,
        agent_model=agent_model,
        max_context_files=max_context_files,
        max_context_chars=max_context_chars,
        max_prompt_chars=max_prompt_chars,
        retry_zero_kept_tests=retry_zero_kept_tests,
    )


@repair_app.command("fix", help="Normalize adopted and agentic tests")
def repair_fix(
    ctx: typer.Context,
    adopted_dir: Annotated[Path, typer.Option(help="Directory containing adopted tests")] = Path(
        str(DEFAULTS["adopted_dir"])
    ),
) -> None:
    _run_steps(ctx, ["adopted-fix"], adopted_dir=str(adopted_dir))


@repair_app.command("comment", help="Comment compile-error lines in adopted and agentic tests")
def repair_comment(
    ctx: typer.Context,
    adopted_dir: Annotated[Path, typer.Option(help="Directory containing adopted tests")] = Path(
        str(DEFAULTS["adopted_dir"])
    ),
) -> None:
    _run_steps(ctx, ["adopted-comment"], adopted_dir=str(adopted_dir))


@repair_app.command("sanitize-es", help="Materialize sanitized EvoSuite tests as *_Sanitized_ESTest")
def repair_sanitize(
    ctx: typer.Context,
    sanitized_es_dir: Annotated[Path, typer.Option(help="Directory to write sanitized EvoSuite tests")] = Path(
        str(DEFAULTS["sanitized_es_dir"])
    ),
) -> None:
    _run_steps(ctx, ["sanitize-es"], sanitized_es_dir=str(sanitized_es_dir))


@coverage_app.command("compare", help="Coverage comparison (manual/auto/adopted)")
def coverage_compare(
    ctx: typer.Context,
    variants: Annotated[str, typer.Option(help="Comma-separated variants: auto,auto-original,adopted,agentic,pr-tests or all")] = "",
) -> None:
    step_configs = _comparison_step_configs(_parse_variants(variants) if variants else set(), step="coverage-comparison")
    if step_configs:
        _run_step_configs(ctx, step_configs)
    raise typer.Exit(code=0)


@coverage_app.command(
    "pr-snapshots",
    help="Measure complete test files at the PR merge base and submitted head",
)
def coverage_pr_snapshots(
    ctx: typer.Context,
    manifest: Annotated[
        Path,
        typer.Option(help="32-target submitted-head and outcome snapshot manifest"),
    ] = Path(str(DEFAULTS["rq4_snapshot_manifest"])),
    output: Annotated[
        Path,
        typer.Option(help="Per-target before/after CUT coverage CSV"),
    ] = Path(str(DEFAULTS["rq4_coverage_output"])),
    build_timeout_seconds: Annotated[
        int,
        typer.Option(help="Maximum native build/test time per revision"),
    ] = int(DEFAULTS["rq4_build_timeout_seconds"]),
) -> None:
    _run_steps(
        ctx,
        ["rq4-snapshot-coverage"],
        rq4_snapshot_manifest=str(manifest),
        rq4_coverage_output=str(output),
        rq4_build_timeout_seconds=build_timeout_seconds,
    )


@coverage_app.command("compare-reduced", help="Coverage comparison for reduced sets")
def coverage_compare_reduced(
    ctx: typer.Context,
    top_n: Annotated[int, typer.Option("--top-n", min=1, help="Top-N reduced tests to compare")]= int(
        DEFAULTS["coverage_compare_top_n"]
    ),
    reduced_out: Annotated[Path, typer.Option(help="Reduced tests output directory")]= Path(
        str(DEFAULTS["reduced_out"])
    ),
    adopted_reduced_out: Annotated[Path, typer.Option(help="Reduced adopted tests output directory")]= Path(
        str(DEFAULTS["adopted_reduced_out"])
    ),
    variants: Annotated[str, typer.Option(help="Comma-separated variants: auto,auto-original,adopted,agentic,pr-tests or all")] = "",
) -> None:
    step_configs = _comparison_step_configs(
        _parse_variants(variants) if variants else set(),
        step="coverage-comparison-reduced",
    )
    if step_configs:
        _run_step_configs(
            ctx,
            step_configs,
            coverage_compare_top_n=top_n,
            reduced_out=str(reduced_out),
            adopted_reduced_out=str(adopted_reduced_out),
        )
    raise typer.Exit(code=0)


def _run_coverage_incremental(
    ctx: typer.Context,
    *,
    variants: str,
    all_classes: bool,
    filtered_dataset: bool,
) -> None:
    parsed_variants = _parse_variants(variants) if variants else set()
    if parsed_variants == {"manual"}:
        print("[agt] Warning: manual variant is the covfilter baseline and has no class_deltas.csv output")
    normalized_variants = ",".join(sorted(parsed_variants)) if parsed_variants else ""
    _run_steps(
        ctx,
        ["coverage-incremental"],
        coverage_incremental_variants=normalized_variants,
        coverage_incremental_all_classes=all_classes,
        filtered_dataset=f"{PIPELINE_OUTPUT_ROOT}/annotation/dataset_filtered.csv" if filtered_dataset else "",
    )


@coverage_app.command("incremental", help="Compare added coverage from covfilter class_deltas.csv")
def coverage_incremental(
    ctx: typer.Context,
    variants: Annotated[
        str,
        typer.Option(help="Comma-separated variants: auto,auto-original,adopted,agentic,pr-tests or all"),
    ] = "",
    all_classes: Annotated[
        bool,
        typer.Option(
            "--all-classes/--no-all-classes",
            help="Include all class_deltas rows instead of only target-class rows",
        ),
    ] = False,
    filtered_dataset: Annotated[
        bool,
        typer.Option(
            "--filtered-dataset/--no-filtered-dataset",
            help="Use results/annotation/dataset_filtered.csv (repo,fqcn) to select targets",
        ),
    ] = False,
) -> None:
    _run_coverage_incremental(ctx, variants=variants, all_classes=all_classes, filtered_dataset=filtered_dataset)


@coverage_app.command(
    "incremental-target-cut",
    help="Build manual-baseline incremental coverage for the exact target CUT from an all-variants matrix",
)
def coverage_incremental_target_cut(
    matrix: Annotated[
        Path,
        typer.Option(help="All-variants coverage matrix used to locate and validate executions"),
    ] = Path(f"{PIPELINE_OUTPUT_ROOT}/aggregate/coverage/coverage_filtered32_all_variants.csv"),
    output: Annotated[
        Path,
        typer.Option(help="Output CSV for exact-target incremental coverage"),
    ] = Path(f"{PIPELINE_OUTPUT_ROOT}/aggregate/coverage/coverage_filtered32_incremental_target_cut.csv"),
    work_dir: Annotated[
        Path,
        typer.Option(help="Temporary JaCoCo report directory"),
    ] = Path(f"{WORKSPACE_ROOT}/pipeline/build/agt/incremental-target-cut"),
    jacoco_cli: Annotated[
        Path,
        typer.Option(help="JaCoCo CLI runnable JAR"),
    ] = Path("vendor/jacoco/org.jacoco.cli-run-0.8.14.jar"),
) -> None:
    try:
        build_incremental_target_cut_report(
            matrix=matrix,
            output=output,
            workspace_root=Path.cwd(),
            work_dir=work_dir,
            jacoco_cli=jacoco_cli,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@coverage_app.command("incremntal", help="Alias for coverage incremental")
def coverage_incremntal_alias(
    ctx: typer.Context,
    variants: Annotated[
        str,
        typer.Option(help="Comma-separated variants: auto,auto-original,adopted,agentic,pr-tests or all"),
    ] = "",
    all_classes: Annotated[
        bool,
        typer.Option(
            "--all-classes/--no-all-classes",
            help="Include all class_deltas rows instead of only target-class rows",
        ),
    ] = False,
    filtered_dataset: Annotated[
        bool,
        typer.Option(
            "--filtered-dataset/--no-filtered-dataset",
            help="Use results/annotation/dataset_filtered.csv (repo,fqcn) to select targets",
        ),
    ] = False,
) -> None:
    _run_coverage_incremental(ctx, variants=variants, all_classes=all_classes, filtered_dataset=filtered_dataset)


if __name__ == "__main__":
    cli()
