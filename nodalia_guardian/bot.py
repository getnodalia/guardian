"""GitHub App reviewer: no approvals unless source, checks and policy all agree."""

import json
import os

from .anthropic_api import analyze
from .app_auth import installation_access_token
from .budget import BudgetLedger, default_db
from .decisions import REVIEW_SCHEMA, REVIEW_SYSTEM, gate, parse_review, review_markdown
from .github_api import GitHubAPI, GitHubError, validate_repo
from .inline import build_inline_comments
from .review import summarize


def review_pr(repo: str, pr: int, *, ai: bool, publish: bool,
              installation_id: int | None = None, auto_approve: bool = False,
              required_checks: list[str] | None = None, token: str | None = None,
              ledger_path=None, monthly_budget: float = 20.0) -> dict:
    owner, _ = validate_repo(repo)
    if owner != "getnodalia":
        raise ValueError("Guardian only reviews repositories owned by getnodalia")
    required_checks = required_checks or []
    if publish and not ai:
        raise ValueError("Publishing a review requires AI analysis")
    if publish and (not installation_id or not os.environ.get("GITHUB_APP_ID") or
                    not os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")):
        raise ValueError("Publishing reviews requires a GitHub App installation")
    if installation_id:
        token = installation_access_token(int(os.environ.get("GITHUB_APP_ID", "0")),
                                          os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", ""), installation_id)
    api = GitHubAPI(token)
    raw = api.inspect_pr(repo, pr)
    summary = summarize(repo, pr, raw)
    summary["patches_available"] = bool(raw["files"]) and all("patch" in f for f in raw["files"])
    summary["patches_truncated"] = summary["files_truncated"] or any(
        "patch" not in f or len(f.get("patch") or "") >= 30_000 for f in raw["files"])
    # No private source may be sent to Claude without separate policy approval.
    if ai and raw["pr"].get("base", {}).get("repo", {}).get("private", False):
        raise ValueError("Refusing to send private repository patches to Claude")
    body = None
    comments = []
    event = "COMMENT"
    review = None
    cost = None
    reasons = ["AI analysis not requested"]
    if ai:
        from .redaction import limit_text
        patches = [{"path": limit_text(f.get("filename"), 400),
                    "patch": limit_text(f.get("patch"), 30_000)} for f in raw["files"][:100]]
        if sum(len(p["patch"]) for p in patches) > 100_000:
            summary["patches_truncated"] = True
            remaining = 100_000
            for entry in patches:
                entry["patch"] = entry["patch"][:remaining]
                remaining -= len(entry["patch"])
        if summary["patches_truncated"]:
            summary["patches_available"] = False
        material = {**summary, "patches": patches}
        llm, cost = analyze(material, os.environ.get("ANTHROPIC_API_KEY", ""),
                            "claude-sonnet-5-5", 3000, BudgetLedger(ledger_path or default_db(), monthly_budget),
                            system=REVIEW_SYSTEM, output_schema=REVIEW_SCHEMA)
        review = parse_review(llm, {f["name"] for f in summary["files"]})
        own = "guardian" in str(raw["pr"].get("head", {}).get("label", "")).lower()
        event, reasons = gate(summary, review, required_checks, auto_approve, own)
        comments, placed_indices = build_inline_comments(review["findings"], raw["files"])
        body = review_markdown(summary, review, reasons, event, placed_indices)
    if publish:
        # Refuse outdated results: the PR head must still match after the AI call.
        live = api.get_pr(repo, pr)
        if live.get("head", {}).get("sha") != summary["head_sha"]:
            raise GitHubError("PR changed during review: refusing to publish stale review")
        if live.get("state") != "open" or live.get("draft"):
            raise GitHubError("PR closed or became draft during review: refusing to publish")
        before_base = (raw["pr"].get("base") or {}).get("sha")
        current_base = (live.get("base") or {}).get("sha")
        if before_base and current_base != before_base:
            raise GitHubError("PR base changed during review: refusing to publish stale comments")
        api.create_review(repo, pr, event=event, body=body,
                          commit_id=summary["head_sha"], comments=comments)
    return {"repo": repo, "pr": pr, "sha": summary["head_sha"], "decision": event,
            "approval_blockers": reasons, "findings": (review or {}).get("findings", []),
            "body": body, "inline_comments": len(comments), "published": publish, "cost_usd": cost, "summary": summary}
