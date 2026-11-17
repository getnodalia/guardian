# Guardian — Professional PR and issue maintenance roadmap

This document distinguishes what is implemented from future capabilities.
The GitHub App uses its existing installation permissions; writing here
does not silently grant permissions or activate extra paid model calls.

## Delivered in this PR: professional code reviews

- One concise Markdown review summary with CI snapshot and collapsible
  limitations instead of a long repeated list.
- One distinct GitHub inline thread per verified changed line, bounded to
  12 comments per review.
- Optional **native GitHub suggested changes** for a single replacement
  line. The human clicks **Apply suggestion** in the GitHub review.
- Check the path and line against the actual unified diff; ambiguous
  findings stay visible in the review summary.
- Never publish a review after the PR closes, becomes draft, or changes
  head/base during model analysis.
- Existing deterministic gates remain authoritative. Automatic approval
  remains disabled on the production LXC.
- No automatic branches, Git changes, labels, closure or issue processing
  are enabled by this PR.

## Planned next: issue and PR triage (Haiku 5.5 only when needed)

1. Deterministic rules first: conventional filenames, labels, authorship,
   category templates and existing exact duplicates. Zero model cost.
2. Claude Haiku 5.5 for ambiguous classification only, with a constrained
   schema for category, labels, confidence and linked issue/PR candidates.
   The model cannot invent a target number: the server verifies that an
   alleged duplicate exists in the same repository and really matches.
3. Auto-label trusted, safe classifications; confidence thresholds and an
   allowlist of existing repo labels prevent creating uncontrolled labels.
4. Post concise linked duplicate explanations. Close low-risk duplicate
   issues only when provenance is verified and policy explicitly permits
   closure; prefer flagging PR duplicates for human approval.
5. Handle issue edits, reopening, changes to labels, new PRs and new
   commits with idempotency and rate limits. Never label bot-authored
   events in a loop.

Recommended initial labels:
- type:bug, type:feature, type:documentation, type:refactor
- area:security, area:ci, area:ui, area:backend
- priority:critical, priority:high, priority:normal, priority:low
- duplicate, needs-triage, needs-tests, blocked, guardian:reviewed

Use per-repository configuration; do not impose unrelated labels globally.

## Planned next: authorized comment commands

Proposed syntax:

- /guardian help
- /guardian review
- /guardian fix
- /guardian fix <review-comment-id>
- /guardian resolve-conflicts
- /guardian duplicate #123
- /guardian label type:bug
- /guardian status

Important: arbitrary interactive HTML buttons cannot be embedded in GitHub
Markdown comments. Native suggested changes provide GitHub's Apply
suggestion control. For non-inline edits use authorized slash commands.

Security gate, before doing anything with write access:
- Verify the signed GitHub webhook and installation ID.
- Verify repository allowlist, issue/PR state, exact sender user ID and
  collaborator permission via GitHub API. Also maintain a separate
  privileged-maintainer allowlist for code writes, closure and commands.
  Do not treat text like @maintainer or author_association as authorization.
- Bind every command to a PR number and immutable head SHA.
- Execute a command at most once per GitHub delivery + comment ID +
  command, using an audit log and explicit state machine.
- Ignore bot comments and untrusted edits to existing commands.
- Rate-limit, budget-check and timeout each operation.
- Do not allow a model prompt or PR description to invoke commands.
- Refuse secrets, private source, non-Nodalia repositories and unknown
  permissions; support read-only simulation before enabling writes.

## Planned next: code fixing and merge-conflict resolution

- Route reproducible code analysis and patch generation to Sonnet 5.5.
- Never execute untrusted PR code in the homelab, production LXC 111,
  or on Proxmox. Use ephemeral isolated GitHub-hosted free runners
  with constrained permissions and no production secrets.
- Apply minimal patches to a **bot-owned fix branch**, not to main;
  never bypass branch rules, required checks or reviews.
- For contributor branches or forks, avoid force pushes. Propose fixes
  as a PR targeting the contributor branch when permissions allow.
- To fix a merge conflict, fetch exact base/head SHAs, merge in isolation,
  present a diff, run all relevant CI and publish only the authorized
  changes. Ambiguous semantic conflicts require human review.
- Never approve Guardian's own fixes, merge automatically or override
  a human review. Only human maintainers can merge after required CI.
- Keep a durable audit trail (requester, command, base/head SHA,
  commits, validation status and token cost).

## Permissions roadmap

Currently in use: Metadata read; Contents read; Pull requests read/write;
Checks read; Commit statuses read; Pull request, Check run, Check suite
webhook events.

Future separate grants requiring GitHub App settings and installation
approval:
- Issues read/write for issue lifecycle, labels and issue comments.
- Issue comment (and optionally pull request review comment) event for
  commands and inline review replies.
- Contents write **only** for separately authorized code fixes.
- Workflows write is not required for patch creation. Avoid granting
  Actions administration or bypass privileges.

Each write-capability increment must have a separate regression test,
a permission audit, a staging run and an explicit production enable
switch in /etc/guardian, not a default-on setting.

## Model cost and operations

- Haiku 5.5 is suited to high-volume classification. Anthropic lists
  up-to-100K input pricing of $0.10 / million input tokens and $0.50 /
  million output tokens (as of 2026-11-17); longer prompts use higher tiers.
- Sonnet 5.5 remains the default for deep code reviews and proposed fixes.
- Implement accurate Haiku accounting (fractional micro-dollar rounding,
  >100K tier and cache usage) before enabling paid Haiku traffic.
- Use the same budget ledger and the Claude Console workspace hard cap,
  with per-repository daily request limits and deduplication.
- The existing LXC 111 worker and webhook receiver remain unchanged
  until the staged features are merged, deployed and validated.

## Rollout

1. Verify this PR's unit tests and GitHub checks; merge after human review.
2. Update LXC 111 and review a **new** non-draft PR with inline findings;
   test suggestion applicability before relying on the format.
3. Add low-risk labeling, triage and commands in separate audited PRs.
4. Enable Sonnet fixing and conflict-resolution only after an isolated
   executor and bot-specific permissions are independently tested.
