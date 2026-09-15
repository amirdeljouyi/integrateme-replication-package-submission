# IntegrateMe Replication Package

IntegrateMe helps automatically generated Java tests fit into an existing test suite. It
first keeps the EvoSuite tests that add coverage, then adapts them in one of two ways:
with a prompted LLM or with a repository-aware coding agent. We compare these LLM-based
and Agentic-based tests with the coverage-filtered ES-based tests.

This repository brings together the implementation, study data, and final results for
32 target classes. It also includes the submitted and observed versions of the 32 pull
requests used in the contribution study.

## Artifact overview

| Directory | Contents |
| --- | --- |
| [`data/`](data/) | Selected classes under test, repository revisions, and collected generated and existing-suite tests. |
| [`experiments/rq1/`](experiments/rq1/) | Incremental line-coverage results. |
| [`experiments/rq2/`](experiments/rq2/) | Integratedness metrics, sensitivity results, mechanism summaries, and test-smell measurements. |
| [`experiments/rq3/`](experiments/rq3/) | Human-evaluation ratings, aggregate results, paired comparisons, and inter-rater reliability. |
| [`experiments/rq4/`](experiments/rq4/) | Pull-request outcomes, standardized coverage results, and contribution manifests. |
| [`implementations/`](implementations/) | Dataset collection, coverage filtering, LLM service, and integration-pipeline source code. |

## Results by research question

The results are stored as CSV and JSON files. You can open them directly; the
implementations do not need to be installed first.

### RQ1: Incremental line coverage

> How much incremental line coverage do the integrated tests add to the existing test
> suite?

- Start with
  [`rq1_filtered_incremental_coverage.csv`](experiments/rq1/rq1_filtered_incremental_coverage.csv)
  for the three variants' measurements on each target.
- [`rq1_aggregate_summary.csv`](experiments/rq1/rq1_aggregate_summary.csv) summarizes
  coverage and retained methods across all targets.
- [`rq1_pairwise_coverage.csv`](experiments/rq1/rq1_pairwise_coverage.csv) reports the
  paired comparisons between variants.
- [`rq1_standalone_coverage.csv`](experiments/rq1/rq1_standalone_coverage.csv) reports
  coverage for the existing suite and standalone EvoSuite suite.
- [`rq1_variant_ordering.csv`](experiments/rq1/rq1_variant_ordering.csv) and
  [`rq1_ordering_summary.csv`](experiments/rq1/rq1_ordering_summary.csv) describe the
  per-target ordering of the variants.

### RQ2: Fit with the target project

> How well do the integrated tests fit the target project, as measured by our
> integratedness metrics?

- Start with [`rq2_metrics_clean.csv`](experiments/rq2/rq2_metrics_clean.csv) for the
  closeness, conformance, and test-smell measurements on each target.
- [`rq2_primary_comparisons.csv`](experiments/rq2/rq2_primary_comparisons.csv) reports
  the primary paired comparisons.
- [`rq2_mechanism_summary.csv`](experiments/rq2/rq2_mechanism_summary.csv) summarizes the
  mechanisms observed in the test artifacts.
- The sensitivity analyses are in
  [`rq2_closeness_sensitivity.csv`](experiments/rq2/rq2_closeness_sensitivity.csv) and
  [`rq2_metric_sensitivity.csv`](experiments/rq2/rq2_metric_sensitivity.csv).
- [`rq2_human_anchor_summary.csv`](experiments/rq2/rq2_human_anchor_summary.csv) reports
  the human-anchor calibration, while
  [`tsdetect_results.csv`](experiments/rq2/tsdetect_results.csv) provides the underlying
  tsDetect measurements.

### RQ3: Human evaluation

> How do the individual components of the integrated tests perform in terms of clarity,
> naturalness, structure, and integration quality, as evaluated by human assessors?

- Start with [`rq3_ratings_long.csv`](experiments/rq3/rq3_ratings_long.csv) for the
  individual ratings.
- [`rq3_variant_summary.csv`](experiments/rq3/rq3_variant_summary.csv) summarizes each
  criterion by variant.
- [`rq3_paired_comparisons.csv`](experiments/rq3/rq3_paired_comparisons.csv) reports the
  paired statistical comparisons.
- [`rq3_reliability.csv`](experiments/rq3/rq3_reliability.csv) reports ordinal
  Krippendorff's alpha with bootstrap confidence intervals.

### RQ4: Contribution outcomes

> How do maintainers decide whether to merge submitted PRs, and what factors influence
> their decisions?

- [`pr_status.csv`](experiments/rq4/pr_status.csv) gives the state of each pull request.
- [`rq4_per_pr_coverage.csv`](experiments/rq4/rq4_per_pr_coverage.csv) gives the
  before-and-after coverage measurement for each contribution.
