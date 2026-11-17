"""Read-only, non-billable GitHub App installation diagnostics.

Never print JWTs, installation access tokens, private keys, webhook payloads,
or unrestricted GitHub API responses. Never create/modify a SQLite database.
"""

import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from .app_auth import app_jwt
from .github_api import GitHubAPI, GitHubError, validate_repo


def _queue_snapshot(db_path: Path, repo: str, number: int) -> dict:
    db_path = Path(db_path)
    if not db_path.is_file():
        return {"available": False, "reason": "database_not_found"}
    uri = "file:" + quote(str(db_path.absolute()), safe="/") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=2) as db:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='maintenance_events'"
            ).fetchone()
            if not table:
                return {"available": False, "reason": "maintenance_table_missing"}
            rows = db.execute(
                """SELECT kind,status,result,created_at
                   FROM maintenance_events
                   WHERE repo=? AND number=?
                   ORDER BY created_at DESC, delivery_id DESC LIMIT 10""",
                (repo, number),
            ).fetchall()
    except sqlite3.Error:
        return {"available": False, "reason": "database_read_failed"}
    return {
        "available": True,
        "matching_events": [
            {"kind": kind, "status": status, "result": result[:150],
             "created_at": created_at}
            for kind, status, result, created_at in rows
        ],
    }


def diagnose_installation(
    app_id: int, installation_id: int, private_key_path: str | Path,
    *, db_path: Path | None = None, repo: str | None = None,
    number: int | None = None,
) -> dict:
    """Return only a narrow, safe projection of app and installation state."""
    if app_id <= 0 or installation_id <= 0:
        raise ValueError("App ID and installation ID must be positive")
    if (repo is None) != (number is None):
        raise ValueError("--repo and --issue must be supplied together")
    if repo is not None:
        validate_repo(repo)
        if not repo.startswith("getnodalia/") or number is None or number <= 0:
            raise ValueError("A positive issue number in getnodalia is required")

    jwt = app_jwt(app_id, Path(private_key_path).read_bytes())
    api = GitHubAPI(jwt)
    app = api._get("/app")
    installation = api._get(f"/app/installations/{installation_id}")
    if app.get("id") != app_id or installation.get("id") != installation_id:
        raise GitHubError("GitHub returned an unexpected app or installation identity")
    if installation.get("app_id") not in (None, app_id):
        raise GitHubError("Installation belongs to a different GitHub App")

    requested = app.get("permissions") or {}
    granted = installation.get("permissions") or {}
    events = app.get("events") or []
    if not isinstance(requested, dict) or not isinstance(granted, dict) or not isinstance(events, list):
        raise GitHubError("Unexpected GitHub App permissions response")

    result = {
        "app_id": app_id,
        "installation_id": installation_id,
        "suspended": installation.get("suspended_at") is not None,
        "repository_selection": installation.get("repository_selection"),
        "app_issues_permission": requested.get("issues", "none"),
        "installation_issues_permission": granted.get("issues", "none"),
        "app_pull_requests_permission": requested.get("pull_requests", "none"),
        "installation_pull_requests_permission": granted.get("pull_requests", "none"),
        "subscribed_to_issues": "issues" in events,
    }
    result["issues_permission_gap"] = (
        result["app_issues_permission"] == "write"
        and result["installation_issues_permission"] != "write"
    )
    result["issues_ready"] = (
        not result["suspended"]
        and result["installation_issues_permission"] == "write"
        and result["subscribed_to_issues"]
    )
    # A short delivery window is useful for diagnostics, but zero events here
    # does NOT prove a delivery never occurred.
    try:
        deliveries = api._get_list("/app/hook/deliveries", {"per_page": 100})
    except GitHubError:
        result["delivery_sample"] = {"available": False}
    else:
        counts = Counter(
            d.get("event") for d in deliveries if isinstance(d, dict)
        )
        result["delivery_sample"] = {
            "available": True,
            "window_size": len(deliveries),
            "issues": counts.get("issues", 0),
            "pull_request": counts.get("pull_request", 0),
        }
    if db_path is not None and repo is not None and number is not None:
        result["queue"] = _queue_snapshot(db_path, repo, number)
    return result
