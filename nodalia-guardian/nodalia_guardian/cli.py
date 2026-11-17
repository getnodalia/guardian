"""CLI entrypoint with safe-by-default read-only behavior."""

import argparse
import json
import os
import sys
from pathlib import Path

from .anthropic_api import analyze, AnthropicAPIError
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
    serve.add_argument("--allowed-repo", action="append", required=True)
    serve.add_argument("--db", type=Path, default=default_db().with_name("deliveries.sqlite3"))

    worker = sub.add_parser("worker", help="process one queued delivery with a GitHub App + Claude")
    worker.add_argument("--db", type=Path, default=default_db().with_name("deliveries.sqlite3"))
    worker.add_argument("--required-check", action="append", default=[])
    worker.add_argument("--auto-approve", action="store_true")
    worker.add_argument("--monthly-budget", type=float, default=20.0)
    worker.add_argument("--confirm-paid-publishing", action="store_true", help="must be set explicitly")

    budget = sub.add_parser("budget", help="show local usage/reservation ledger")
    budget.add_argument("--monthly-budget", type=float, default=20.0)
    budget.add_argument("--ledger", type=Path, default=default_db())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "budget":
            ledger = BudgetLedger(args.ledger, args.monthly_budget)
            print(f"Local current-month spend and reservations: ${ledger.total_micro() / 1e6:.6f} / ${args.monthly_budget:.2f} USD")
            print("This is not an account-wide hard limit; set a Claude Console workspace spend limit.")
            return 0
        if args.command == "serve":
            from .webhooks import DeliveryQueue, make_server
            queue = DeliveryQueue(args.db)
            server = make_server(args.host, args.port, os.environ.get("GUARDIAN_WEBHOOK_SECRET", ""),
                                 queue, set(args.allowed_repo))
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
    except (ValueError, GitHubError, AnthropicAPIError, BudgetExceeded, OSError) as exc:
        print(f"guardian: {exc}", file=sys.stderr)
        return 2
