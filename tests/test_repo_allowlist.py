"""Offline tests for repository installation allowlists and pricing guards."""

import json
import stat
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from nodalia_guardian.repo_allowlist import load_repos, installed_repositories
from nodalia_guardian.cli import build_parser
from nodalia_guardian.credential_guard import has_credential_markers

class AllowlistTests(unittest.TestCase):
    @staticmethod
    def read_fixture(path: Path, *, owner: int = 0, mode: int = 0o640,
                     group: int = 456) -> set[str]:
        with (
            patch("nodalia_guardian.repo_allowlist.DEFAULT_REPO_FILE", path),
            patch("nodalia_guardian.repo_allowlist.grp.getgrnam",
                  return_value=SimpleNamespace(gr_gid=group)),
            patch("nodalia_guardian.repo_allowlist.os.fstat",
                  return_value=SimpleNamespace(
                      st_mode=stat.S_IFREG | mode,
                      st_uid=owner, st_gid=group,
                      st_size=path.stat().st_size,
                  )),
        ):
            return load_repos()

    def test_reads_only_explicit_nodalia_names(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "repos.json"
            path.write_text(json.dumps({
                "repositories": ["getnodalia/agora", "getnodalia/forja"]
            }))
            self.assertEqual(self.read_fixture(path),
                             {"getnodalia/agora", "getnodalia/forja"})
            path.write_text(json.dumps({
                "repositories": ["getnodalia/agora", "attacker/forja"]
            }))
            with self.assertRaises(ValueError):
                self.read_fixture(path)

    def test_rejects_path_arguments_and_path_traversal(self):
        with self.assertRaises(TypeError):
            load_repos(Path("/etc/guardian/../../etc/passwd"))
        parser = build_parser()
        with redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as exc:
                parser.parse_args([
                    "serve", "--allowed-repo-file", "../../etc/passwd",
                ])
        self.assertEqual(exc.exception.code, 2)
        flags = parser.parse_args(["serve", "--allowed-repo-file"])
        self.assertIs(flags.allowed_repo_file, True)

    def test_rejects_wrong_owner_or_permissive_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "repos.json"
            path.write_text(json.dumps({"repositories": ["getnodalia/agora"]}))
            for kwargs in ({"owner": 1000}, {"mode": 0o666},
                           {"mode": 0o600}, {"group": 999}):
                with self.subTest(kwargs=kwargs):
                    if "group" in kwargs:
                        with (patch("nodalia_guardian.repo_allowlist.DEFAULT_REPO_FILE", path),
                              patch("nodalia_guardian.repo_allowlist.grp.getgrnam",
                                    return_value=SimpleNamespace(gr_gid=456)),
                              patch("nodalia_guardian.repo_allowlist.os.fstat",
                                    return_value=SimpleNamespace(
                                        st_mode=stat.S_IFREG | 0o640,
                                        st_uid=0, st_gid=999,
                                        st_size=path.stat().st_size))):
                            with self.assertRaises(ValueError):
                                load_repos()
                    else:
                        with self.assertRaises(ValueError):
                            self.read_fixture(path, **kwargs)

    def test_invalid_or_symlinked_allowlist_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "target.json"
            target.write_text(json.dumps({"repositories": ["getnodalia/agora"]}))
            alias = Path(temp) / "alias.json"
            alias.symlink_to(target)
            # O_NOFOLLOW refuses an attacker-created link, even if its
            # target has a syntactically valid repository list.
            with patch("nodalia_guardian.repo_allowlist.DEFAULT_REPO_FILE", alias):
                with self.assertRaises(ValueError):
                    load_repos()

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
        args = build_parser().parse_args(["serve", "--allowed-repo-file"])
        self.assertEqual(args.allowed_repo, [])
        self.assertTrue(args.allowed_repo_file)


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
