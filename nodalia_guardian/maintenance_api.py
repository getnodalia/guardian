"""Minimal GitHub issue/PR maintenance client with least-privilege writes."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .github_api import GitHubAPI, GitHubError, validate_repo

_LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_. -]{0,49}$")


class MaintenanceAPI(GitHubAPI):
    def _path(self, repo: str) -> str:
        owner, name = validate_repo(repo)
        if owner != "getnodalia":
            raise ValueError("Maintenance is limited to getnodalia")
        return f"/repos/{owner}/{name}"

    def _write(self, method: str, path: str, payload: dict, *, expect_array: bool = False) -> dict | list[dict]:
        if method not in {"POST", "PATCH"} or not self._token:
            raise GitHubError("write access requires a GitHub App installation token")
        url = "https://api.github.com" + path
        request = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "nodalia-guardian/0.3",
            }
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read(100_001)
        except urllib.error.HTTPError as exc:
            raise GitHubError(f"GitHub maintenance write returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise GitHubError("GitHub maintenance write failed to connect") from None
        if len(raw) > 100_000:
            raise GitHubError("GitHub maintenance response too large")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise GitHubError("GitHub maintenance response was not JSON") from None
        if expect_array:
            if (not isinstance(data, list) or len(data) > 500
                    or any(not isinstance(item, dict)
                           or not isinstance(item.get("name"), str)
                           for item in data)):
                raise GitHubError("GitHub maintenance response was not a label list")
        elif not isinstance(data, dict):
            raise GitHubError("GitHub maintenance response was not an object")
        return data

    def issue(self, repo: str, number: int) -> dict:
        if number < 1:
            raise ValueError("invalid issue or PR number")
        return self._get(self._path(repo) + f"/issues/{number}")

    def issue_comment(self, repo: str, comment_id: int) -> dict:
        if comment_id < 1:
            raise ValueError("invalid comment id")
        return self._get(self._path(repo) + f"/issues/comments/{comment_id}")

    def permission(self, repo: str, login: str) -> str:
        if not _LOGIN.fullmatch(login):
            return "none"
        result = self._get(
            self._path(repo) + "/collaborators/" + urllib.parse.quote(login, safe="") + "/permission"
        )
        return str(result.get("permission") or "none").lower()

    def existing_labels(self, repo: str) -> set[str]:
        values = self._get_list(self._path(repo) + "/labels", {"per_page": 100})
        return {x["name"] for x in values if isinstance(x, dict) and isinstance(x.get("name"), str)}

    def add_labels(self, repo: str, number: int, labels: list[str]) -> list[dict]:
        labels = sorted(set(labels))
        if not labels or len(labels) > 8 or any(not _LABEL.fullmatch(x) for x in labels):
            raise ValueError("labels must be a short predefined list")
        return self._write("POST", self._path(repo) + f"/issues/{number}/labels",
                           {"labels": labels}, expect_array=True)

    def create_label(self, repo: str, name: str, color: str, description: str) -> dict:
        if not _LABEL.fullmatch(name) or not re.fullmatch(r"[0-9a-fA-F]{6}", color):
            raise ValueError("invalid label")
        return self._write("POST", self._path(repo) + "/labels",
                           {"name": name, "color": color, "description": description[:100]})

    def comment(self, repo: str, number: int, body: str) -> dict:
        if not isinstance(body, str) or not 1 <= len(body) <= 3000:
            raise ValueError("invalid comment")
        return self._write("POST", self._path(repo) + f"/issues/{number}/comments",
                           {"body": body})

    def close_issue(self, repo: str, number: int) -> dict:
        return self._write("PATCH", self._path(repo) + f"/issues/{number}",
                           {"state": "closed", "state_reason": "not_planned"})

    def close_pr(self, repo: str, number: int) -> dict:
        return self._write("PATCH", self._path(repo) + f"/pulls/{number}",
                           {"state": "closed"})

    def open_prs(self, repo: str) -> list[dict]:
        return self._get_list(self._path(repo) + "/pulls",
                              {"state": "open", "per_page": 100})

    def similar_issues(self, repo: str, title: str) -> list[dict]:
        # Search is a candidate source only. Caller must independently verify
        # exact normalized title AND exact normalized body before auto-closure.
        terms = [x for x in re.findall(r"[\w-]+", title) if len(x) > 3][:6]
        if not terms:
            return []
        query = f"repo:{repo} is:issue " + " ".join(terms)
        result = self._get("/search/issues", {"q": query, "per_page": 30})
        return result.get("items", [])[:30]

    def create_check(self, repo: str, sha: str, *, title: str,
                     summary: str, actions: list[dict] | None = None) -> dict:
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError("invalid SHA")
        actions = actions or []
        if len(actions) > 3 or any(
            not isinstance(a, dict) or
            not all(isinstance(a.get(k), str) and a[k] for k in
                    ("label", "description", "identifier")) or
            a["identifier"] not in {"guardian_fix", "guardian_resolve_conflicts"}
            for a in actions
        ):
            raise ValueError("invalid check-run requested actions")
        return self._write("POST", self._path(repo) + "/check-runs", {
            "name": "Nodalia Guardian · Maintain",
            "head_sha": sha,
            "status": "completed",
            "conclusion": "neutral",
            "output": {"title": title[:100], "summary": summary[:3000]},
            "actions": actions,
        })
