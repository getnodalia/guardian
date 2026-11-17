"""Signed-webhook intake and durable queue for issue/PR maintenance.

Only a small, bounded projection of a signed event is persisted. Event payload
content is untrusted; a worker must re-fetch current state from GitHub before
changing labels, closing items or acting on a command.
"""

import json
import re

_MAX_BODY = 3000
_SAFE_COMMAND = re.compile(r"^/guardian(?:\s+([a-z][a-z-]*))?(?:\s+(.{1,120}))?$", re.I)


def parse_command(body: object) -> tuple[str, str] | None:
    if not isinstance(body, str) or len(body) > 300:
        return None
    match = _SAFE_COMMAND.fullmatch(body.strip())
    if not match:
        return None
    return (match.group(1) or "help").lower(), (match.group(2) or "").strip()


def parse_maintenance(body: bytes, event: str, allowed_repos: set[str]) -> dict | None:
    """Extract only events we intentionally support.

    Never trust 'sender' as authorization: the maintenance worker re-reads the
    source comment and checks repo permissions and allowlisted user IDs.
    """
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    repository = data.get("repository")
    repo = repository.get("full_name") if isinstance(repository, dict) else None
    if not isinstance(repo, str) or repo not in allowed_repos or not repo.startswith("getnodalia/"):
        return None
    installation = data.get("installation") or {}
    if not isinstance(installation, dict):
        return None
    installation_id = installation.get("id")
    if type(installation_id) is not int or installation_id < 1:
        return None
    sender = data.get("sender") or {}
    if not isinstance(sender, dict) or sender.get("type") == "Bot":
        return None
    user_id, login = sender.get("id"), sender.get("login")
    if type(user_id) is not int or not isinstance(login, str) or not login:
        return None
    action = data.get("action")
    obj = None
    kind = None
    comment_id = None
    command = None
    if event == "issues" and action in {"opened", "reopened", "edited"}:
        obj, kind = data.get("issue"), "issue"
    elif event == "pull_request" and action in {
        "opened", "reopened", "edited", "ready_for_review"
    }:
        obj, kind = data.get("pull_request"), "pull_request"
    elif event == "issue_comment" and action == "created":
        obj = data.get("issue")
        comment = data.get("comment")
        if not isinstance(comment, dict):
            return None
        command = parse_command(comment.get("body"))
        if command is None:
            return None
        comment_id = comment.get("id")
        kind = "command"
    elif event == "check_run" and action == "requested_action":
        # Check-run buttons are supported by GitHub, but require separate
        # Checks: write permission and a caller-identity permission gate.
        # Do not allow other apps to inject actions into this queue.
        check_run = data.get("check_run") or {}
        requested = data.get("requested_action") or {}
        app = check_run.get("app") or {} if isinstance(check_run, dict) else {}
        if (not isinstance(app, dict) or not isinstance(requested, dict)
                or type(app.get("id")) is not int):
            return None
        identifier = requested.get("identifier")
        if identifier not in {"guardian_fix", "guardian_resolve_conflicts"}:
            return None
        prs = check_run.get("pull_requests") or []
        if not isinstance(prs, list) or len(prs) != 1:
            return None
        obj, kind = prs[0], "check_action"
        command = ("fix", "") if identifier == "guardian_fix" else ("resolve-conflicts", "")
        comment_id = check_run.get("id")
        # The live GitHub App ID must match before accepting a button.
        import os
        if str(app["id"]) != os.environ.get("GITHUB_APP_ID"):
            return None
    else:
        return None

    if not isinstance(obj, dict):
        return None
    number = obj.get("number")
    if type(number) is not int or number < 1:
        return None
    if kind in {"command", "check_action"}:
        if type(comment_id) is not int or comment_id < 1:
            return None
        if kind == "command" and not isinstance(obj.get("pull_request"), dict):
            # Issue commands are allowed only for help/status/label/duplicate.
            if command[0] not in {"help", "status", "label", "duplicate"}:
                return None
    return {
        "repo": repo,
        "number": number,
        "installation": installation_id,
        "kind": kind,
        "sender_id": user_id,
        "sender": login[:100],
        "comment_id": comment_id,
        "command": list(command) if command else None,
        # We do not store title, issue body, credentials or freeform comments.
        "action": action,
    }


class MaintenanceQueue:
    """Shared SQLite connection and queue semantics with the PR reviewer."""

    def __init__(self, queue):
        self.queue = queue
        with self.queue.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS maintenance_events (
                delivery_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                number INTEGER NOT NULL,
                installation INTEGER NOT NULL,
                kind TEXT NOT NULL,
                sender_id INTEGER NOT NULL,
                sender TEXT NOT NULL,
                comment_id INTEGER,
                command_json TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                result TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    def add(self, delivery_id: str, item: dict | None) -> int:
        if item is None:
            return 0
        if not isinstance(delivery_id, str) or not 1 <= len(delivery_id) <= 90:
            return 0
        with self.queue.connection() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO maintenance_events
                  (delivery_id,repo,number,installation,kind,sender_id,sender,
                   comment_id,command_json)
                  VALUES (?,?,?,?,?,?,?,?,?)""",
                (delivery_id, item["repo"], item["number"], item["installation"],
                 item["kind"], item["sender_id"], item["sender"],
                 item["comment_id"], json.dumps(item["command"])),
            )
            return cursor.rowcount

    def claim(self):
        with self.queue.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT delivery_id,repo,number,installation,kind,sender_id,
                          sender,comment_id,command_json
                   FROM maintenance_events WHERE status='queued'
                   ORDER BY created_at,delivery_id LIMIT 1"""
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE maintenance_events SET status='processing' WHERE delivery_id=?",
                    (row[0],),
                )
            db.commit()
        return row

    def finish(self, row, status: str, result: str):
        if status not in {"done", "failed", "skipped"}:
            raise ValueError("invalid maintenance result")
        with self.queue.connection() as db:
            db.execute(
                "UPDATE maintenance_events SET status=?,result=? WHERE delivery_id=?",
                (status, str(result)[:150], row[0]),
            )
