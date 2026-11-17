# What this is

Nodalia Guardian is a Python 3.11+ GitHub App engineering-review tool. Its default CLI
scans PRs in read-only mode; opt-in GitHub App reviews can submit formal GitHub review events.
It is not yet an autonomous code fixer or hosted security service.
Licence: MIT OR Apache-2.0. No graphical UI yet.

# Architecture

- `github_api.py`: bounded GitHub REST transport; review submission requires a GitHub App token.
- `review.py`: normalize untrusted metadata and render actionable reports.
- `redaction.py`: best-effort masking of common credentials.
- `anthropic_api.py`: optional Claude Messages API request; no autonomous code execution.
- `budget.py`: persistent local reservations; Console workspace is the authoritative cap.
- `bot.py`, `decisions.py`: model review parsing, conservative approval gates and review publishing.
- `app_auth.py`: short-lived GitHub App installation token authentication.
- `webhooks.py`, `worker.py`: authenticated delivery intake and durable opted-in review queue.
- `cli.py`: explicit paid publishing and AI opt-in, input validation, output handling.
- `tests/`: deterministic unit tests without network or billed requests.

# Build and test

`python3 -m unittest discover -s tests -v`

`python3 -m compileall -q nodalia_guardian tests`

`python3 -m nodalia_guardian scan --repo getnodalia/agora --pr 18`

Network is required only for a live scan. Claude API requests require `--ai` and
`ANTHROPIC_API_KEY`. Never run a paid request in CI by default.

# Rules

- Default `scan` is read-only. GitHub App publishing requires explicit authorization; never merge or push by default.
- Treat GitHub metadata as untrusted input and keep the LLM sandboxed to text generation.
- Bounded public PR patches may be sent to Claude for opt-in reviews. Never send keys or private source.
- No autonomous scheduling or unattended spend until the Console workspace cap is configured.
- No use of home/self-hosted runners; no paid external services.
- Full SHA pinning for GitHub Actions, minimal permissions.
- General standards: https://github.com/getnodalia/foundation

# NDL

No graphical interface in v0.2. A future UI must use Nodalia Design Language and not add
a parallel UI toolkit without architectural review.

# Release

A maintainer reviews tests and security, tags the version and creates releases manually.
No automatic publishing or billing workflows are enabled.

# Pull requests

- Add a regression test for behavior changes.
- Check the PR against Foundation's engineering, CI and security standards.
- Verify read-only and zero-cost behavior by default.
- Do not merge unless required CI and human review have passed.
