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
  osascript -e 'on run argv' \
    -e 'display notification (item 1 of argv) with title "Weekly Maintenance"' \
    -e 'end run' "$message"
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


# Update sections keep separate approval and result records. A failed section
# does not suppress the independent Mac App Store, npm, or Mole decisions.
brew_normal="skipped"
brew_greedy="skipped"
mas_result="skipped"
npm_result="skipped"
mole_result="skipped"

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
  brew_dialog="Homebrew通常候補: ${package_count}件。$brew_output

承認すると順に (1) 表示した通常候補の更新（0件なら省略）、(2) brew upgrade --cask --greedy（通常候補にないcaskも実行時に再判定）を実行します。(2)は通常候補0件でも作用する場合があり、全対象は候補一覧で固定できません。"
  if approval "$brew_dialog"; then
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

printf '%s\n' 'Mac App Store: mas upgrade は実行時に更新可能なアプリ全体を更新します。週次checkに含まれず、個別候補は固定しません。'
if approval 'Mac App Store: mas upgradeで実行時に更新可能なアプリ全体を更新します。週次checkに含まれず、個別候補は固定しません。実行しますか？'; then
  if mas upgrade; then
    mas_result="success"
  else
    mas_result="failed"
    error=1
  fi
else
  mas_result="not approved"
fi

TEXTLINT_NPM_PACKAGES=(
    "textlint"
    "@textlint-ja/textlint-rule-no-dropping-i"
    "@textlint-ja/textlint-rule-no-filler"
    "@textlint-ja/textlint-rule-no-insert-dropping-sa"
    "@textlint-ja/textlint-rule-no-insert-re"
    "@textlint-ja/textlint-rule-no-synonyms"
    "@textlint-ja/textlint-rule-preset-ai-writing"
    "@textlint-rule/textlint-rule-no-unmatched-pair"
    "textlint-rule-abbr-within-parentheses"
    "textlint-rule-alive-link"
    "textlint-rule-common-misspellings"
    "textlint-rule-date-weekday-mismatch"
    "textlint-rule-doubled-spaces"
    "textlint-rule-en-capitalization"
    "textlint-rule-en-max-word-count"
    "textlint-rule-ja-hiragana-fukushi"
    "textlint-rule-ja-hiragana-hojodoushi"
    "textlint-rule-ja-hiragana-keishikimeishi"
    "textlint-rule-ja-no-abusage"
    "textlint-rule-ja-no-inappropriate-words"
    "textlint-rule-ja-no-orthographic-variants"
    "textlint-rule-ja-no-redundant-expression"
    "textlint-rule-ja-no-successive-word"
    "textlint-rule-ja-overlooked-typo"
    "textlint-rule-ja-unnatural-alphabet"
    "textlint-rule-no-dead-link"
    "textlint-rule-no-double-negative-ja"
    "textlint-rule-no-doubled-conjunction"
    "textlint-rule-no-doubled-conjunctive-particle-ga"
    "textlint-rule-no-doubled-joshi"
    "textlint-rule-no-dropping-the-ra"
    "textlint-rule-no-empty-element"
    "textlint-rule-no-empty-section"
    "textlint-rule-no-hankaku-kana"
    "textlint-rule-no-kangxi-radicals"
    "textlint-rule-no-mix-dearu-desumasu"
    "textlint-rule-no-nfd"
    "textlint-rule-no-start-duplicated-conjunction"
    "textlint-rule-no-todo"
    "textlint-rule-no-zero-width-spaces"
    "textlint-rule-period-in-header"
    "textlint-rule-period-in-list-item"
    "textlint-rule-prefer-tari-tari"
    "textlint-rule-preset-ja-spacing"
    "textlint-rule-preset-ja-technical-writing"
    "textlint-rule-preset-japanese"
    "textlint-rule-preset-jtf-style"
    "textlint-rule-prh"
    "textlint-rule-sentence-length"
    "textlint-rule-spelling"
    "textlint-rule-terminology"
)

printf 'textlint関連npmパッケージ: %s件。グローバルに各@latestをインストールします。\n' "${#TEXTLINT_NPM_PACKAGES[@]}"
printf '  %s\n' "${TEXTLINT_NPM_PACKAGES[@]}"
if approval "textlint関連npmパッケージ${#TEXTLINT_NPM_PACKAGES[@]}件をグローバルに各@latestへ更新します。全対象名はターミナルの一覧を確認してください。実行しますか？"; then
  npm_specs=()
  for name in "${TEXTLINT_NPM_PACKAGES[@]}"; do
    npm_specs+=("${name}@latest")
  done
  if npm install --global --no-audit --no-fund "${npm_specs[@]}"; then
    npm_result="success"
  else
    npm_result="failed"
    error=1
  fi
else
  npm_result="not approved"
fi

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
