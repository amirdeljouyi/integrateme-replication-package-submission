# IntegrateMe Replication Package

This repository is the compact replication package for the IntegrateMe study. It
contains the implementation of the toolchain, the study dataset, and the final results
for all four research questions.

The package is intended for three uses:

1. inspect the final reported results;
2. reuse or test an individual implementation; and
3. reproduce selected pipeline stages with a newly created workspace.

It is a submission snapshot, not a byte-for-byte archive of every experimental run.
Intermediate outputs, validation and legacy directories, analysis and chart-generation
scripts, notebooks, workspaces, and internal project documentation are deliberately
excluded. Consequently, the final tables can be inspected directly, while an exact
end-to-end regeneration requires the archived inputs and intermediate state used by the
authors.

## Repository layout

| Path | Contents |
| --- | --- |
| [`data/`](data/) | Selected classes under test, repository revisions, and collected generated and existing-suite tests. |
| [`experiments/rq1/`](experiments/rq1/) | Final incremental-coverage results. |
| [`experiments/rq2/`](experiments/rq2/) | Final integration-quality results, metric sensitivities, and tsDetect output. |
| [`experiments/rq3/`](experiments/rq3/) | Final human-evaluation ratings, summaries, paired comparisons, and reliability results. |
| [`experiments/rq4/`](experiments/rq4/) | Final pull-request outcomes and standardized coverage results. |
| [`experiments/rq4/pr_snapshots/`](experiments/rq4/pr_snapshots/) | Submitted and observed contribution snapshots for the 32 study targets. |
| [`implementations/coverage-filter/`](implementations/coverage-filter/) | Java coverage filtering, prioritization, and top-N reduction tool. |
| [`implementations/dataset-collection/`](implementations/dataset-collection/) | Python repository-enrichment and class-selection tools. |
| [`implementations/llm-server/`](implementations/llm-server/) | GraphQL service used to send integration prompts to an LLM. |
| [`implementations/integration-pipeline/`](implementations/integration-pipeline/) | Python orchestration for compilation, coverage, filtering, integration, comparison, and contribution preparation. |

## Inspecting the final results

All final tabular results are CSV or JSON files directly inside the corresponding RQ
directory. They can be opened with any spreadsheet or statistical package; no pipeline
execution is needed.

| RQ | Study focus | Useful starting files |
| --- | --- | --- |
| RQ1 | Incremental coverage of the ES-based, LLM-based, and Agentic-based variants | [`rq1_aggregate_summary.csv`](experiments/rq1/rq1_aggregate_summary.csv), [`rq1_filtered_incremental_coverage.csv`](experiments/rq1/rq1_filtered_incremental_coverage.csv), and [`rq1_pairwise_coverage.csv`](experiments/rq1/rq1_pairwise_coverage.csv) |
| RQ2 | Integration quality and mechanisms | [`rq2_metrics_clean.csv`](experiments/rq2/rq2_metrics_clean.csv), [`rq2_primary_comparisons.csv`](experiments/rq2/rq2_primary_comparisons.csv), and [`rq2_mechanism_summary.csv`](experiments/rq2/rq2_mechanism_summary.csv) |
| RQ3 | Human evaluation | [`rq3_ratings_long.csv`](experiments/rq3/rq3_ratings_long.csv), [`rq3_variant_summary.csv`](experiments/rq3/rq3_variant_summary.csv), [`rq3_paired_comparisons.csv`](experiments/rq3/rq3_paired_comparisons.csv), and [`rq3_reliability.csv`](experiments/rq3/rq3_reliability.csv) |
| RQ4 | Contribution outcomes and coverage | [`pr_status.csv`](experiments/rq4/pr_status.csv), [`rq4_coverage_aggregate.csv`](experiments/rq4/rq4_coverage_aggregate.csv), [`rq4_per_pr_coverage.csv`](experiments/rq4/rq4_per_pr_coverage.csv), and [`pr_snapshot_manifest.csv`](experiments/rq4/pr_snapshot_manifest.csv) |

For a quick command-line inspection:

```bash
git clone https://github.com/amirdeljouyi/integrateme-replication-package-submission.git
cd integrateme-replication-package-submission

# Preview one aggregate result file.
python -c "import csv; print(*csv.DictReader(open('experiments/rq1/rq1_aggregate_summary.csv')), sep='\n')"

# List the final result files.
find experiments -maxdepth 2 -type f | sort
```

## Requirements

- Git
- Python 3.12 and `venv` for the Python components
- JDK 17 for the integration pipeline
- Maven 3.9 or later to rebuild the coverage-filter jar
- JDK 21 for target projects whose Maven or Gradle builds require it
- A GitHub token for live dataset collection
- An LLM provider credential or a local Ollama installation for LLM-backed integration

Create separate virtual environments for the Python components because their dependency
sets serve different purposes.

## Using the dataset-collection tools

These scripts query GitHub, enrich repository metadata, detect Java versions, and select
active classes with corresponding tests. Run them from their own directory because their
input paths are relative to that directory.

```bash
cd implementations/dataset-collection
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# Use a fine-grained token with read-only access to public repository metadata.
export GITHUB_TOKEN=your_token_here

python __main__.py
python recognize_java.py
python select_cut_classes.py
```

