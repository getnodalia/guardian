"""Offline tests: never publish a paid review to a PR closed mid-analysis."""

import json
import os
import unittest
from unittest.mock import patch

from nodalia_guardian.bot import review_pr
from nodalia_guardian.github_api import GitHubError


SHA = "a" * 40
REPO = "getnodalia/agora"


class PublicationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "sha": SHA, "files": [],
            "pr": {
                "state": "open", "draft": False,
                "base": {"repo": {"private": False}},
                "head": {"label": "user:feature", "sha": SHA},
            },
        }
        self.summary = {
            "head_sha": SHA, "files": [], "checks": [],
            "files_truncated": False, "checks_truncated": False,
            "failed_count": 0, "pending_count": 0, "draft": False,
        }

    def run_review(self, *, state, draft):
        response = json.dumps({
            "verdict": "comment", "summary": "Check authorization",
            "findings": [],
        })
        with (
            patch.dict(os.environ, {
                "GITHUB_APP_ID": "5794704",
                "GITHUB_APP_PRIVATE_KEY_PATH": "/not-a-real-private-key",
                "ANTHROPIC_API_KEY": "test-placeholder",
            }),
            patch("nodalia_guardian.bot.installation_access_token",
                  return_value="test-token"),
            patch("nodalia_guardian.bot.GitHubAPI") as api,
            patch("nodalia_guardian.bot.summarize", return_value=self.summary),
            patch("nodalia_guardian.bot.analyze",
                  return_value=(response, 0.001)),
            patch("nodalia_guardian.bot.BudgetLedger"),
        ):
            api.return_value.inspect_pr.return_value = self.raw
            api.return_value.get_pr.return_value = {
                "head": {"sha": SHA}, "state": state, "draft": draft,
            }
            with self.assertRaisesRegex(GitHubError, "closed or became draft"):
                review_pr(REPO, 18, ai=True, publish=True,
                          installation_id=123, auto_approve=False)
            api.return_value.create_review.assert_not_called()

    def test_closed_during_model_call_blocks_publication(self):
        self.run_review(state="closed", draft=False)

    def test_became_draft_during_model_call_blocks_publication(self):
        self.run_review(state="open", draft=True)


if __name__ == "__main__":
    unittest.main()
