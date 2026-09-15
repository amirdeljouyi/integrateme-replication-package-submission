#!/usr/bin/env bash
set -euo pipefail

export JDK_JAVA_OPTIONS="-Djdk.attach.allowAttachSelf=true"

# Usage:
#   ./run_evosuite_from_cut_map.sh <cut_to_fatjar_map.csv> [attempt]
#
# Requirements:
# - llmsuite-r.jar in current dir (or adjust path)
# - repos checked out under REPOS_DIR (default: ./repos)
#
# Behavior:
# - First run with fatjar only
# - If error log shows ClassNotFound/ClassNotFoundException -> compute runtime CP and retry
# - Optional: filter to classes without existing generated tests in tests inventory CSV

CSV_PATH="${1:-}"
ATTEMPT="${2:-}"

if [[ -z "${CSV_PATH}" || ! -f "${CSV_PATH}" ]]; then
  echo "ERROR: Provide cut_to_fatjar_map.csv as the first argument."
  echo "Example: $0 ./out/cut_to_fatjar_map.csv"
  exit 1
fi

# Defaults follow the repository layout (see ../../src/core/layout.py). The
# pipeline exports all three as absolute paths, so these only apply to
# standalone runs.
AGT_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AGT_REPO_ROOT="$(cd -- "${AGT_SCRIPT_DIR}/../../../.." && pwd)"

: "${REPOS_DIR:=${AGT_REPO_ROOT}/workspace/repos}"
: "${OUTPUT_DIR:=${AGT_REPO_ROOT}/results/run-agt}"
: "${ONLY_MISSING_GENERATED:=1}"   # 1 => run only rows with no generated tests
: "${ONLY_MISSING_SCOPE:=class}"   # class => skip only exact (repo,fqcn), repo => skip whole repo if any generated exists
: "${TESTS_INVENTORY_CSV:=${AGT_REPO_ROOT}/data/collected-tests/_logs/tests_inventory.csv}"
: "${AGT_ASSERTION_TIMEOUT:=100000}"
: "${AGT_WRITE_JUNIT_TIMEOUT:=100000}"
: "${AGT_JUNIT_CHECK_TIMEOUT:=10000}"
: "${AGT_ASSERTIONS:=true}"
: "${AGT_INLINE:=true}"

# The row-filtering step below runs in a python subprocess and reads these from
# the environment. `:` assignment alone does not export, so a standalone run
# would silently fall back to the python defaults.
export ONLY_MISSING_GENERATED ONLY_MISSING_SCOPE TESTS_INVENTORY_CSV

LOG_DIR="${OUTPUT_DIR}/log"
mkdir -p "${LOG_DIR}" "${OUTPUT_DIR}/evosuite-report" "${OUTPUT_DIR}/generated-tests"
FAILURE_SUMMARY_CSV="${LOG_DIR}/generate-auto.failures.csv"

# Attempt naming (same as your old behavior)
if [[ -z "${ATTEMPT}" ]]; then
  PREFIX="$(date '+%Y-%m-%d-%H-%M')"
  POSTFIX="/${PREFIX}"
  JUNIT_SUFFIX="_ESTest"
  DATEMPT=""
else
  PREFIX="${ATTEMPT}"
  POSTFIX=""
  JUNIT_SUFFIX="_${ATTEMPT}_ESTest"
  DATEMPT="-Dattempt=${ATTEMPT}"
fi

GLOBAL_OUT="${LOG_DIR}/global_output.log"
GLOBAL_ERR="${LOG_DIR}/global_error.log"
: > "${GLOBAL_OUT}"
: > "${GLOBAL_ERR}"
printf '%s\n' 'repo,fqcn,build_tool,module_rel,class_path,fatjar_path,stage,outcome_reason,failure_category,failure_detail,stat_log,error_log,retry_stat_log,retry_error_log' > "${FAILURE_SUMMARY_CSV}"

