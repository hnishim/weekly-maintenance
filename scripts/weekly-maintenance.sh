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

collect_brew() {
  if [[ "$mode" == "run" ]]; then
    if ! brew update; then
      printf 'Homebrew: brew update failed; upgrade skipped.\n' >&2
      error=1
      return
    fi
  fi
  if brew_output="$(brew outdated --quiet)"; then
    brew_ok=1
    while IFS= read -r package; do
      [[ -n "$package" ]] && packages+=("$package")
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
    -e 'display dialog (item 1 of argv) buttons {"Cancel", "OK"} default button "Cancel" cancel button "Cancel" giving up after 120' \
    -e 'end run' "$message" 2>&1)"; then
    [[ "$reply" == *"button returned:OK"* && "$reply" != *"gave up:true"* ]]
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
    printf 'Homebrew: %s\n' "$([[ "$brew_ok" -eq 1 ]] && printf '%s candidates' "${#packages[@]}" || printf 'check failed')"
    if [[ "$brew_ok" -eq 1 && "${#packages[@]}" -gt 0 ]]; then
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
  if [[ "${#packages[@]}" -gt 0 || "$mole_has_candidates" -eq 1 ]]; then
    message="Candidates: Homebrew ${#packages[@]}, Mole $([[ "$mole_has_candidates" -eq 1 ]] && printf 'yes' || printf 'no'). Check $report; run manually when convenient."
    if ! notify "$message"; then
      printf 'macOS notification failed; see %s\n' "$report" >&2
      error=1
    fi
  fi
  exit "$error"
fi

if [[ "$brew_ok" -eq 1 && "${#packages[@]}" -gt 0 ]]; then
  printf 'Current Homebrew update candidates (%s):\n' "${#packages[@]}"
  printf '  %s\n' "${packages[@]}"
  if approval "Homebrew will upgrade only these ${#packages[@]} currently listed package(s): $brew_output"; then
    if ! brew upgrade "${packages[@]}"; then
      printf 'Homebrew upgrade failed.\n' >&2
      error=1
    fi
  else
    printf 'Homebrew upgrade skipped: not approved.\n'
  fi
fi
if [[ "$mole_ok" -eq 1 && "$mole_has_candidates" -eq 1 ]]; then
  printf 'Current Mole dry-run (reference only):\n%s\n' "$mole_output"
  # Keep the macOS dialog bounded. The full preview remains in the terminal.
  summary="$(printf '%s\n' "$mole_output" | sed -n '1,8p' | cut -c 1-100)"
  if approval "Mole currently reports (reference only): $summary

The actual mo clean rescans at execution time and may clean different files. Approve Mole's entire normal cleanup, including its own prompts and permissions?"; then
    # Run in the user-initiated terminal; preserve Mole's own interaction.
    if ! mo clean; then
      printf 'Mole cleanup failed.\n' >&2
      error=1
    fi
  else
    printf 'Mole cleanup skipped: not approved.\n'
  fi
fi
exit "$error"
