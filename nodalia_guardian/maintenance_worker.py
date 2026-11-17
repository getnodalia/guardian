"""One event per run: conservative GitHub maintenance under explicit feature flags.

No shell, repository checkout, arbitrary code execution, unrestricted PR writes,
or secrets in webhooks. Elevated commands require both repo permission AND an
operator user-ID allowlist. Actions are idempotent across delivery retries.
"""

import hashlib
import json
import os
import re

from .app_auth import installation_access_token
from .github_api import GitHubError
from .maintenance import MaintenanceQueue, parse_command
from .maintenance_api import MaintenanceAPI
from .triage import (
    LABELS, classify_haiku, exact_issue_duplicate,
    exact_pr_duplicate, heuristic_labels,
)


def allowed_ids() -> set[int]:
    ids = set()
    for part in os.environ.get("GUARDIAN_MAINTAINER_IDS", "").split(","):
        if part.strip().isdecimal() and int(part.strip()) > 0:
            ids.add(int(part.strip()))
    return ids


def privileged(api: MaintenanceAPI, repo: str, login: str, user_id: int,
               *, require_admin: bool = False) -> bool:
    if user_id not in allowed_ids():
        return False
    permission = api.permission(repo, login)
    return permission in ({"admin", "maintain"} if require_admin
                          else {"admin", "maintain", "write"})


def _mark_once(queue: MaintenanceQueue, fingerprint: str) -> bool:
    with queue.queue.connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS maintenance_actions (
            fingerprint TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        return db.execute(
            "INSERT OR IGNORE INTO maintenance_actions (fingerprint) VALUES (?)",
            (fingerprint,)
        ).rowcount == 1


def _fingerprint(repo: str, number: int, kind: str, state: dict) -> str:
    return hashlib.sha256(json.dumps({
        "repo": repo, "number": number, "kind": kind,
        "title": state.get("title"), "body": str(state.get("body") or "")[:3000],
        "sha": (state.get("head") or {}).get("sha"),
    }, sort_keys=True).encode()).hexdigest()


def label_item(api: MaintenanceAPI, repo: str, number: int,
               labels: set[str]) -> list[str]:
    present = api.existing_labels(repo)
    verified = sorted(labels.intersection(present).intersection(LABELS))
    if verified:
        api.add_labels(repo, number, verified)
    return verified


def _queue_review(queue: MaintenanceQueue, delivery_id: str, repo: str,
                  number: int, installation: int, sha: str) -> int:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        return 0
    # The existing review worker re-checks SHA, CI and PR state before spending.
    return queue.queue.add(
        "manual-" + hashlib.sha256(delivery_id.encode()).hexdigest()[:50],
        [(repo, number, installation, sha)],
    )


def _command(api: MaintenanceAPI, queue: MaintenanceQueue, row, issue: dict,
             command: tuple[str, str], *, allow_closure: bool,
             allow_operations: bool) -> str:
    delivery, repo, number, installation, kind, sender_id, sender, comment_id, _ = row
    if kind == "command":
        live_comment = api.issue_comment(repo, comment_id)
        author = live_comment.get("user") or {}
        if (author.get("id") != sender_id or author.get("login") != sender
                or parse_command(live_comment.get("body")) != command):
            return "Unverified or edited comment"
    cmd, args = command
    is_pr = bool(issue.get("pull_request"))
    if cmd == "help":
        api.comment(repo, number,
                    "**Nodalia Guardian** · Commands: /guardian status, "
                    "/guardian review, /guardian label <existing-label>, "
                    "/guardian duplicate #123, /guardian fix, "
                    "/guardian resolve-conflicts. "
                    "Sensitive operations require a configured maintainer.")
        return "Help sent"
    if cmd == "status":
        api.comment(repo, number,
                    "**Nodalia Guardian** · Item is "
                    + str(issue.get("state", "unknown"))
                    + ". Automated reviews never bypass mandatory CI.")
        return "Status sent"
    if not privileged(api, repo, sender, sender_id):
        return "Unauthorized command (requires maintainer ID and repo write access)"
    if cmd == "label":
        if args not in LABELS or args not in api.existing_labels(repo):
            return "Label not in configured allowlist or missing from repo"
        label_item(api, repo, number, {args})
        return "Label applied"
    if cmd == "review":
        if not is_pr or issue.get("state") != "open":
            return "Only open PRs can be reviewed"
        pr = api.get_pr(repo, number)
        if pr.get("draft"):
            return "Draft PR cannot be reviewed"
        count = _queue_review(queue, delivery, repo, number, installation,
                              (pr.get("head") or {}).get("sha", ""))
        if count:
            api.comment(repo, number,
                        "**Nodalia Guardian** · Review requested. "
                        "It will run after CI completes and within the configured budget.")
        return "Review enqueued" if count else "Already queued"
    if cmd == "duplicate":
        m = re.fullmatch(r"#?([0-9]{1,8})", args)
        if not m or int(m.group(1)) == number:
            return "Invalid duplicate target"
        target_num = int(m.group(1))
        target = api.issue(repo, target_num)
        if bool(target.get("pull_request")) != is_pr:
            return "Duplicate must be same issue/PR kind"
        if target_num >= number:
            return "Duplicate reference must point to an older item"
        if not privileged(api, repo, sender, sender_id, require_admin=True):
            return "Duplicate closure requires privileged maintainer"
        if not allow_closure:
            return "Closure feature is disabled"
        if issue.get("state") != "open":
            return "Item is already closed"
        if not _mark_once(queue, f"close:{repo}:{number}:{target_num}"):
            return "Duplicate closure already handled"
        api.comment(repo, number,
                    f"**Nodalia Guardian** · Marked as duplicate of #{target_num} "
                    f"by authorized maintainer @{sender}. Closing this item.")
        label_item(api, repo, number, {"duplicate"})
        (api.close_pr if is_pr else api.close_issue)(repo, number)
        return "Duplicate closed"
    if cmd in {"fix", "resolve-conflicts"}:
        if not is_pr or not privileged(api, repo, sender, sender_id, require_admin=True):
            return "PR and privileged maintainer required"
        if issue.get("state") != "open":
            return "PR must be open"
        if not allow_operations:
            return "Code-fix operations are disabled"
        pr = api.get_pr(repo, number)
        if pr.get("draft"):
            return "Draft PR"
        sha = (pr.get("head") or {}).get("sha")
        if not isinstance(sha, str) or len(sha) != 40:
            return "Missing head SHA"
        # The subsequent fixer works only on bot-owned branches.
        # It cannot push to contributor branches or the protected main.
        with queue.queue.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS guardian_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL, pr INTEGER NOT NULL, installation INTEGER NOT NULL,
                requester_id INTEGER NOT NULL, requester TEXT NOT NULL,
                target_comment_id INTEGER, kind TEXT NOT NULL, sha TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(repo,pr,sha,kind)
            )""")
            count = db.execute(
                """INSERT OR IGNORE INTO guardian_operations
                   (repo,pr,installation,requester_id,requester,target_comment_id,kind,sha)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (repo, number, installation, sender_id, sender,
                 int(args.lstrip("#")) if cmd == "fix" and args else None,
                 cmd, sha)
            ).rowcount
        if count:
            api.comment(repo, number,
                        f"**Nodalia Guardian** · Authorized \`{cmd}\` request "
                        "queued for an isolated bot-owned branch. "
                        "Guardian will not merge or approve its own corrections.")
        return "Operation queued" if count else "Operation already queued"
    return "Unsupported guardian command"


