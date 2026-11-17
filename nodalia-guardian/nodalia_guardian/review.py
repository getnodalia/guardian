"""Normalize untrusted GitHub data into a deliberately small review context."""

from .redaction import limit_text

_FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "error"}
_OK = {"success", "neutral", "skipped"}


def summarize(repo: str, number: int, raw: dict) -> dict:
    pr = raw["pr"]
    checks_raw = raw.get("checks", {})
    statuses_raw = raw.get("statuses", {})
    check_runs = checks_raw.get("check_runs", [])[:100]
    status_runs = statuses_raw.get("statuses", [])[:100]
    checks = []
    for c in check_runs:
        checks.append({
            "name": limit_text(c.get("name"), 160),
            "status": limit_text(c.get("status"), 30),
            "conclusion": limit_text(c.get("conclusion") or "pending", 40),
            "summary": limit_text((c.get("output") or {}).get("summary", ""), 500),
            "url": str(c.get("html_url") or "")[:500],
        })
    for c in status_runs:
        checks.append({
            "name": limit_text(c.get("context"), 160),
            "status": "completed" if c.get("state") != "pending" else "in_progress",
            "conclusion": limit_text(c.get("state") or "pending", 40),
            "summary": limit_text(c.get("description"), 500),
            "url": str(c.get("target_url") or "")[:500],
        })
    failed = [c for c in checks if c["conclusion"].lower() in _FAILED]
    pending = [c for c in checks if c["conclusion"].lower() not in _FAILED | _OK]
    files = []
    for item in raw.get("files", [])[:100]:
        files.append({"name": limit_text(item.get("filename"), 400),
                      "status": limit_text(item.get("status"), 30),
                      "additions": int(item.get("additions") or 0),
                      "deletions": int(item.get("deletions") or 0)})
    return {
        "repository": repo,
        "pull_request": number,
        "url": f"https://github.com/{repo}/pull/{number}",
        "title": limit_text(pr.get("title"), 250),
        "description": limit_text(pr.get("body"), 1500),
        "head_sha": raw["sha"],
        "draft": bool(pr.get("draft")),
        "changed_files_total": pr.get("changed_files", len(files)),
        "files": files,
        "files_truncated": len(files) >= 100 and pr.get("changed_files", 0) > 100,
        "checks": checks,
        "checks_truncated": checks_raw.get("total_count", 0) > 100 or len(status_runs) >= 100,
        "failed_count": len(failed),
        "pending_count": len(pending),
        "failed_names": [x["name"] for x in failed],
    }


def escape_inline(text: object) -> str:
    s = str(text or "").replace("\\", "\\\\").replace("`", "\\`")
    return s.replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")


def markdown(summary: dict, ai: str | None = None, cost_usd: float | None = None) -> str:
    rows = [
        "# Nodalia Guardian — PR review",
        "",
        f"**Repository:** `{escape_inline(summary['repository'])}` · **PR:** #{summary['pull_request']}",
        f"**Title:** {escape_inline(summary['title'])}",
        f"**Commit:** `{escape_inline(summary['head_sha'])}`",
        "",
        "## CI status",
        "",
    ]
    if not summary["checks"]:
        rows.append("No check runs were returned. This does **not** mean the PR passed CI.")
    else:
        rows.append(f"**Failed:** {summary['failed_count']} · **Pending:** {summary['pending_count']} · **Observed:** {len(summary['checks'])}")
        rows.extend(["", "| Check | Result |", "|---|---|"])
        rows.extend(f"| {escape_inline(c['name'])} | {escape_inline(c['conclusion'])} |" for c in summary["checks"])
    if summary["checks_truncated"]:
        rows.append("\n> Check results were truncated; do not treat this as a complete gate review.")
    rows.extend(["", "## Changed files", ""])
    rows.extend(f"- `{escape_inline(f['name'])}` (+{f['additions']} / -{f['deletions']})" for f in summary["files"])
    if not summary["files"]:
        rows.append("No files returned.")
    if summary["files_truncated"]:
        rows.append("\n> File list was truncated to 100; further pages were not analyzed.")
    rows.extend(["", "## Assessment", ""])
    if summary["failed_count"]:
        rows.append("CI has failing results. Investigate the named checks before considering a merge.")
    elif summary["pending_count"]:
        rows.append("Some checks have not completed. The PR is not yet verified as passing.")
    elif summary["checks"]:
        rows.append("No failures observed in the fetched checks; confirm required checks and branch protection separately.")
    else:
        rows.append("There is insufficient CI data to assess this PR.")
    if ai:
        rows.extend(["", "## Claude analysis", "", ai.strip(), ""])
    if cost_usd is not None:
        rows.extend(["", f"Estimated API request cost: ${cost_usd:.6f} USD (verify in Claude Console)."])
    rows.extend(["", "_Read-only report. Guardian did not change the repository, merge a PR, or execute code._", ""])
    return "\n".join(rows)
