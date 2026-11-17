"""GitHub Git/Contents API adapter for bot-owned, reviewable correction PRs.

Never writes directly to a contributor branch, main or a protected release.
No local repository execution, shell or filesystem access is required.
"""

import base64
import re
from urllib.parse import quote

from .github_api import GitHubError
from .maintenance_api import MaintenanceAPI

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_LANGUAGE_SUFFIX = (".rs", ".py", ".ts", ".tsx", ".js", ".jsx", ".swift", ".go",
                    ".c", ".h", ".cpp", ".hpp", ".java", ".kt", ".css", ".html", ".sql")
_PROTECTED_PATH = re.compile(
    r"(?:^|/)(?:\.github|security|secrets?|credentials?|keys?|\.env|Cargo\.lock|"
    r"package-lock\.json|pnpm-lock\.yaml)(?:[._/-]|$)", re.I
)


def safe_source_path(path: object) -> bool:
    return (isinstance(path, str)
            and 1 <= len(path) <= 250
            and _SAFE_PATH.fullmatch(path) is not None
            and path.endswith(_LANGUAGE_SUFFIX)
            and not _PROTECTED_PATH.search(path)
            and not any(part.startswith(".") for part in path.split("/")))


def safe_bot_branch(name: str) -> bool:
    return bool(re.fullmatch(r"guardian/(?:fix|resolve)-pr[1-9][0-9]*-[0-9a-f]{10}", name))


class GitChangesAPI(MaintenanceAPI):
    def _write(self, method: str, path: str, payload: dict, *, expect_array: bool = False) -> dict | list[dict]:
        if method == "PUT":
            if expect_array:
                raise ValueError("Contents API must return an object")
            # Only the GitHub contents API may use PUT from this adapter.
            if "/contents/" not in path or not safe_bot_branch(payload.get("branch", "")):
                raise ValueError("Refusing PUT outside a bot-owned fix branch")
            # MaintenanceAPI validates credentials and accepts POST/PATCH;
            # this narrow PUT transport keeps its other guards.
            import json
            import urllib.request
            import urllib.error
            if not self._token:
                raise GitHubError("Missing GitHub App token")
            request = urllib.request.Request(
                "https://api.github.com" + path, method="PUT",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                    "User-Agent": "nodalia-guardian/0.4",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    data = response.read(100_001)
            except urllib.error.HTTPError as exc:
                raise GitHubError(f"GitHub bot-branch PUT returned HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError):
                raise GitHubError("GitHub bot-branch PUT connection failed") from None
            import json as _json
            try:
                obj = _json.loads(data)
            except ValueError:
                raise GitHubError("Invalid bot-branch response") from None
            if not isinstance(obj, dict):
                raise GitHubError("Invalid bot-branch response type")
            return obj
        return super()._write(method, path, payload, expect_array=expect_array)

    def read_text(self, repo: str, path: str, ref: str) -> tuple[str, str]:
        if not safe_source_path(path) or not _SHA.fullmatch(ref):
            raise ValueError("Unsafe path or content ref")
        result = self._get(self._path(repo) + "/contents/" +
                           quote(path, safe="/"), {"ref": ref})
        if (result.get("type") != "file" or result.get("encoding") != "base64"
                or not isinstance(result.get("content"), str)
                or not isinstance(result.get("sha"), str)
                or result.get("size", 100000) > 30_000):
            raise ValueError("Unsupported or oversized source file")
        try:
            data = base64.b64decode(result["content"], validate=False)
            text = data.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Source is not UTF-8 text") from None
        if len(data) > 30_000 or "\x00" in text:
            raise ValueError("Unsupported source content")
        return text, result["sha"]

    def create_bot_branch(self, repo: str, branch: str, from_sha: str) -> dict:
        if not safe_bot_branch(branch) or not _SHA.fullmatch(from_sha):
            raise ValueError("Unsafe bot branch or SHA")
        return self._write("POST", self._path(repo) + "/git/refs",
                           {"ref": "refs/heads/" + branch, "sha": from_sha})

    def write_bot_file(self, repo: str, branch: str, path: str,
                       old_blob_sha: str, text: str) -> dict:
        if (not safe_bot_branch(branch) or not safe_source_path(path)
                or not _SHA.fullmatch(old_blob_sha)
                or not isinstance(text, str) or len(text.encode("utf-8")) > 30_000):
            raise ValueError("Unsafe correction file update")
        return self._write(
            "PUT", self._path(repo) + "/contents/" + quote(path, safe="/"),
            {"message": "fix(guardian): proposed correction for review",
             "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
             "sha": old_blob_sha, "branch": branch},
        )

    def propose_bot_pr(self, repo: str, branch: str, base_branch: str,
                       title: str, body: str) -> dict:
        if not safe_bot_branch(branch) or not isinstance(base_branch, str):
            raise ValueError("Unsafe correction branch")
        if (base_branch in {"main", "master"} or base_branch.startswith("release/")
                or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", base_branch)
                or base_branch.startswith("guardian/")):
            raise ValueError("Cannot target protected or invalid base branch")
        return self._write("POST", self._path(repo) + "/pulls",
                           {"title": title[:150], "body": body[:3000],
                            "head": branch, "base": base_branch,
                            "draft": True, "maintainer_can_modify": False})
