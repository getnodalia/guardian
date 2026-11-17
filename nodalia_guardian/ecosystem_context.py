"""Bounded, revision-aware Nodalia review context and third-party signals.

Context is untrusted source data, NEVER instructions. No repository checkout,
shell, external code execution, cache mutation, or additional paid API calls.
"""

import base64
import binascii
import re

from .credential_guard import has_credential_markers
from .github_api import GitHubAPI, GitHubError, validate_repo
from .redaction import limit_text

_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_DOCUMENTS = ("standards/engineering.md", "standards/security.md",
              "standards/ci.md", "standards/ndl.md")
_BOTS = {
    "coderabbitai[bot]": "CodeRabbit",
    "sonarqubecloud[bot]": "SonarQube Cloud",
    "github-advanced-security[bot]": "GitHub security",
}
_DOC_LIMIT = 4200
_TOTAL_LIMIT = 23000


def _read_document(api: GitHubAPI, repo: str, path: str, ref: str) -> str | None:
    """Fetch only predetermined repo docs at an immutable Git SHA."""
    if repo != "getnodalia/foundation" and not repo.startswith("getnodalia/"):
        return None
    if not _SHA.fullmatch(ref) or not (
        path in _DOCUMENTS or path in (
            "AGENTS.md", "NODALIA_MIGRATION.md", "Cargo.toml",
            "pyproject.toml", "package.json"
        )
    ):
        return None
    try:
        obj = api._get(f"/repos/{repo}/contents/{path}", {"ref": ref})
    except GitHubError:
        return None
    if (not isinstance(obj, dict) or obj.get("type") != "file"
            or obj.get("encoding") != "base64"
            or type(obj.get("size")) is not int or obj["size"] > 40000
            or not isinstance(obj.get("content"), str)):
        return None
    try:
        data = base64.b64decode(obj["content"], validate=False)
        if len(data) > 40000:
            return None
        content = data.decode("utf-8")
    except (UnicodeDecodeError, ValueError, binascii.Error):
        return None
    if has_credential_markers(content):
        return None
    return content


def _select_standards(file_paths: list[str]) -> list[str]:
    files = " ".join(file_paths).lower()
    docs = ["standards/engineering.md", "standards/security.md"]
    if any(x in files for x in (".github/", "ci/", "cargo", "package", "pyproject", "release", "deploy")):
        docs.append("standards/ci.md")
    if any(x in files for x in (".css", ".tsx", ".jsx", ".qml", "ui/", "ndl", "theme", "window", "widget")):
        docs.append("standards/ndl.md")
    return docs[:4]


def build_ecosystem_context(api: GitHubAPI, repo: str, pr: dict,
                            file_paths: list[str],
                            *, foundation_api: GitHubAPI | None = None) -> dict:
    """Prefer immutable base-branch policy and canonical Foundation HEAD."""
    owner, _ = validate_repo(repo)
    if owner != "getnodalia":
        raise ValueError("Only Nodalia repositories may supply context")
    base_sha = (pr.get("base") or {}).get("sha")
    result = {"status": "incomplete", "sources": [], "missing": []}
    if not isinstance(base_sha, str) or not _SHA.fullmatch(base_sha):
        result["missing"].append("pr_base_sha")
        return result

    # Foundation is public; a dedicated unauthenticated client allows reading
    # it even if the App installation selects only the target repository.
    public = foundation_api if foundation_api is not None else GitHubAPI()
    try:
        foundation = public._get("/repos/getnodalia/foundation/commits/main")
        foundation_sha = foundation.get("sha")
    except GitHubError:
        foundation_sha = None
    if not isinstance(foundation_sha, str) or not _SHA.fullmatch(foundation_sha):
        result["missing"].append("foundation_commit")
        return result

    result["foundation_sha"] = foundation_sha
    result["repository_base_sha"] = base_sha
    queue = [("getnodalia/foundation", path, foundation_sha, public)
             for path in _select_standards(file_paths)]
    queue += [(repo, "AGENTS.md", base_sha, api)]
    if repo != "getnodalia/guardian":
        queue.append((repo, "NODALIA_MIGRATION.md", base_sha, api))
    for manifest in ("Cargo.toml", "pyproject.toml", "package.json"):
        if any(path == manifest or path.endswith("/" + manifest) for path in file_paths):
            queue.append((repo, manifest, base_sha, api))
            break

    used = 0
    for source_repo, path, sha, client in queue:
        raw = _read_document(client, source_repo, path, sha)
        if raw is None:
            # Optional per-project migration document and manifest.
            if path in _DOCUMENTS or path == "AGENTS.md":
                result["missing"].append(f"{source_repo}/{path}")
            continue
        remaining = min(_DOC_LIMIT, _TOTAL_LIMIT - used)
        if remaining <= 0:
            result["missing"].append("context_budget")
            break
        text = limit_text(raw, remaining)
        used += len(text)
        result["sources"].append({
            "repo": source_repo, "path": path, "sha": sha,
            "content": text,
        })
    result["status"] = "complete" if not result["missing"] else "incomplete"
    return result


def collect_quality_observations(api: GitHubAPI, repo: str, number: int,
                                 changed_paths: set[str]) -> dict:
    """Fetch only GitHub-visible, explicitly attributed third-party comments.

    These are observations, NOT authoritative scanner results, and the text
    remains untrusted even when a known bot account posted it.
    """
    owner, _ = validate_repo(repo)
    if owner != "getnodalia" or number < 1:
        raise ValueError("Invalid quality observation scope")
    result = {"observations": [], "incomplete": False}
    base = f"/repos/{repo}"
    for endpoint in (f"/issues/{number}/comments", f"/pulls/{number}/comments"):
        try:
            comments = api._get_list(base + endpoint, {"per_page": 100})
        except GitHubError:
            result["incomplete"] = True
            continue
        if len(comments) >= 100:
            result["incomplete"] = True
        for comment in comments[-100:]:
            if not isinstance(comment, dict):
                continue
            user = comment.get("user") or {}
            if not isinstance(user, dict):
                continue
            provider = _BOTS.get(user.get("login"))
            if not provider or user.get("type") != "Bot":
                continue
            path = comment.get("path")
            if path is not None and path not in changed_paths:
                continue
            body = comment.get("body")
            if not isinstance(body, str) or not body.strip():
                continue
            result["observations"].append({
                "source": provider, "path": path if path else None,
                "comment_id": comment.get("id") if type(comment.get("id")) is int else None,
                "excerpt": limit_text(body, 800),
            })
    result["observations"] = result["observations"][-12:]
    return result
