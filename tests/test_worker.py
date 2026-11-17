"""Offline tests: webhook events must not spend credits on unready PRs."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodalia_guardian.worker import process_one
from nodalia_guardian.webhooks import DeliveryQueue


SHA = "a" * 40
REPO = "getnodalia/agora"


class WorkerReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = DeliveryQueue(Path(self.temp.name) / "deliveries.sqlite3")
        self.raw = {"sha": SHA, "pr": {"state": "open", "draft": False}}
        self.summary = {
            "checks": [{"name": "CI", "status": "completed", "conclusion": "success"}],
            "pending_count": 0,
            "checks_truncated": False,
        }

    def enqueue(self, delivery="delivery-1"):
        self.queue.add(delivery, [(REPO, 18, 123, SHA)])

    def run_once(self):
        with (
            patch("nodalia_guardian.worker.installation_access_token", return_value="test-token"),
            patch("nodalia_guardian.worker.GitHubAPI") as client,
            patch("nodalia_guardian.worker.summarize", return_value=self.summary),
            patch("nodalia_guardian.worker.review_pr",
                  return_value={"decision": "COMMENT"}) as review,
            patch.dict("os.environ", {"GITHUB_APP_ID": "5794704",
                                      "GITHUB_APP_PRIVATE_KEY_PATH": "/no-real-key"}),
        ):
            client.return_value.inspect_pr.return_value = self.raw
            result = process_one(self.queue, required_checks=[], auto_approve=False)
            return result, review

    def test_draft_is_skipped_without_ai_or_publish(self):
        self.raw["pr"]["draft"] = True
        self.enqueue()
        result, review = self.run_once()
        self.assertEqual(result["reason"], "Draft PR")
        review.assert_not_called()

    def test_closed_pr_is_skipped(self):
        self.raw["pr"]["state"] = "closed"
        self.enqueue()
        result, review = self.run_once()
        self.assertEqual(result["reason"], "PR is not open")
        review.assert_not_called()

    def test_pending_ci_is_skipped(self):
        self.summary["pending_count"] = 1
        self.enqueue()
        result, review = self.run_once()
        self.assertEqual(result["reason"], "CI not complete")
        review.assert_not_called()

    def test_missing_or_truncated_ci_is_skipped(self):
        self.enqueue()
        self.summary["checks"] = []
        result, review = self.run_once()
        self.assertEqual(result["reason"], "CI not complete")
        review.assert_not_called()
        self.enqueue("delivery-2")
        self.summary["checks"] = [{"name": "CI", "status": "completed", "conclusion": "success"}]
        self.summary["checks_truncated"] = True
        result, review = self.run_once()
        self.assertEqual(result["reason"], "CI not complete")
        review.assert_not_called()

    def test_ready_pr_reviews_once_and_dedupes_order_changes(self):
        self.summary["checks"].append(
            {"name": "Lint", "status": "completed", "conclusion": "failure"})
        self.enqueue()
        result, review = self.run_once()
        self.assertEqual(result["status"], "done")
        self.assertEqual(review.call_count, 1)
        self.enqueue("delivery-2")
        self.summary["checks"].reverse()
        result, review = self.run_once()
        self.assertEqual(result["reason"], "Duplicate analysis")
        review.assert_not_called()

    def test_guardian_owned_check_does_not_trigger_second_paid_review(self):
        self.enqueue()
        first, review = self.run_once()
        self.assertEqual(first["status"], "done")
        self.enqueue("delivery-2")
        self.summary["checks"].append({
            "name": "Nodalia Guardian · Maintain",
            "status": "completed", "conclusion": "neutral",
        })
        result, review = self.run_once()
        self.assertEqual(result["reason"], "Duplicate analysis")
        review.assert_not_called()

    def test_new_sha_supersedes_old_delivery(self):
        self.enqueue()
        self.raw["sha"] = "b" * 40
        result, review = self.run_once()
        self.assertEqual(result["reason"], "Head SHA changed")
        review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
