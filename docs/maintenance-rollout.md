# Guardian issue and PR maintenance — staged rollout

Adds a second, idempotent SQLite queue. It does not start a maintenance worker
or grant any additional App permissions in production.

## Capabilities

- Triage new and updated issues/PRs with a curated per-repo label taxonomy.
  Deterministic rules are free. Haiku 5.5 is opt-in only when classification
  is ambiguous, via the same budget ledger and Console workspace limit.
- Detect exact issue duplicates (same normalized title and substantive body)
  and PR duplicates (same HEAD SHA and base repository and branch).
  Semantic similarity alone can never trigger automatic closure.
- Duplicate closure is disabled unless --enable-closure is explicitly set.
  Guardian comments with a reference before closing.
- Authenticated /guardian help, status, review, label, duplicate and operation
  requests. Elevated commands require both GitHub collaborator permission
  and a configured maintainer numeric user-ID allowlist.
- Guard against comment edits and cross-repository event replay by fetching
  current GitHub state, checking signed and allowlisted events, and rejecting
  multiline or shell-style commands.
- Fix and conflict operation requests are enqueued separately. This PR
  does NOT execute changes. A later isolated executor needs Contents:write.

## Permissions required

- Issues: Read and write for labels, issue comments and lifecycle
- Pull requests: Read and write (existing review, PR closure, comments)
- Metadata: Read
- Existing Contents: Read; Checks: Read; Commit statuses: Read
- Webhook events: Issues, Pull request, Issue comment, plus existing
  Check run and Check suite

Check-run action buttons need Checks:write and explicit setup. Code fixes
need Contents:write and an isolated executor. Do not grant elevated
permissions ahead of those separately tested integrations.

## Deployment on LXC 111 after merge

1. Preserve all existing backups, the SQLite database, the signing key and
   /etc/guardian environment files. Back up the database through normal
   Proxmox/Guardian backup procedures.
2. Pull main into /opt/guardian, run offline tests, restart webhook service.
3. Update GitHub App permissions/events, reapprove installation if requested.
4. Provision curated labels per pilot repository with the setup-labels
   command and an explicit --confirm-write.
5. Set GUARDIAN_MAINTAINER_IDS in /etc/guardian/maintenance.env to trusted
   numeric GitHub user IDs; do not use names or author_association.
6. Test one maintenance event manually:
   python -m nodalia_guardian maintain --db /var/lib/guardian/deliveries.sqlite3
   --enable-triage --confirm-write
   It processes exactly one event; no Haiku call by default.
7. Install a separate guardian-maintenance systemd timer/service with the
   same sandbox restrictions as guardian-worker. Never grant filesystem
   access outside /var/lib/guardian.
8. Only after verifying a 20 USD Console workspace cap, opt in to
   --enable-haiku --confirm-paid, then --enable-closure if separately audited.

## Security behavior

Every delivery is claimed once and finalized done/failed/skipped. No retry
after uncertain writes. A model cannot close an item by itself. A
low-confidence classification produces needs-triage; a missing label is
skipped rather than silently created at runtime. Code and secrets never
execute on Proxmox. Existing PR review automation remains independent.

Custom executable HTML buttons cannot be embedded in GitHub comments.
Native Apply suggestion is supported by inline review threads. Other
actions use signed, permission-checked /guardian commands or
GitHub Check-run requested_actions (separate Checks:write permission).
