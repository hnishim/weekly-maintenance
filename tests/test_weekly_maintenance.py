"""HIR-293/HIR-307: notification-only check and separately approved run.

All external commands are faked. These tests do not establish actual macOS
notification delivery, Mole deletion behavior, GUI authorization, or launchd timing.
"""
import json
import os
import shutil
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "weekly-maintenance.sh"
PLIST = ROOT / "launchd" / "my.launchd.weekly-maintenance.plist"

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
        if args[1:] == ["--cask", "--greedy"]:
            if os.environ.get("BREW_GREEDY_FAIL") == "1":
                sys.exit(14)
        elif os.environ.get("BREW_UPGRADE_FAIL") == "1":
            sys.exit(9)
    elif args[:1] == ["cleanup"] and os.environ.get("BREW_CLEANUP_FAIL") == "1":
        sys.exit(15)
    elif args[:1] == ["autoremove"] and os.environ.get("BREW_AUTOREMOVE_FAIL") == "1":
        sys.exit(16)
elif name == "mo":
    if args[:2] == ["clean", "--dry-run"]:
        if os.environ.get("MO_DRY_RUN_FAIL") == "1":
            sys.exit(10)
        print(os.environ.get("MO_PREVIEW", ""), end="")
    elif args[:1] == ["clean"]:
        if os.environ.get("MO_CLEAN_FAIL") == "1":
            sys.exit(11)
elif name == "mas":
    if os.environ.get("TOOL_MISSING") == "mas":
        sys.exit(127)
    if os.environ.get("MAS_FAIL") == "1":
        sys.exit(17)
elif name == "npm":
    if os.environ.get("TOOL_MISSING") == "npm":
        sys.exit(127)
    if args and args[0] == "prefix":
        if os.environ.get("NPM_PREFIX_FAIL") == "1":
            sys.exit(23)
        print(os.environ.get("NPM_PREFIX", "/tmp/test-global-prefix"))
    elif args and args[0] == "outdated":
        if os.environ.get("NPM_OUTDATED_FAIL") == "1":
            print("invalid npm listing")
            sys.exit(24)
        content = os.environ.get("NPM_OUTDATED_JSON", "{}")
        print(content)
        sys.exit(1 if content.strip() != "{}" else 0)
    elif args and args[0] in ("update", "install"):
        if os.environ.get("NPM_FAIL") == "1":
            sys.exit(18)
elif name == "terminal-notifier":
    if os.environ.get("NOTIFICATION_FAIL") == "1":
        sys.exit(12)
    if os.environ.get("NOTIFIER_UNAVAILABLE") == "1":
        sys.exit(127)
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
        elif answer == "unexpected":
            print("no approval")
        elif answer == "spoofed":
            print("button returned:OK; button returned:Cancel")
        elif answer == "spoofed_prefix":
            print("warning: button returned:OK")
        else:
            print("button returned:OK")