def process_maintenance(queue: MaintenanceQueue, *, enable_triage: bool,
                        enable_haiku: bool, enable_closure: bool,
                        enable_operations: bool, monthly_budget: float = 20) -> dict | None:
    row = queue.claim()
    if row is None:
        return None
    delivery, repo, number, installation, kind, sender_id, sender, comment_id, encoded = row
    try:
        token = installation_access_token(
            int(os.environ["GITHUB_APP_ID"]),
            os.environ["GITHUB_APP_PRIVATE_KEY_PATH"], installation
        )
        api = MaintenanceAPI(token)
        issue = api.issue(repo, number)
        if issue.get("state") != "open":
            queue.finish(row, "skipped", "Closed issue or PR")
            return {"status": "skipped", "reason": "Closed"}
        if kind in {"command", "check_action"}:
            command = json.loads(encoded)
            if not (isinstance(command, list) and len(command) == 2):
                raise ValueError("Invalid command payload")
            outcome = _command(api, queue, row, issue, tuple(command),
                               allow_closure=enable_closure,
                               allow_operations=enable_operations)
            queue.finish(row, "done", outcome)
            return {"status": "done", "kind": "command", "result": outcome}
        if not enable_triage:
            queue.finish(row, "skipped", "Triage disabled")
            return {"status": "skipped", "reason": "Triage disabled"}
        is_pr = kind == "pull_request"
        if is_pr != bool(issue.get("pull_request")):
            raise ValueError("Unexpected issue/PR kind")
        if not _mark_once(queue, _fingerprint(repo, number, kind, issue)):
            queue.finish(row, "skipped", "Triage already performed for this version")
            return {"status": "skipped", "reason": "Duplicate event"}
        title, body = str(issue.get("title") or ""), str(issue.get("body") or "")
        labels = heuristic_labels(title, body)
        # Use the model only for genuinely ambiguous items. Private content is
        # never sent to Anthropic without a separate explicit policy.
        if enable_haiku and not any(label.startswith("type:") for label in labels):
            metadata = api._get(api._path(repo))
            if metadata.get("private"):
                labels.add("needs-triage")
            else:
                haiku_labels, _ = classify_haiku(
                    title, body, key=os.environ.get("ANTHROPIC_API_KEY", ""),
                    monthly_budget=monthly_budget,
                )
                labels |= haiku_labels
        if not any(label.startswith("type:") for label in labels):
            labels.add("needs-triage")
        duplicate = None
        if is_pr:
            pr = api.get_pr(repo, number)
            duplicate = exact_pr_duplicate(pr, api.open_prs(repo))
        else:
            duplicate = exact_issue_duplicate(issue, api.similar_issues(repo, title))
        if duplicate:
            labels.add("duplicate")
        applied = label_item(api, repo, number, labels)
        if duplicate:
            api.comment(repo, number,
                        f"**Nodalia Guardian · Duplicate**\n\n"
                        f"This appears to be an exact duplicate of #{duplicate}. "
                        "The match was verified deterministically, not based only on AI.")
            if enable_closure:
                (api.close_pr if is_pr else api.close_issue)(repo, number)
        queue.finish(row, "done", "Labels: " + ",".join(applied)[:100])
        return {"status": "done", "kind": kind, "labels": applied,
                "duplicate": duplicate, "closed": bool(duplicate and enable_closure)}
    except Exception as exc:
        # No secret, payload body, source code or token is logged.
        queue.finish(row, "failed", type(exc).__name__)
        return {"status": "failed", "error_type": type(exc).__name__,
                "repo": repo, "number": number}
