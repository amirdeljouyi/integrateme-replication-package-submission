#!/usr/bin/env bash
set -euo pipefail

# Defaults follow the repository layout (see ../src/core/layout.py). The
# pipeline always passes all four explicitly; these matter for standalone runs.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"

MAP="${REPO_ROOT}/workspace/out/cut_to_fatjar_map.csv"
REPOS_DIR="${REPO_ROOT}/workspace/repos"
EVOSUITE_ROOT="${REPO_ROOT}/results/run-agt"
OUT_DIR="${REPO_ROOT}/data/collected-tests"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --map) MAP="$2"; shift 2 ;;
    --repos) REPOS_DIR="$2"; shift 2 ;;
    --evosuite-root) EVOSUITE_ROOT="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    *)
      echo "[ERROR] Unknown arg: $1"
      exit 2
      ;;
  esac
done

if [[ -z "${MAP}" ]]; then
  echo "[ERROR] --map is required (e.g., ./cut_to_fatjar_map.csv)"
  exit 2
fi
if [[ ! -f "${MAP}" ]]; then
  echo "[ERROR] map csv not found: ${MAP}"
  exit 2
fi

mkdir -p "${OUT_DIR}/generated" "${OUT_DIR}/manual" "${OUT_DIR}/_logs"
INV_CSV="${OUT_DIR}/_logs/tests_inventory.csv"
WARN_LOG="${OUT_DIR}/_logs/warnings.log"
: > "${WARN_LOG}"

echo "repo,fqcn,generated_count,manual_count,generated_files,manual_files" > "${INV_CSV}"

# -------- helpers --------

safe_repo() { echo "$1" | sed 's|/|_|g'; }
fqcn_to_path() { echo "$1" | sed 's|\.|/|g'; }

csv_escape() {
  local s="$1"
  s="${s//\"/\"\"}"
  echo "\"$s\""
}

copy_file() {
  local src="$1"
  local dst_dir="$2"
  mkdir -p "$dst_dir"
  cp -a "$src" "$dst_dir/"
}

# Add basename to a semicolon-separated list if not already present (dedup, keep order)
# Usage: list="$(list_add_unique "$list" "$bn")"
list_add_unique() {
  local list="$1"
  local item="$2"
  [[ -n "$item" ]] || { echo "$list"; return; }
  local token=";$item;"
  if [[ ";$list;" == *"$token"* ]]; then
    echo "$list"
  else
    if [[ -z "$list" ]]; then
      echo "$item"
    else
      echo "${list};${item}"
    fi
  fi
}

# Scaffold filter for inventory (still copied physically)
is_scaffolding() {
  local bn="$1"
  [[ "$bn" == *_scaffolding.java || "$bn" == *_scaffolding.kt || "$bn" == *_scaffolding.groovy ]]
}

