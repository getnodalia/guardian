"""Offline checks for non-billable installation permission diagnostics."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodalia_guardian.github_api import GitHubError
from nodalia_guardian.installation_diagnostics import diagnose_installation

APP = 5794704
INSTALLATION = 182801897


class InstallationDiagnosticsTests(unittest.TestCase):
    def run_diagnosis(self, *, installed_issues=None, events=None, db_path=None):
        with tempfile.TemporaryDirectory() as td:
            key = Path(td) / "app.pem"
            key.write_bytes(b"fake-key-for-offline-mock")
            app = {
                "id": APP,
                "permissions": {"issues": "write", "pull_requests": "write"},
                "events": ["pull_request", "issues"] if events is None else events,
            }
            installation = {
                "id": INSTALLATION, "app_id": APP,
                "permissions": {"pull_requests": "write", **(
                    {"issues": installed_issues} if installed_issues else {}
                )},
                "suspended_at": None, "repository_selection": "selected",
            }
            with patch(
                "nodalia_guardian.installation_diagnostics.app_jwt",
                return_value="not-a-real-jwt",
            ) as jwt:
                with patch(
                    "nodalia_guardian.installation_diagnostics.GitHubAPI"
                ) as api_type:
                    api = api_type.return_value
                    api._get.side_effect = [app, installation]
                    api._get_list.return_value = [
                        {"event": "pull_request"}, {"event": "check_run"}
                    ]
                    result = diagnose_installation(
                        APP, INSTALLATION, key, db_path=db_path,
                        repo="getnodalia/agora" if db_path else None,
                        number=29 if db_path else None,
                    )
                    jwt.assert_called_once_with(APP, b"fake-key-for-offline-mock")
                    api_type.assert_called_once_with("not-a-real-jwt")
                    self.assertEqual(api._get.call_count, 2)
                    return result

    def test_gap_is_reported_not_mistaken_for_approval(self):
        report = self.run_diagnosis()
        self.assertTrue(report["issues_permission_gap"])
        self.assertFalse(report["issues_ready"])
        self.assertEqual(report["installation_issues_permission"], "none")
        self.assertTrue(report["subscribed_to_issues"])
        self.assertEqual(report["delivery_sample"]["issues"], 0)
        self.assertNotIn("not-a-real-jwt", json.dumps(report))

    def test_granted_permission_is_ready_even_without_recent_delivery(self):
        report = self.run_diagnosis(installed_issues="write")
        self.assertTrue(report["issues_ready"])
        self.assertFalse(report["issues_permission_gap"])
        # An empty recent sample never proves that the webhook failed.
        self.assertEqual(report["delivery_sample"]["issues"], 0)

    def test_unsubscribed_app_cannot_receive_issues(self):
        report = self.run_diagnosis(
            installed_issues="write", events=["pull_request"]
        )
        self.assertFalse(report["subscribed_to_issues"])
        self.assertFalse(report["issues_ready"])

    def test_does_not_create_a_missing_database(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "does-not-exist.sqlite3"
            report = self.run_diagnosis(db_path=db)
            self.assertFalse(db.exists())
            self.assertEqual(report["queue"]["reason"], "database_not_found")

    def test_reads_existing_maintenance_queue_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "deliveries.sqlite3"
            with sqlite3.connect(db) as connection:
                connection.execute(
                    """CREATE TABLE maintenance_events (
                         delivery_id TEXT PRIMARY KEY, repo TEXT, number INTEGER,
                         kind TEXT, status TEXT, result TEXT, created_at TEXT)"""
                )
                connection.execute(
                    """INSERT INTO maintenance_events VALUES
                       (?,?,?,?,?,?,?)""",
                    ("delivery-1", "getnodalia/agora", 29, "issue",
                     "done", "Labels: type:bug", "2026-11-17 15:00:00"),
                )
            before = db.stat().st_size
            report = self.run_diagnosis(db_path=db)
            self.assertEqual(db.stat().st_size, before)
            self.assertEqual(
                report["queue"]["matching_events"][0]["status"], "done"
            )

    def test_invalid_scope_fails_before_private_key_read_or_network(self):
        with patch("nodalia_guardian.installation_diagnostics.app_jwt") as jwt:
            with self.assertRaises(ValueError):
                diagnose_installation(
                    APP, INSTALLATION, "/unused",
                    repo="anotherorg/repo", number=29,
                )
            jwt.assert_not_called()

    def test_mismatched_app_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            key = Path(td) / "key.pem"
            key.write_bytes(b"test")
            with patch(
                "nodalia_guardian.installation_diagnostics.app_jwt",
                return_value="test-jwt",
            ):
                with patch(
                    "nodalia_guardian.installation_diagnostics.GitHubAPI"
                ) as api_type:
                    api_type.return_value._get.side_effect = [
                        {"id": APP + 1}, {"id": INSTALLATION, "app_id": APP}
                    ]
                    with self.assertRaises(GitHubError):
                        diagnose_installation(APP, INSTALLATION, key)


if __name__ == "__main__":
    unittest.main()
