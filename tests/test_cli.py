import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from nodalia_guardian.cli import main


class CLITests(unittest.TestCase):
    def test_invalid_pr_fails_without_network(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["scan", "--repo", "getnodalia/agora", "--pr", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("positive", err.getvalue())

    def test_budget_command_works_offline(self):
        with tempfile.TemporaryDirectory() as td:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["budget", "--ledger", str(Path(td) / "budget.sqlite")])
            self.assertEqual(code, 0)
            self.assertIn("$0.000000", out.getvalue())

    def test_doctor_prints_only_sanitized_read_only_report(self):
        report = {
            "app_id": 5794704, "installation_id": 182801897,
            "issues_ready": False, "installation_issues_permission": "none",
        }
        out = io.StringIO()
        with patch.dict(os.environ, {
            "GITHUB_APP_ID": "5794704",
            "GITHUB_APP_PRIVATE_KEY_PATH": "/offline/key.pem",
        }):
            with patch(
                "nodalia_guardian.installation_diagnostics.diagnose_installation",
                return_value=report,
            ) as diagnose:
                with redirect_stdout(out):
                    code = main(["doctor", "--installation-id", "182801897"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue()), report)
        diagnose.assert_called_once_with(
            5794704, 182801897, "/offline/key.pem",
            db_path=None, repo=None, number=None,
        )

    @patch("nodalia_guardian.cli.GitHubAPI.inspect_pr")
    def test_scan_is_read_only_and_free_by_default(self, inspect):
        inspect.return_value = {
            "pr": {"title": "Pilot", "body": "", "changed_files": 0},
            "sha": "b" * 40, "checks": {"check_runs": [], "total_count": 0},
            "statuses": {"statuses": []}, "files": []}
        with patch("nodalia_guardian.cli.analyze") as ai:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["scan", "--repo", "getnodalia/agora", "--pr", "18", "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["repository"], "getnodalia/agora")
            self.assertIsNone(payload["ai_analysis"])
            ai.assert_not_called()