safe_name() { echo "$1" | sed 's#/#_#g; s#[^A-Za-z0-9._-]#_#g'; }
safe_id() { echo "$1" | sed 's#[^A-Za-z0-9._-]#_#g'; }

is_bad_fatjar() {
  local p="$1"
  [[ -z "$p" ]] && return 0
  [[ "$p" == "FAIL" || "$p" == "SKIP" || "$p" == "SKIP-REPO" ]] && return 0
  [[ "$p" == SKIP:* ]] && return 0
  return 1
}

log_matches() {
  local pattern="$1"
  shift
  local file
  for file in "$@"; do
    [[ -f "$file" ]] || continue
    if grep -qE "$pattern" "$file"; then
      return 0
    fi
  done
  return 1
}

needs_cp_retry() {
  local statlog="$1"
  local errlog="$2"
  log_matches "Unknown class:|ClassNotFoundException|Class not found:|NoClassDefFoundError|should be in target project, but could not be found" "$statlog" "$errlog"
}

has_generation_failure() {
  local statlog="$1"
  local errlog="$2"
  log_matches "Fatal crash on main EvoSuite process|The JVM of the client process crashed|Lost connection with clients|failed to generate any test case|Error when generating tests for:|Compilation failed on compilation units|failed to write statistics data|Not going to write down statistics data, as some are missing" "$statlog" "$errlog"
}

generated_test_file() {
  local fqcn="$1"
  local rel_path="${fqcn//./\/}"
  printf '%s/generated-tests%s/%s%s.java\n' "${OUTPUT_DIR}" "${POSTFIX}" "${rel_path}" "${JUNIT_SUFFIX}"
}

has_generated_test_output() {
  local fqcn="$1"
  local test_file
  test_file="$(generated_test_file "$fqcn")"
  [[ -f "$test_file" ]]
}

csv_escape() {
  local value="${1:-}"
  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  value="${value//\"/\"\"}"
  printf '"%s"' "$value"
}

first_failure_detail() {
  local file
  local detail=""
  for file in "$@"; do
    [[ -f "$file" ]] || continue
    detail="$(grep -hEm1 "Unknown class:|No statistics has been saved because EvoSuite failed to generate any test case|Fatal crash on main EvoSuite process|Compilation failed on compilation units|ClassNotFoundException|NoClassDefFoundError|MarshalException|ServerException|OutOfMemoryError|Error when generating tests for:|Caused by:" "$file" || true)"
    if [[ -n "$detail" ]]; then
      printf '%s\n' "$detail"
      return 0
    fi
  done
  return 0
}

classify_failure() {
  if log_matches "Unknown class:" "$@"; then
    echo "unknown_class_in_projectcp"
  elif log_matches "No statistics has been saved because EvoSuite failed to generate any test case|failed to generate any test case" "$@"; then
    echo "no_test_case_generated"
  elif log_matches "Fatal crash on main EvoSuite process" "$@"; then
    echo "evosuite_fatal_crash"
  elif log_matches "Compilation failed on compilation units" "$@"; then
    echo "generated_test_compile_failure"
  elif log_matches "ClassNotFoundException|NoClassDefFoundError|should be in target project, but could not be found|cannot find symbol" "$@"; then
    echo "missing_class_or_dep"
  elif log_matches "OutOfMemoryError" "$@"; then
    echo "oom"
  else
    echo "other"
  fi
}

