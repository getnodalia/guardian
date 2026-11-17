# Guardian — production acceptance checklist

This checklist is for the existing Debian 13 LXC 111 on Proxmox.
**Nothing below is performed automatically when a PR merges.**

## Branch integration

- [ ] Confirm PR #4 (inline comments and native suggestions) is merged.
- [ ] Confirm PR #5 (issues, Haiku, labels, duplicate handling, commands)
      is merged.
- [ ] Merge this PR #6 **only after** tests pass against latest main
      and human review approves the new GitHub write capabilities.
- [ ] Confirm checks pass on the merge commit. Never bypass main rules.

## API and permissions

- [ ] Installation scoped first to getnodalia/agora; expand later through
      GitHub App Manage installations after testing costs and permissions.
- [ ] Permissions: Metadata read, Contents read/write, Pull requests
      read/write, Issues read/write, Checks read/write, Commit statuses read.
- [ ] Events: Pull request, Issues, Issue comment, Check run, Check suite.
- [ ] Accept new GitHub App permission requests for the installation.
- [ ] Claude Console workspace has a real $20/month cap; auto-reload off.
- [ ] No Blacksmith runners, paid infrastructure, trials or local PC runners.

## Preserve production state

- [ ] Preserve existing Proxmox and Guardian backups.
- [ ] Do NOT replace /etc/guardian/private-key.pem or Claude keys.
- [ ] Do NOT delete or recreate /var/lib/guardian/*.sqlite3.
- [ ] git -C /opt/guardian pull --ff-only origin main
- [ ] /opt/guardian/.venv/bin/python -m unittest discover -s /opt/guardian/tests -v
- [ ] bash /opt/guardian/deploy/install-systemd.sh
- [ ] Set GUARDIAN_MAINTAINER_IDS=241863489 (or desired confirmed
      GitHub numeric IDs) in /etc/guardian/maintenance.env, owner root,
      group guardian, permission 640.
- [ ] Confirm all four installed new units with systemd-analyze verify.

## Pilot with no paid requests

- [ ] Provision curated labels in getnodalia/agora via:
      runuser -u guardian -- env HOME=/var/lib/guardian
      GITHUB_APP_ID=5794704
      GITHUB_APP_PRIVATE_KEY_PATH=/etc/guardian/private-key.pem
      /opt/guardian/.venv/bin/python -m nodalia_guardian setup-labels
      --repo getnodalia/agora --installation-id 182801897 --confirm-write
- [ ] Enable guardian-maintenance.timer with only --enable-triage and
      --confirm-write (the installed service defaults to no paid Haiku
      and no automatic closure).
- [ ] Open one test issue and PR; check existing labels, SQLite status
      and the absence of unauthorized comments.
- [ ] Test /guardian help and /guardian status from a collaborator.
- [ ] Test an unprivileged comment cannot close or modify a branch.
- [ ] Test /guardian review on a ready PR and verify CI gates.
- [ ] Inspect existing Guardian PR review de-duplication.

## Paid triage, closure, code fixes and buttons

- [ ] Use the provided guardian-maintenance-haiku.conf.example drop-in
      only after the Claude Console cap is confirmed.
- [ ] Enable exact duplicate closure with a separate audited drop-in
      (avoid accidentally overriding Haiku and closure ExecStart flags).
- [ ] Enable guardian-fixes.timer only when Contents:write was granted
      and an authorized maintainer wants draft correction PR proposals.
- [ ] Keep explicit GUARDIAN_MAINTAINER_IDS allowlist.
- [ ] To enable Check-run buttons, set GUARDIAN_ENABLE_CHECK_ACTIONS=1
      in maintenance.env and install the optional
      guardian-worker-maintenance.conf.example systemd drop-in so the
      existing PR review worker receives the feature flag.
- [ ] Check "Propose fix" and "Resolve conflicts" appear on the Checks
      tab after a new review. Only authorized admins may trigger them.
- [ ] Verify a fix creates a draft PR with a guardian/fix-pr branch.
- [ ] Verify a merge resolution creates a two-parent commit and
      draft PR, not a push to the original feature branch.
- [ ] Verify test failures or ambiguous model output block changes,
      and no PR is auto-merged or auto-approved by its author.
- [ ] Monitor: journalctl -u guardian-worker.service -u
      guardian-maintenance.service -u guardian-fixes.service -n 100
      --no-pager; compare with Claude Console usage.

## Expand to the Nodalia organization

- [ ] Install the GitHub App on additional intended getnodalia repos.
- [ ] Run sync-repos in LXC 111 as root (it writes only
      /etc/guardian/repos.json with root:guardian 640, excluding Guardian
      itself).
- [ ] Install the optional guardian-webhook-allowlist.conf.example
      drop-in, reload systemd, restart just guardian-webhook.service,
      confirm /healthz still returns ok.
- [ ] Verify each repository's required CI and labels; do not force
      a single global required-check list on different stacks.
- [ ] For sensitive/private repositories: model traffic is blocked
      until an independent data-protection policy explicitly allows it.
      Label-only deterministic triage remains possible.

## Emergency stop

The existing reviewer and webhook can remain online while the new
features are stopped:

    systemctl disable --now guardian-fixes.timer guardian-maintenance.timer

Do not remove the database or previous backups. Do not run GitHub Actions
with privileged secrets on untrusted PR code. All generated suggestions
and merge resolutions are **proposals** requiring human validation.