# Return: count|semicolon-separated-unique-basename-list (excluding scaffolding)
collect_generated_for_fqcn() {
  local fqcn="$1"
  local repo_safe="$2"

  local simple="${fqcn##*.}"
  local fqcn_path
  fqcn_path="$(fqcn_to_path "$fqcn")"

  local dest_dir="${OUT_DIR}/generated/${repo_safe}/${fqcn}"
  local copied_list=""
  local copied_count=0

  shopt -s nullglob

  local roots=("${EVOSUITE_ROOT}"/generated-tests*)
  for root in "${roots[@]}"; do
    [[ -d "$root" ]] || continue

    local direct_dir="$root/${fqcn_path%/*}"
    if [[ -d "$direct_dir" ]]; then
      local matches=(
        "$direct_dir"/*"${simple}"*ESTest*.java
        "$direct_dir"/*"${simple}"*ESTest*.kt
        "$direct_dir"/*"${simple}"*ESTest*.groovy
      )
      for f in "${matches[@]}"; do
        [[ -f "$f" ]] || continue
        copy_file "$f" "$dest_dir"
        local bn="$(basename "$f")"

        # Inventory: exclude scaffolding + dedup
        if ! is_scaffolding "$bn"; then
          local before="$copied_list"
          copied_list="$(list_add_unique "$copied_list" "$bn")"
          if [[ "$copied_list" != "$before" ]]; then
            copied_count=$((copied_count+1))
          fi
        fi
      done
    fi
  done

  # Fallback: global search if nothing inventoried
  if [[ $copied_count -eq 0 ]]; then
    local roots2=("${EVOSUITE_ROOT}"/generated-tests*)
    for root in "${roots2[@]}"; do
      [[ -d "$root" ]] || continue
      while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        copy_file "$f" "$dest_dir"
        local bn="$(basename "$f")"

        if ! is_scaffolding "$bn"; then
          local before="$copied_list"
          copied_list="$(list_add_unique "$copied_list" "$bn")"
          if [[ "$copied_list" != "$before" ]]; then
            copied_count=$((copied_count+1))
          fi
        fi
      done < <(find "$root" -type f \( \
          -name "*${simple}*ESTest*.java" -o -name "*${simple}*ESTest*.kt" -o -name "*${simple}*ESTest*.groovy" \
        \) 2>/dev/null || true)
      [[ $copied_count -gt 0 ]] && break
    done
  fi

  shopt -u nullglob

  echo "${copied_count}|${copied_list}"
}

# Return: count|semicolon-separated-unique-basename-list (dedup manual too)
collect_manual_for_row() {
  local repo="$1"
  local repo_safe="$2"
  local fqcn="$3"
  local test_paths="$4"

  local repo_dir="${REPOS_DIR}/${repo_safe}"
  if [[ ! -d "$repo_dir" ]]; then
    echo "[WARN] repo dir missing: $repo_dir" >> "${WARN_LOG}"
    echo "0|"
    return
  fi

  local dest_dir="${OUT_DIR}/manual/${repo_safe}/${fqcn}"
  local copied_list=""
  local copied_count=0

  local IFS=';|'
  read -ra parts <<< "$test_paths"

  for rel in "${parts[@]}"; do
    rel="$(echo "$rel" | xargs)"
    [[ -n "$rel" ]] || continue

    local src="${repo_dir}/${rel}"

    if [[ -f "$src" ]]; then
      copy_file "$src" "$dest_dir"
      local bn="$(basename "$src")"
      local before="$copied_list"
      copied_list="$(list_add_unique "$copied_list" "$bn")"
      if [[ "$copied_list" != "$before" ]]; then
        copied_count=$((copied_count+1))
      fi
      continue
    fi

    if [[ -d "$src" ]]; then
      mkdir -p "$dest_dir"
      while IFS= read -r f; do
        [[ -f "$f" ]] || continue
        copy_file "$f" "$dest_dir"
        local bn="$(basename "$f")"
        local before="$copied_list"
        copied_list="$(list_add_unique "$copied_list" "$bn")"
        if [[ "$copied_list" != "$before" ]]; then
          copied_count=$((copied_count+1))
        fi
      done < <(find "$src" -maxdepth 4 -type f \( -name "*.java" -o -name "*.kt" -o -name "*.groovy" \) 2>/dev/null || true)
      continue
    fi

    echo "[WARN] test path missing: ${repo}:${rel}" >> "${WARN_LOG}"
  done

  echo "${copied_count}|${copied_list}"
}

# -------- CSV column detection (by header) --------
header="$(head -n 1 "${MAP}")"

col_index() {
  local name="$1"
  echo "$header" | awk -v RS=',' -v n="$name" '{i++; if ($0==n) print i}' | head -n 1
}

IDX_REPO="$(col_index repo || true)"
IDX_FQCN="$(col_index fqcn || true)"
IDX_TESTPATHS="$(col_index test_paths || true)"

if [[ -z "${IDX_REPO}" || -z "${IDX_FQCN}" || -z "${IDX_TESTPATHS}" ]]; then
  echo "[ERROR] CSV must contain headers: repo,fqcn,test_paths"
  echo "[ERROR] Found header: ${header}"
  exit 2
fi

echo "[INFO] Reading: ${MAP}"
echo "[INFO] repos: ${REPOS_DIR}"
echo "[INFO] evosuite root: ${EVOSUITE_ROOT}"
echo "[INFO] out: ${OUT_DIR}"
echo "[INFO] inventory csv: ${INV_CSV}"

while IFS= read -r line; do
  [[ -n "$line" ]] || continue

  repo="$(echo "$line" | awk -F',' -v i="$IDX_REPO" '{print $i}' | xargs)"
  fqcn="$(echo "$line" | awk -F',' -v i="$IDX_FQCN" '{print $i}' | xargs)"
  test_paths="$(echo "$line" | awk -F',' -v i="$IDX_TESTPATHS" '{print $i}' | xargs)"

  [[ -n "$repo" && -n "$fqcn" ]] || continue
  repo_safe="$(safe_repo "$repo")"

  gret="$(collect_generated_for_fqcn "$fqcn" "$repo_safe")"
  gcount="${gret%%|*}"
  gfiles="${gret#*|}"
  if [[ "$gcount" -eq 0 ]]; then
    echo "[WARN] no generated (non-scaffolding) tests found for ${repo}:${fqcn}" >> "${WARN_LOG}"
  fi

  mcount="0"
  mfiles=""
  if [[ -n "$test_paths" ]]; then
    mret="$(collect_manual_for_row "$repo" "$repo_safe" "$fqcn" "$test_paths")"
    mcount="${mret%%|*}"
    mfiles="${mret#*|}"
    if [[ "$mcount" -eq 0 ]]; then
      echo "[WARN] no manual tests copied for ${repo}:${fqcn}" >> "${WARN_LOG}"
    fi
  else
    echo "[WARN] empty test_paths for ${repo}:${fqcn}" >> "${WARN_LOG}"
  fi

  echo "$(csv_escape "$repo"),$(csv_escape "$fqcn"),${gcount},${mcount},$(csv_escape "$gfiles"),$(csv_escape "$mfiles")" >> "${INV_CSV}"

done < <(tail -n +2 "${MAP}")

echo "[DONE] Collected into: ${OUT_DIR}"
echo "[DONE] Inventory CSV: ${INV_CSV}"
if [[ -s "${WARN_LOG}" ]]; then
  echo "[INFO] Warnings written to: ${WARN_LOG}"
fi