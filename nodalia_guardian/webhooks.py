"""Authenticated GitHub webhook intake and persistent, explicit-opt-in work queue."""

import hashlib
import hmac
import json
import sqlite3
from contextlib import contextmanager, closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_WEBHOOK = 512_000


class WebhookError(ValueError):
    pass


def verify(body: bytes, signature: str | None, secret: str) -> None:
    if not secret or not signature or not signature.startswith("sha256="):
        raise WebhookError("Missing webhook authentication")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookError("Invalid webhook signature")


def parse_delivery(body: bytes, event: str, allowed_repos: set[str]) -> list[tuple[str, int, int, str]]:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise WebhookError("Invalid webhook JSON") from exc
    if not isinstance(payload, dict):
        raise WebhookError("Invalid webhook shape")
    repository = payload.get("repository")
    repo = repository.get("full_name") if isinstance(repository, dict) else None
    if not isinstance(repo, str) or not repo.startswith("getnodalia/") or repo not in allowed_repos:
        return []
    installation = payload.get("installation")
    inst_id = installation.get("id") if isinstance(installation, dict) else None
    if type(inst_id) is not int or inst_id < 1:
        raise WebhookError("Missing GitHub App installation ID")
    action = payload.get("action")
    prs = []
    if event == "pull_request" and action in {"opened", "synchronize", "reopened", "ready_for_review"}:
        prs = [payload.get("pull_request")]
    elif event in {"check_suite", "check_run"} and action == "completed":
        check = payload.get(event) or {}
        prs = check.get("pull_requests", []) if isinstance(check, dict) else []
    if not isinstance(prs, list):
        return []
    output = []
    for pr in prs[:15]:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        head = pr.get("head", {})
        sha = head.get("sha") if isinstance(head, dict) else None
        if type(number) is int and number > 0 and isinstance(sha, str) and len(sha) == 40:
            output.append((repo, number, inst_id, sha))
    return output


class DeliveryQueue:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                id TEXT NOT NULL, repo TEXT NOT NULL, pr INTEGER NOT NULL, installation INTEGER NOT NULL,
                sha TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(id, repo, pr))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewed (
                repo TEXT NOT NULL, pr INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                PRIMARY KEY(repo,pr,fingerprint))""")

    @contextmanager
    def connection(self):
        # sqlite3's connection context manager commits/rolls back but does not close.
        # Own the connection explicitly so long-running workers do not leak descriptors.
        with closing(sqlite3.connect(self.db_path, timeout=10)) as db:
            with db:
                yield db

    def add(self, delivery_id: str, items: list[tuple[str, int, int, str]]) -> int:
        if not delivery_id or len(delivery_id) > 90:
            raise WebhookError("Invalid GitHub delivery ID")
        inserted = 0
        with self.connection() as db:
            for repo, pr, inst, sha in items:
                cursor = db.execute("""INSERT OR IGNORE INTO deliveries
                    (id,repo,pr,installation,sha) VALUES(?,?,?,?,?)""",
                    (delivery_id, repo, pr, inst, sha))
                inserted += cursor.rowcount
        return inserted

    def reserve_fingerprint(self, repo: str, pr: int, fingerprint: str) -> bool:
        with self.connection() as db:
            cursor = db.execute("INSERT OR IGNORE INTO reviewed(repo,pr,fingerprint) VALUES (?,?,?)",
                                (repo,pr,fingerprint))
            return cursor.rowcount > 0

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id,repo,pr,installation,sha FROM deliveries
                WHERE status='queued' ORDER BY created_at, id LIMIT 1""").fetchone()
            if row:
                db.execute("UPDATE deliveries SET status='processing',attempts=attempts+1 WHERE id=? AND repo=? AND pr=?", (row[0], row[1], row[2]))
            db.commit()
        return row

    def finish(self, row, status: str, result: str):
        if status not in {"done", "failed", "skipped"}:
            raise ValueError("Invalid queue status")
        with self.connection() as db:
            db.execute("UPDATE deliveries SET status=?,result=? WHERE id=? AND repo=? AND pr=?",
                       (status, result[:1000], row[0], row[1], row[2]))


def make_server(address: str, port: int, secret: str, queue: DeliveryQueue,
                allowed_repos: set[str]):
    if not secret:
        raise ValueError("GUARDIAN_WEBHOOK_SECRET is required")
    if not allowed_repos or any(not x.startswith("getnodalia/") for x in allowed_repos):
        raise ValueError("Configure an explicit getnodalia repository allowlist")

    from .maintenance import MaintenanceQueue, parse_maintenance
    maintenance = MaintenanceQueue(queue)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok\n")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path != "/webhook":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if size <= 0 or size > MAX_WEBHOOK:
                self.send_error(413)
                return
            body = self.rfile.read(size)
            try:
                verify(body, self.headers.get("X-Hub-Signature-256"), secret)
                items = parse_delivery(body, self.headers.get("X-GitHub-Event", ""), allowed_repos)
                delivery_id = self.headers.get("X-GitHub-Delivery", "")
                inserted = queue.add(delivery_id, items)
                maintenance_item = parse_maintenance(
                    body, self.headers.get("X-GitHub-Event", ""), allowed_repos
                )
                maintenance_inserted = maintenance.add(delivery_id, maintenance_item)
            except WebhookError:
                self.send_error(403)
                return
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"queued": inserted, "maintenance_queued": maintenance_inserted}).encode())

        def log_message(self, format, *args):
            # Do not log webhook bodies or authentication headers.
            return

    return ThreadingHTTPServer((address, port), Handler)