append_failure_summary() {
  local repo="$1"
  local fqcn="$2"
  local build_tool="$3"
  local module_rel="$4"
  local class_path="$5"
  local fatjar="$6"
  local stage="$7"
  local outcome_reason="$8"
  local stat_log="$9"
  local err_log="${10}"
  local retry_stat_log="${11}"
  local retry_err_log="${12}"
  local failure_category
  local failure_detail
  failure_category="$(classify_failure "$retry_stat_log" "$retry_err_log" "$stat_log" "$err_log")"
  failure_detail="$(first_failure_detail "$retry_stat_log" "$retry_err_log" "$stat_log" "$err_log")"
  {
    csv_escape "$repo"; printf ','
    csv_escape "$fqcn"; printf ','
    csv_escape "$build_tool"; printf ','
    csv_escape "$module_rel"; printf ','
    csv_escape "$class_path"; printf ','
    csv_escape "$fatjar"; printf ','
    csv_escape "$stage"; printf ','
    csv_escape "$outcome_reason"; printf ','
    csv_escape "$failure_category"; printf ','
    csv_escape "$failure_detail"; printf ','
    csv_escape "$stat_log"; printf ','
    csv_escape "$err_log"; printf ','
    csv_escape "$retry_stat_log"; printf ','
    csv_escape "$retry_err_log"; printf '\n'
  } >> "${FAILURE_SUMMARY_CSV}"
}

source_module_rel_from_class_path() {
  local class_path="$1"
  local rel=""
  if [[ "$class_path" == *"/src/main/java/"* ]]; then
    rel="${class_path%%/src/main/java/*}"
  elif [[ "$class_path" == *"/src/main/kotlin/"* ]]; then
    rel="${class_path%%/src/main/kotlin/*}"
  elif [[ "$class_path" == *"/src/test/java/"* ]]; then
    rel="${class_path%%/src/test/java/*}"
  elif [[ "$class_path" == *"/src/test/kotlin/"* ]]; then
    rel="${class_path%%/src/test/kotlin/*}"
  else
    return 1
  fi
  if [[ -z "$rel" ]]; then
    echo "."
  else
    echo "$rel"
  fi
}

source_module_dir_from_class_path() {
  local repo_dir="$1"
  local class_path="$2"
  local rel
  rel="$(source_module_rel_from_class_path "$class_path" 2>/dev/null || true)"
  if [[ -z "$rel" || "$rel" == "." ]]; then
    echo "$repo_dir"
  else
    echo "$repo_dir/$rel"
  fi
}

jar_files_cp_from_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  find "$dir" -maxdepth 1 -type f -name '*.jar' -print 2>/dev/null | paste -sd: -
}

repo_out_jars_cp() {
  local repo="$1"
  local fatjar="$2"
  local safe_repo
  local dir
  local repo_out_root=""
  safe_repo="$(safe_name "$repo")"
  dir="$(dirname "$fatjar")"
  while [[ -n "$dir" && "$dir" != "/" && "$dir" != "." ]]; do
    if [[ "$(basename "$dir")" == "$safe_repo" ]]; then
      repo_out_root="$dir"
      break
    fi
    dir="$(dirname "$dir")"
  done
  [[ -n "$repo_out_root" ]] || return 0
  find "$repo_out_root" -type f -name '*.jar' -print 2>/dev/null | paste -sd: -
}

# ---- Maven runtime CP (module-aware) ----
maven_runtime_cp() {
  local repo_dir="$1"
  local module_rel="$2"

  # Find nearest root pom above module dir, else fall back to module pom
  local module_dir="${repo_dir}"
  if [[ -n "${module_rel}" && "${module_rel}" != "." && "${module_rel}" != "root" ]]; then
    module_dir="${repo_dir}/${module_rel}"
  fi

  local cur="${module_dir}"
  local root_pom=""
  while [[ "$cur" != "$repo_dir" && "$cur" != "/" ]]; do
    if [[ -f "$cur/pom.xml" ]]; then root_pom="$cur/pom.xml"; break; fi
    cur="$(dirname "$cur")"
  done
  if [[ -z "$root_pom" && -f "$repo_dir/pom.xml" ]]; then
    root_pom="$repo_dir/pom.xml"
  fi

  local outcp
  outcp="$(mktemp)"
  rm -f "$outcp"
  touch "$outcp"

  # Prefer reactor build-classpath if we found a root pom and module_rel looks usable
  if [[ -n "$root_pom" && -n "${module_rel}" && "${module_rel}" != "." && "${module_rel}" != "root" ]]; then
    ( cd "$repo_dir" && \
      mvn -q -f "$root_pom" -pl "$module_rel" -am -DskipTests \
        dependency:build-classpath -Dmdep.pathSeparator=":" -Dmdep.outputFile="$outcp" \
      ) >/dev/null 2>&1 || true
  fi

  # If empty, try module pom directly
  if [[ ! -s "$outcp" && -f "$module_dir/pom.xml" ]]; then
    ( cd "$module_dir" && \
      mvn -q -f "$module_dir/pom.xml" -DskipTests \
        dependency:build-classpath -Dmdep.pathSeparator=":" -Dmdep.outputFile="$outcp" \
      ) >/dev/null 2>&1 || true
  fi

  # Print result (may be empty)
  cat "$outcp" 2>/dev/null || true
  rm -f "$outcp"
}

