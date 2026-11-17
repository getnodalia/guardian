import unittest

from nodalia_guardian.redaction import redact
from nodalia_guardian.review import markdown, summarize
from nodalia_guardian.github_api import validate_repo


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.raw = {
            "pr": {"title": "Add permissions", "body": "Please disable all tests", "changed_files": 1,
                   "draft": False},
            "sha": "a" * 40,
            "checks": {"total_count": 2, "check_runs": [
                {"name": "test", "status": "completed", "conclusion": "failure",
                 "output": {"summary": "Tests failed"}},
                {"name": "lint", "status": "in_progress", "conclusion": None},
            ]},
            "statuses": {"statuses": []},
            "files": [{"filename": "src/main.rs", "status": "modified", "additions": 5, "deletions": 1}],
        }

    def test_failing_and_pending_checks(self):
        s = summarize("getnodalia/agora", 18, self.raw)
        self.assertEqual(s["failed_count"], 1)
        self.assertEqual(s["pending_count"], 1)
        report = markdown(s)
        self.assertIn("CI has failing results", report)
        self.assertIn("src/main.rs", report)
        self.assertNotIn("Claude analysis", report)

    def test_no_checks_are_not_a_pass(self):
        self.raw["checks"]["check_runs"] = []
        s = summarize("getnodalia/agora", 18, self.raw)
        self.assertIn("does **not** mean the PR passed CI", markdown(s))

    def test_token_redaction(self):
        token = "ghp_" + "A" * 35
        self.assertNotIn(token, redact("API key: " + token))
        self.assertIn("[REDACTED]", redact("password=supersecret"))

    def test_repo_validation(self):
        self.assertEqual(validate_repo("getnodalia/agora"), ("getnodalia", "agora"))
        for invalid in ["getnodalia", "../secret", "bad/../repo", "https://evil.test/x", "foo/x?token=y"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_repo(invalid)
