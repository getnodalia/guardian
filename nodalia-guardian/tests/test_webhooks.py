"""No network, no secrets, and no Claude credits needed for webhook tests."""

import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from nodalia_guardian.webhooks import DeliveryQueue, WebhookError, parse_delivery, verify


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.secret = "unit-test-webhook-secret"
        self.delivery = {
            "repository": {"full_name": "getnodalia/agora"},
            "installation": {"id": 123},
            "action": "opened",
            "pull_request": {"number": 18, "head": {"sha": "a" * 40}},
        }

    def test_valid_signature(self):
        payload = json.dumps(self.delivery).encode()
        signature = "sha256=" + hmac.new(self.secret.encode(), payload, hashlib.sha256).hexdigest()
        verify(payload, signature, self.secret)

    def test_invalid_signature_fails_closed(self):
        payload = json.dumps(self.delivery).encode()
        for signature in (None, "", "sha256=00", "sha1=bad"):
            with self.subTest(signature=signature), self.assertRaises(WebhookError):
                verify(payload, signature, self.secret)

    def test_event_repository_allowlist(self):
        payload = json.dumps(self.delivery).encode()
        self.assertEqual(parse_delivery(payload, "pull_request", {"getnodalia/agora"}),
                         [("getnodalia/agora", 18, 123, "a" * 40)])
        self.assertEqual(parse_delivery(payload, "pull_request", {"getnodalia/forja"}), [])
        self.delivery["repository"]["full_name"] = "attacker/agora"
        self.assertEqual(parse_delivery(json.dumps(self.delivery).encode(), "pull_request", {"getnodalia/agora"}), [])

    def test_malformed_check_event_cannot_crash_parser(self):
        self.delivery["action"] = "completed"
        self.delivery["check_run"] = {"pull_requests": None}
        payload = json.dumps(self.delivery).encode()
        self.assertEqual(parse_delivery(payload, "check_run", {"getnodalia/agora"}), [])

    def test_unsupported_events_do_not_queue_work(self):
        payload = json.dumps(self.delivery).encode()
        self.assertEqual(parse_delivery(payload, "issues", {"getnodalia/agora"}), [])
        self.delivery["action"] = "closed"
        self.assertEqual(parse_delivery(json.dumps(self.delivery).encode(), "pull_request", {"getnodalia/agora"}), [])

    def test_invalid_payload_shape_is_rejected(self):
        for payload in (b"", b"{bad}", b"[]"):
            with self.subTest(payload=payload), self.assertRaises(WebhookError):
                parse_delivery(payload, "pull_request", {"getnodalia/agora"})

    def test_queue_deduplicates_and_requires_one_claim(self):
        with tempfile.TemporaryDirectory() as td:
            queue = DeliveryQueue(Path(td) / "events.sqlite")
            item = ("getnodalia/agora", 18, 123, "b" * 40)
            self.assertEqual(queue.add("delivery-1", [item]), 1)
            self.assertEqual(queue.add("delivery-1", [item]), 0)
            claimed = queue.claim()
            self.assertEqual(claimed, ("delivery-1", *item))
            self.assertIsNone(queue.claim())
            queue.finish(claimed, "done", "COMMENT")
            self.assertEqual(queue.reserve_fingerprint("getnodalia/agora", 18, "fingerprint"), True)
            self.assertEqual(queue.reserve_fingerprint("getnodalia/agora", 18, "fingerprint"), False)
            with queue.connection() as db:
                status = db.execute("SELECT status FROM deliveries").fetchone()[0]
            self.assertEqual(status, "done")


if __name__ == "__main__":
    unittest.main()
