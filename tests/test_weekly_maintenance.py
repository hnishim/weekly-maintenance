"""HIR-293: weekly notification and separately authorized manual execution.

All external commands are faked. These tests do not establish actual macOS
notification delivery, Mole deletion behavior, GUI authorization, or launchd timing.
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

FAKE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["TEST_CALL_LOG"], "a", encoding="utf-8") as file:
    file.write(json.dumps([name, *args]) + "\n")

if name == "brew":
    if args[:1] == ["update"]:
        if os.environ.get("BREW_UPDATE_FAIL") == "1":
            sys.exit(7)
    elif args[:1] == ["outdated"]:
        if os.environ.get("BREW_OUTDATED_FAIL") == "1":
            sys.exit(8)
        print(os.environ.get("BREW_OUTDATED", ""), end="")
    elif args[:1] == ["upgrade"]:
        if os.environ.get("BREW_UPGRADE_FAIL") == "1":
            sys.exit(9)
elif name == "mo":
    if args[:2] == ["clean", "--dry-run"]:
        if os.environ.get("MO_DRY_RUN_FAIL") == "1":
            sys.exit(10)
        print(os.environ.get("MO_PREVIEW", ""), end="")
    elif args[:1] == ["clean"]:
        if os.environ.get("MO_CLEAN_FAIL") == "1":
            sys.exit(11)
elif name == "osascript":
    script = " ".join(args)
    if "display notification" in script:
        if os.environ.get("NOTIFICATION_FAIL") == "1":
            sys.exit(12)
    elif "display dialog" in script:
        marker = Path(os.environ["TEST_DIALOG_COUNTER"])
        i = int(marker.read_text()) if marker.exists() else 0
        marker.write_text(str(i + 1))
        choices = os.environ.get("DIALOG_ANSWERS", "OK").split("|")
        answer = choices[min(i, len(choices) - 1)]
        if answer in ("error", "timeout"):
            sys.exit(13)
        if answer == "Cancel":
            print("button returned:Cancel")
        else:
            print("button returned:OK")
'''


class WeeklyMaintenanceTests(unittest.TestCase):
    def run_case(self, mode, initial_report=None, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            for name in ("brew", "mo", "osascript"):
                file = fake_bin / name
                file.write_text(FAKE, encoding="utf-8")
                file.chmod(0o755)
            call_log = base / "calls.jsonl"
            call_log.touch()
            report = base / "last-check.txt"
            if initial_report is not None:
                report.write_text(initial_report, encoding="utf-8")
            env = dict(os.environ)
            env.update({
                "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
                "TEST_CALL_LOG": str(call_log),
                "TEST_DIALOG_COUNTER": str(base / "dialog-index"),
                "WEEKLY_MAINTENANCE_REPORT": str(report),
                "BREW_OUTDATED": "",
                "MO_PREVIEW": "",
                "DIALOG_ANSWERS": "OK",
            })
            env.update(overrides)
            args = ["bash", str(SCRIPT), mode]
            result = subprocess.run(
                args, cwd=ROOT, env=env, text=True, capture_output=True,
                timeout=30,
            )
            calls = [json.loads(line) for line in call_log.read_text().splitlines()]
            content = report.read_text(encoding="utf-8") if report.exists() else None
            return result, calls, content

    @staticmethod
    def matches(calls, name, *prefix):
        return [
            c for c in calls
            if c[0] == name and c[1:1 + len(prefix)] == list(prefix)
        ]

    @staticmethod
    def destructive(calls):
        return [
            c for c in calls
            if (c[0] == "brew" and c[1:2] == ["upgrade"])
            or (c[0] == "mo" and c[1:] == ["clean"])
        ]

    def test_check_with_no_candidates_does_not_notify_or_modify(self):
        result, calls, report = self.run_case("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.matches(calls, "osascript"))
        self.assertFalse(self.destructive(calls))
        self.assertFalse(self.matches(calls, "brew", "update"))
        self.assertTrue(self.matches(calls, "brew", "outdated"))
        self.assertTrue(self.matches(calls, "mo", "clean", "--dry-run"))
        self.assertIsNotNone(report)
        self.assertIn("Homebrew", report)
        self.assertIn("Mole", report)

    def assert_check_never_updates_or_prompts(self, calls):
        self.assertFalse(self.matches(calls, "brew", "update"))
        self.assertFalse(self.matches(calls, "brew", "upgrade"))
        self.assertNotIn(["mo", "clean"], calls)
        self.assertFalse(any("display dialog" in " ".join(c)
                             for c in self.matches(calls, "osascript")))

    def test_check_notifies_only_for_brew_candidates_and_writes_result(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\nbeta\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        notifications = self.matches(calls, "osascript")
        self.assertEqual(len(notifications), 1)
        self.assertIn("display notification", " ".join(notifications[0]))
        self.assertNotIn("display dialog", " ".join(notifications[0]))
        self.assertIn("alpha", report)
        self.assertIn("beta", report)
        self.assertIn("run", report)
        self.assert_check_never_updates_or_prompts(calls)

    def test_check_notifies_for_mole_candidates_without_brew_candidates(self):
        result, calls, report = self.run_case(
            "check", MO_PREVIEW="Potential cleanup: 2 GiB\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        notifications = self.matches(calls, "osascript")
        self.assertEqual(len(notifications), 1)
        self.assertIn("display notification", " ".join(notifications[0]))
        self.assertIn("Mole", report)
        self.assert_check_never_updates_or_prompts(calls)

    def test_check_with_both_candidates_does_not_ask_for_approval(self):
        _, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
        )
        self.assertIsNotNone(report)
        self.assertIn("alpha", report)
        self.assertIn("2 GiB", report)
        notifications = self.matches(calls, "osascript")
        self.assertEqual(len(notifications), 1)
        self.assertIn("display notification", " ".join(notifications[0]))
        self.assert_check_never_updates_or_prompts(calls)

    def test_failed_notification_still_keeps_last_check(self):
        _, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\n", NOTIFICATION_FAIL="1",
        )
        self.assertTrue(self.matches(calls, "osascript"))
        self.assertIsNotNone(report)
        self.assertIn("alpha", report)
        self.assert_check_never_updates_or_prompts(calls)

    def test_check_failure_does_not_trigger_modification(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED_FAIL="1", MO_DRY_RUN_FAIL="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assert_check_never_updates_or_prompts(calls)
        self.assertIsNotNone(report)

    def test_run_rechecks_current_brew_candidates_and_upgrades_named_set(self):
        result, calls, _ = self.run_case(
            "run", initial_report="Previous check: old-alpha and old-mole\n",
            BREW_OUTDATED="new-alpha\nnew-beta\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.matches(calls, "brew", "update"))
        self.assertTrue(self.matches(calls, "brew", "outdated"))
        dialogs = [
            c for c in self.matches(calls, "osascript")
            if "display dialog" in " ".join(c)
        ]
        self.assertEqual(len(dialogs), 1)
        self.assertIn("new-alpha", " ".join(dialogs[0]))
        self.assertIn("new-beta", " ".join(dialogs[0]))
        self.assertNotIn("old-alpha", " ".join(dialogs[0]))
        self.assertEqual(
            self.matches(calls, "brew", "upgrade"),
            [["brew", "upgrade", "new-alpha", "new-beta"]],
        )

    def test_run_rechecks_mole_instead_of_reusing_previous_report(self):
        result, calls, _ = self.run_case(
            "run", initial_report="Previous Mole: old-cache 1 GiB\n",
            MO_PREVIEW="Current Mole: new-cache 2 GiB\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.matches(calls, "mo", "clean", "--dry-run")), 1)
        dialogs = [c for c in self.matches(calls, "osascript")
                   if "display dialog" in " ".join(c)]
        self.assertEqual(len(dialogs), 1)
        self.assertIn("new-cache", " ".join(dialogs[0]))
        self.assertNotIn("old-cache", " ".join(dialogs[0]))
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)

    def test_run_no_candidates_never_requests_approval_or_changes(self):
        _, calls, _ = self.run_case("run")
        self.assertFalse(self.matches(calls, "osascript"))
        self.assertFalse(self.destructive(calls))

    def test_run_reject_timeout_and_error_never_upgrade(self):
        for answer in ("Cancel", "timeout", "error"):
            with self.subTest(answer=answer):
                _, calls, _ = self.run_case(
                    "run", BREW_OUTDATED="alpha\n",
                    DIALOG_ANSWERS=answer,
                )
                self.assertFalse(self.matches(calls, "brew", "upgrade"))
                self.assertFalse(self.matches(calls, "mo", "clean")
                                 and any(c == ["mo", "clean"] for c in calls))

    def test_run_reject_timeout_and_error_never_clean(self):
        for answer in ("Cancel", "timeout", "error"):
            with self.subTest(answer=answer):
                _, calls, _ = self.run_case(
                    "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
                    DIALOG_ANSWERS=answer,
                )
                self.assertNotIn(["mo", "clean"], calls)

    def test_mole_long_summary_can_authorize_normal_clean(self):
        preview = "Potential cleanup: 2 GiB\n" + ("cache category\n" * 400)
        result, calls, _ = self.run_case(
            "run", MO_PREVIEW=preview, DIALOG_ANSWERS="OK",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        dialogs = [
            c for c in self.matches(calls, "osascript")
            if "display dialog" in " ".join(c)
        ]
        self.assertEqual(len(dialogs), 1)
        text = " ".join(dialogs[0])
        self.assertIn("Mole", text)
        self.assertTrue("rescan" in text.lower() or "再走査" in text)
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)

    def test_approvals_remain_independent(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="Cancel|OK",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.matches(calls, "brew", "upgrade"))
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)

    def test_check_failure_on_one_side_does_not_block_other(self):
        _, calls, _ = self.run_case(
            "run", BREW_UPDATE_FAIL="1",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
        )
        self.assertFalse(self.matches(calls, "brew", "upgrade"))
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)
        _, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\n", MO_DRY_RUN_FAIL="1",
        )
        self.assertEqual(self.matches(calls, "brew", "upgrade"),
                         [["brew", "upgrade", "alpha"]])
        self.assertNotIn(["mo", "clean"], calls)

    def test_failed_dry_run_must_not_start_clean(self):
        result, calls, _ = self.run_case(
            "run", MO_DRY_RUN_FAIL="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(["mo", "clean"], calls)

    def test_failed_destructive_commands_return_nonzero(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\n", BREW_UPGRADE_FAIL="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.matches(calls, "brew", "upgrade"))
        result, calls, _ = self.run_case(
            "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
            MO_CLEAN_FAIL="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(["mo", "clean"], calls)

    def test_plist_schedules_only_check_at_monday_0830(self):
        with PLIST.open("rb") as file:
            plist = plistlib.load(file)
        self.assertEqual(
            plist["StartCalendarInterval"],
            {"Weekday": 1, "Hour": 8, "Minute": 30},
        )
        args = plist["ProgramArguments"]
        self.assertIn("weekly-maintenance.sh", " ".join(args))
        self.assertEqual(args[-1], "check")
        self.assertNotIn("RunAtLoad", plist)
        self.assertNotIn("KeepAlive", plist)


if __name__ == "__main__":
    unittest.main()
