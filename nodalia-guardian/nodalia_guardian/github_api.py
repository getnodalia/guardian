"""Minimal read-only GitHub API client (stdlib only)."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MAX_RESPONSE_BYTES = 2_000_000


class GitHubError(RuntimeError):
    pass


def validate_repo(repo: str) -> tuple[str, str]:
    if not _REPO_RE.fullmatch(repo) or ".." in repo:
        raise ValueError("Repository must look like owner/repo")
    return tuple(repo.split("/", 1))


class GitHubAPI:
    def __init__(self, token: str | None = None, timeout: int = 20):
        self._token = token or None
        self._timeout = timeout

    def _get(self, path: str, query: dict | None = None) -> dict:
        uri = "https://api.github.com" + path
        if query:
            uri += "?" + urllib.parse.urlencode(query)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "nodalia-guardian/0.1",
        }
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(uri, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                data = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"GitHub returned HTTP {exc.code} for {path}") from None
        except urllib.error.URLError:
            raise GitHubError("Could not connect to the GitHub API") from None
        if len(data) > _MAX_RESPONSE_BYTES:
            raise GitHubError("GitHub response too large; refusing unbounded data")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise GitHubError("Invalid JSON from GitHub") from None
        if not isinstance(result, dict):
            raise GitHubError("Unexpected GitHub API response")
        return result

    def _get_list(self, path: str, query: dict | None = None) -> list:
        uri = "https://api.github.com" + path
        if query:
            uri += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "nodalia-guardian/0.1"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(uri, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                data = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"GitHub returned HTTP {exc.code} for {path}") from None
        except urllib.error.URLError:
            raise GitHubError("Could not connect to the GitHub API") from None
        if len(data) > _MAX_RESPONSE_BYTES:
            raise GitHubError("GitHub file list too large; refusing unbounded data")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise GitHubError("Invalid JSON from GitHub") from None
        if not isinstance(result, list):
            raise GitHubError("Unexpected GitHub file-list response")
        return result

    def inspect_pr(self, repo: str, number: int) -> dict:
        owner, name = validate_repo(repo)
        if number < 1:
            raise ValueError("Pull request number must be positive")
        base = f"/repos/{owner}/{name}"
        pr = self._get(base + f"/pulls/{number}")
        sha = pr.get("head", {}).get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise GitHubError("Pull request does not contain a valid HEAD SHA")
        checks = self._get(base + f"/commits/{sha}/check-runs", {"per_page": 100})
        statuses = self._get(base + f"/commits/{sha}/status")
        files = self._get_list(base + f"/pulls/{number}/files", {"per_page": 100})
        return {"pr": pr, "sha": sha, "checks": checks, "statuses": statuses, "files": files}


    def get_pr(self, repo: str, number: int) -> dict:
        owner, name = validate_repo(repo)
        if number < 1:
            raise ValueError("PR number must be positive")
        return self._get(f"/repos/{owner}/{name}/pulls/{number}")

    def create_review(self, repo: str, number: int, *, event: str, body: str, commit_id: str) -> dict:
        """Submit a GitHub App review; intentionally does not support merge or pushes."""
        owner, name = validate_repo(repo)
        if not self._token:
            raise GitHubError("Publishing a review requires an installation token")
        if number < 1 or event not in {"APPROVE", "COMMENT", "REQUEST_CHANGES"}:
            raise ValueError("Invalid review action")
        if not isinstance(body, str) or not 1 <= len(body) <= 25_000:
            raise ValueError("Invalid review message length")
        if not re.fullmatch(r"[a-fA-F0-9]{40}", commit_id):
            raise ValueError("Invalid commit SHA")
        payload = json.dumps({"event": event, "body": body, "commit_id": commit_id}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{name}/pulls/{number}/reviews",
            data=payload, method="POST",
            headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self._token}",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "nodalia-guardian/0.2"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                data = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"GitHub review submission returned HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise GitHubError("Unable to connect to GitHub when submitting review") from None
        if len(data) > _MAX_RESPONSE_BYTES:
            raise GitHubError("Unexpectedly large review response")
        try:
            return json.loads(data)
        except ValueError:
            raise GitHubError("Invalid review response JSON") from None
