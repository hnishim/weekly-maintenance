#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/my.launchd.weekly-maintenance"
RUNTIME_SCRIPT="$RUNTIME_DIR/weekly-maintenance.sh"
PLIST_NAME=my.launchd.weekly-maintenance.plist
TARGET_PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"

mkdir -p "$RUNTIME_DIR" "$HOME/Library/LaunchAgents"

temp_script="$(mktemp "${TMPDIR:-/tmp}/weekly-maintenance.sh.XXXXXX")"
temp_plist="$(mktemp "${TMPDIR:-/tmp}/weekly-maintenance.plist.XXXXXX")"
trap 'rm -f "$temp_script" "$temp_plist"' EXIT

# iCloud Driveのソースをローカル実行領域へコピーします。
cp "$SCRIPT_DIR/scripts/weekly-maintenance.sh" "$temp_script"
chmod 755 "$temp_script"
mv "$temp_script" "$RUNTIME_SCRIPT"

cp "$SCRIPT_DIR/launchd/$PLIST_NAME" "$temp_plist"
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:1 $RUNTIME_SCRIPT" "$temp_plist"
plutil -lint "$temp_plist"

if launchctl print "gui/$(id -u)/my.launchd.weekly-maintenance" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/my.launchd.weekly-maintenance"
fi
mv "$temp_plist" "$TARGET_PLIST"
trap - EXIT
launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"

printf 'Registered %s\n' "$TARGET_PLIST"
printf 'Runtime script: %s\n' "$RUNTIME_SCRIPT"
