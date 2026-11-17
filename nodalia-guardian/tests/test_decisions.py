"""Conservative review gates are independent of AI claims."""

import json
import unittest

from nodalia_guardian.decisions import InvalidReview, gate, parse_review


class DecisionsTests(unittest.TestCase):
    def setUp(self):
        self.summary = {
            "draft": False,
            "files_truncated": False,
            "patches_truncated": False,
            "checks_truncated": False,
            "patches_available": True,
            "failed_count": 0,
            "pending_count": 0,
            "checks": [{"name": "CI / test", "conclusion": "success"}],
            "files": [{"name": "src/main.rs"}],
        }
        self.approve = {"verdict": "approve", "summary": "No issues observed", "findings": []}

    def test_approval_requires_all_gates(self):
        event, reasons = gate(self.summary, self.approve, ["CI / test"], auto_approve=True)
        self.assertEqual((event, reasons), ("APPROVE", []))

    def test_cannot_approve_without_explicit_required_checks(self):
        event, reasons = gate(self.summary, self.approve, [], auto_approve=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("Required check allowlist not configured", reasons)

    def test_cannot_approve_own_correction(self):
        event, reasons = gate(self.summary, self.approve, ["CI / test"], auto_approve=True, own_correction=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("Guardian cannot approve its own correction", reasons)

    def test_ci_failure_blocks_approval(self):
        self.summary["failed_count"] = 1
        self.summary["checks"][0]["conclusion"] = "failure"
        event, reasons = gate(self.summary, self.approve, ["CI / test"], auto_approve=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("CI reports failures", reasons)

    def test_sensitive_paths_block_approval(self):
        self.summary["files"] = [{"name": ".github/workflows/ci.yml"}]
        event, reasons = gate(self.summary, self.approve, ["CI / test"], auto_approve=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("Security/CI/infrastructure-sensitive files changed", reasons)

    def test_missing_patches_block_approval(self):
        self.summary["patches_available"] = False
        event, reasons = gate(self.summary, self.approve, ["CI / test"], auto_approve=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("Complete source changes not available", reasons)

    def test_review_schema_rejects_fabricated_file(self):
        response = json.dumps({"verdict": "request_changes", "summary": "Bug", "findings":
                               [{"severity": "high", "path": "secrets.env", "message": "Problem"}]})
        with self.assertRaises(InvalidReview):
            parse_review(response, {"src/main.rs"})

    def test_model_cannot_force_approval_on_open_findings(self):
        recommendation = {"verdict": "approve", "summary": "No worries", "findings":
                          [{"severity": "high", "path": "src/main.rs", "message": "Security regression"}]}
        event, reasons = gate(self.summary, recommendation, ["CI / test"], auto_approve=True)
        self.assertEqual(event, "COMMENT")
        self.assertIn("Model reported substantive findings", reasons)


if __name__ == "__main__":
    unittest.main()