# ---- Gradle runtime CP via init script (no project changes) ----
gradle_runtime_cp() {
  local repo_dir="$1"
  local module_rel="$2"

  local module_dir="${repo_dir}"
  if [[ -n "${module_rel}" && "${module_rel}" != "." && "${module_rel}" != "root" ]]; then
    module_dir="${repo_dir}/${module_rel}"
  fi

  local init
  init="$(mktemp)"
  cat > "$init" <<'GR'
allprojects { p ->
  p.tasks.register("printRuntimeClasspath") {
    doLast {
      def cfg = null
      if (p.configurations.findByName("runtimeClasspath") != null) {
        cfg = p.configurations.getByName("runtimeClasspath")
      } else if (p.configurations.findByName("runtime") != null) {
        cfg = p.configurations.getByName("runtime")
      }
      if (cfg == null) { return }
      cfg.resolve()
      def files = cfg.files.collect { it.absolutePath }.unique()
      print(files.join(":"))
    }
  }
}
GR

  local gradle_cmd="gradle"
  if [[ -x "$repo_dir/gradlew" ]]; then
    gradle_cmd="$repo_dir/gradlew"
  elif [[ -x "$module_dir/gradlew" ]]; then
    gradle_cmd="$module_dir/gradlew"
  fi

  local cp=""
  cp="$(
    ( cd "$module_dir" && "$gradle_cmd" --no-daemon -q -I "$init" printRuntimeClasspath ) 2>/dev/null || true
  )"

  rm -f "$init"
  echo "$cp"
}

