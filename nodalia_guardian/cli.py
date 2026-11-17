"""CLI entrypoint with safe-by-default read-only behavior."""

import argparse
import json
import os
import sys
from pathlib import Path

from .anthropic_api import analyze, AnthropicAPIError
from .app_auth import AppAuthError
from .budget import BudgetExceeded, BudgetLedger, MODEL_PRICES, default_db
from .github_api import GitHubAPI, GitHubError, validate_repo
from .review import markdown, summarize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nodalia-guardian", description="Read-only PR and CI review for Nodalia")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="Inspect a GitHub PR and its commit checks")
    scan.add_argument("--repo", required=True, help="owner/repo, e.g. getnodalia/agora")
    scan.add_argument("--pr", type=int, required=True, help="pull request number")
    scan.add_argument("--ai", action="store_true", help="opt into a paid Claude API request")
    scan.add_argument("--model", choices=sorted(MODEL_PRICES), default="claude-sonnet-5-5")
    scan.add_argument("--max-output-tokens", type=int, default=1000)
    scan.add_argument("--monthly-budget", type=float, default=20.0)
    scan.add_argument("--ledger", type=Path, default=default_db())
    scan.add_argument("--json", action="store_true", help="output machine-readable JSON instead of Markdown")
    scan.add_argument("--output", type=Path, help="write report to a local file")
    review = sub.add_parser("review", help="review a PR with Claude; optional GitHub App publishing")
    review.add_argument("--repo", required=True)
    review.add_argument("--pr", required=True, type=int)
    review.add_argument("--ai", action="store_true", help="explicitly spend API credits")
    review.add_argument("--publish", action="store_true", help="submit GitHub App review")
    review.add_argument("--installation-id", type=int)
    review.add_argument("--auto-approve", action="store_true", help="allow approval if all deterministic gates pass")
    review.add_argument("--required-check", action="append", default=[], help="exact check context; repeat per required check")
    review.add_argument("--monthly-budget", type=float, default=20.0)
    review.add_argument("--output", type=Path)

    serve = sub.add_parser("serve", help="receive and queue authenticated GitHub App webhooks")
    serve.add_argument("--host", default="127.0.0.1", help="bind loopback behind a HTTPS reverse proxy")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--allowed-repo", action="append", default=[])
    # Boolean opt-in only: callers can never choose a filesystem path.
    # load_repos() reads the fixed /etc/guardian/repos.json.
    serve.add_argument("--allowed-repo-file", action="store_true",
                       help="include repositories from fixed /etc/guardian/repos.json")
    serve.add_argument("--db", type=Path, default=default_db().with_name("deliveries.sqlite3"))

    worker = sub.add_parser("worker", help="process one queued delivery with a GitHub App + Claude")
    worker.add_argument("--db", type=Path, default=default_db().with_name("deliveries.sqlite3"))
    worker.add_argument("--required-check", action="append", default=[])
    worker.add_argument("--auto-approve", action="store_true")
    worker.add_argument("--monthly-budget", type=float, default=20.0)
    worker.add_argument("--confirm-paid-publishing", action="store_true", help="must be set explicitly")

    maintain = sub.add_parser("maintain", help="process one issue, PR or maintainer command event")
    maintain.add_argument("--db", type=Path, default=default_db().with_name("deliveries.sqlite3"))
    maintain.add_argument("--enable-triage", action="store_true", help="allow low-risk GitHub labels")
    maintain.add_argument("--enable-haiku", action="store_true", help="opt in to paid Haiku triage")
    maintain.add_argument("--enable-closure", action="store_true", help="allow only verified duplicate closure")
    maintain.add_argument("--enable-operations", action="store_true", help="queue authorized code fixes")
    maintain.add_argument("--confirm-paid", action="store_true", help="explicitly authorize API spending")
    maintain.add_argument("--confirm-write", action="store_true", help="explicitly authorize GitHub mutations")
    maintain.add_argument("--monthly-budget", type=float, default=20.0)

    labels = sub.add_parser("setup-labels", help="seed standardized labels in one authorized repository")
    labels.add_argument("--repo", required=True)
    labels.add_argument("--installation-id", type=int, required=True)
    labels.add_argument("--confirm-write", action="store_true")

    fixes = sub.add_parser("fix-worker", help="process one authorized Sonnet code operation")
    fixes.add_argument("--db", type=Path, default=default_db().with_name("deliveries.sqlite3"))
    fixes.add_argument("--enable-fixes", action="store_true")
    fixes.add_argument("--enable-conflicts", action="store_true")
    fixes.add_argument("--confirm-paid", action="store_true")
    fixes.add_argument("--confirm-write", action="store_true")
    fixes.add_argument("--monthly-budget", type=float, default=20.0)

    buttons = sub.add_parser("setup-buttons", help="add native GitHub Checks action buttons to one PR")
    buttons.add_argument("--repo", required=True)
    buttons.add_argument("--pr", type=int, required=True)
    buttons.add_argument("--installation-id", type=int, required=True)
    buttons.add_argument("--confirm-write", action="store_true")

    sync = sub.add_parser("sync-repos", help="build allowlist from App installation repositories")
    sync.add_argument("--installation-id", type=int, required=True)
    sync.add_argument("--confirm-write", action="store_true")

    doctor = sub.add_parser("doctor", help="read-only GitHub App installation and queue diagnostics")
    doctor.add_argument("--app-id", type=int, help="GitHub App ID; defaults to GITHUB_APP_ID")
    doctor.add_argument("--installation-id", required=True, type=int)
    doctor.add_argument("--private-key", type=Path, help="defaults to GITHUB_APP_PRIVATE_KEY_PATH")
    doctor.add_argument("--db", type=Path, help="existing SQLite DB, never modified")
    doctor.add_argument("--repo", help="getnodalia/repository for queue inspection")
    doctor.add_argument("--issue", type=int, help="issue or PR number to inspect")

    budget = sub.add_parser("budget", help="show local usage/reservation ledger")
    budget.add_argument("--monthly-budget", type=float, default=20.0)
    budget.add_argument("--ledger", type=Path, default=default_db())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            from .installation_diagnostics import diagnose_installation
            app_id = args.app_id or os.environ.get("GITHUB_APP_ID")
            private_key = args.private_key or os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")
            if not app_id or not private_key:
                raise ValueError("Doctor needs a GitHub App ID and private key path")
            report = diagnose_installation(
                int(app_id), args.installation_id, private_key,
                db_path=args.db, repo=args.repo, number=args.issue,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["issues_ready"] else 2
        if args.command == "budget":
            ledger = BudgetLedger(args.ledger, args.monthly_budget)
            print(f"Local current-month spend and reservations: ${ledger.total_micro() / 1e6:.6f} / ${args.monthly_budget:.2f} USD")
            print("This is not an account-wide hard limit; set a Claude Console workspace spend limit.")
            return 0
        if args.command == "serve":
            from .webhooks import DeliveryQueue, make_server
            queue = DeliveryQueue(args.db)
            allowed_repos = set(args.allowed_repo)
            if args.allowed_repo_file:
                from .repo_allowlist import load_repos
                allowed_repos |= load_repos()
            server = make_server(args.host, args.port, os.environ.get("GUARDIAN_WEBHOOK_SECRET", ""),
                                 queue, allowed_repos)
            print(f"Guardian webhook listener on {args.host}:{args.port}; webhook path /webhook")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        if args.command == "worker":
            if not args.confirm_paid_publishing:
                raise ValueError("worker requires --confirm-paid-publishing; may consume Claude credits and publish GitHub reviews")
            if not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("GITHUB_APP_ID") and
                    os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")):
                raise ValueError("worker requires Anthropic and GitHub App credentials")
            from .webhooks import DeliveryQueue
            from .worker import process_one
            queue = DeliveryQueue(args.db)
            result = process_one(queue, required_checks=args.required_check, auto_approve=args.auto_approve,
                                 monthly_budget=args.monthly_budget)
            print(json.dumps(result or {"status": "empty"}, ensure_ascii=False))
            return 2 if result and result["status"] == "failed" else 0
        if args.command == "maintain":
            if not args.confirm_write:
                raise ValueError("maintenance requires --confirm-write")
            if args.enable_haiku and (not args.confirm_paid or not os.environ.get("ANTHROPIC_API_KEY")):
                raise ValueError("Haiku requires --confirm-paid and an Anthropic API key")
            if not (0 < args.monthly_budget <= 100):
                raise ValueError("Monthly budget must be between 0 and 100")
            if not (os.environ.get("GITHUB_APP_ID") and
                    os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH")):
                raise ValueError("Maintenance requires GitHub App credentials")
            from .maintenance import MaintenanceQueue
            from .maintenance_worker import process_maintenance
            from .webhooks import DeliveryQueue
            result = process_maintenance(
                MaintenanceQueue(DeliveryQueue(args.db)),
                enable_triage=args.enable_triage, enable_haiku=args.enable_haiku,
                enable_closure=args.enable_closure,
                enable_operations=args.enable_operations,
                monthly_budget=args.monthly_budget,
            )
            print(json.dumps(result or {"status": "empty"}, ensure_ascii=False))
            return 2 if result and result.get("status") == "failed" else 0
        if args.command == "setup-labels":
            if not args.confirm_write:
                raise ValueError("setup-labels requires --confirm-write")
            from .app_auth import installation_access_token
            from .maintenance_api import MaintenanceAPI
            from .triage import LABELS
            validate_repo(args.repo)
            token = installation_access_token(
                int(os.environ["GITHUB_APP_ID"]),
                os.environ["GITHUB_APP_PRIVATE_KEY_PATH"], args.installation_id,
            )
            api = MaintenanceAPI(token)
            existing = api.existing_labels(args.repo)
            added = []
            for name, (color, description) in LABELS.items():
                if name not in existing:
                    api.create_label(args.repo, name, color, description)
                    added.append(name)
            print(json.dumps({"added": added}, ensure_ascii=False))
            return 0
        if args.command == "fix-worker":
            if not (args.confirm_write and args.confirm_paid):
                raise ValueError("fix-worker requires --confirm-write and --confirm-paid")
            if not (0 < args.monthly_budget <= 100):
                raise ValueError("Invalid fix-worker budget")
            if not (os.environ.get("GITHUB_APP_ID") and
                    os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH") and
                    os.environ.get("ANTHROPIC_API_KEY")):
                raise ValueError("Fix worker credentials are incomplete")
            from .operations import OperationQueue, process_operation
            from .webhooks import DeliveryQueue
            result = process_operation(
                OperationQueue(DeliveryQueue(args.db)),
                enable_fixes=args.enable_fixes,
                enable_conflicts=args.enable_conflicts,
                monthly_budget=args.monthly_budget,
            )
            print(json.dumps(result or {"status": "empty"}, ensure_ascii=False))
            return 2 if result and result.get("status") == "failed" else 0
        if args.command == "setup-buttons":
            if not args.confirm_write or os.environ.get("GUARDIAN_ENABLE_CHECK_ACTIONS") != "1":
                raise ValueError("Check buttons require --confirm-write and GUARDIAN_ENABLE_CHECK_ACTIONS=1")
            from .app_auth import installation_access_token
            from .maintenance_api import MaintenanceAPI
            token = installation_access_token(
                int(os.environ["GITHUB_APP_ID"]),
                os.environ["GITHUB_APP_PRIVATE_KEY_PATH"],
                args.installation_id,
            )
            api = MaintenanceAPI(token)
            pr = api.get_pr(args.repo, args.pr)
            if pr.get("state") != "open" or pr.get("draft"):
                raise ValueError("Check buttons require an open non-draft PR")
            head_sha = (pr.get("head") or {}).get("sha")
            result = api.create_check(
                args.repo, head_sha,
                title="Guardian · Suggested actions",
                summary="Buttons request an authorization-checked maintenance operation. "
                        "They never merge a PR automatically.",
                actions=[
                    {"label": "Propose fix",
                     "description": "Open a draft PR with a Sonnet-generated correction",
                     "identifier": "guardian_fix"},
                    {"label": "Resolve conflicts",
                     "description": "Prepare a reviewable, isolated merge proposal",
                     "identifier": "guardian_resolve_conflicts"},
                ],
            )
            print(json.dumps({"check_run_id": result.get("id")}))
            return 0
        if args.command == "sync-repos":
            if not args.confirm_write:
                raise ValueError("sync-repos requires --confirm-write")
            from .repo_allowlist import installed_repositories, write_repos
            names = installed_repositories(
                int(os.environ["GITHUB_APP_ID"]),
                os.environ["GITHUB_APP_PRIVATE_KEY_PATH"],
                args.installation_id,
            )
            write_repos(names)
            print(json.dumps({"repository_count": len(names),
                              "allowlist": "/etc/guardian/repos.json"}, ensure_ascii=False))
            return 0
        if args.command == "review":
            from .bot import review_pr
            result = review_pr(args.repo, args.pr, ai=args.ai, publish=args.publish,
                               installation_id=args.installation_id, auto_approve=args.auto_approve,
                               required_checks=args.required_check, monthly_budget=args.monthly_budget)
            report = (result["body"] or "No billable model review requested.") + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(report, encoding="utf-8")
                print(f"Review written to {args.output}")
            else:
                print(report)
            return 0
        validate_repo(args.repo)
        if args.pr < 1:
            raise ValueError("PR number must be positive")
        if not 1 <= args.max_output_tokens <= 4096:
            raise ValueError("--max-output-tokens must be between 1 and 4096")
        if args.monthly_budget <= 0 or args.monthly_budget > 100:
            raise ValueError("--monthly-budget must be between 0 and 100 USD")
        raw = GitHubAPI(os.environ.get("GITHUB_TOKEN")).inspect_pr(args.repo, args.pr)
        summary = summarize(args.repo, args.pr, raw)
        ai_text = None
        cost = None
        if args.ai:
            ledger = BudgetLedger(args.ledger, args.monthly_budget)
            ai_text, cost = analyze(summary, os.environ.get("ANTHROPIC_API_KEY", ""), args.model, args.max_output_tokens, ledger)
        if args.json:
            result = {**summary, "ai_analysis": ai_text, "estimated_cost_usd": cost}
            report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        else:
            report = markdown(summary, ai_text, cost)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding="utf-8")
            print(f"Report written to {args.output}")
        else:
            print(report)
        return 0
    except (ValueError, GitHubError, AnthropicAPIError, AppAuthError, BudgetExceeded, OSError) as exc:
        print(f"guardian: {exc}", file=sys.stderr)
        return 2
