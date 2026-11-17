"""Offline tests for opt-in issue/PR classification and signed command routing."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodalia_guardian.budget import price_micro_usd
from nodalia_guardian.maintenance import MaintenanceQueue, parse_command, parse_maintenance
from nodalia_guardian.maintenance_worker import _command, privileged
from nodalia_guardian.triage import (
    HAIKU_SCHEMA, classify_haiku, exact_issue_duplicate,
    exact_pr_duplicate, heuristic_labels,
)
from nodalia_guardian.webhooks import DeliveryQueue

REPO = "getnodalia/agora"


def event(event_name="issues", action="opened"):
    result = {
        "repository": {"full_name": REPO},
        "installation": {"id": 182801897},
        "sender": {"id": 241863489, "login": "danielmigueltejedor", "type": "User"},
        "action": action,
        "issue": {"number": 42, "title": "Broken auth"},
        "pull_request": {"number": 42, "head": {"sha": "a" * 40}},
    }
    return result


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = MaintenanceQueue(DeliveryQueue(Path(self.temp.name) / "db.sqlite"))

    def test_issue_and_pr_event(self):
        data = event()
        item = parse_maintenance(json.dumps(data).encode(), "issues", {REPO})
        self.assertEqual(item["kind"], "issue")
        data["action"] = "ready_for_review"
        item = parse_maintenance(json.dumps(data).encode(), "pull_request", {REPO})
        self.assertEqual(item["kind"], "pull_request")

    def test_signing_scope_and_bot_filter(self):
        data = event()
        data["sender"]["type"] = "Bot"
        self.assertIsNone(parse_maintenance(json.dumps(data).encode(), "issues", {REPO}))
        data["sender"]["type"] = "User"
        self.assertIsNone(parse_maintenance(json.dumps(data).encode(), "issues", {"getnodalia/forja"}))

    def test_only_exact_created_command(self):
        data = event("issue_comment", "created")
        data["issue"]["pull_request"] = {"url": "https://api.github.com"}
        data["comment"] = {"id": 123, "body": "/guardian label type:bug"}
        item = parse_maintenance(json.dumps(data).encode(), "issue_comment", {REPO})
        self.assertEqual(item["command"], ["label", "type:bug"])
        data["action"] = "edited"
        self.assertIsNone(parse_maintenance(json.dumps(data).encode(), "issue_comment", {REPO}))
        self.assertIsNone(parse_command("/guardian fix; rm -rf /"))
        self.assertIsNone(parse_command("/guardian fix\n/guardian duplicate #13"))

    def test_queue_is_idempotent_and_claims_once(self):
        item = parse_maintenance(json.dumps(event()).encode(), "issues", {REPO})
        self.assertEqual(self.queue.add("delivery-1", item), 1)
        self.assertEqual(self.queue.add("delivery-1", item), 0)
        row = self.queue.claim()
        self.assertEqual(row[1], REPO)
        self.assertEqual(row[2], 42)
        self.assertIsNone(self.queue.claim())
        self.queue.finish(row, "done", "classified")

    def test_help_command_cannot_trigger_code_write(self):
        self.assertEqual(parse_command("/guardian help"), ("help", ""))
        self.assertEqual(parse_command("/guardian resolve-conflicts"),
                         ("resolve-conflicts", ""))


class TriageTests(unittest.TestCase):
    def test_haiku_price_rounded_up_per_request(self):
        self.assertEqual(price_micro_usd("claude-haiku-5-5", 1, 0), 1)
        self.assertEqual(price_micro_usd("claude-haiku-5-5", 1_000_000, 1_000_000), 600000)

    def test_heuristic_labels(self):
        result = heuristic_labels("Fix TOTP login crash", "Backend auth issue")
        self.assertIn("type:bug", result)
        self.assertIn("area:security", result)

    def test_haiku_schema_is_compatible_with_api_structured_outputs(self):
        props = HAIKU_SCHEMA["properties"]
        # Numerical JSON Schema constraints generate HTTP 400 with Claude API.
        self.assertEqual(props["confidence"]["type"], "number")
        self.assertNotIn("minimum", props["confidence"])
        self.assertNotIn("maximum", props["confidence"])
        self.assertIn("unknown", props["category"]["enum"])
        self.assertFalse(HAIKU_SCHEMA["additionalProperties"])

    def test_haiku_validates_confidence_locally_after_api_response(self):
        class Ledger:
            pass
        for confidence in (-0.1, 1.1, True):
            with self.subTest(confidence=confidence):
                response = json.dumps({
                    "category": "bug", "area": "security",
                    "priority": "high", "confidence": confidence,
                })
                with patch("nodalia_guardian.triage.analyze",
                           return_value=(response, 0.00001)):
                    with patch("nodalia_guardian.triage.BudgetLedger",
                               return_value=Ledger()):
                        with self.assertRaises(ValueError):
                            classify_haiku("A title", "Description", key="test")

    def test_exact_issue_duplicate_requires_substantive_body(self):
        old = {"number": 3, "title": "Can't authorize requests",
               "body": "A detailed reproduction for the authorization failure."}
        current = {**old, "number": 10}
        self.assertEqual(exact_issue_duplicate(current, [old]), 3)
        self.assertIsNone(exact_issue_duplicate({**current, "body": "short"}, [old]))
        self.assertIsNone(exact_issue_duplicate(current, [
            {**old, "body": "A different detailed reproduction of failure."}
        ]))

    def test_exact_pr_duplicate_requires_same_code_and_base(self):
        old = {"number": 7, "state": "open", "head": {"sha": "a" * 40},
               "base": {"ref": "main", "repo": {"id": 18}}}
        current = {**old, "number": 15}
        self.assertEqual(exact_pr_duplicate(current, [old]), 7)
        self.assertIsNone(exact_pr_duplicate(current, [
            {**old, "base": {"ref": "feat/organizations", "repo": {"id": 18}}}
        ]))

    def test_haiku_low_confidence_no_risky_labels(self):
        class Ledger:
            pass
        with patch("nodalia_guardian.triage.analyze",
                   return_value=(json.dumps({
                       "category": "bug", "area": "security",
                       "priority": "critical", "confidence": 0.6
                   }), 0.00001)):
            with patch("nodalia_guardian.triage.BudgetLedger", return_value=Ledger()):
                labels, cost = classify_haiku("A title", "Description", key="test")
        self.assertEqual(labels, {"needs-triage"})

    def test_unconfigured_operator_never_can_write(self):
        api = type("API", (), {"permission": lambda self, repo, login: "admin"})()
        with patch.dict(os.environ, {"GUARDIAN_MAINTAINER_IDS": ""}):
            self.assertFalse(privileged(api, REPO, "danielmigueltejedor", 241863489))


class CommandSecurityTests(unittest.TestCase):
    def test_changed_or_forged_comment_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = MaintenanceQueue(DeliveryQueue(Path(tmp) / "db.sqlite"))
            row = ("delivery-1", REPO, 42, 182801897, "command",
                   241863489, "danielmigueltejedor", 123,
                   json.dumps(["label", "type:bug"]))
            class FakeAPI:
                def issue_comment(self, *_):
                    return {"user": {"id": 241863489, "login": "danielmigueltejedor"},
                            "body": "/guardian label area:security"}
            outcome = _command(FakeAPI(), queue, row, {"state": "open"},
                               ("label", "type:bug"), allow_closure=False,
                               allow_operations=False)
            self.assertEqual(outcome, "Unverified or edited comment")


if __name__ == "__main__":
    unittest.main()