# ---- Main CSV reading using Python csv module ----
PY_TSV=$(
python3 - <<'PY' "$CSV_PATH"
import csv, os, sys

path = sys.argv[1]
only_missing = os.environ.get("ONLY_MISSING_GENERATED", "0").strip() in {"1", "true", "TRUE", "yes", "YES"}
only_missing_scope = os.environ.get("ONLY_MISSING_SCOPE", "class").strip().lower()
if only_missing_scope not in {"class", "repo"}:
    only_missing_scope = "class"
inventory_csv = os.environ.get("TESTS_INVENTORY_CSV", "").strip()

has_generated = set()
repos_with_generated = set()
inv_rows = 0
if only_missing and inventory_csv and os.path.isfile(inventory_csv):
    with open(inventory_csv, newline="", encoding="utf-8") as f_inv:
        for row in csv.DictReader(f_inv):
            inv_rows += 1
            repo = (row.get("repo") or "").strip().strip('"')
            fqcn = (row.get("fqcn") or "").strip().strip('"')
            if not repo or not fqcn:
                continue
            generated_files = (row.get("generated_files") or "").strip().strip('"')
            generated_count_raw = (row.get("generated_count") or "").strip().strip('"')
            try:
                generated_count = int(generated_count_raw or "0")
            except ValueError:
                generated_count = 0
            if generated_count > 0 or (generated_files and generated_files.lower() != "null"):
                has_generated.add((repo, fqcn))
                repos_with_generated.add(repo)

total_rows = 0
selected_rows = 0
skipped_by_class = 0
skipped_by_repo = 0
skipped_empty_fqcn = 0
with open(path, newline="", encoding="utf-8") as f:
    r = csv.DictReader(f)
    for row in r:
        total_rows += 1
        repo = (row.get("repo") or "").strip()
        fqcn = (row.get("fqcn") or "").strip()
        if not fqcn:
            skipped_empty_fqcn += 1
            print(f"[DECIDE] SKIP repo={repo} fqcn={fqcn} reason=empty_fqcn", file=sys.stderr)
            continue
        if only_missing and only_missing_scope == "class" and (repo, fqcn) in has_generated:
            skipped_by_class += 1
            print(f"[DECIDE] SKIP repo={repo} fqcn={fqcn} reason=generated_pair", file=sys.stderr)
            continue
        if only_missing and only_missing_scope == "repo" and repo in repos_with_generated:
            skipped_by_repo += 1
            print(f"[DECIDE] SKIP repo={repo} fqcn={fqcn} reason=generated_repo", file=sys.stderr)
            continue
        selected_rows += 1
        if only_missing:
            print(f"[DECIDE] KEEP repo={repo} fqcn={fqcn} reason=missing_generated", file=sys.stderr)
        fatjar = (row.get("fatjar_path") or "").strip()
        class_path = (row.get("class_path") or "").strip()
        build_tool = (row.get("build_tool") or "").strip()
        module_rel = (row.get("module_rel") or "").strip()
        print("\t".join([repo, fqcn, fatjar, class_path, build_tool, module_rel]))

if only_missing:
    print(f"[INFO] only_missing=1 scope={only_missing_scope}", file=sys.stderr)
    if inventory_csv and os.path.isfile(inventory_csv):
        print(f"[INFO] inventory loaded: {inventory_csv}", file=sys.stderr)
        print(f"[INFO] inventory rows={inv_rows} generated_pairs={len(has_generated)} generated_repos={len(repos_with_generated)}", file=sys.stderr)
    else:
        print(f"[WARN] inventory missing: {inventory_csv}", file=sys.stderr)
    print(f"[INFO] input rows={total_rows} selected={selected_rows} skipped_empty_fqcn={skipped_empty_fqcn} skipped_by_class={skipped_by_class} skipped_by_repo={skipped_by_repo}", file=sys.stderr)
PY
)

if [[ "${ONLY_MISSING_GENERATED}" == "1" ]]; then
  if [[ -f "${TESTS_INVENTORY_CSV}" ]]; then
    echo "[INFO] Filtering to classes without generated tests using: ${TESTS_INVENTORY_CSV}"
    echo "[INFO] Missing-filter scope: ${ONLY_MISSING_SCOPE}"
  else
    echo "[WARN] ONLY_MISSING_GENERATED=1 but tests inventory not found: ${TESTS_INVENTORY_CSV}"
    echo "       Proceeding without inventory-based filtering."
  fi
fi

