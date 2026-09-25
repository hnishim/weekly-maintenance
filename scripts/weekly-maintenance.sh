#!/usr/bin/env bash
# Weekly candidate checks are notification-only. Changes require explicit 'run'.
set -u
export HOMEBREW_NO_AUTO_UPDATE=1
mode="${1:-}"
if [[ "$mode" != "check" && "$mode" != "run" ]]; then
  printf 'Usage: %s {check|run}\n' "$0" >&2
  exit 2
fi
script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
report="${WEEKLY_MAINTENANCE_REPORT:-$HOME/Library/Logs/weekly-maintenance/last-check.txt}"
error=0
brew_output=""
mole_output=""
brew_ok=0
mole_ok=0
mole_has_candidates=0
packages=()
package_count=0

collect_brew() {
  if [[ "$mode" == "run" ]]; then
    printf 'Homebrew: brew update は更新候補を確認するための定義更新です（アプリ自体は更新しません）。\n'
    if ! brew update; then
      printf 'Homebrew: brew update failed; all Homebrew changes skipped.\n' >&2
      error=1
      return
    fi
  fi
  if brew_output="$(brew outdated --quiet)"; then
    brew_ok=1
    while IFS= read -r package; do
      [[ -n "$package" ]] || continue
      duplicate=0
      if [[ "$package_count" -gt 0 ]]; then
        for existing in "${packages[@]}"; do
          if [[ "$existing" == "$package" ]]; then
            duplicate=1
            break
          fi
        done
      fi
      if [[ "$duplicate" -eq 0 ]]; then
        packages+=("$package")
        package_count=$((package_count + 1))
      fi
    done <<< "$brew_output"
  else
    printf 'Homebrew: candidate check failed: %s\n' "$brew_output" >&2
    error=1
  fi
}
collect_mole() {
  if mole_output="$(mo clean --dry-run)"; then
    mole_ok=1
    # Mole emits a summary even when there is no reclaimable space.
    if [[ -n "$mole_output" &&
          "$mole_output" != *"No significant reclaimable space detected"* &&
          "$mole_output" != *"No additional reclaimable space detected"* ]]; then
      mole_has_candidates=1
    fi
  else
    printf 'Mole: dry-run failed: %s\n' "$mole_output" >&2
    error=1
  fi
}
approval() {
  local message="$1" reply
  # Pass message as an argument, not as executable AppleScript source.
  if reply="$(osascript -e 'on run argv' \
    -e 'display dialog (item 1 of argv) buttons {"Cancel", "OK"} default button "Cancel" cancel button "Cancel"' \
    -e 'end run' "$message" 2>&1)"; then
    [[ "$reply" == "button returned:OK" ]]
  else
    return 1
  fi
}
notify() {
  local message="$1"
  if ! command -v terminal-notifier >/dev/null 2>&1; then
    printf 'terminal-notifier is unavailable; install it and retry, or use the manual entry.\n' >&2
    return 1
  fi
  terminal-notifier -title "Weekly Maintenance" -message "$message" \
    -open 'warp://tab_config/weekly-maintenance'
}

collect_brew
collect_mole
if [[ "$mode" == "check" ]]; then
  if ! mkdir -p "$(dirname "$report")"; then
    printf 'Could not create report directory.\n' >&2
    error=1
  elif ! {
    printf 'Checked: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
    printf 'Homebrew: %s\n' "$([[ "$brew_ok" -eq 1 ]] && printf '%s candidates' "$package_count" || printf 'check failed')"
    if [[ "$brew_ok" -eq 1 && "$package_count" -gt 0 ]]; then
      printf '%s\n' "$brew_output"
    fi
    printf 'Mole: %s\n' "$([[ "$mole_ok" -eq 0 ]] && printf 'check failed' || { [[ "$mole_has_candidates" -eq 1 ]] && printf 'candidates found' || printf 'no candidates'; })"
    if [[ "$mole_ok" -eq 1 ]]; then
      printf '%s\n' "$mole_output"
    fi
    printf '\nManual execution (rechecks current candidates, asks separately):\n'
    printf "open 'warp://tab_config/weekly-maintenance'\\n"
    printf 'bash %q run\n' "$script_path"
  } > "$report"; then
    printf 'Could not write check report: %s\n' "$report" >&2
    error=1
  fi
  if [[ "$package_count" -gt 0 || "$mole_has_candidates" -eq 1 ]]; then
    message="Candidates: Homebrew $package_count, Mole $([[ "$mole_has_candidates" -eq 1 ]] && printf 'yes' || printf 'no'). Check $report; run manually when convenient."
    if ! notify "$message"; then
      printf 'macOS notification failed; see %s\n' "$report" >&2
      error=1
    fi
  fi
  exit "$error"
fi


brew_normal="skipped"
brew_greedy="skipped"
mas_result="skipped"
npm_result="skipped"
mole_result="skipped"
mas_ok=0
npm_ok=0
npm_specs=()
npm_count=0
npm_prefix=""
npm_summary=""
if command -v mas >/dev/null 2>&1; then mas_ok=1; else mas_result="skipped (mas unavailable)"; error=1; fi
if command -v npm >/dev/null 2>&1; then
  if npm_prefix="$(npm prefix --global)" && [[ -n "$npm_prefix" ]]; then
    npm_json="$(npm outdated --global --depth=0 --json 2>/dev/null)"
    npm_exit=$?
    if [[ "$npm_exit" -le 1 ]]; then
      npm_summary="$(printf '%s' "$npm_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d,dict); [print("{}\t{}\t{}\t{}".format(k,v.get("current","?"),v.get("wanted","?"),v.get("latest","?"))) for k,v in d.items() if isinstance(v,dict)]')" && npm_ok=1
      if [[ "$npm_ok" -eq 1 && -n "$npm_summary" ]]; then
        while IFS=$'\t' read -r name current wanted latest; do
          [[ -n "$name" ]] || continue
          npm_specs+=("${name}@latest")
          npm_count=$((npm_count + 1))
        done <<< "$npm_summary"
      elif [[ "$npm_ok" -eq 1 ]]; then npm_result="no candidates"; fi
    fi
  fi
