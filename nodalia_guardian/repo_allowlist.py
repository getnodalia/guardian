"""Explicit repository allowlist for safely expanding Guardian beyond Agora."""

import json
import os
import tempfile
from pathlib import Path

from .app_auth import installation_access_token
from .github_api import GitHubAPI

DEFAULT_REPO_FILE = Path("/etc/guardian/repos.json")


def load_repos(path: Path) -> set[str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Repository allowlist file missing or unsafe")
    if path.stat().st_size > 100_000:
        raise ValueError("Repository allowlist file oversized")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Invalid repository allowlist JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("repositories"), list):
        raise ValueError("Repository allowlist requires a repositories array")
    repos = data["repositories"]
    if (not repos or len(repos) > 500
            or any(not isinstance(x, str) or not x.startswith("getnodalia/")
                   or x.count("/") != 1 or len(x) > 150 for x in repos)):
        raise ValueError("Invalid getnodalia repository allowlist")
    from .github_api import validate_repo
    for name in repos:
        validate_repo(name)
    return set(repos)


def installed_repositories(app_id: int, private_key_path: str,
                           installation_id: int) -> list[str]:
    """Read ALL pages of the installation's repositories before writing."""
    token = installation_access_token(app_id, private_key_path, installation_id)
    api = GitHubAPI(token)
    names = set()
    for page in range(1, 11):
        result = api._get("/installation/repositories", {"per_page": 100, "page": page})
        repos = result.get("repositories")
        if not isinstance(repos, list):
            raise ValueError("Invalid installed repositories response")
        for item in repos:
            if isinstance(item, dict) and isinstance(item.get("full_name"), str):
                full_name = item["full_name"]
                if full_name.startswith("getnodalia/"):
                    names.add(full_name)
        if len(repos) < 100:
            break
    else:
        raise ValueError("Installation has over 1000 repos; refuse incomplete list")
    # Prevent bot-triggered review loops on Guardian's own correction PRs.
    names.discard("getnodalia/guardian")
    if not names:
        raise ValueError("Installation contains no eligible Nodalia repositories")
    return sorted(names)


def write_repos(repos: list[str], path: Path = DEFAULT_REPO_FILE) -> None:
    if path != DEFAULT_REPO_FILE:
        raise ValueError("Repository allowlist may only be written to /etc/guardian/repos.json")
    # Do not create or modify any system configuration except one allowlist.
    if os.geteuid() != 0:
        raise PermissionError("Run repository allowlist synchronization as root inside LXC 111")
    import grp
    try:
        group_id = grp.getgrnam("guardian").gr_gid
    except KeyError:
        raise RuntimeError("guardian service group missing") from None
    serialized = json.dumps({"repositories": sorted(set(repos))}, indent=2) + "\n"
    folder = path.parent
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=folder, prefix=".repos-", delete=False) as out:
            temp_name = out.name
            out.write(serialized)
        os.chmod(temp_name, 0o640)
        os.chown(temp_name, 0, group_id)
        # No half-written file even when the service is reading concurrently.
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
