"""Verify a single GitHub review can publish separate anchored discussions."""

import io
import json
import os
import unittest
from unittest.mock import patch

from nodalia_guardian.bot import review_pr
from nodalia_guardian.github_api import GitHubAPI


SHA = "a" * 40
BASE = "b" * 40
PATCH = "@@ -1,1 +1,2 @@\n previous\n+new_code()\n"


class ReviewPublishingTests(unittest.TestCase):
    def test_github_api_sends_comments_in_single_review(self):
        api = GitHubAPI("fake-installation-token")
        comments = [{
            "path": "main.py", "line": 2, "side": "RIGHT",
            "body": "**MEDIUM · Guardian**\n\nCheck this.",
        }]
        with patch("nodalia_guardian.github_api.urllib.request.urlopen",
                   return_value=io.BytesIO(b"{}")) as network:
            api.create_review("getnodalia/agora", 24, event="REQUEST_CHANGES",
                              body="Review summary", commit_id=SHA, comments=comments)
        request = network.call_args.args[0]
        data = json.loads(request.data)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(data["event"], "REQUEST_CHANGES")
        self.assertEqual(data["comments"], comments)
        self.assertEqual(data["commit_id"], SHA)

    def test_invalid_inline_shape_rejected_before_network(self):
        api = GitHubAPI("fake-installation-token")
        with patch("nodalia_guardian.github_api.urllib.request.urlopen") as network:
            with self.assertRaisesRegex(ValueError, "Invalid inline review comment"):
                api.create_review("getnodalia/agora", 24, event="COMMENT",
                                  body="Summary", commit_id=SHA,
                                  comments=[{"path": "main.py", "line": -1,
                                             "side": "RIGHT", "body": "Bad"}])
            network.assert_not_called()

    def test_bot_publishes_one_review_and_one_verified_line_comment(self):
        raw = {
            "sha": SHA,
            "pr": {
                "state": "open", "draft": False,
                "base": {"sha": BASE, "repo": {"private": False}},
                "head": {"label": "dev:feature", "sha": SHA},
            },
            "files": [{"filename": "main.py", "patch": PATCH}],
        }
        summary = {
            "head_sha": SHA, "files": [{"name": "main.py"}],
            "checks": [], "failed_count": 0, "pending_count": 0,
            "draft": False, "files_truncated": False, "checks_truncated": False,
            "patches_truncated": False, "patches_available": True,
        }
        response = json.dumps({
            "verdict": "request_changes", "summary": "Correct the call.",
            "findings": [{
                "severity": "medium", "path": "main.py", "line": 2,
                "message": "Wrong call", "suggestion": "fixed_call()",
            }],
        })
        with (
            patch.dict(os.environ, {
                "GITHUB_APP_ID": "5794704",
                "GITHUB_APP_PRIVATE_KEY_PATH": "/no-real-key",
                "ANTHROPIC_API_KEY": "test-token",
            }),
            patch("nodalia_guardian.bot.installation_access_token",
                  return_value="fake-installation-token"),
            patch("nodalia_guardian.bot.GitHubAPI") as gh,
            patch("nodalia_guardian.bot.summarize", return_value=summary),
            patch("nodalia_guardian.bot.analyze", return_value=(response, 0.001)),
            patch("nodalia_guardian.bot.BudgetLedger"),
        ):
            gh.return_value.inspect_pr.return_value = raw
            gh.return_value.get_pr.return_value = {
                "state": "open", "draft": False,
                "base": {"sha": BASE}, "head": {"sha": SHA},
            }
            result = review_pr("getnodalia/agora", 24, ai=True, publish=True,
                               installation_id=123, auto_approve=False)
            kwargs = gh.return_value.create_review.call_args.kwargs
        self.assertEqual(result["inline_comments"], 1)
        self.assertEqual(kwargs["event"], "REQUEST_CHANGES")
        self.assertEqual(len(kwargs["comments"]), 1)
        self.assertIn("```suggestion\nfixed_call()\n```",
                      kwargs["comments"][0]["body"])
        self.assertNotIn("Wrong call", kwargs["body"])
        self.assertIn("Correct the call.", kwargs["body"])


if __name__ == "__main__":
    unittest.main()
