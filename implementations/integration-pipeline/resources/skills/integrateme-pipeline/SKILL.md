---
name: integrateme-pipeline
description: Use the IntegrateMe integration pipeline to compile selected Java test variants, execute tests with JaCoCo, collect or compare coverage, filter coverage-contributing methods, and diagnose target-level failures. Apply when the pipeline is explicitly available to an integration agent, not for generic Maven/Gradle testing or manuscript-result regeneration.
---

# IntegrateMe Pipeline

Use the existing CLI instead of reconstructing project-specific compile or coverage
commands. Keep the operation scoped to the requested target and variant.

## Workflow

1. Locate the replication-package root and read the integration pipeline's `README.md`.
   Treat accepted runs and frozen experiment artifacts as evidence, not scratch data.
   When this skill is temporarily linked into a target repository, resolve the skill
   directory to its real location; the pipeline root is three parent directories above
   it (`docs/skills/integrateme-pipeline` to the integration-pipeline root).
2. Work from `implementations/integration-pipeline` and inspect `python -m src
   --help` plus the selected subcommand's help before constructing a command. If the
   default interpreter lacks the dependencies in `requirements.txt`, use the
   repository's configured environment; do not install packages without permission.
3. Resolve the target selector and variant from `experiments/targets.csv`, the test
   inventory, or the relevant manifest. Do not guess between similarly named classes.
4. Verify that the selected test source, repository clone, CUT fat JAR, dependency
   inputs, and JaCoCo tools exist.
5. Read [compile and coverage modes](references/compile-and-coverage.md), then run the
   smallest applicable command. Put the global `--includes` option before the
   subcommand.
6. Inspect the target row in the produced summary and its referenced log. A zero exit
   status alone does not establish that the target compiled, passed, or produced
   usable CUT coverage.
7. Report the exact command, target, variant, source artifact, summary/log paths,
   mechanical outcome, and whether any source remediation occurred.

## Safety and provenance

- Require a target-specific `--includes` selector unless the user explicitly requests
  a cohort-wide run. Never use the `all` workflow for a focused check.
- `compile`, `run`, and `filter` can write build outputs, summaries, JaCoCo execution
  data, and—in remediation paths—modified test sources. Before running against a
  tracked canonical source, inspect its Git state and use an isolated copy or worktree
  when remediation could alter it.
- Do not hand-edit `experiments/<rq>/results/`, `pipeline-output/aggregate/`, or
  generated manuscript artifacts. Do not delete or detach `.exec` files from their
  run directories.
- Do not replace an accepted run merely because a newer attempt exists. Record the new
  attempt and leave acceptance decisions to the experiment ledger.
- This skill operates the pipeline. It does not authorize changing tests, rerunning an
  entire RQ, refreshing PRs, or revising paper claims unless the user asks.
