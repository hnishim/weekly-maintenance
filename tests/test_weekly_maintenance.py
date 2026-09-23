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
    if os.environ.get("NPM_FAIL") == "1":
        sys.exit(18)
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

# Legacy update scope: changing the list requires an explicit requirement update.
EXPECTED_TEXTLINT_PACKAGES = (
    "textlint",
    "@textlint-ja/textlint-rule-no-dropping-i",
    "@textlint-ja/textlint-rule-no-filler",
    "@textlint-ja/textlint-rule-no-insert-dropping-sa",
    "@textlint-ja/textlint-rule-no-insert-re",
    "@textlint-ja/textlint-rule-no-synonyms",
    "@textlint-ja/textlint-rule-preset-ai-writing",
    "@textlint-rule/textlint-rule-no-unmatched-pair",
    "textlint-rule-abbr-within-parentheses",
    "textlint-rule-alive-link",
    "textlint-rule-common-misspellings",
    "textlint-rule-date-weekday-mismatch",
    "textlint-rule-doubled-spaces",
    "textlint-rule-en-capitalization",
    "textlint-rule-en-max-word-count",
    "textlint-rule-ja-hiragana-fukushi",
    "textlint-rule-ja-hiragana-hojodoushi",
    "textlint-rule-ja-hiragana-keishikimeishi",
    "textlint-rule-ja-no-abusage",
    "textlint-rule-ja-no-inappropriate-words",
    "textlint-rule-ja-no-orthographic-variants",
    "textlint-rule-ja-no-redundant-expression",
    "textlint-rule-ja-no-successive-word",
    "textlint-rule-ja-overlooked-typo",
    "textlint-rule-ja-unnatural-alphabet",
    "textlint-rule-no-dead-link",
    "textlint-rule-no-double-negative-ja",
    "textlint-rule-no-doubled-conjunction",
    "textlint-rule-no-doubled-conjunctive-particle-ga",
    "textlint-rule-no-doubled-joshi",
    "textlint-rule-no-dropping-the-ra",
    "textlint-rule-no-empty-element",
    "textlint-rule-no-empty-section",
    "textlint-rule-no-hankaku-kana",
    "textlint-rule-no-kangxi-radicals",
    "textlint-rule-no-mix-dearu-desumasu",
    "textlint-rule-no-nfd",
    "textlint-rule-no-start-duplicated-conjunction",
    "textlint-rule-no-todo",
    "textlint-rule-no-zero-width-spaces",
    "textlint-rule-period-in-header",
    "textlint-rule-period-in-list-item",
    "textlint-rule-prefer-tari-tari",
    "textlint-rule-preset-ja-spacing",
    "textlint-rule-preset-ja-technical-writing",
    "textlint-rule-preset-japanese",
    "textlint-rule-preset-jtf-style",
    "textlint-rule-prh",
    "textlint-rule-sentence-length",
    "textlint-rule-spelling",
    "textlint-rule-terminology",
)


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
            for name in ("brew", "mo", "osascript", "mas", "npm"):
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
    def dialogs(calls):
        return [c for c in calls
                if c[0] == "osascript" and "display dialog" in " ".join(c)]

    @staticmethod
    def destructive(calls):
        return [
            c for c in calls
            if (c[0] == "brew" and c[1:2] == ["upgrade"])
            or (c[0] == "brew" and c[1:2] in (["cleanup"], ["autoremove"]))
            or (c[0] in ("mas", "npm"))
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
        self.assertEqual(len(dialogs), 3)
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
        self.assertEqual(len(dialogs), 4)
        self.assertIn("new-cache", " ".join(dialogs[-1]))
        self.assertNotIn("old-cache", " ".join(dialogs[-1]))
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)

    def test_run_no_normal_candidates_still_requires_broad_scope_approval(self):
        result, calls, _ = self.run_case("run", DIALOG_ANSWERS="Cancel|Cancel|Cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        dialogs = self.dialogs(calls)
        self.assertEqual(len(dialogs), 3)
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
        self.assertEqual(len(dialogs), 4)
        text = " ".join(dialogs[-1])
        self.assertIn("Mole", text)
        self.assertTrue("rescan" in text.lower() or "再走査" in text)
        self.assertEqual(sum(c == ["mo", "clean"] for c in calls), 1)

    def test_approvals_remain_independent(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="Cancel|Cancel|Cancel|OK",
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
        result, calls, _ = self.run_case("run", DIALOG_ANSWERS="OK|Cancel|Cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.matches(calls, "brew", "upgrade"),
                         [["brew", "upgrade", "--cask", "--greedy"]])
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))
    def test_normal_brew_candidates_are_deduplicated_before_greedy_recheck(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\nalpha\nbeta\n",
            DIALOG_ANSWERS="OK|Cancel|Cancel")
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
                self.assertTrue(self.matches(calls, "npm", "install"))
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
                self.assertTrue(self.matches(calls, "npm", "install"))
    def test_mas_and_npm_are_independently_approved(self):
        for answers, mas_expected, npm_expected in (
            ("Cancel|OK|Cancel", True, False),
            ("Cancel|Cancel|OK", False, True),
            ("Cancel|unexpected|OK", False, True),
            ("Cancel|OK|unexpected", True, False),
        ):
            with self.subTest(answers=answers):
                _, calls, _ = self.run_case("run", DIALOG_ANSWERS=answers)
                self.assertEqual(["mas", "upgrade"] in calls, mas_expected)
                self.assertEqual(bool(self.matches(calls, "npm", "install")), npm_expected)
                self.assertEqual(len(self.dialogs(calls)), 3)

    def test_mas_or_npm_failure_does_not_block_mole(self):
        for flag in ("MAS_FAIL", "NPM_FAIL"):
            with self.subTest(flag=flag):
                result, calls, _ = self.run_case(
                    "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
                    DIALOG_ANSWERS="Cancel|OK|OK|OK", **{flag: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(["mas", "upgrade"], calls)
                self.assertTrue(self.matches(calls, "npm", "install"))
                self.assertIn(["mo", "clean"], calls)

    def test_npm_full_legacy_package_scope_and_cli_flags(self):
        result, calls, _ = self.run_case("run", DIALOG_ANSWERS="Cancel|Cancel|OK")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.matches(calls, "npm", "install"), [[
            "npm", "install", "--global", "--no-audit", "--no-fund",
            *(name + "@latest" for name in EXPECTED_TEXTLINT_PACKAGES),
        ]])
        self.assertEqual(len(set(EXPECTED_TEXTLINT_PACKAGES)),
                         len(EXPECTED_TEXTLINT_PACKAGES))
        self.assertTrue(all(name in result.stdout for name in EXPECTED_TEXTLINT_PACKAGES))

    def test_unapproved_or_unexpected_dialog_never_modifies_its_section(self):
        for answer in ("Cancel", "error", "unexpected", "spoofed",
                       "spoofed_prefix"):
            with self.subTest(answer=answer):
                _, calls, _ = self.run_case("run", BREW_OUTDATED="alpha\n",
                    DIALOG_ANSWERS=answer + "|Cancel|Cancel")
                self.assertFalse(any(c[0] == "brew" and
                                     c[1:2] in (["upgrade"], ["cleanup"], ["autoremove"])
                                     for c in calls))
                self.assertFalse(self.destructive(calls))

    def test_mole_rejects_spoofed_ok_and_never_runs_cleanup(self):
        for answer in ("spoofed", "spoofed_prefix"):
            with self.subTest(answer=answer):
                _, calls, _ = self.run_case(
                    "run", MO_PREVIEW="Potential cleanup: 2 GiB\n",
                    DIALOG_ANSWERS="Cancel|Cancel|Cancel|" + answer)
                self.assertEqual(len(self.dialogs(calls)), 4)
                self.assertNotIn(["mo", "clean"], calls)

    def test_all_approval_dialogs_have_no_120_second_timeout(self):
        _, calls, _ = self.run_case("run", BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="Cancel|Cancel|Cancel|Cancel")
        self.assertEqual(len(self.dialogs(calls)), 4)
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
        result, calls, _ = self.run_case("run", BREW_OUTDATED="alpha\n",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="OK|Cancel|OK|Cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        for label in ("Homebrew normal", "Homebrew greedy",
                      "Mac App Store", "npm", "Mole"):
            self.assertIn(label.lower(), result.stdout.lower())
        for label in ("Homebrew normal", "Homebrew greedy", "npm"):
            self.assert_section_result(result.stdout, label, "success")
        self.assert_section_result(result.stdout, "Mac App Store", "not_approved")
        self.assert_section_result(result.stdout, "Mole", "not_approved")
        for forbidden in ("Homebrew cleanup:", "Homebrew autoremove:"):
            self.assertNotIn(forbidden.lower(), result.stdout.lower())
        self.assertFalse(self.matches(calls, "brew", "cleanup"))
        self.assertFalse(self.matches(calls, "brew", "autoremove"))
    def test_run_reports_failed_greedy_and_independent_sections(self):
        result, calls, _ = self.run_case(
            "run", BREW_OUTDATED="alpha\n", BREW_GREEDY_FAIL="1",
            MO_PREVIEW="Potential cleanup: 2 GiB\n",
            DIALOG_ANSWERS="OK|OK|OK|Cancel")
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
            DIALOG_ANSWERS="Cancel|Cancel|Cancel|Cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_section_result(result.stdout, "Homebrew normal", "no_candidates")
        self.assert_section_result(result.stdout, "Homebrew greedy", "not_approved")
        self.assert_section_result(result.stdout, "Mac App Store", "not_approved")
        self.assert_section_result(result.stdout, "npm", "not_approved")
        self.assert_section_result(result.stdout, "Mole", "not_approved")
        for forbidden in ("Homebrew cleanup:", "Homebrew autoremove:"):
            self.assertNotIn(forbidden.lower(), result.stdout.lower())
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
                    DIALOG_ANSWERS="Cancel|OK|OK|OK")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(["mas", "upgrade"], calls)
                self.assertTrue(self.matches(calls, "npm", "install"))
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
