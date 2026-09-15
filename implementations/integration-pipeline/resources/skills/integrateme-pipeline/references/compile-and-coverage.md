# Compile and coverage modes

All commands run from `implementations/integration-pipeline`. Use the interpreter from
the configured project environment when plain `python` cannot import the dependencies.

## Select one target

`--includes` is a global option and therefore precedes the subcommand:

```bash
python -m src --includes 'com.example.TargetClass' <subcommand>
```

Confirm the exact selector from the canonical target or inventory row. Do not broaden
the pattern merely because a first attempt selects no target.

## Compile generated or existing tests

The standalone `compile` command supports `auto`, `auto-original`, and `manual`:

```bash
python -m src --includes 'com.example.TargetClass' compile --variants auto,manual
```

Use `--skip-passed` when the request does not require replacing a previously passing
compile attempt. The compiler may retry through source remediation; the summary reports
`remediated_compile` and the log identifies the affected source. Do not describe such a
result as an unchanged-source compile.

The LLM-based, Agentic-based, and PR-test variants compile as part of their `run` or
`filter` paths rather than through the standalone `compile` command.

Primary output:

- `pipeline-output/compile/compile_summary.csv`
- the row-specific `log_file` recorded in that CSV

## Execute tests and collect JaCoCo coverage

Choose only the variants needed:

```bash
python -m src --includes 'com.example.TargetClass' run --variants auto,manual
python -m src --includes 'com.example.TargetClass' run --variants adopted,agentic
python -m src --includes 'com.example.TargetClass' run --variants pr-tests
```

Use `auto-100` or `improved` only when their reduced source lineage is the intended
input. For `auto-100`, confirm the `--auto-100-dir` source rather than assuming the
default contains the desired run.

Inspect:

- `pipeline-output/coverage/coverage_summary.csv`
- `pipeline-output/coverage/coverage_errors.csv`
- `pipeline-output/coverage/coverage_zero_hit.csv`
- `pipeline-output/coverage/coverage_report_issues.csv`
- the target's JaCoCo execution/report artifacts and logs

Distinguish compilation failure, test failure, zero CUT hits, and coverage-report
mapping failure. They are different outcomes.

## Identify coverage-contributing methods

Use `filter` when the question is which individual generated or adapted methods add
coverage beyond the existing-test baseline:

```bash
python -m src --includes 'com.example.TargetClass' filter --variants auto
python -m src --includes 'com.example.TargetClass' filter --variants adopted,agentic
python -m src --includes 'com.example.TargetClass' filter --variants pr-tests
```

Prefer `--skip-passed` or `--skip-passed-by-status` for a diagnostic check that should
not repeat a verified filter run. Do not enable compile-line commenting merely to obtain
a passing result unless the user explicitly requests that remediation and the changed
source is reviewed.

Inspect the selected variant's `class_deltas.csv`, method/test deltas, summary row, and
compile/run logs. A method contributes only when its incremental delta is positive under
the recorded baseline.

## Compare already collected coverage

These commands aggregate existing measurements; they do not replace the prerequisite
compile, execution, or filter stages:

```bash
python -m src --includes 'com.example.TargetClass' coverage compare --variants auto,agentic
python -m src --includes 'com.example.TargetClass' coverage compare-reduced --variants auto,agentic --top-n 5
python -m src --includes 'com.example.TargetClass' coverage incremental --variants auto,agentic
```

Use `coverage incremental-target-cut` only with a verified all-variants matrix. Use
`coverage pr-snapshots` only for the frozen RQ4 manifest workflow; it compares Git
revisions and is not a substitute for RQ1 coverage collection.

## Diagnose before retrying

For a failed target:

1. Read the latest target/variant summary row and its log.
2. Confirm the source and repository revision used by that attempt.
3. Classify the failure as missing input, compilation, test execution, zero hit,
   report extraction, timeout, or environment mismatch.
4. Retry only the failed stage with the same target selector and explicit variant.
5. Preserve the failed attempt and report whether the retry changed inputs or merely
   corrected the execution environment.