The stored CSV inputs let you inspect and reuse these stages. The ranking notebook used
between enrichment and class selection is intentionally excluded from this compact
package, so this directory alone does not recreate the original ranked project list from
scratch. Configuration constants and output schemas are documented in the
[`dataset-collection README`](implementations/dataset-collection/README.md).

## Using the coverage-filter tool

The Java tool measures candidate test methods against an existing-suite baseline, keeps
methods that add coverage, ranks them by coverage contribution, and can emit a reduced
top-N test class.

```bash
cd implementations/coverage-filter
mvn -DskipTests package
```

The shaded jar is written to
`target/coverage-filter-1.0-SNAPSHOT.jar`. Its two main entry points are:

- `app.CoverageFilterApp` for class-level comparison or incremental method filtering;
- `app.GenerateReducedAgtTestApp` for top-N source reduction.

Both commands need compiled test classes, the system-under-test classes or fat jar, the
JaCoCo agent, and the runtime dependency directory. Their argument order and complete
examples are in the [`coverage-filter README`](implementations/coverage-filter/README.md).

## Using the LLM server

The LLM server exposes a Strawberry GraphQL endpoint at `/graphql`. It routes model names
beginning with `gpt-`, `chatgpt-`, or `o` to OpenAI, names ending in `-hf` to a Hugging
Face endpoint, and other names to a local Ollama server.

```bash
cd implementations/llm-server
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# OpenAI example. Keep credentials in the environment; never commit them.
export OPENAI_API_KEY=your_key_here
python -m run_server --model gpt-5 --host 127.0.0.1 --port 8000
```

Alternative provider configuration:

```bash
# Hugging Face: the model name must end in -hf.
export HF_KEY=your_key_here
export HF_URL=https://your-inference-endpoint.example
python -m run_server --model your-model-hf --host 127.0.0.1 --port 8000

# Ollama: start Ollama first, then use its local model name.
python -m run_server --model codellama:7b-instruct --host 127.0.0.1 --port 8000
```

The server accepts the `Prompt` fields `prompt_text`, `prompt_type`, and
`additional_param`; see [`schema.py`](implementations/llm-server/schema.py) and the
[`LLM-server README`](implementations/llm-server/README.md).

## Using the integration pipeline

The integration pipeline orchestrates repository cloning, target builds, generated-test
collection, compilation, JaCoCo measurement, coverage filtering, LLM-based integration,
metric comparison, and contribution preparation.

Set up and verify the Python CLI first:

```bash
cd implementations/integration-pipeline
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python -m src --help
python -m unittest discover -s tests -v
```

The main launcher uses the package-level `data/` directory and creates a new
package-level `workspace/` for cloned repositories, builds, temporary files, and
intermediate pipeline outputs:

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

Continue with a specific command instead of `clone` or `fatjar`, for example `compile`,
`run`, `filter`, `reduce`, `llm all`, `llm agent`, `coverage incremental-target-cut`, or
`coverage pr-snapshots`. To rerun the RQ4 snapshot measurement, use:

```bash
python -m src coverage pr-snapshots \
  --manifest ../../experiments/rq4/pr_snapshot_manifest.csv
```

These operations can be expensive: they clone and build third-party repositories, call
external model services, and write intermediate files under `workspace/`. The workspace
and pipeline outputs are intentionally not part of this repository. See the
[`integration-pipeline README`](implementations/integration-pipeline/README.md) for the
full command sequence, configuration flags, and output paths.

## Reproducibility boundary

The final result files and the implementation source are archival artifacts. The
following omitted material is needed for an exact end-to-end rerun of the reported study:

- intermediate pipeline outputs and compiled target workspaces;
- validation and legacy experiment material;
- analysis and chart-generation scripts;
- notebooks and internal operational documentation; and
- external repository and model-service state that may change over time.

Use the recorded revisions in [`data/repo_head_hashes.txt`](data/repo_head_hashes.txt)
and the RQ4 snapshot manifest when reconstructing target repositories. Newly executed
LLM or GitHub-dependent stages should be treated as a new run, not as a deterministic
recreation of the archived results.

## Release and Zenodo archiving

GitHub releases provide a versioned source archive. To archive a release in Zenodo:

1. sign in to Zenodo with GitHub and enable this repository in Zenodo's GitHub settings;
2. publish a GitHub release from the version tag;
3. wait for Zenodo to create the deposition, complete its metadata, and publish it; and
4. add the resulting DOI badge and citation to this README in a follow-up release.

Until a DOI is assigned, cite the GitHub release by repository name, version, release
date, and release URL. The license for reuse should be selected explicitly by the
authors in GitHub and Zenodo; this snapshot does not infer one.

## Scope and privacy notes

- API keys and GitHub tokens are read from environment variables and are not stored in
  the repository.
- The final RQ4 Java files include test fixtures and literal example credentials inherited
  from upstream projects; these are research snapshots, not operational secrets.
- Machine-specific absolute paths in generated test fixtures are replaced with neutral
  `/path/to/` prefixes; these values are not required as local installation paths.
