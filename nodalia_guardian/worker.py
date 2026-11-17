"""Opt-in GitHub webhook worker. Durable deduplication, no automatic retries."""

import hashlib
import json
import os

from .app_auth import installation_access_token
from .bot import review_pr
from .github_api import GitHubAPI
from .review import summarize
from .webhooks import DeliveryQueue


def process_one(queue: DeliveryQueue, *, required_checks: list[str], auto_approve: bool,
                monthly_budget: float = 20.0) -> dict | None:
    row = queue.claim()
    if row is None:
        return None
    delivery_id, repo, pr, installation, expected_sha = row
    try:
        token = installation_access_token(int(os.environ["GITHUB_APP_ID"]),
                                          os.environ["GITHUB_APP_PRIVATE_KEY_PATH"], installation)
        raw = GitHubAPI(token).inspect_pr(repo, pr)
        if raw["sha"] != expected_sha:
            queue.finish(row, "skipped", "Head SHA changed")
            return {"status": "skipped", "reason": "Head SHA changed"}
        if raw["pr"].get("state") != "open":
            queue.finish(row, "skipped", "PR is not open")
            return {"status": "skipped", "reason": "PR is not open"}
        if raw["pr"].get("draft"):
            queue.finish(row, "skipped", "Draft PR")
            return {"status": "skipped", "reason": "Draft PR"}
        summary = summarize(repo, pr, raw)
        # Check-run completion webhooks can enqueue a PR multiple times as CI
        # progresses. Wait for a complete, non-pending check snapshot before
        # spending tokens or reserving a review fingerprint. The next signed
        # completion webhook will enqueue a new delivery.
        if not summary["checks"] or summary["pending_count"] or summary["checks_truncated"]:
            queue.finish(row, "skipped", "CI not complete")
            return {"status": "skipped", "reason": "CI not complete"}
        signature = hashlib.sha256(json.dumps({
            "sha": raw["sha"],
            "checks": sorted((c["name"], c["status"], c["conclusion"])
                             for c in summary["checks"]),
            "truncated": summary["checks_truncated"],
        }, sort_keys=True).encode()).hexdigest()
        if not queue.reserve_fingerprint(repo, pr, signature):
            queue.finish(row, "skipped", "Already reviewed the same commit and check states")
            return {"status": "skipped", "reason": "Duplicate analysis"}
        # A crash after model invocation may incur charges. Never automatically retry an
        # uncertain request. A new commit or CI transition creates a different fingerprint.
        result = review_pr(repo, pr, ai=True, publish=True, installation_id=installation,
                           auto_approve=auto_approve, required_checks=required_checks,
                           monthly_budget=monthly_budget)
        queue.finish(row, "done", result["decision"])
        return {"status": "done", "decision": result["decision"], "repo": repo, "pr": pr}
    except Exception as exc:
        # Never leak token/secret/API response in logs or persisted queue messages.
        queue.finish(row, "failed", type(exc).__name__)
        return {"status": "failed", "error_type": type(exc).__name__, "repo": repo, "pr": pr}
