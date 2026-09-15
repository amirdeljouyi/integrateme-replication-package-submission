# Coverage Filter Tool

Java tool for:

1. filtering generated tests against a manual-test baseline,
2. prioritizing test methods by added coverage impact, and
3. generating reduced top-N test classes.

This module is used by the integration pipeline, but can also be run directly.

## What It Does

Given:

- one manual test class (baseline), and
- one generated/adopted test class (candidate),

the tool runs each candidate test method with JaCoCo and keeps only methods that add new coverage beyond the current kept set.

It then ranks methods by added impact and can generate a reduced `<TestClass>_TopN` source containing only the most impactful test methods.

## Main Entrypoints

- `app.CoverageFilterApp`
  - `class` mode: class-level comparison (manual vs generated class).
  - `filter` mode: incremental method-level keep/drop + ranking CSVs.
- `app.GenerateReducedAgtTestApp`
  - Reads `test_deltas_kept.csv` and generates a reduced top-N class.

## Build

From this `coverage/` directory:

```bash
mvn -DskipTests package
```

Output jar (shaded/fat jar):

- `target/coverage-filter-1.0-SNAPSHOT.jar`

## Filtering and Prioritization Logic

Filtering is implemented in `CoverageFilterApp#runIncrementalFiltering`.

1. Run manual test class only, store `baseline_manual.exec`.
2. Discover candidate test methods from the generated class (`ListTests`).
3. Methods are evaluated in alphabetical order.
4. For each method `M`:
   1. Run `[manualClass, generatedClass#M]` in one fork with JaCoCo.
   2. Build `CoverageSet` from the run.
   3. Keep `M` iff candidate coverage adds any unit beyond current coverage (`CoverageSet#addsAnythingBeyond`).
5. After all methods, run `[manualClass + all kept methods]` for final aggregate deltas.

Coverage unit granularity (`CoverageAnalyzer#analyze`):

- `pkg/Foo::bar|LINE` when method has covered lines.
- `pkg/Foo::bar|BRANCH` when method has covered branches.

### Ranking (Prioritization)

Each candidate gets totals vs manual baseline:

- `added_lines`
- `added_methods`
- `added_branches`
- `added_instructions`

Sort order (descending):

1. `added_lines`
2. `added_instructions`
3. `added_branches`
4. `added_methods`

This ranking is written to:

- `test_deltas_all.csv` (all candidates)
- `test_deltas_kept.csv` (kept-only)

## Reduction Logic

Reduction is implemented by `GenerateReducedAgtTestApp` + `TopNReducedTestClassGenerator`.

Given original test source + `test_deltas_kept.csv`:

1. Parse CSV rows into `TestDelta`.
2. Sort by the same ranking order (unless `sort=false`).
3. Take top-N selectors.
4. Generate `<OriginalClass>_TopN.java` that keeps:
   - selected top-N test methods,
   - all lifecycle methods,
   - all non-test helper methods,
   - all fields and constructors.

If zero test methods are selected/copied, generation fails.

## CSV Outputs (filter mode)

Created under `<workDir>`:

- `kept_agt.csv`
  - Header: `test_selector`
- `test_deltas_all.csv`
  - Header: `test_selector,added_lines,added_methods,added_branches,added_instructions`
- `test_deltas_kept.csv`
  - Same schema as above, kept-only
- `class_deltas.csv`
  - Header: `class_name,added_lines,added_methods,added_branches,added_instructions`
- `line_deltas_kept.csv`
  - Header: `test_selector,class_name,newly_covered_lines,upgraded_to_full_lines`
  - Line ranges are compacted as `a-b;c;d-e`.

## Run CoverageFilterApp Directly

Usage:

```text
<mode: class|filter>
<classesDirRel> <workDirRel>
<manualTestClass> <agtTestClass>
<jacocoAgentRel> <sutClassesRel> <libsDir>
<testClassesDirRel>
[testTimeoutMs]
```

Example (`filter` mode):

```bash
java -cp "libs/*:target/coverage-filter-1.0-SNAPSHOT.jar:/path/to/test-classes" \
  app.CoverageFilterApp \
  filter \
  /path/to/sut/classes-or-fatjar \
  /tmp/covfilter/out \
  com.example.ManualTest \
  com.example.Generated_ESTest \
  /path/to/org.jacoco.agent-run-0.8.14.jar \
  /path/to/sut/classes-or-fatjar \
  /path/to/libs \
  /path/to/test-classes \
  240000
```

Notes:

- `libsDir` must be a directory, not `libs/*`.
- `classesDirRel` can be a classes directory or a fat jar; jar input is auto-extracted for analysis.
- Non-absolute paths are resolved relative to the jar location.
- Timeout defaults to 2 minutes per test (`test.timeout.ms` / `TEST_TIMEOUT_MS`).
- Test forks exit explicitly after reporting results, so application-created
  non-daemon threads cannot keep `RunMany` alive. The parent runner also applies
  a selector-count-aware deadline and terminates the complete process tree if a
  fork does not exit during the grace period.
- Optional JVM property `jacoco.includes` is passed into JaCoCo agent args if set.

## Run Reduction Directly

Usage:

```text
<originalTestJava> <testDeltasCsv> <N> <outDir> [sort=true|false]
```

Example:

```bash
java -cp "target/coverage-filter-1.0-SNAPSHOT.jar" \
  app.GenerateReducedAgtTestApp \
  /path/to/QuteProcessor_ESTest.java \
  /tmp/covfilter/out/test_deltas_kept.csv \
  5 \
  /tmp/reduced \
  true
```

## Run `RunMany` in Background on macOS

A helper script is provided at:

- `scripts/runmany-daemon-macos.sh`

Make it executable once:

```bash
chmod +x scripts/runmany-daemon-macos.sh
```

Start in background (detached):

```bash
scripts/runmany-daemon-macos.sh start \
  --cp "libs/*:target/coverage-filter-1.0-SNAPSHOT.jar:/path/to/test-classes:/path/to/sut-classes" \
  --timeout-ms 240000 \
  com.example.Generated_ESTest#testMethodA \
  com.example.Generated_ESTest#testMethodB
```

Manage process:

```bash
scripts/runmany-daemon-macos.sh status
scripts/runmany-daemon-macos.sh logs --follow
scripts/runmany-daemon-macos.sh stop
```

By default, PID/log files are stored under `.runmany-daemon/`.
Set `RUNMANY_DAEMON_DIR` to override this location.

## Internal Class Map

- `app.CoverageFilterApp`: orchestrates class/filter modes.
- `app.ForkedJacocoRunner`: forks JVMs with JaCoCo agent and executes selectors.
- `app.ListTests`: discovers test methods (JUnit 4/5).
- `app.RunMany`: runs `fqcn` or `fqcn#method` selectors.
- `jacoco.CoverageAnalyzer`: computes coverage sets and deltas.
- `io.CsvReportWriter`: writes filtering output CSVs.
- `app.GenerateReducedAgtTestApp`: top-N reduction CLI.
- `io.TopNReducedTestClassGenerator`: Spoon-based reduced source generation.
