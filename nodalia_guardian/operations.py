"""Process one privileged, auditable code-change request and exit.

Only draft pull requests on fresh Guardian branches may be created.
The reviewer must never auto-approve or auto-merge Guardian's own changes.
"""

import json
import os

from .app_auth import installation_access_token
from .fixer import propose_fix
from .git_changes import GitChangesAPI
from .maintenance_worker import allowed_ids, privileged
from .merge_resolver import propose_merge_resolution


class OperationQueue:
    def __init__(self, queue):
        self.queue = queue
        with self.queue.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS guardian_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL, pr INTEGER NOT NULL, installation INTEGER NOT NULL,
                requester_id INTEGER NOT NULL, requester TEXT NOT NULL,
                target_comment_id INTEGER, kind TEXT NOT NULL, sha TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                result TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(repo,pr,sha,kind)
            )""")
            # Upgrade an existing table that was created before this version.
            columns = {item[1] for item in db.execute("PRAGMA table_info(guardian_operations)")}
            if "requester" not in columns:
                db.execute("ALTER TABLE guardian_operations ADD COLUMN requester TEXT NOT NULL DEFAULT ''")
            if "target_comment_id" not in columns:
                db.execute("ALTER TABLE guardian_operations ADD COLUMN target_comment_id INTEGER")
            if "result" not in columns:
                db.execute("ALTER TABLE guardian_operations ADD COLUMN result TEXT NOT NULL DEFAULT ''")

    def claim(self):
        with self.queue.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id,repo,pr,installation,requester_id,requester,
                          target_comment_id,kind,sha
                   FROM guardian_operations
                   WHERE status='queued' ORDER BY id LIMIT 1"""
            ).fetchone()
            if row:
                db.execute("UPDATE guardian_operations SET status='processing' WHERE id=?",
                           (row[0],))
            db.commit()
        return row

    def finish(self, row, state: str, result: str):
        if state not in {"done", "failed", "skipped"}:
            raise ValueError("Unexpected operation result")
        with self.queue.connection() as db:
            db.execute(
                "UPDATE guardian_operations SET status=?,result=? WHERE id=?",
                (state, str(result)[:300], row[0]),
            )


def process_operation(queue: OperationQueue, *, enable_fixes: bool,
                      enable_conflicts: bool, monthly_budget: float = 20.0) -> dict | None:
    row = queue.claim()
    if row is None:
        return None
    _, repo, number, installation, sender_id, login, target_comment, kind, sha = row
    try:
        if kind not in {"fix", "resolve-conflicts"}:
            raise ValueError("Unknown privileged operation")
        if not ((kind == "fix" and enable_fixes)
                or (kind == "resolve-conflicts" and enable_conflicts)):
            queue.finish(row, "skipped", "Operation capability disabled")
            return {"status": "skipped", "reason": "Capability disabled"}
        if sender_id not in allowed_ids() or not login:
            queue.finish(row, "skipped", "Requester is no longer allowlisted")
            return {"status": "skipped", "reason": "Not authorized"}
        token = installation_access_token(
            int(os.environ["GITHUB_APP_ID"]),
            os.environ["GITHUB_APP_PRIVATE_KEY_PATH"], installation,
        )
        api = GitChangesAPI(token)
        if not privileged(api, repo, login, sender_id, require_admin=True):
            queue.finish(row, "skipped", "Maintainer permission revoked")
            return {"status": "skipped", "reason": "Not authorized"}
        # Confirm numeric user ID still belongs to the account with this login.
        user = api._get("/users/" + login)
        if user.get("id") != sender_id:
            queue.finish(row, "skipped", "Requester identity changed")
            return {"status": "skipped", "reason": "Unverified identity"}
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("No Anthropic key configured")
        if kind == "fix":
            result = propose_fix(
                api, repo, number, sha, comment_id=target_comment, key=key,
                monthly_budget=monthly_budget,
            )
        else:
            result = propose_merge_resolution(
                api, repo, number, sha, key=key, monthly_budget=monthly_budget,
            )
        # The branch and draft PR are already created; a comment failure is
        # not automatically retried because the operation has side effects.
        url = result.get("url")
        queue.finish(row, "done", url or "Draft PR created")
        if url:
            try:
                api.comment(
                    repo, number,
                    f"**Nodalia Guardian** · Proposed a reviewable draft PR: {url}\n\n"
                    "I did not modify your original branch or run your code. "
                    "A maintainer must review the diff and required CI before merging.",
                )
            except Exception:
                # Do not re-run a successfully written operation on comment failure.
                pass
        return {"status": "done", "kind": kind, "repo": repo,
                "pr": number, "proposal": url}
    except Exception as exc:
        queue.finish(row, "failed", type(exc).__name__)
        return {"status": "failed", "kind": kind,
                "repo": repo, "pr": number, "error_type": type(exc).__name__}
