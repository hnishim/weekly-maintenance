#!/bin/bash
# HIR-293: each destructive operation requires its own explicit GUI approval.
set -u
PATH="${PATH:-/usr/bin:/bin}:/opt/homebrew/bin:/usr/local/bin"
export PATH

failed=0
log() { printf '%s\n' "$*"; }
error() { printf '%s\n' "$*" >&2; failed=1; }

approve() {
  local answer
  # Pass the preview as data, never interpolate command output into AppleScript.
  if ! answer=$(osascript -e '
    on run argv
      set response to display dialog (item 1 of argv) buttons {"Cancel", "OK"} default button "Cancel" giving up after 300
      if gave up of response then return "TIMEOUT"
      return button returned of response
    end run
  ' "$1" 2>&1); then
    log "Approval declined or dialog failed: $answer"
    return 1
  fi
  case "$answer" in
    OK|"button returned:OK") return 0 ;;
    *) log "Approval not received: $answer"; return 1 ;;
  esac
}

# Handle the two checks independently: one failure must not skip the other.
if ! command -v brew >/dev/null 2>&1; then
  error "Homebrew is unavailable"
else
  if ! brew update; then
    error "brew update failed; skipping Homebrew upgrade"
  else
    if outdated=$(brew outdated --quiet 2>&1); then
      packages=()
      invalid=0
      while IFS= read -r package; do
        [[ -z "$package" ]] && continue
        # --quiet must contain names, not descriptions or shell fragments.
        if [[ "$package" =~ ^[a-zA-Z0-9][a-zA-Z0-9+._@/-]*$ ]]; then
          packages+=("$package")
        else
          invalid=1
        fi
      done <<< "$outdated"
      if (( invalid )); then
        error "Cannot interpret all brew outdated entries; skipping upgrade: $outdated"
      elif (( ${#packages[@]} > 0 )); then
        preview=$(printf '%s\n' "${packages[@]}")
        log "Homebrew updates (${#packages[@]}):"
        log "$preview"
        if approve "Update these ${#packages[@]} Homebrew packages?"$'\n'"$preview"; then
          if ! brew upgrade "${packages[@]}"; then
            error "brew upgrade failed"
          fi
        else
          log "Homebrew upgrade not authorized"
        fi
      else
        log "No Homebrew updates"
      fi
    else
      error "brew outdated failed: $outdated"
    fi
  fi
fi

if ! command -v mo >/dev/null 2>&1; then
  error "Mole is unavailable"
else
  if mole_preview=$(mo clean --dry-run 2>&1); then
    if [[ -z "$mole_preview" ]]; then
      log "No Mole cleanup candidates"
    elif [[ ${#mole_preview} -gt 1800 ]]; then
      # A truncated dialog cannot authorize the full removal scope.
      error "Mole preview too long to show in full; no cleanup performed"
    elif [[ "$mole_preview" != *$'\n/'* && "$mole_preview" != /* && "$mole_preview" != *$'\n~/'* ]]; then
      # A size or a generic status message does not identify deletion targets.
      log "Mole preview does not identify deletion targets; no cleanup performed: $mole_preview"
    else
      log "Mole cleanup preview (not an exact execution snapshot):"
      log "$mole_preview"
      if approve "Mole will rescan before cleaning. Review the targets below. Continue to Mole's own confirmation?"$'\n'"$mole_preview"; then
        # No --force or noninteractive bypass. Keep the agent DISABLED until
        # actual Mole prompt/TTY behavior is verified on the user's Mac.
        if ! mo clean; then
          error "mo clean failed"
        fi
      else
        log "Mole cleanup not authorized"
      fi
    fi
  else
    error "mo clean --dry-run failed: $mole_preview"
  fi
fi

exit "$failed"
