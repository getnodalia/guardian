"""Regression tests for the GitHub REST response shapes used by Guardian.

GitHub's POST /issues/{number}/labels returns an array, whereas issue,
comment, check-run and repository writes return JSON objects. Both must be
validated independently, without logging API credentials or response bodies.
"""

import io
import json
import unittest
from unittest.mock import patch

from nodalia_guardian.git_changes import GitChangesAPI
from nodalia_guardian.github_api import GitHubError
from nodalia_guardian.maintenance_api import MaintenanceAPI


class MaintenanceWriteTests(unittest.TestCase):
    def setUp(self):
        self.api = MaintenanceAPI("unit-test-github-token")

    @staticmethod
    def response(payload):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_add_labels_accepts_github_array_response(self):
        labels = [{"name": "type:feature", "color": "a2eeef"},
                  {"name": "area:backend", "color": "0e8a16"}]
        with patch("nodalia_guardian.maintenance_api.urllib.request.urlopen",
                   return_value=self.response(labels)) as send:
            result = self.api.add_labels(
                "getnodalia/agora", 25, ["type:feature", "area:backend"]
            )
        self.assertEqual(result, labels)
        request = send.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/repos/getnodalia/agora/issues/25/labels"))
        self.assertEqual(
            json.loads(request.data),
            {"labels": ["area:backend", "type:feature"]},
        )

    def test_label_endpoint_rejects_wrong_response_type(self):
        with patch("nodalia_guardian.maintenance_api.urllib.request.urlopen",
                   return_value=self.response({"name": "type:feature"})):
            with self.assertRaisesRegex(GitHubError, "not a label list"):
                self.api.add_labels("getnodalia/agora", 27, ["type:feature"])

    def test_label_endpoint_rejects_malformed_array(self):
        with patch("nodalia_guardian.maintenance_api.urllib.request.urlopen",
                   return_value=self.response([{"name": "type:bug"}, "malformed"])):
            with self.assertRaisesRegex(GitHubError, "not a label list"):
                self.api.add_labels("getnodalia/agora", 27, ["type:bug"])

    def test_other_write_endpoints_still_require_json_object(self):
        with patch("nodalia_guardian.maintenance_api.urllib.request.urlopen",
                   return_value=self.response({"id": 42, "body": "Hello"})):
            self.assertEqual(
                self.api.comment("getnodalia/agora", 27, "Hello")["id"], 42
            )
        with patch("nodalia_guardian.maintenance_api.urllib.request.urlopen",
                   return_value=self.response([{"body": "Hello"}])):
            with self.assertRaisesRegex(GitHubError, "not an object"):
                self.api.comment("getnodalia/agora", 27, "Hello")

    def test_git_changes_adapter_inherits_label_response_contract(self):
        api = GitChangesAPI("unit-test-github-token")
        with patch("nodalia_guardian.maintenance_api.urllib.request.urlopen",
                   return_value=self.response([{"name": "type:feature"}])):
            self.assertEqual(
                api.add_labels("getnodalia/agora", 25, ["type:feature"]),
                [{"name": "type:feature"}],
            )

    def test_git_contents_write_rejects_array_expectation(self):
        api = GitChangesAPI("unit-test-github-token")
        with self.assertRaisesRegex(ValueError, "must return an object"):
            api._write(
                "PUT", "/repos/getnodalia/agora/contents/src/main.py",
                {"branch": "guardian/fix-pr25-aaaaaaaaaa"},
                expect_array=True,
            )


if __name__ == "__main__":
    unittest.main()