'''

class WeeklyMaintenanceTests(unittest.TestCase):
    def run_case(self, mode, initial_report=None, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fake_bin = base / "bin"
            fake_bin.mkdir()
            source_dir = base / "source script with spaces"
            source_dir.mkdir()
            source_script = source_dir / "weekly maintenance.sh"
            shutil.copyfile(SCRIPT, source_script)
            for name in ("brew", "mo", "osascript", "terminal-notifier", "mas", "npm"):
                if name == overrides.get("TOOL_MISSING"):
                    continue
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
                "NPM_OUTDATED_JSON": '{"sample-cli":{"current":"1.0.0","wanted":"1.1.0","latest":"2.0.0"}}',
            })
            env.update(overrides)
            if overrides.get("TOOL_MISSING") in ("mas", "npm", "terminal-notifier"):
                # Keep only the utilities required by the script and fake tools.
                # An inherited /usr/bin may contain real npm on Linux CI.
                for utility in ("bash", "python3", "dirname", "basename",
                                "date", "mkdir", "sed", "cut"):
                    target = shutil.which(utility)
                    self.assertIsNotNone(target, utility)
                    (fake_bin / utility).symlink_to(target)
                env["PATH"] = str(fake_bin)
            args = ["bash", str(source_script), mode]
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
    def npm_mutations(calls):
        return [c for c in calls if c[0] == "npm" and c[1:2] in (["update"], ["install"])]

    @staticmethod
    def dialogs(calls):
        return [c for c in calls
                if c[0] == "osascript" and "display dialog" in " ".join(c)]

    @staticmethod
    def destructive(calls):
        return [
            c for c in calls
            if (c[0] == "brew" and c[1:2] == ["upgrade"])
            or (c[0] == "brew" and c[1:2] in (["cleanup"], ["autoremove"]))
            or (c[0] == "mas" and c[1:2] == ["upgrade"])
            or (c[0] == "npm" and c[1:2] in (["update"], ["install"]))
            or (c[0] == "mo" and c[1:] == ["clean"])
        ]

    def test_check_with_no_candidates_does_not_notify_or_modify(self):
        result, calls, report = self.run_case("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.matches(calls, "osascript"))
        self.assertFalse(self.matches(calls, "terminal-notifier"))
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
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))
        self.assertFalse(self.matches(calls, "mas"))
        self.assertFalse(self.matches(calls, "npm"))
        self.assertFalse(any("display dialog" in " ".join(c)
                             for c in self.matches(calls, "osascript")))

    def test_check_notifies_only_for_brew_candidates_and_writes_result(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\nbeta\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        notifications = self.matches(calls, "terminal-notifier")
        self.assertEqual(len(notifications), 1)
        self.assertIn("-open", notifications[0])
        self.assertIn("warp://tab_config/weekly-maintenance", notifications[0])
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
        notifications = self.matches(calls, "terminal-notifier")
        self.assertEqual(len(notifications), 1)
        self.assertIn("-open", notifications[0])
        self.assertIn("warp://tab_config/weekly-maintenance", notifications[0])
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
        notifications = self.matches(calls, "terminal-notifier")
        self.assertEqual(len(notifications), 1)
        self.assertIn("-open", notifications[0])
        self.assertIn("warp://tab_config/weekly-maintenance", notifications[0])
        self.assert_check_never_updates_or_prompts(calls)

    def test_failed_notification_still_keeps_last_check(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\n", NOTIFICATION_FAIL="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("macOS notification failed", result.stderr)
        self.assertTrue(self.matches(calls, "terminal-notifier"))
        self.assertIsNotNone(report)
        self.assertIn("alpha", report)
        self.assertIn("open 'warp://tab_config/weekly-maintenance'", report)
        self.assertIn("bash ", report)
        self.assert_check_never_updates_or_prompts(calls)

    def test_check_failure_does_not_trigger_modification(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED_FAIL="1", MO_DRY_RUN_FAIL="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assert_check_never_updates_or_prompts(calls)
        self.assertIsNotNone(report)

    def test_notification_keeps_direct_run_fallback_with_spaces(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("open 'warp://tab_config/weekly-maintenance'", report)
        self.assertIn("bash ", report)
        self.assertIn("run", report)
        self.assertIn("source\\ script\\ with\\ spaces/weekly\\ maintenance.sh run", report)
        notifications = self.matches(calls, "terminal-notifier")
        self.assertEqual(len(notifications), 1)
        self.assertEqual(
            notifications[0][notifications[0].index("-open") + 1],
            "warp://tab_config/weekly-maintenance",
        )
        self.assert_check_never_updates_or_prompts(calls)

    def test_unavailable_notifier_reports_failure_but_preserves_manual_entry(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\n", TOOL_MISSING="terminal-notifier",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("macOS notification failed", result.stderr)
        self.assertIn("terminal-notifier", result.stderr)
        self.assertFalse(self.matches(calls, "terminal-notifier"))
        self.assertIsNotNone(report)
        self.assertIn("alpha", report)
        self.assertIn("open 'warp://tab_config/weekly-maintenance'", report)
        self.assertIn("bash ", report)
        self.assertIn("run", report)
        self.assert_check_never_updates_or_prompts(calls)

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
        self.assertIn(["brew", "upgrade", "new-alpha", "new-beta"], calls)
        self.assertIn(["brew", "upgrade", "--cask", "--greedy"], calls)

    def test_run_rechecks_mole_instead_of_reusing_previous_report(self):
        result, calls, _ = self.run_case(
            "run", initial_report="Previous Mole: old-cache 1 GiB\n",
            MO_PREVIEW="Current Mole: new-cache 2 GiB\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.matches(calls, "mo", "clean", "--dry-run")), 1)
        dialogs = [c for c in self.matches(calls, "osascript")
                   if "display dialog" in " ".join(c)]
        self.assertEqual(len(dialogs), 2)
        self.assertIn("new-cache", " ".join(dialogs[-1]))
        self.assertNotIn("old-cache", " ".join(dialogs[-1]))
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)

    def test_run_no_normal_candidates_still_requires_broad_scope_approval(self):
        result, calls, _ = self.run_case("run", DIALOG_ANSWERS="Cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        dialogs = self.dialogs(calls)
        self.assertEqual(len(dialogs), 1)
        self.assertIn("greedy", " ".join(dialogs[0]).lower())
        self.assertNotIn("cleanup", " ".join(dialogs[0]).lower())
        self.assertNotIn("autoremove", " ".join(dialogs[0]).lower())
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
        self.assertEqual(len(dialogs), 2)
        text = " ".join(dialogs[-1])
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
        self.assertIn(["brew", "upgrade", "alpha"], calls)
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


    def test_invalid_mode_never_invokes_any_external_command(self):
        result, calls, _ = self.run_case("brew-batch")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_zero_candidates_approved_brew_runs_only_greedy(self):
        result, calls, _ = self.run_case("run", DIALOG_ANSWERS="OK")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.matches(calls, "brew", "upgrade"),
                         [["brew", "upgrade", "--cask", "--greedy"]])
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))
    def test_normal_brew_candidates_are_deduplicated_before_greedy_recheck(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\nalpha\nbeta\n",
            DIALOG_ANSWERS="OK")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.matches(calls, "brew", "upgrade"),
                         [["brew", "upgrade", "alpha", "beta"],
                          ["brew", "upgrade", "--cask", "--greedy"]])
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))
    def test_brew_preflight_failure_skips_all_brew_mutations_but_not_other_tools(self):
        for flag in ("BREW_UPDATE_FAIL", "BREW_OUTDATED_FAIL"):
            with self.subTest(flag=flag):
                result, calls, _ = self.run_case(
                    "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
                    DIALOG_ANSWERS="OK", **{flag: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(c[0] == "brew" and
                                     c[1:2] in (["upgrade"], ["cleanup"], ["autoremove"])
                                     for c in calls))
                self.assertIn(["mas", "upgrade"], calls)
                self.assertTrue(self.npm_mutations(calls))
                self.assertIn(["mo", "clean"], calls)

    def test_each_brew_stage_failure_stops_later_brew_stages_only(self):
        cases = (
            ("BREW_UPGRADE_FAIL", [["brew", "upgrade", "alpha"]]),
            ("BREW_GREEDY_FAIL", [["brew", "upgrade", "alpha"],
                                  ["brew", "upgrade", "--cask", "--greedy"]]),
        )
        for flag, expected in cases:
            with self.subTest(flag=flag):
                result, calls, _ = self.run_case(
                    "run", BREW_OUTDATED="alpha\n",
                    DIALOG_ANSWERS="OK", **{flag: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.matches(calls, "brew", "upgrade"), expected)
                self.assertFalse(self.matches(calls, "brew", "cleanup"))
                self.assertFalse(self.matches(calls, "brew", "autoremove"))
                self.assertIn(["mas", "upgrade"], calls)
                self.assertTrue(self.npm_mutations(calls))
    def test_all_updates_share_exactly_one_approval(self):
        for answer, expected in (("OK", True), ("Cancel", False),
                                 ("unexpected", False), ("spoofed", False),
                                 ("error", False)):
            with self.subTest(answer=answer):
                _, calls, _ = self.run_case(
                    "run", BREW_OUTDATED="alpha\n",
                    DIALOG_ANSWERS=answer,
                )
                self.assertEqual(len(self.dialogs(calls)), 1)
                self.assertEqual(bool(self.matches(calls, "brew", "upgrade")), expected)
                self.assertEqual(["mas", "upgrade"] in calls, expected)
                self.assertEqual(bool(self.npm_mutations(calls)), expected)
                self.assertFalse(self.matches(calls, "brew", "cleanup"))
                self.assertFalse(self.matches(calls, "brew", "autoremove"))

    def test_mas_or_npm_failure_does_not_block_mole(self):
        for flag in ("MAS_FAIL", "NPM_FAIL"):
            with self.subTest(flag=flag):
                result, calls, _ = self.run_case(
                    "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
                    DIALOG_ANSWERS="OK|OK", **{flag: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(["mas", "upgrade"], calls)
                self.assertTrue(self.npm_mutations(calls))
                self.assertIn(["mo", "clean"], calls)

    def test_global_npm_scope_follows_installed_outdated_packages_not_legacy_list(self):
        outdated = ('{"sample-cli":{"current":"1.0.0","wanted":"1.1.0","latest":"2.0.0"},'
                    '"@example/lint":{"current":"2.0.0","wanted":"2.1.0","latest":"2.1.0"}}')
        result, calls, _ = self.run_case(
            "run", DIALOG_ANSWERS="OK", NPM_OUTDATED_JSON=outdated,
            NPM_PREFIX="/tmp/global prefix",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.matches(calls, "npm", "outdated"))
        self.assertTrue(self.matches(calls, "npm", "prefix"))
        mutations = self.npm_mutations(calls)
        self.assertEqual(len(mutations), 1)
        command = mutations[0]
        self.assertTrue("--global" in command or "-g" in command, command)
        # The approved scope includes major upgrades: a bare global "update"
        # may follow a narrower wanted range on some npm versions. Explicit
        # @latest specs are required for precisely the discovered packages.
        self.assertEqual(command[1], "install", command)
        specs = [arg for arg in command[2:] if not arg.startswith("-")]
        self.assertCountEqual(specs,
                              ["sample-cli@latest", "@example/lint@latest"],
                              command)
        self.assertTrue(any(c[0] == "npm" and c[1:2] == ["outdated"]
                            and ("--global" in c or "-g" in c)
                            and "--json" in c for c in calls))
        self.assertTrue(any(c[0] == "npm" and c[1:2] == ["prefix"]
                            and ("--global" in c or "-g" in c) for c in calls))
        messages = "\n".join(c[-1] for c in self.dialogs(calls))
        scope = result.stdout + messages
        for expected in ("sample-cli", "@example/lint", "/tmp/global prefix",
                         "1.0.0", "1.1.0", "2.0.0", "@latest"):
            self.assertIn(expected, scope)
        # The broadest version range must be visible before the user consents,
        # not merely printed to the terminal after the update has started.
        self.assertIn("sample-cli", messages)
        self.assertIn("2.0.0", messages)
        self.assertTrue("major" in messages.lower() or "メジャー" in messages)
        self.assertTrue("latest" in messages.lower() or "最新版" in messages)
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("TEXTLINT_NPM_PACKAGES", script)
        self.assertNotIn("textlint関連npmパッケージ", script)
        self.assertFalse(any("@textlint" in " ".join(c) for c in self.npm_mutations(calls)))

    def test_unapproved_or_unexpected_dialog_never_modifies_its_section(self):
        for answer in ("Cancel", "error", "unexpected", "spoofed",
                       "spoofed_prefix"):
            with self.subTest(answer=answer):
                _, calls, _ = self.run_case(
                    "run", BREW_OUTDATED="alpha\n",
                    DIALOG_ANSWERS=answer + "|Cancel",
                )
                self.assertFalse(self.destructive(calls))
                self.assertEqual(len(self.dialogs(calls)), 1)

    def test_mole_rejects_spoofed_ok_and_never_runs_cleanup(self):
        for answer in ("spoofed", "spoofed_prefix"):
            with self.subTest(answer=answer):
                _, calls, _ = self.run_case(
                    "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
                    DIALOG_ANSWERS="Cancel|" + answer,
                )
                self.assertEqual(len(self.dialogs(calls)), 2)
                self.assertNotIn(["mo", "clean"], calls)

    def test_all_approval_dialogs_have_no_120_second_timeout(self):
        _, calls, _ = self.run_case("run", BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="Cancel|Cancel")
        self.assertEqual(len(self.dialogs(calls)), 2)
        for dialog in self.dialogs(calls):
            self.assertNotIn("giving up after", " ".join(dialog))

    @staticmethod
    def assert_section_result(output, label, expected_status):
        # Check the status on the same output line as the affected section,
        # not merely whether a section name and a status appear somewhere.
        import re
        status_words = {
            "success": r"success|successful|succeeded|成功|完了",
            "failed": r"fail(?:ed|ure)?|error|失敗",
            "not_approved": r"not approved|declined|denied|未承認|不承認|拒否",
            "skipped": r"skip(?:ped)?|省略|スキップ|実行せず|実行なし",
            "no_candidates": r"no candidates|候補なし|候補ゼロ|対象なし|対象ゼロ",
        }
        lines = [line for line in output.splitlines()
                 if label.lower() in line.lower()]
        assert lines, f"result line for {label!r} was not reported: {output!r}"
        assert any(re.search(status_words[expected_status], line, re.I)
                   for line in lines), (
            f"section {label!r} must report {expected_status}: {lines!r}"
        )

    def test_run_reports_each_update_section_without_explicit_brew_cleanup(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="OK|Cancel",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for label in ("Homebrew normal", "Homebrew greedy",
                      "Mac App Store", "npm"):
            self.assert_section_result(result.stdout, label, "success")
        self.assert_section_result(result.stdout, "Mole", "not_approved")
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))

    def test_run_reports_failed_greedy_and_independent_sections(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\n", BREW_GREEDY_FAIL="1",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="OK|Cancel")
        self.assertNotEqual(result.returncode, 0)
        self.assert_section_result(result.stdout, "Homebrew normal", "success")
        self.assert_section_result(result.stdout, "Homebrew greedy", "failed")
        self.assert_section_result(result.stdout, "Mac App Store", "success")
        self.assert_section_result(result.stdout, "npm", "success")
        self.assert_section_result(result.stdout, "Mole", "not_approved")
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))
    def test_run_reports_no_candidates_and_unapproved_sections_separately(self):
        result, calls, _ = self.run_case(
            "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
            NPM_OUTDATED_JSON="{}",
            DIALOG_ANSWERS="Cancel|Cancel",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_section_result(result.stdout, "Homebrew normal", "no_candidates")
        self.assert_section_result(result.stdout, "Homebrew greedy", "not_approved")
        self.assert_section_result(result.stdout, "Mac App Store", "not_approved")
        self.assert_section_result(result.stdout, "npm", "no_candidates")
        self.assert_section_result(result.stdout, "Mole", "not_approved")
        self.assertFalse(self.npm_mutations(calls))
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))

    def test_no_explicit_brew_cleanup_or_autoremove_in_any_mode_or_brew_outcome(self):
        scenarios = (
            ("check", {}),
            ("run", {"BREW_OUTDATED": "alpha\n", "DIALOG_ANSWERS": "OK"}),
            ("run", {"BREW_OUTDATED": "", "DIALOG_ANSWERS": "OK"}),
            ("run", {"BREW_OUTDATED": "alpha\n", "DIALOG_ANSWERS": "Cancel"}),
            ("run", {"BREW_UPDATE_FAIL": "1"}),
            ("run", {"BREW_OUTDATED_FAIL": "1"}),
            ("run", {"BREW_OUTDATED": "alpha\n", "BREW_UPGRADE_FAIL": "1"}),
            ("run", {"BREW_OUTDATED": "alpha\n", "BREW_GREEDY_FAIL": "1"}),
        )
        for mode, options in scenarios:
            with self.subTest(mode=mode, options=options):
                result, calls, _ = self.run_case(mode, **options)
                self.assertFalse(self.matches(calls, "brew", "cleanup"))
                self.assertFalse(self.matches(calls, "brew", "autoremove"))
                for dialog in self.dialogs(calls):
                    message = dialog[-1].lower()
                    self.assertNotIn("brew cleanup", message)
                    self.assertNotIn("brew autoremove", message)
                self.assertNotIn("Homebrew cleanup:", result.stdout)
                self.assertNotIn("Homebrew autoremove:", result.stdout)

    def test_check_report_quotes_manual_run_path_with_spaces(self):
        result, calls, report = self.run_case(
            "check", BREW_OUTDATED="alpha\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source\\ script\\ with\\ spaces/weekly\\ maintenance.sh run", report)
        self.assert_check_never_updates_or_prompts(calls)

    def test_unavailable_mas_or_npm_records_failure_and_keeps_other_sections(self):
        for missing in ("mas", "npm"):
            with self.subTest(missing=missing):
                result, calls, _ = self.run_case(
                    "run", TOOL_MISSING=missing,
                    MO_PREVIEW="Potential cleanup: 2 GiB\n",
                    DIALOG_ANSWERS="OK|OK",
                )
                self.assertNotEqual(result.returncode, 0)
                if missing == "mas":
                    self.assertFalse(self.matches(calls, "mas"),
                                     "missing mas must never be invoked")
                    self.assertTrue(self.npm_mutations(calls))
                else:
                    self.assertFalse(self.matches(calls, "npm"),
                                     "missing npm must never be invoked")
                    self.assertIn(["mas", "upgrade"], calls)
                self.assertIn(["mo", "clean"], calls)
                self.assertEqual(len(self.dialogs(calls)), 2)

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


    def test_npm_no_outdated_global_package_does_not_mutate(self):
        result, calls, _ = self.run_case(
            "run", NPM_OUTDATED_JSON="{}", DIALOG_ANSWERS="OK",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.matches(calls, "npm", "outdated"))
        self.assertEqual(self.npm_mutations(calls), [])
        self.assert_section_result(result.stdout, "npm", "no_candidates")

    def test_npm_candidate_or_prefix_check_failure_skips_npm_only(self):
        for fail in ("NPM_OUTDATED_FAIL", "NPM_PREFIX_FAIL"):
            with self.subTest(fail=fail):
                result, calls, _ = self.run_case(
                    "run", DIALOG_ANSWERS="OK", **{fail: "1"},
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.npm_mutations(calls))
                self.assertIn(["mas", "upgrade"], calls)
                self.assertIn(["brew", "upgrade", "--cask", "--greedy"], calls)

    def test_npm_update_failure_does_not_block_mole(self):
        result, calls, _ = self.run_case(
            "run", NPM_FAIL="1",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="OK|OK",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.npm_mutations(calls))
        self.assertIn(["mas", "upgrade"], calls)
        self.assertIn(["mo", "clean"], calls)

    def test_update_approval_shows_all_scopes_and_preflight_exclusions(self):
        result, calls, _ = self.run_case(
            "run", BREW_UPDATE_FAIL="1", NPM_OUTDATED_FAIL="1",
            DIALOG_ANSWERS="OK",
        )
        self.assertNotEqual(result.returncode, 0)
        dialogs = self.dialogs(calls)
        self.assertEqual(len(dialogs), 1)
        dialog = " ".join(dialogs[0]).lower()
        for marker in ("homebrew", "greedy", "mas", "npm"):
            self.assertIn(marker, dialog)
        for marker in ("homebrew", "npm"):
            self.assertTrue(any(marker in line and any(word in line for word in ("除外", "実行しない", "対象外", "skipped", "excluded")) for line in dialog.splitlines()), f"{marker} not excluded: {dialog}")
        self.assertFalse(self.matches(calls, "brew", "upgrade"))
        self.assertFalse(self.npm_mutations(calls))
        self.assertIn(["mas", "upgrade"], calls)

if __name__ == "__main__":
    unittest.main()
