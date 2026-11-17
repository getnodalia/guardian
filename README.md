# Nodalia Guardian

**An engineering review GitHub App for the Nodalia ecosystem.**

Guardian has a **free, read-only `scan` command** for pull requests and CI, and an
**opt-in GitHub App reviewer** that reads bounded diffs, requests a Claude analysis and can
publish `COMMENT`, `REQUEST_CHANGES` or a gated `APPROVE` review. The latter is not enabled
by default: it requires explicit flags, real GitHub App credentials and API credits.

The webhook receiver verifies signatures and stores events in a SQLite queue. A **separate,
explicitly activated** worker performs reviews; receiving a webhook alone never spends
credits. Guardian currently **does not correct code, push commits, merge PRs, deploy, or
execute repository tests**. Autonomous corrections remain a planned, separately permissioned
capability. CI status is only a snapshot, not proof that branch protection has passed.

Guardian uses **the exact official Nodalia logo chosen by the owner as its GitHub App avatar**,
not a generated graphic. To set up the app, install only the needed permissions and upload the
avatar in GitHub, see [GitHub App setup](docs/github-app-setup.md).

## Professional reviews (first phase)

Guardian can now publish a **concise review summary** and separate **inline
comments on verified added lines** of the PR diff. If a finding has a valid
one-line safe replacement outside sensitive paths, GitHub displays its native
**Apply suggestion** control. Unanchored findings remain in the summary;
comments never invent diff locations.

The automatic worker still uses the existing read-only+review permissions.
It cannot edit branches, assign labels, process issues or close duplicate PRs.
Those capabilities require separate staging and GitHub App permission grants.

See [the professional maintenance roadmap](docs/maintenance-automation.md)
for planned Haiku-assisted triage, labels, slash commands, duplicate handling
and isolated Sonnet-generated corrections.

See [`AGENTS.md`](AGENTS.md) and [Nodalia Foundation](https://github.com/getnodalia/foundation).


## Requirements

- Python 3.11 or later; standard library only at runtime
- Internet connectivity for GitHub API; `GITHUB_TOKEN` optional for public repositories
- `ANTHROPIC_API_KEY` from your *linked Claude Console organization*, only if using `--ai`
- Before paid use, an Anthropic Console workspace with a spending limit (recommend **$20/month**
  for the pilot); disable auto-reload and confirm the Max credits have been claimed

## Quick start

```bash
# No pip install or dependencies required for a local checkout.
python3 -m unittest discover -s tests -v
python3 -m nodalia_guardian scan --repo getnodalia/agora --pr 18 \
  --output reports/agora-18.md
```

The report is generated from GitHub live data; no paid AI call is made.

To add Claude analysis:

```bash
# Do not paste or commit the API key. Supply it through your shell's secret manager.
export ANTHROPIC_API_KEY='YOUR_KEY_FROM_LINKED_CLAUDE_CONSOLE'
python3 -m nodalia_guardian scan --repo getnodalia/agora --pr 18 \
  --ai --model claude-sonnet-5-5 --monthly-budget 20 \
  --output reports/agora-18-ai.md
```

Do not commit `.env` or reports with internal information. Use `--json` for structured output.
Check local spend with `python3 -m nodalia_guardian budget`.

## GitHub credentials

The GitHub API can inspect public repositories without a token, subject to rate limiting. For
higher rate limits or private PRs, set `GITHUB_TOKEN` to a fine-grained token with only the
required repository access. For this MVP use **Metadata: read**, **Pull requests: read**,
**Checks: read** and **Commit statuses: read**; do not grant write privileges. Avoid sending
private PR metadata to Anthropic until approved by the code owners.

## What Guardian reads

1. `GET /repos/{owner}/{repo}/pulls/{number}`
2. `GET /repos/{owner}/{repo}/pulls/{number}/files?per_page=100`
3. `GET /repos/{owner}/{repo}/commits/{head_sha}/check-runs?per_page=100`
4. `GET /repos/{owner}/{repo}/commits/{head_sha}/status`

It examines **the PR head commit**, not the synthetic merge commit GitHub may use to run PR CI.
The first 100 changed files and checks are included; a truncation warning appears where known.
The free `scan` does not send diffs to Claude. Opt-in `review --ai` sends
bounded **public** changed-line patches (never full CI logs or private source)
so Claude can identify issues in changed code. An adversary can control PR text
and check output: Guardian treats these as **untrusted data** and does not execute instructions
found inside them. Redaction is best-effort, not a guarantee against secret leakage.

## Cost controls (important)

- The initial AI model is `claude-sonnet-5-5` (published base rates at 2026-11-17:
  $2 per 1M input tokens, $10 per 1M output tokens).
- `--ai` is required to initiate **one** billable Messages API call.
- `--max-output-tokens 1000` by default, up to 4096.
- A SQLite ledger at `~/.local/state/nodalia-guardian/usage.sqlite3` estimates cost and reserves
  generously before each call. Failed/uncertain requests keep their reservation, since network
  timeouts can still incur charges. The ledger is per host and resets its period each UTC month.
- This ledger **cannot guarantee an actual account-wide hard cap** (e.g. if you run in multiple
  devices, ephemeral GitHub runners, price changes or other API clients). Set the actual monthly
  workspace spending limit in **Claude Console**, inspect the usage there and disable credit
  auto-reload. Never depend on the ledger alone.
- The tool refuses a local requested monthly budget above $100, but your linked Console
  organization may have other users or workloads consuming the same credits.
- The API key and token are never committed or written into reports by the tool.

Claude Max monthly credits pay for direct Claude API use in the linked organization; they do not
pay for interactive Claude Code. Confirm your credits and billing status before running with AI.

## Security and permissions

- Read-only scan by default; a **separately authenticated GitHub App** can post reviews only
  through an explicit `review --ai --publish` or opt-in worker command.
- No autonomous code changes, commits, merging, or publishing releases.
- The GitHub-hosted `Guardian · Offline tests` workflow runs compilation and
offline unit tests on PRs and main. It has read-only permissions, uses a SHA-
pinned first-party checkout action, and never uses Claude credentials.
- Explicit least-privilege GitHub Actions token and commit-pinned checkout action.
- Never turn off CI to obtain a green status. Unknown/absent checks are not treated as passed.
- Do not provide production tokens to untrusted forks or PR-triggered workflows.

## Roadmap

1. Register the branded GitHub App and pilot a **single** signed webhook delivery and review
   in Ágora, after confirming credits, costs and approval semantics.
2. Improve handling of CI pagination, check-run provenance, in-progress workflow states and
   bounded annotations, without leaking secrets or private source.
3. Add opt-in correction PRs with separately authorized Contents: write, isolated execution,
   explicit branch selection and verification. Do not let Guardian approve its own commits.
4. Add more advanced review routing and diagnostics after evaluating actual code-review quality.
5. Add scheduling only after authoritative provider spend limits and safe retry semantics.

## Development

```bash
python3 -m compileall -q nodalia_guardian tests
python3 -m unittest discover -s tests -v
python3 -m nodalia_guardian --help
```

No cloud resources, paid runners or vendor lock-in are required to run the read-only MVP.
