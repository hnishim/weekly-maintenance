"""HIR-293: behavioral tests with all external commands replaced by fakes.

The suite is intentionally red until the implementation and LaunchAgent exist.
It does not validate actual macOS, Homebrew, Mole, or GUI behavior.
"""
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "weekly-maintenance.sh"
PLIST = ROOT / "launchd" / "com.hnishim.weekly-maintenance.plist"

FAKE_COMMAND = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TEST_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([name, *args]) + "\n")

if name == "brew":
    if args and args[0] == "update":
        if os.environ.get("BREW_UPDATE_FAIL") == "1":
            print("brew update failed", file=sys.stderr)
            sys.exit(1)
    elif args and args[0] == "outdated":
        if os.environ.get("BREW_OUTDATED_FAIL") == "1":
            print("brew outdated failed", file=sys.stderr)
            sys.exit(1)
        print(os.environ.get("BREW_OUTDATED", ""), end="")
    elif args and args[0] == "upgrade":
        if os.environ.get("BREW_UPGRADE_FAIL") == "1":
            print("brew upgrade failed", file=sys.stderr)
            sys.exit(1)
elif name == "mo":
    if args[:2] == ["clean", "--dry-run"]:
        if os.environ.get("MO_DRY_RUN_FAIL") == "1":
            print("mo preview failed", file=sys.stderr)
            sys.exit(1)
        print(os.environ.get("MO_PREVIEW", ""), end="")
    elif args[:1] == ["clean"]:
        if os.environ.get("MO_CLEAN_FAIL") == "1":
            print("mo clean failed", file=sys.stderr)
            sys.exit(1)
elif name == "osascript":
    counter = Path(os.environ["TEST_DIALOG_COUNTER"])
    index = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(index + 1))
    answers = os.environ.get("DIALOG_ANSWERS", "OK").split("|")
    answer = answers[min(index, len(answers) - 1)]
    if answer == "error":
        print("dialog failed", file=sys.stderr)
        sys.exit(1)
    if answer == "timeout":
        print("dialog timed out", file=sys.stderr)
        sys.exit(124)
    if answer == "Cancel":
        print("button returned:Cancel")
        sys.exit(1)
    print("button returned:OK")
'''


class WeeklyMaintenanceTests(unittest.TestCase):
    def run_case(self, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fake_bin = directory / "bin"
            fake_bin.mkdir()
            for command in ("brew", "mo", "osascript"):
                target = fake_bin / command
                target.write_text(FAKE_COMMAND, encoding="utf-8")
                target.chmod(0o755)
            call_log = directory / "calls.jsonl"
            call_log.touch()
            env = os.environ.copy()
            env.update({
                "PATH": str(fake_bin) + os.pathsep + env["PATH"],
                "TEST_CALL_LOG": str(call_log),
                "TEST_DIALOG_COUNTER": str(directory / "dialog-index"),
                "BREW_OUTDATED": "",
                "MO_PREVIEW": "",
                "DIALOG_ANSWERS": "OK",
            })
            env.update(overrides)
            result = subprocess.run(
                ["bash", str(SCRIPT)], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=30,
            )
            calls = [
                json.loads(line) for line in call_log.read_text().splitlines()
            ]
            return result, calls

    @staticmethod
    def called(calls, program, *prefix):
        return [call for call in calls
                if call[0] == program and call[1:1 + len(prefix)] == list(prefix)]

    def test_no_targets_never_prompts_or_modifies(self):
        _, calls = self.run_case()
        self.assertTrue(self.called(calls, "brew", "update"))
        self.assertTrue(self.called(calls, "brew", "outdated"))
        self.assertTrue(self.called(calls, "mo", "clean", "--dry-run"))
        self.assertFalse(self.called(calls, "osascript"))
        self.assertFalse(self.called(calls, "brew", "upgrade"))
        self.assertFalse(self.called(calls, "mo", "clean") and
                         any(c[0] == "mo" and c[1:] == ["clean"] for c in calls))

    def test_brew_only_approved_shows_package_before_upgrade(self):
        _, calls = self.run_case(BREW_OUTDATED="alpha\nbeta\n")
        dialogs = self.called(calls, "osascript")
        self.assertEqual(len(dialogs), 1)
        self.assertIn("alpha", " ".join(dialogs[0]))
        self.assertIn("beta", " ".join(dialogs[0]))
        self.assertEqual(len(self.called(calls, "brew", "upgrade")), 1)
        self.assertFalse(any(c == ["mo", "clean"] for c in calls))

    def test_brew_rejected_never_upgrades(self):
        _, calls = self.run_case(
            BREW_OUTDATED="alpha\n", DIALOG_ANSWERS="Cancel",
        )
        self.assertEqual(len(self.called(calls, "osascript")), 1)
        self.assertFalse(self.called(calls, "brew", "upgrade"))

    def test_brew_timeout_never_upgrades(self):
        _, calls = self.run_case(
            BREW_OUTDATED="alpha\n", DIALOG_ANSWERS="timeout",
        )
        self.assertFalse(self.called(calls, "brew", "upgrade"))

    def test_dialog_error_never_upgrades(self):
        _, calls = self.run_case(
            BREW_OUTDATED="alpha\n", DIALOG_ANSWERS="error",
        )
        self.assertFalse(self.called(calls, "brew", "upgrade"))

    def test_brew_check_failure_does_not_suppress_mole_preview(self):
        _, calls = self.run_case(
            BREW_UPDATE_FAIL="1", MO_PREVIEW="Cache candidates: 2 GiB\n",
            DIALOG_ANSWERS="Cancel",
        )
        self.assertFalse(self.called(calls, "brew", "upgrade"))
        self.assertTrue(self.called(calls, "mo", "clean", "--dry-run"))
        self.assertTrue(self.called(calls, "osascript"))
        self.assertFalse(any(c == ["mo", "clean"] for c in calls))

    def test_mole_dry_run_failure_never_deletes_or_blocks_brew(self):
        _, calls = self.run_case(
            BREW_OUTDATED="alpha\n", MO_DRY_RUN_FAIL="1",
        )
        self.assertEqual(len(self.called(calls, "brew", "upgrade")), 1)
        self.assertFalse(any(c == ["mo", "clean"] for c in calls))

    def test_mole_rejected_never_deletes(self):
        _, calls = self.run_case(
            MO_PREVIEW="Cache candidates: 2 GiB\n",
            DIALOG_ANSWERS="Cancel",
        )
        dialogs = self.called(calls, "osascript")
        self.assertEqual(len(dialogs), 1)
        self.assertIn("2 GiB", " ".join(dialogs[0]))
        self.assertFalse(any(c == ["mo", "clean"] for c in calls))

    def test_separate_approvals_do_not_leak_between_operations(self):
        _, calls = self.run_case(
            BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Cache candidates: 2 GiB\n",
            DIALOG_ANSWERS="Cancel|OK",
        )
        self.assertEqual(len(self.called(calls, "osascript")), 2)
        self.assertFalse(self.called(calls, "brew", "upgrade"))
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)

    def test_plist_only_schedules_monday_at_0830(self):
        with PLIST.open("rb") as file:
            plist = plistlib.load(file)
        self.assertEqual(plist["StartCalendarInterval"],
                         {"Weekday": 1, "Hour": 8, "Minute": 30})
        self.assertNotIn("RunAtLoad", plist)
        self.assertNotIn("KeepAlive", plist)
        self.assertIn("ProgramArguments", plist)
        self.assertTrue(any("weekly-maintenance.sh" in str(arg)
                            for arg in plist["ProgramArguments"]))


if __name__ == "__main__":
    unittest.main()