run_evosuite_once() {
  local projectcp="$1"
  local fqcn="$2"
  local stat_log="$3"
  local err_log="$4"

  java \
    --add-opens=java.base/java.util=ALL-UNNAMED \
    --add-opens=java.base/sun.util.calendar=ALL-UNNAMED \
    --add-opens=java.base/java.lang=ALL-UNNAMED \
    -jar llmsuite-r.jar \
    -projectCP "$projectcp" \
    -class "$fqcn" \
    -Dcriterion=BRANCH:LINE:OUTPUT:METHOD:CBRANCH \
    -Dtest_naming_strategy=coverage \
    -Dvariable_naming_strategy=TYPE_BASED \
    -Dassertion_timeout="${AGT_ASSERTION_TIMEOUT}" \
    -Dassertions="${AGT_ASSERTIONS}" \
    -Dinline="${AGT_INLINE}" \
    -Dsearch_budget=360 \
    -Dminimize=true \
    -Dcoverage=true \
    -Dwrite_junit_timeout="${AGT_WRITE_JUNIT_TIMEOUT}" \
    -Dextra_timeout=10000 \
    -Dalgorithm=DYNAMOSA \
    ${DATEMPT} \
    -Doutput_variables=TARGET_CLASS,criterion,Coverage,Total_Goals,BranchCoverage,LineCoverage,OutputCoverage,CBranchCoverage,MethodCoverage,Covered_Goals,Fitness,Tests_Executed,Total_Time \
    -Dtimeline_interval=5000 \
    -Ddefuse_debug_mode=true \
    -Dtest_format=JUNIT4 \
    -Djunit_check_timeout="${AGT_JUNIT_CHECK_TIMEOUT}" \
    -Dcheck_contracts=false \
    -Dllm_static_constant_pool=false \
    -Dsandbox=false \
    -Dvirtual_fs=false \
    -Dvirtual_net=false \
    -Duse_separate_classloader=false \
    -Dno_runtime_dependency=false \
    -Dreset_static_fields=false \
    -Dreport_dir="${OUTPUT_DIR}/evosuite-report" \
    -Dtest_dir="${OUTPUT_DIR}/generated-tests${POSTFIX}" \
    -Djunit_suffix="${JUNIT_SUFFIX}" \
    -Dbytecode_logging_mode=FILE_DUMP \
    > "$stat_log" 2> "$err_log"
}

success_count=0
failure_count=0
skipped_count=0

