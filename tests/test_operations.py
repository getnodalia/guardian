"""Offline safety tests for draft fixes, check buttons and 3-way merges."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nodalia_guardian.fixer import _validate_proposed_patch, propose_fix
from nodalia_guardian.git_changes import GitChangesAPI, safe_bot_branch, safe_source_path
from nodalia_guardian.maintenance import parse_maintenance
from nodalia_guardian.merge_resolver import merge_plan, propose_merge_resolution
from nodalia_guardian.operations import OperationQueue
from nodalia_guardian.webhooks import DeliveryQueue

SHA = "a" * 40
BASE = "b" * 40
ANCESTOR = "c" * 40
REPO = "getnodalia/agora"


def file(sha, mode="100644"):
    return {"sha": sha, "mode": mode, "type": "blob"}


class FixTests(unittest.TestCase):
    def test_source_path_allowlist(self):
        self.assertTrue(safe_source_path("apps/server/src/lib.rs"))
        self.assertFalse(safe_source_path(".github/workflows/ci.yml"))
        self.assertFalse(safe_source_path("config/.env"))
        self.assertFalse(safe_source_path("Cargo.lock"))
        self.assertFalse(safe_source_path("../escape.py"))
        self.assertFalse(safe_source_path("src/secret_key.py"))

    def test_branch_prefix_required(self):
        self.assertTrue(safe_bot_branch("guardian/fix-pr24-" + SHA[:10]))
        self.assertFalse(safe_bot_branch("main"))
        self.assertFalse(safe_bot_branch("guardian/fix-pr24-INVALID"))

    def test_single_exact_replacement_only(self):
        source = "let x = 1;\nlet y = 2;\n"
        raw = json.dumps({"path": "src/main.rs",
                          "old": "let x = 1;", "new": "let x = 3;",
                          "explanation": "Fix"})
        updated, _ = _validate_proposed_patch(source, "src/main.rs", raw)
        self.assertIn("let x = 3;", updated)
        with self.assertRaises(ValueError):
            _validate_proposed_patch("repeat repeat", "src/main.rs", json.dumps({
                "path": "src/main.rs", "old": "repeat", "new": "unsafe",
                "explanation": "Bad"
            }))
        with self.assertRaises(ValueError):
            _validate_proposed_patch(source, "src/main.rs",
                                     raw.replace("src/main.rs", "other.rs"))

    def test_propose_fix_creates_bot_branch_and_draft_pr_only(self):
        api = MagicMock(spec=GitChangesAPI)
        api.get_pr.return_value = {
            "state": "open", "draft": False,
            "head": {"sha": SHA, "ref": "feat/new",
                     "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"private": False}},
        }
        api._path.return_value = "/repos/getnodalia/agora"
        api._get_list.return_value = [{
            "id": 901, "user": {"login": "nodalia-guardian[bot]"},
            "commit_id": SHA, "path": "src/main.rs",
            "body": "**MEDIUM · Nodalia Guardian** fix the line",
        }]
        api.read_text.return_value = ("fn main() { bad(); }\n", "d" * 40)
        api.propose_bot_pr.return_value = {"html_url": "https://github.com/getnodalia/agora/pull/101"}
        with (
            patch("nodalia_guardian.fixer.analyze", return_value=(
                json.dumps({"path": "src/main.rs", "old": "bad();",
                            "new": "good();", "explanation": "Verified"}), 0.004
            )),
            patch("nodalia_guardian.fixer.BudgetLedger"),
        ):
            result = propose_fix(api, REPO, 24, SHA, comment_id=None,
                                 key="test-secret")
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(result["branch"].startswith("guardian/fix-pr24"))
        api.create_bot_branch.assert_called_once()
        api.write_bot_file.assert_called_once()
        self.assertIn("good();", api.write_bot_file.call_args.args[-1])
        self.assertEqual(api.propose_bot_pr.call_args.args[2], "feat/new")

    def test_fix_refuses_stale_head_before_token_usage(self):
        api = MagicMock(spec=GitChangesAPI)
        api.get_pr.return_value = {
            "state": "open", "draft": False,
            "head": {"sha": BASE, "ref": "feat/new",
                     "repo": {"full_name": REPO}},
            "base": {"ref": "main", "repo": {"private": False}},
        }
        with patch("nodalia_guardian.fixer.analyze") as paid:
            with self.assertRaisesRegex(ValueError, "stale"):
                propose_fix(api, REPO, 24, SHA, comment_id=None, key="test")
            paid.assert_not_called()


class MergeTests(unittest.TestCase):
    def test_3way_merge_keeps_both_unique_changes(self):
        a = {"old.txt": file("1" * 40),
             "base.txt": file("2" * 40)}
        h = {**a, "old.txt": file("3" * 40),
             "head_only.py": file("4" * 40)}
        b = {**a, "base.txt": file("5" * 40),
             "base_only.py": file("6" * 40)}
        changes, conflicts = merge_plan(a, h, b)
        self.assertEqual(conflicts, [])
        self.assertEqual({c["path"] for c in changes}, {"base.txt", "base_only.py"})

    def test_same_path_different_changes_requires_resolution(self):
        a = {"src/a.py": file("1" * 40)}
        h = {"src/a.py": file("2" * 40)}
        b = {"src/a.py": file("3" * 40)}
        changes, conflicts = merge_plan(a, h, b)
        self.assertEqual(changes, [])
        self.assertEqual(conflicts, ["src/a.py"])

    def test_deletion_from_base_is_a_tree_entry(self):
        a = {"src/a.py": file("1" * 40)}
        h = dict(a)
        b = {}
        changes, conflicts = merge_plan(a, h, b)
        self.assertEqual(changes[0]["sha"], None)
        self.assertEqual(conflicts, [])

    def test_symlink_conflicts_are_rejected(self):
        a = {"src/a.py": file("1" * 40)}
        h = {"src/a.py": file("2" * 40)}
        b = {"src/a.py": {"sha": "3" * 40, "mode": "120000",
                          "type": "blob", "unsupported": True}}
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            merge_plan(a, h, b)

    def test_merge_draft_has_two_parents_and_never_writes_original_branch(self):
        api = MagicMock(spec=GitChangesAPI)
        api._path.return_value = "/repos/getnodalia/agora"
        api.get_pr.return_value = {
            "state": "open", "draft": False,
            "head": {"sha": SHA, "ref": "feat/new",
                     "repo": {"full_name": REPO}},
            "base": {"sha": BASE, "ref": "main",
                     "repo": {"full_name": REPO, "private": False}},
        }
        ancestor_tree = {
            "tree": [{"path": "src/main.py", "sha": "1" * 40,
                      "type": "blob", "mode": "100644"}],
            "truncated": False,
        }
        head_tree = {"tree": [{"path": "src/main.py", "sha": "2" * 40,
                              "type": "blob", "mode": "100644"}],
                     "truncated": False}
        base_tree = {"tree": [
            {"path": "src/main.py", "sha": "1" * 40,
             "type": "blob", "mode": "100644"},
            {"path": "src/extra.py", "sha": "3" * 40,
             "type": "blob", "mode": "100644"},
        ], "truncated": False}

        def get_data(path, query=None):
            if "/compare/" in path:
                return {"merge_base_commit": {"sha": ANCESTOR}}
            if path.endswith("/git/commits/" + ANCESTOR):
                return {"tree": {"sha": "d" * 40}}
            if path.endswith("/git/commits/" + SHA):
                return {"tree": {"sha": "e" * 40}}
            if path.endswith("/git/commits/" + BASE):
                return {"tree": {"sha": "f" * 40}}
            if path.endswith("/git/trees/" + "d" * 40):
                return ancestor_tree
            if path.endswith("/git/trees/" + "e" * 40):
                return head_tree
            if path.endswith("/git/trees/" + "f" * 40):
                return base_tree
            raise AssertionError(path)
        api._get.side_effect = get_data

        def post(method, path, payload):
            if path.endswith("/git/trees"):
                return {"sha": "7" * 40}
            if path.endswith("/git/commits"):
                self.assertEqual(payload["parents"], [SHA, BASE])
                return {"sha": "8" * 40}
            raise AssertionError(path)

        api._write.side_effect = post
        api.propose_bot_pr.return_value = {"html_url": "https://github.com/getnodalia/agora/pull/102"}
        with patch("nodalia_guardian.merge_resolver.analyze") as paid:
            result = propose_merge_resolution(api, REPO, 24, SHA, key="test")
            paid.assert_not_called()
        self.assertEqual(result["conflict_files"], 0)
        self.assertEqual(result["base_paths"], 1)
        self.assertEqual(api.create_bot_branch.call_args.args[-1], "8" * 40)
        self.assertEqual(api.propose_bot_pr.call_args.args[2], "feat/new")


class CheckActionTests(unittest.TestCase):
    def test_only_own_gitHub_app_buttons_are_enqueued(self):
        data = {
            "repository": {"full_name": REPO},
            "installation": {"id": 182801897},
            "sender": {"id": 241863489, "login": "danielmigueltejedor", "type": "User"},
            "action": "requested_action",
            "check_run": {"id": 9899, "app": {"id": 5794704},
                          "pull_requests": [{"number": 24}]},
            "requested_action": {"identifier": "guardian_fix"},
        }
        with patch.dict(os.environ, {"GITHUB_APP_ID": "5794704"}):
            item = parse_maintenance(json.dumps(data).encode(), "check_run", {REPO})
        self.assertEqual(item["command"], ["fix", ""])
        data["check_run"]["app"]["id"] = 123
        with patch.dict(os.environ, {"GITHUB_APP_ID": "5794704"}):
            self.assertIsNone(parse_maintenance(json.dumps(data).encode(), "check_run", {REPO}))

    def test_operations_queue_claim_is_durable_and_exclusive(self):
        with tempfile.TemporaryDirectory() as temp:
            queue = OperationQueue(DeliveryQueue(Path(temp) / "db.sqlite"))
            with queue.queue.connection() as db:
                db.execute(
                    """INSERT INTO guardian_operations
                       (repo,pr,installation,requester_id,requester,
                        target_comment_id,kind,sha)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (REPO, 24, 182801897, 241863489,
                     "danielmigueltejedor", None, "fix", SHA),
                )
            row = queue.claim()
            self.assertEqual(row[1], REPO)
            self.assertIsNone(queue.claim())
            queue.finish(row, "done", "Draft PR created")


if __name__ == "__main__":
    unittest.main()