- [`rq4_coverage_aggregate.csv`](experiments/rq4/rq4_coverage_aggregate.csv) summarizes
  the line- and branch-coverage results.
- [`pr_snapshot_manifest.csv`](experiments/rq4/pr_snapshot_manifest.csv) maps each target
  and pull request to its recorded revisions and snapshot files.
- [`pr_snapshots/`](experiments/rq4/pr_snapshots/) contains the base, submitted, and
  observed Java files plus the contribution diff for each target.

## Getting the package

```bash
git clone https://github.com/amirdeljouyi/integrateme-replication-package-submission.git
cd integrateme-replication-package-submission
```

After cloning, open the files under `experiments/rq1/` through `experiments/rq4/` in a
spreadsheet application, R, Python, or any other tool that reads CSV and JSON files.

## Software requirements

The result files require no specialized software. Running the implementations requires:

- Python 3.12 and `venv`;
- JDK 17 for the coverage and integration tools;
- Maven 3.9 or later to build the coverage-filter jar;
- JDK 21 for target projects that require it;
- a GitHub token for live repository collection; and
- an OpenAI or Hugging Face credential, or a local Ollama model, for LLM-backed steps.

Use a separate Python virtual environment for each Python component.

## Dataset-collection implementation

[`implementations/dataset-collection/`](implementations/dataset-collection/) contains the
scripts used to enrich GitHub repository metadata, detect Java versions, and select
active classes that have corresponding tests.

```bash
cd implementations/dataset-collection
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export GITHUB_TOKEN=your_token_here
python __main__.py
python recognize_java.py
python select_cut_classes.py
```

Run these commands from the component directory because its input and output paths are
relative to that directory. See the
[`dataset-collection README`](implementations/dataset-collection/README.md) for the input
files, selection criteria, and output schemas.

## Coverage-filter implementation

[`implementations/coverage-filter/`](implementations/coverage-filter/) contains the Java
tool that measures candidate test methods against the existing-suite baseline, removes
methods without an additional coverage contribution, ranks the retained methods, and
generates reduced top-N test classes.

Build and test it with:

```bash
cd implementations/coverage-filter
mvn test
mvn -DskipTests package
```

The shaded jar is written to `target/coverage-filter-1.0-SNAPSHOT.jar`. The command-line
entry points are `app.CoverageFilterApp` for coverage comparison and filtering, and
`app.GenerateReducedAgtTestApp` for top-N reduction. See the
[`coverage-filter README`](implementations/coverage-filter/README.md) for their argument
order and complete examples.

## LLM-server implementation

[`implementations/llm-server/`](implementations/llm-server/) provides the GraphQL service
used by the LLM-based integration path. The service is available at `/graphql` and
accepts `prompt_text`, `prompt_type`, and `additional_param` as defined in
[`schema.py`](implementations/llm-server/schema.py).

```bash
cd implementations/llm-server
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export OPENAI_API_KEY=your_key_here
python -m run_server --model gpt-5 --host 127.0.0.1 --port 8000
```

Model names beginning with `gpt-`, `chatgpt-`, or `o` use OpenAI; names ending in `-hf`
use the configured Hugging Face endpoint; other names use a local Ollama server. See the
[`LLM-server README`](implementations/llm-server/README.md) for component details.

## Integration-pipeline implementation

[`implementations/integration-pipeline/`](implementations/integration-pipeline/)
orchestrates repository cloning, target builds, generated-test collection, compilation,
JaCoCo measurement, coverage filtering, LLM-based integration, metric comparison, and
contribution preparation.

Set up the command-line interface and run its unit tests:

```bash
cd implementations/integration-pipeline
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python -m src --help
python -m unittest discover -s tests -v
```

The launcher reads the package-level data and creates a package-level `workspace/` for
repositories, builds, logs, and intermediate outputs. Start by cloning the target
repositories and building their fat jars:

```bash
./run_integration_pipeline.sh \
  ../../data/collected-tests/_logs/tests_inventory.csv \
  ../../workspace/out/cut_to_fatjar_map.csv \
  clone

./run_integration_pipeline.sh \
  ../../data/collected-tests/_logs/tests_inventory.csv \
  ../../workspace/out/cut_to_fatjar_map.csv \
  fatjar
```

The remaining commands cover compilation, test execution, filtering, reduction, LLM and
agent integration, metric comparison, and coverage measurement. For example, the RQ4
snapshot measurement uses:

```bash
python -m src coverage pr-snapshots \
  --manifest ../../experiments/rq4/pr_snapshot_manifest.csv
```

See the [`integration-pipeline README`](implementations/integration-pipeline/README.md)
for the complete command sequence and configuration options.
