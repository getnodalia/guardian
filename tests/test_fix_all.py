"""Offline safety and regression tests for bounded multi-file fix-all."""

import json
import unittest
from unittest.mock import MagicMock, patch

from nodalia_guardian.fix_all import (
    _current_review_findings, _validate_multi_patch, propose_fix_all,
)
from nodalia_guardian.git_changes import GitChangesAPI, safe_bot_branch
from nodalia_guardian.maintenance import parse_command, parse_maintenance

REPO = "getnodalia/agora"
SHA = "a" * 40
BLOB = "b" * 40


def changes(*replacements):
    return json.dumps({
        "summary": "Correct current, verified review findings",
        "changes": [{
            "path": path, "old": old, "new": new,
            "reason": "Necessary verified correction"
        } for path, old, new in replacements],
    })


def reviewed_file(path, status="modified"):
    return {"filename": path, "status": status}


def inline(path, *, sha=SHA, login="nodalia-guardian[bot]", line=12,
           body="Guardian: MEDIUM reproducible missing check"):
    return {
        "id": 888, "user": {"login": login, "type": "Bot"},
        "commit_id": sha, "path": path, "line": line, "body": body,
    }


def pr(sha=SHA, *, private=False, ref="feat/feature"):
    return {
        "state": "open", "draft": False,
        "head": {"sha": sha, "ref": ref,
                 "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"private": private}},
    }


class FixAllTests(unittest.TestCase):
    def test_slash_command_requires_exact_name_no_arguments(self):
        self.assertEqual(parse_command("/guardian fix-all"), ("fix-all", ""))
        self.assertIsNone(parse_command("/guardian fix-all 123"))
        self.assertIsNone(parse_command("/guardian fix-all; anything"))

    def test_bot_branch_and_protected_source_rules(self):
        self.assertTrue(safe_bot_branch("guardian/fixall-pr29-" + SHA[:10]))
        self.assertFalse(safe_bot_branch("guardian/fixall-pr29-INVALID"))
        self.assertFalse(safe_bot_branch("main"))

    def test_current_review_comments_only_and_changed_paths_only(self):
        api = MagicMock(spec=GitChangesAPI)
        api._path.return_value = "/repos/getnodalia/agora"
        api._get_list.side_effect = [
            [reviewed_file("src/api.py"), reviewed_file("src/ui.py"),
             reviewed_file(".github/workflows/ci.yml")],
            [
                inline("src/api.py"),
                inline("src/ui.py", login="coderabbitai[bot]"),
                inline("src/ui.py", sha="c" * 40),
                inline(".github/workflows/ci.yml"),
                inline("src/api.py", login="malicious[bot]"),
                inline("src/api.py", line=None),
                inline("src/api.py", body="-----BEGIN PRIVATE KEY-----"),
            ],
        ]
        found = _current_review_findings(api, REPO, 29, SHA)
        self.assertEqual({x["path"] for x in found}, {"src/api.py", "src/ui.py"})
        self.assertEqual(len(found), 2)
        self.assertEqual({x["source"] for x in found}, {"Guardian", "CodeRabbit"})

    def test_rejects_unbounded_changed_files_or_comments(self):
        api = MagicMock(spec=GitChangesAPI)
        api._path.return_value = "/repos/getnodalia/agora"
        api._get_list.return_value = [reviewed_file("src/a.rs")] * 100
        with self.assertRaisesRegex(ValueError, "Too many"):
            _current_review_findings(api, REPO, 29, SHA)

    def test_multi_patch_must_refer_to_allowed_unique_files(self):
        original = {"src/a.py": "old_value = 1\n", "src/b.py": "second = 1\n"}
        updated, explanation = _validate_multi_patch(
            original, changes(("src/a.py", "old_value = 1", "old_value = 2"),
                              ("src/b.py", "second = 1", "second = 2"))
        )
        self.assertEqual(len(updated), 2)
        self.assertIn("old_value = 2", updated["src/a.py"])
        with self.assertRaisesRegex(ValueError, "unverified"):
            _validate_multi_patch(
                original, changes(("src/foreign.py", "bad", "good"))
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _validate_multi_patch(
                original, changes(("src/a.py", "old_value", "updated"),
                                  ("src/a.py", "old_value", "updated"))
            )
        with self.assertRaisesRegex(ValueError, "exact|ambiguous|Unsafe"):
            _validate_multi_patch(
                original, changes(("src/a.py", "missing_code", "invented"))
            )

    def test_multi_patch_rejects_credentials_in_generated_content(self):
        with self.assertRaisesRegex(ValueError, "credential"):
            _validate_multi_patch(
                {"src/a.py": "x = 1\n"},
                changes(("src/a.py", "x = 1",
                         "-----BEGIN PRIVATE KEY-----\nsecret"))
            )

    def test_proposes_multi_file_draft_no_main_write_no_auto_merge(self):
        api = MagicMock(spec=GitChangesAPI)
        api.get_pr.return_value = pr()
        api._path.return_value = "/repos/getnodalia/agora"
        api._get_list.side_effect = [
            [reviewed_file("src/a.py"), reviewed_file("src/b.py")],
            [inline("src/a.py"), inline("src/b.py", login="coderabbitai[bot]")],
        ]
        api.read_text.side_effect = [
            ("x = 1\n", BLOB), ("y = 1\n", "c" * 40)
        ]
        api.propose_bot_pr.return_value = {
            "html_url": "https://github.com/getnodalia/agora/pull/100"
        }
        with (patch("nodalia_guardian.fix_all.analyze",
                    return_value=(
                        changes(("src/a.py", "x = 1", "x = 2"),
                                ("src/b.py", "y = 1", "y = 2")),
                        0.0005,
                    )) as model,
              patch("nodalia_guardian.fix_all.BudgetLedger")):
            outcome = propose_fix_all(
                api, REPO, 29, SHA, key="offline-only", monthly_budget=20
            )
        self.assertEqual(outcome["status"], "proposed")
        self.assertEqual(outcome["files"], ["src/a.py", "src/b.py"])
        self.assertEqual(model.call_count, 1)
        self.assertEqual(api.write_bot_file.call_count, 2)
        branch = api.create_bot_branch.call_args.args[1]
        self.assertTrue(branch.startswith("guardian/fixall-pr29-"))
        for call in api.write_bot_file.call_args_list:
            self.assertEqual(call.args[1], branch)
        self.assertEqual(api.propose_bot_pr.call_args.args[2], "feat/feature")
        api._write.assert_not_called()

    def test_fails_before_paid_call_when_no_current_findings(self):
        api = MagicMock(spec=GitChangesAPI)
        api.get_pr.return_value = pr()
        api._path.return_value = "/repos/getnodalia/agora"
        api._get_list.side_effect = [
            [reviewed_file("src/a.py")],
            [inline("src/a.py", sha="c" * 40)],
        ]
        with patch("nodalia_guardian.fix_all.analyze") as model:
            with self.assertRaisesRegex(ValueError, "No current"):
                propose_fix_all(api, REPO, 29, SHA, key="offline")
            model.assert_not_called()
            api.create_bot_branch.assert_not_called()

    def test_rejects_private_stale_or_high_budget_without_model(self):
        api = MagicMock(spec=GitChangesAPI)
        for candidate in (pr(private=True), pr(sha="c" * 40),
                          pr(ref="main")):
            api.get_pr.return_value = candidate
            with patch("nodalia_guardian.fix_all.analyze") as model:
                with self.assertRaises(ValueError):
                    propose_fix_all(api, REPO, 29, SHA, key="offline")
                model.assert_not_called()
        with self.assertRaisesRegex(ValueError, "20 USD"):
            propose_fix_all(api, REPO, 29, SHA, key="offline",
                            monthly_budget=21)

    def test_check_action_from_other_app_is_rejected(self):
        payload = {
            "repository": {"full_name": REPO},
            "installation": {"id": 182801897},
            "sender": {"id": 241863489, "login": "maintainer", "type": "User"},
            "action": "requested_action",
            "check_run": {"id": 89, "app": {"id": 5794704},
                          "pull_requests": [{"number": 29}]},
            "requested_action": {"identifier": "guardian_fix_all"},
        }
        with patch.dict("os.environ", {"GITHUB_APP_ID": "5794704"}):
            event = parse_maintenance(
                json.dumps(payload).encode(), "check_run", {REPO}
            )
        self.assertEqual(event["command"], ["fix-all", ""])
        payload["check_run"]["app"]["id"] = 1
        with patch.dict("os.environ", {"GITHUB_APP_ID": "5794704"}):
            self.assertIsNone(parse_maintenance(
                json.dumps(payload).encode(), "check_run", {REPO}
            ))


if __name__ == "__main__":
    unittest.main()
