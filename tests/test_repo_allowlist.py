"""Offline tests for repository installation allowlists and pricing guards."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodalia_guardian.repo_allowlist import load_repos, installed_repositories
from nodalia_guardian.cli import build_parser
from nodalia_guardian.credential_guard import has_credential_markers

class AllowlistTests(unittest.TestCase):
    def test_reads_only_explicit_nodalia_names(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "repos.json"
            path.write_text(json.dumps({
                "repositories": ["getnodalia/agora", "getnodalia/forja"]
            }))
            self.assertEqual(load_repos(path),
                             {"getnodalia/agora", "getnodalia/forja"})
            path.write_text(json.dumps({
                "repositories": ["getnodalia/agora", "attacker/forja"]
            }))
            with self.assertRaises(ValueError):
                load_repos(path)

    def test_invalid_or_symlinked_allowlist_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target.json"
            target.write_text(json.dumps({"repositories": ["getnodalia/agora"]}))
            alias = Path(temp) / "alias.json"
            alias.symlink_to(target)
            with self.assertRaises(ValueError):
                load_repos(alias)

    def test_sync_paginates_and_excludes_guardian(self):
        pages = [
            {"repositories": [{"full_name": "getnodalia/agora"}] * 99 +
                              [{"full_name": "getnodalia/guardian"}]},
            {"repositories": [{"full_name": "getnodalia/forja"},
                              {"full_name": "someone/else"}]},
        ]
        with (
            patch("nodalia_guardian.repo_allowlist.installation_access_token",
                  return_value="fake-install-token"),
            patch("nodalia_guardian.repo_allowlist.GitHubAPI") as cls,
        ):
            cls.return_value._get.side_effect = pages
            names = installed_repositories(5794704, "fake-file", 182801897)
        self.assertEqual(names, ["getnodalia/agora", "getnodalia/forja"])

    def test_serve_accepts_safe_repo_file(self):
        args = build_parser().parse_args([
            "serve", "--allowed-repo-file", "/etc/guardian/repos.json"
        ])
        self.assertEqual(args.allowed_repo, [])
        self.assertEqual(str(args.allowed_repo_file), "/etc/guardian/repos.json")


class SecretGuardTests(unittest.TestCase):
    def test_blocks_private_keys_and_api_tokens(self):
        self.assertTrue(has_credential_markers(
            "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"))
        self.assertTrue(has_credential_markers(
            "ANTHROPIC_API_KEY=sk-ant-something"))
        self.assertFalse(has_credential_markers(
            "def verify_access_token(token):\n    return token is not None\n"))

if __name__ == "__main__":
    unittest.main()
