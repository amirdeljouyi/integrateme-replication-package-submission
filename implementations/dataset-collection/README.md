# Dataset Collection Process

This folder builds a ranked list of Java repositories and then selects CUTs
(Classes Under Test) per repository.

The process is driven mainly by:

- `__main__.py` (metadata enrichment from GitHub APIs),
- `data/analysis.ipynb` (ranking/scoring + top-project selection),
- `select_cut_classes.py` (class-level CUT selection).

Before running the scripts, export either:

- `GITHUB_TOKEN` for a single token, or
- `GITHUB_TOKENS` for multiple tokens separated by commas or newlines.

Example:

```bash
export GITHUB_TOKEN=your_token_here
```

## Pipeline Overview

1. Start from a repository list (`data/initial_projects.csv`).
2. Deduplicate to `data/output.csv` (done in `analysis.ipynb` first cells).
3. Enrich repo metadata via GitHub APIs (`__main__.py`) -> `data/selected_projects_add.csv`.
4. Score/filter projects in `analysis.ipynb` and export top projects
   (notebook currently writes `data/top_200_projects_java.csv`).
5. Select active tested classes per project (`select_cut_classes.py`) ->
   `data/selected_cut_classes.csv`.

## Step 1: Enrich Repository Metadata (`__main__.py`)

Run from repository root:

```bash
python implementations/dataset-collection/__main__.py
```

Inputs/outputs:

- Input: `implementations/dataset-collection/data/output.csv`
- Output: `implementations/dataset-collection/data/selected_projects_add.csv`
- Required input column: `Name` (`owner/repo` format)

What it collects per repo:

- Popularity: stars, forks, watchers
- Issue/PR counts: open/closed/merged totals
- Release and commit history counts
- Repo status flags: private/fork/archived/issues enabled
- Activity dates: created/updated/pushed
- Workflow heuristics:
  - workflow filename contains `test`
  - workflow content mentions test commands (`mvn test`, `gradle test`, etc.)
- Community maturity:
  - `has_contributing` (checks common CONTRIBUTING file locations)
  - `unique_pr_authors` (paginated GraphQL)
- Java file counts from repo tree (`test_files`, `src_files`)

Implementation notes:

- Uses round-robin token rotation across tokens loaded from `GITHUB_TOKEN` / `GITHUB_TOKENS`.
- Uses both GraphQL (`/graphql`) and REST (`/repos/...`) endpoints.

## Step 2: Rank and Filter Projects (`data/analysis.ipynb`)

Open and run notebook:

```bash
jupyter notebook implementations/dataset-collection/data/analysis.ipynb
```

Notebook stages:

1. Deduplicate `initial_projects.csv` by repository name and produce `output.csv`.
2. Build score components from enriched data:
   - `popularity_score`
   - `activity_score`
   - `pr_score`
   - `testing_score`
   - `community_score`
   - `maturity_score` (in later notebook cells)
3. Compute `overall_score` from weighted components.
4. Apply filters (final pass in notebook):
   - `language == "Java"`
   - `test_ratio >= 0.01`
   - `test_files >= 10`
   - normalized `java_version_num <= 21`
5. Sort by `overall_score` and export top 200.

Notebook output used for selection:

- `implementations/dataset-collection/data/top_200_projects_java.csv`

## Step 3: Select CUT Classes (`select_cut_classes.py`)

Run from repository root:

```bash
python implementations/dataset-collection/select_cut_classes.py
```

Default config in script:

- Input file: `./data/top_200_projects.csv`
- Output file: `./data/selected_cut_classes.csv`
- `NUM_TOP_PROJECTS = 100`
- `CUTS_PER_PROJECT = 2`
- `MIN_COMMITS_PER_CLASS = 10`
- `RECENT_DAYS_THRESHOLD = 730` days

Selection logic:

1. Fetch full repo tree (`git/trees/HEAD?recursive=1`).
2. Classify Java files into source/test by path heuristics.
3. Match source class to tests by naming:
   - `FooTest`, `FooTests`, `FooIT`, `TestFoo`
4. For each source file, fetch commit history for that path:
   - keep if commits >= threshold and file updated recently
5. Rank candidates by:
   - higher `commit_count`
   - more recent `last_commit_date`
6. Keep top K classes per project.

Output schema (`selected_cut_classes.csv`):

- `repo`
- `class_path`
- `test_paths` (semicolon-separated)
- `commit_count`
- `last_commit_date`

## End-to-End Repro Command Sequence

From repository root:

```bash
python implementations/dataset-collection/__main__.py
python implementations/dataset-collection/recognize_java.py
# run notebook cells in implementations/dataset-collection/data/analysis.ipynb
python implementations/dataset-collection/select_cut_classes.py
```

If you want CUT selection to use notebook output directly, either:

- change `TOP_PROJECTS_CSV` in `select_cut_classes.py` to `./data/top_200_projects_java.csv`, or
- copy/rename that file to `./data/top_200_projects.csv`.

## Dependencies

Install requirements (one of):

```bash
pip install -r implementations/dataset-collection/requirements.txt
```

or (older notebook-focused set):

```bash
pip install -r implementations/dataset-collection/virtualenv-requirements.txt
```

## Known Caveat

Current `__main__.py` has a bug in `count_java_tests_and_sources_from_tree`
(`implementations/dataset-collection/__main__.py:176`) where the test-folder helper logic is
accidentally replaced by an early `return`. That can make `test_files/src_files`
incorrect during enrichment. `select_cut_classes.py` has a correct test-folder
heuristic implementation and is not affected by this specific issue.