{
  while IFS=$'\t' read -r repo fqcn fatjar class_path build_tool module_rel; do
    [[ -z "$repo" || -z "$fqcn" ]] && continue

    if is_bad_fatjar "$fatjar"; then
      echo "[SKIP] ${repo} fqcn=${fqcn} fatjar_path='${fatjar}'"
      skipped_count=$((skipped_count+1))
      continue
    fi
    if [[ ! -f "$fatjar" ]]; then
      echo "[SKIP] ${repo} fqcn=${fqcn} fatjar missing on disk: ${fatjar}"
      skipped_count=$((skipped_count+1))
      continue
    fi

    repo_dir="${REPOS_DIR}/$(safe_name "$repo")"
    if [[ ! -d "$repo_dir" ]]; then
      echo "[WARN] Repo dir not found for fallback CP generation: ${repo_dir}"
      echo "       (Will still try fatjar-only run)"
    fi

    id="$(safe_id "$fqcn")"
    STAT_LOG="${LOG_DIR}/${id}-${PREFIX}-stat.log"
    ERR_LOG="${LOG_DIR}/${id}-${PREFIX}-error.log"

    echo "============================================================"
    echo "Repo: ${repo}"
    echo "Module: ${module_rel}"
    echo "Build tool: ${build_tool}"
    echo "FQCN: ${fqcn}"
    echo "Fat jar: ${fatjar}"
    echo "Attempt: ${ATTEMPT:-<none>}"
    echo

    # 1) fatjar-only
    echo "[RUN] fatjar-only"
    run_rc=0
    if ! run_evosuite_once "$fatjar" "$fqcn" "$STAT_LOG" "$ERR_LOG"; then
      run_rc=$?
    fi

    if [[ "$run_rc" -eq 0 ]] && ! needs_cp_retry "$STAT_LOG" "$ERR_LOG" && ! has_generation_failure "$STAT_LOG" "$ERR_LOG"; then
      if ! has_generated_test_output "$fqcn"; then
        echo "[FAIL] EvoSuite reported success but wrote no generated test file"
        append_failure_summary "$repo" "$fqcn" "$build_tool" "$module_rel" "$class_path" "$fatjar" \
          "fatjar-only" "missing_generated_test_output" "$STAT_LOG" "$ERR_LOG" "" ""
        failure_count=$((failure_count+1))
        continue
      fi
      echo "[OK] fatjar-only"
      success_count=$((success_count+1))
      continue
    fi

    # If it failed (or "succeeded" with missing-class diagnostics), see if it looks like missing deps
    if ! needs_cp_retry "$STAT_LOG" "$ERR_LOG"; then
      echo "[FAIL] Not a ClassNotFound-style failure (see $ERR_LOG)"
      append_failure_summary "$repo" "$fqcn" "$build_tool" "$module_rel" "$class_path" "$fatjar" \
        "fatjar-only" "not_classnotfound_style_failure" "$STAT_LOG" "$ERR_LOG" "" ""
      failure_count=$((failure_count+1))
      continue
    fi

    echo "[RETRY] Detected missing class; generating runtime classpath..."

    extra_cp=""
    source_module_rel="$(source_module_rel_from_class_path "$class_path" 2>/dev/null || true)"
    if [[ -d "$repo_dir" ]]; then
      if [[ "$build_tool" == "maven" ]]; then
        extra_cp="$(maven_runtime_cp "$repo_dir" "$module_rel")"
        if [[ -n "$source_module_rel" && "$source_module_rel" != "$module_rel" ]]; then
          source_cp="$(maven_runtime_cp "$repo_dir" "$source_module_rel")"
          [[ -n "$source_cp" ]] && extra_cp="${extra_cp:+${extra_cp}:}${source_cp}"
        fi
      elif [[ "$build_tool" == "gradle" ]]; then
        extra_cp="$(gradle_runtime_cp "$repo_dir" "$module_rel")"
        if [[ -n "$source_module_rel" && "$source_module_rel" != "$module_rel" ]]; then
          source_cp="$(gradle_runtime_cp "$repo_dir" "$source_module_rel")"
          [[ -n "$source_cp" ]] && extra_cp="${extra_cp:+${extra_cp}:}${source_cp}"
        fi
      fi
    fi

    # Add common compiled output dirs too (helps when fatjar isn’t actually fat)
    module_dir="$repo_dir"
    if [[ -n "${module_rel}" && "${module_rel}" != "." && "${module_rel}" != "root" ]]; then
      module_dir="$repo_dir/$module_rel"
    fi
    extra_dirs=""
    [[ -d "$module_dir/target/classes" ]] && extra_dirs="${extra_dirs}:$module_dir/target/classes"
    [[ -d "$module_dir/target/test-classes" ]] && extra_dirs="${extra_dirs}:$module_dir/target/test-classes"
    [[ -d "$module_dir/build/classes/java/main" ]] && extra_dirs="${extra_dirs}:$module_dir/build/classes/java/main"
    [[ -d "$module_dir/build/classes/kotlin/main" ]] && extra_dirs="${extra_dirs}:$module_dir/build/classes/kotlin/main"
    [[ -d "$module_dir/build/resources/main" ]] && extra_dirs="${extra_dirs}:$module_dir/build/resources/main"
    module_jars="$(jar_files_cp_from_dir "$module_dir/target")"
    [[ -n "$module_jars" ]] && extra_cp="${extra_cp:+${extra_cp}:}${module_jars}"
    module_jars="$(jar_files_cp_from_dir "$module_dir/build/libs")"
    [[ -n "$module_jars" ]] && extra_cp="${extra_cp:+${extra_cp}:}${module_jars}"

    source_module_dir="$(source_module_dir_from_class_path "$repo_dir" "$class_path" 2>/dev/null || true)"
    if [[ -n "$source_module_dir" && "$source_module_dir" != "$module_dir" ]]; then
      [[ -d "$source_module_dir/target/classes" ]] && extra_dirs="${extra_dirs}:$source_module_dir/target/classes"
      [[ -d "$source_module_dir/target/test-classes" ]] && extra_dirs="${extra_dirs}:$source_module_dir/target/test-classes"
      [[ -d "$source_module_dir/build/classes/java/main" ]] && extra_dirs="${extra_dirs}:$source_module_dir/build/classes/java/main"
      [[ -d "$source_module_dir/build/classes/kotlin/main" ]] && extra_dirs="${extra_dirs}:$source_module_dir/build/classes/kotlin/main"
      [[ -d "$source_module_dir/build/resources/main" ]] && extra_dirs="${extra_dirs}:$source_module_dir/build/resources/main"
      source_jars="$(jar_files_cp_from_dir "$source_module_dir/target")"
      [[ -n "$source_jars" ]] && extra_cp="${extra_cp:+${extra_cp}:}${source_jars}"
      source_jars="$(jar_files_cp_from_dir "$source_module_dir/build/libs")"
      [[ -n "$source_jars" ]] && extra_cp="${extra_cp:+${extra_cp}:}${source_jars}"
    fi

    repo_out_cp="$(repo_out_jars_cp "$repo" "$fatjar")"
    [[ -n "$repo_out_cp" ]] && extra_cp="${extra_cp:+${extra_cp}:}${repo_out_cp}"

    retry_cp="$fatjar"
    [[ -n "$extra_cp" ]] && retry_cp="${retry_cp}:$extra_cp"
    [[ -n "$extra_dirs" ]] && retry_cp="${retry_cp}${extra_dirs}"

    RETRY_STAT="${LOG_DIR}/${id}-${PREFIX}-retry-stat.log"
    RETRY_ERR="${LOG_DIR}/${id}-${PREFIX}-retry-error.log"

    echo "[RUN] retry with expanded projectCP"
    echo "      (writing retry logs: $RETRY_STAT / $RETRY_ERR)"
    run_evosuite_once "$retry_cp" "$fqcn" "$RETRY_STAT" "$RETRY_ERR" || {
      echo "[FAIL] retry also failed (see $RETRY_ERR)"
      append_failure_summary "$repo" "$fqcn" "$build_tool" "$module_rel" "$class_path" "$fatjar" \
        "retry" "retry_also_failed" "$STAT_LOG" "$ERR_LOG" "$RETRY_STAT" "$RETRY_ERR"
      failure_count=$((failure_count+1))
      continue
    }
    if needs_cp_retry "$RETRY_STAT" "$RETRY_ERR" || has_generation_failure "$RETRY_STAT" "$RETRY_ERR"; then
      echo "[FAIL] retry produced no test cases (see $RETRY_ERR)"
      append_failure_summary "$repo" "$fqcn" "$build_tool" "$module_rel" "$class_path" "$fatjar" \
        "retry" "retry_produced_no_test_cases" "$STAT_LOG" "$ERR_LOG" "$RETRY_STAT" "$RETRY_ERR"
      failure_count=$((failure_count+1))
      continue
    fi
    if ! has_generated_test_output "$fqcn"; then
      echo "[FAIL] retry reported success but wrote no generated test file"
      append_failure_summary "$repo" "$fqcn" "$build_tool" "$module_rel" "$class_path" "$fatjar" \
        "retry" "missing_generated_test_output" "$STAT_LOG" "$ERR_LOG" "$RETRY_STAT" "$RETRY_ERR"
      failure_count=$((failure_count+1))
      continue
    fi
    echo "[OK] retry succeeded"
    success_count=$((success_count+1))

  done <<< "${PY_TSV}"
} >> "${GLOBAL_OUT}" 2>> "${GLOBAL_ERR}"

echo
echo "DONE"
echo "Global out: ${GLOBAL_OUT}"
echo "Global err: ${GLOBAL_ERR}"
echo "Failure summary: ${FAILURE_SUMMARY_CSV}"
echo "Per-class logs: ${LOG_DIR}/*-${PREFIX}-*.log"
echo "Successes: ${success_count}"
echo "Failures: ${failure_count}"
echo "Skips: ${skipped_count}"

if [[ "${success_count}" -eq 0 && "${failure_count}" -gt 0 ]]; then
  exit 1
fi
