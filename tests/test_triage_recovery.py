"""Offline regressions: triage must not lose labels when Haiku/API fails."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from nodalia_guardian.anthropic_api import AnthropicAPIError
from nodalia_guardian.github_api import GitHubError
from nodalia_guardian.maintenance import MaintenanceQueue, parse_maintenance
from nodalia_guardian.maintenance_worker import (
    _fingerprint, _mark_once, process_maintenance,
)
from nodalia_guardian.webhooks import DeliveryQueue

REPO = "getnodalia/agora"


def event(action="opened", *, kind="issues"):
    item = {
        "repository": {"full_name": REPO},
        "installation": {"id": 182801897},
        "sender": {"id": 241863489, "login": "danielmigueltejedor",
                   "type": "User"},
        "action": action,
        "issue": {"number": 42},
        "pull_request": {"number": 42, "head": {"sha": "a" * 40}},
    }
    return parse_maintenance(json.dumps(item).encode("utf-8"), kind, {REPO})


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = MaintenanceQueue(
            DeliveryQueue(Path(self.tmp.name) / "deliveries.sqlite3")
        )
        self.api = MagicMock()
        self.api.issue.return_value = {
            "number": 42, "state": "open",
            "title": "La sesión cambia de estado después de recuperar la conexión",
            "body": "Estado inconsistente al reconectar.",
        }
        self.api.existing_labels.return_value = {
            "type:bug", "needs-triage", "type:feature",
        }
        self.api.similar_issues.return_value = []
        self.api.open_prs.return_value = []
        self.api._get.return_value = {"private": False}
        self.api.add_labels.return_value = [{"name": "needs-triage"}]
        self.auth = patch(
            "nodalia_guardian.maintenance_worker.installation_access_token",
            return_value="fake-token",
        )
        self.client = patch(
            "nodalia_guardian.maintenance_worker.MaintenanceAPI",
            return_value=self.api,
        )
        self.auth.start()
        self.client.start()
        self.addCleanup(self.auth.stop)
        self.addCleanup(self.client.stop)

    def submit(self, delivery, action="opened", kind="issues"):
        self.assertEqual(
            self.queue.add(delivery, event(action, kind=kind)), 1
        )
        return process_maintenance(
            self.queue, enable_triage=True, enable_haiku=True,
            enable_closure=False, enable_operations=False, monthly_budget=20
        )

    def test_haiku_failure_falls_back_to_label_without_retry(self):
        with patch(
            "nodalia_guardian.maintenance_worker.classify_haiku",
            side_effect=AnthropicAPIError("Claude API returned HTTP 400"),
        ) as model:
            first = self.submit("delivery-1")
            second = self.submit("delivery-2", "edited")
        self.assertEqual(first["status"], "done")
        self.assertEqual(first["labels"], ["needs-triage"])
        self.assertEqual(first["haiku"], "model_unavailable")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(model.call_count, 1)
        self.api.add_labels.assert_called_once_with(
            REPO, 42, ["needs-triage"]
        )

    def test_failed_github_write_can_recover_on_new_event(self):
        self.api.issue.return_value = {
            "number": 42, "state": "open",
            "title": "Fix crash on login",
            "body": "A reproducible crash occurs during login.",
        }
        self.api.existing_labels.side_effect = [
            GitHubError("GitHub returned HTTP 403"),
            {"type:bug"},
        ]
        first = self.submit("delivery-1")
        self.assertEqual(first["status"], "failed")
        # Old versions stored a triage fingerprint before any API writes.
        # Keeping those legacy rows must not block recovery.
        _mark_once(
            self.queue,
            _fingerprint(REPO, 42, "issue", self.api.issue.return_value),
        )
        second = self.submit("delivery-2", "edited")
        self.assertEqual(second["status"], "done")
        self.assertEqual(second["labels"], ["type:bug"])
        self.api.add_labels.assert_called_once_with(
            REPO, 42, ["type:bug"]
        )

    def test_pr_synchronize_reclassifies_new_commit_only(self):
        self.api.issue.return_value = {
            "number": 42, "state": "open", "pull_request": {},
            "title": "Add connection recovery", "body": "New behavior",
        }
        self.api.existing_labels.return_value = {"type:feature"}
        self.api.get_pr.side_effect = [
            {"number": 42, "title": "Add connection recovery", "body": "New behavior",
             "head": {"sha": "a" * 40}, "base": {"sha": "c" * 40}},
            {"number": 42, "title": "Add connection recovery", "body": "New behavior",
             "head": {"sha": "b" * 40}, "base": {"sha": "c" * 40}},
        ]
        first = self.submit("delivery-1", "opened", "pull_request")
        second = self.submit("delivery-2", "synchronize", "pull_request")
        self.assertEqual(first["status"], "done")
        self.assertEqual(second["status"], "done")
        self.assertEqual(self.api.add_labels.call_count, 2)
        self.assertEqual(first["haiku"], "not_needed")


if __name__ == "__main__":
    unittest.main()
