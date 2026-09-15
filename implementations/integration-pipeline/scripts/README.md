# scripts/

Helpers that sit outside the pipeline package. They fall into three kinds, and
the distinction matters when deciding whether something belongs here or in
`src/steps/`.

## 1. Invoked by the pipeline during a run

Called as subprocesses by pipeline steps. Treat them as part of the tool: they
must stay runnable and their interfaces are depended on.

- `collect_tests.sh` - folds AGT output and repository manual tests into
  `data/collected-tests/`, writing `_logs/tests_inventory.csv`. Invoked by the
  `generate-auto` and `sync` steps.
- `run-agt/run-agt.sh` - vendored AGT (llmsuite) generator and its jar. Invoked
  by `generate-auto`; writes to `pipeline-output/run-agt/`.

## 2. Manual and interactive utilities

Run by a person, usually with judgement in the loop. They are deliberately not
pipeline steps because they need supervision or a human decision between calls.

- `prepare_real_prs.py` - discovery and backup utility for submitting real pull
  requests upstream. Driven per repository (`list --repo`, `backup --repo
  --label`); see `../docs/REAL_PR_WORKFLOW.md`.
- `run_fat_build.sh` - selects a JDK 21 from SDKMAN and delegates to
  `python3 -m src fatjar`.
- `kill_java_and_runmany.sh` - operational helper for clearing stuck JVMs.
- `make_repo_roots.py` - rebuilds `repo_roots.csv` from a `cut_to_fatjar_map.csv`
  and a repos directory.
- `remove_newer_bytecode_from_jars.py` - strips class files compiled for newer
  bytecode levels out of jars, rewriting them in place.

## 3. One-off experiment remediation (`oneoff/`)

Written to repair a specific situation once. They are **not** general behaviour
and must not be promoted into `src/steps/`: they carry hardcoded evaluation
target IDs and may rewrite recorded results in place.

- `oneoff/merge_pr_tests_into_agentic.py` - merges the reduced PR tests into the
  matching agentic outputs under `pipeline-output/llm-out/`. Contains per-target patches
  for T09, T19, T30 and T32, and edits files in place (idempotent via the
  `// Added PR tests` marker). Use `--check` to preview.

## Where does new code go?

If it runs unattended as part of producing results, and its behaviour is the
same for every target, it belongs in `src/steps/` with a CLI command. If it
needs a human between invocations, it belongs in kind 2. If it hardcodes a
target ID, it belongs in `oneoff/` - or it should be generalised first.