fi
if [[ "$npm_ok" -eq 0 ]]; then npm_result="skipped (npm unavailable or preflight failed)"; error=1; fi
brew_scope="Homebrew: 通常候補${package_count}件。
${brew_output}
続いてgreedy cask更新（通常候補外も実行時に再評価）。"
if [[ "$brew_ok" -eq 0 ]]; then brew_scope="Homebrew通常更新・greedy cask: 前提確認失敗のため対象外（実行しない）。"; fi
mas_scope="Mac App Store: mas upgradeで全更新候補を更新。"
if [[ "$mas_ok" -eq 0 ]]; then mas_scope="Mac App Store: 未配置のため対象外（実行しない）。"; fi
npm_scope="npm: global prefix ${npm_prefix}。${npm_count}件を@latestへ更新（メジャー更新を含む）。専用pnpm textlint runtimeは対象外。${npm_summary}"
if [[ "$npm_ok" -eq 0 ]]; then npm_scope="npm: 前提確認失敗のため対象外（実行しない）。"; fi
printf '%s\n' "$brew_scope" "$mas_scope" "$npm_scope"
batch_approved=0
if approval "更新処理を一括承認しますか？
${brew_scope}
${mas_scope}
${npm_scope}"; then batch_approved=1; fi
if [[ "$brew_ok" -eq 1 ]]; then
  if [[ "$package_count" -gt 0 ]]; then
    printf 'Homebrew 通常更新候補（%s件）:\n' "$package_count"
    printf '  %s\n' "${packages[@]}"
  else
    printf 'Homebrew 通常更新候補: 0件。\n'
    brew_normal="no candidates"
  fi
  printf '%s\n' \
    'Homebrew 承認対象・実行順:' \
    '1. 表示した通常候補のみを更新（0件なら省略）。' \
    '2. brew upgrade --cask --greedy: 通常候補にない自動更新対応cask等も含め実行時に再判定して更新。' \
    '2は通常候補が0件でも対象となる場合があります。全対象をこの候補一覧で固定できません。'
  if [[ "$batch_approved" -eq 1 ]]; then
    brew_stages_ok=1
    if [[ "$package_count" -gt 0 ]]; then
      if brew upgrade "${packages[@]}"; then
        brew_normal="success"
      else
        brew_normal="failed"
        brew_stages_ok=0
        error=1
      fi
    fi
    if [[ "$brew_stages_ok" -eq 1 ]]; then
      if brew upgrade --cask --greedy; then
        brew_greedy="success"
      else
        brew_greedy="failed"
        brew_stages_ok=0
        error=1
      fi
    else
      brew_greedy="skipped (previous Homebrew stage failed)"
    fi
  else
    if [[ "$package_count" -gt 0 ]]; then
      brew_normal="not approved"
    fi
    brew_greedy="not approved"
  fi
else
  brew_normal="skipped (Homebrew preflight failed)"
  brew_greedy="skipped (Homebrew preflight failed)"
fi

if [[ "$batch_approved" -eq 1 && "$mas_ok" -eq 1 ]]; then
  if mas upgrade; then mas_result="success"; else mas_result="failed"; error=1; fi
elif [[ "$batch_approved" -eq 0 && "$mas_ok" -eq 1 ]]; then mas_result="not approved"; fi
if [[ "$batch_approved" -eq 1 && "$npm_ok" -eq 1 && "$npm_count" -gt 0 ]]; then
  if npm install --global --no-audit --no-fund "${npm_specs[@]}"; then npm_result="success"; else npm_result="failed"; error=1; fi
elif [[ "$batch_approved" -eq 0 && "$npm_ok" -eq 1 && "$npm_count" -gt 0 ]]; then npm_result="not approved"; fi
if [[ "$mole_ok" -eq 1 && "$mole_has_candidates" -eq 1 ]]; then
  printf 'Mole 現在のドライラン（参考情報）:\n%s\n' "$mole_output"
  summary="$(printf '%s\n' "$mole_output" | sed -n '1,8p' | cut -c 1-100)"
  if approval "Mole 現在の清掃候補（参考）: $summary

実際のmo cleanは実行時に再走査し、削除対象が変わる場合があります。Mole自身の追加確認・権限要求を含む通常清掃を承認しますか？"; then
    # Keep Mole's native interactive confirmation and permission prompts.
    if mo clean; then
      mole_result="success"
    else
      mole_result="failed"
      error=1
    fi
  else
    mole_result="not approved"
  fi
elif [[ "$mole_ok" -eq 1 ]]; then
  mole_result="skipped (no candidates)"
else
  mole_result="failed (dry-run failed)"
fi

printf '\n実行結果:\n'
printf 'Homebrew normal: %s\n' "$brew_normal"
printf 'Homebrew greedy: %s\n' "$brew_greedy"
printf 'Mac App Store: %s\n' "$mas_result"
printf 'npm: %s\n' "$npm_result"
printf 'Mole: %s\n' "$mole_result"
exit "$error"
