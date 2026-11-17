# Guardian — proposed fixes, merge resolution and Check-run buttons

This feature is **off by default**. Guardian's existing Sonnet PR-review worker
continues as before until the new PRs are merged and deployed.

## Permissions to approve in GitHub App settings

- Pull requests: Read & write (already in the pilot; creates draft fix PRs)
- Contents: **Read & write** (new: only bot-owned branches are written)
- Checks: **Read & write** (new: action buttons in the PR Checks tab)
- Issues: Read & write (from maintenance triage PR)
- Metadata: Read
- Events: Pull request, Check run, Check suite, Issues, Issue comment

If app permissions change, the installation administrator may need to accept
them. Do not grant Actions:write, Workflows:write, administrator, secrets,
or branch protection bypass permissions. The correction worker does not run
untrusted repository code in your Proxmox LXC or use paid runners.

## Available commands on PR conversations

- /guardian help — show supported commands
- /guardian status — show item status
- /guardian review — enqueue a normal Sonnet review after required CI
- /guardian label type:bug — add an existing allowlisted label
- /guardian duplicate #123 — authorized maintainer closure with reference
- /guardian fix — propose a correction for the most relevant current
  Guardian inline review comment
- /guardian fix 12345 — target one specific current inline review comment ID
- /guardian resolve-conflicts — propose a 3-way merge in a draft PR

Commands require a repo collaborator with write access. Commands changing
source or closing work additionally require a numeric user ID listed in
GUARDIAN_MAINTAINER_IDS and maintain/admin repository permission.

GitHub does not allow arbitrary executable buttons inside Markdown comments.
The inline suggestion block uses native **Apply suggestion**. Guardian can
add **Propose fix** and **Resolve conflicts** buttons to the PR **Checks tab**
via a completed GitHub Check Run, which sends a signed
check_run.requested_action event.

## How the code fixer works

1. Authorized user creates a command or requests an action.
2. Guardian stores an immutable PR head SHA in SQLite, once per operation.
3. The operation worker re-verifies user ID, GitHub permissions, PR status
   and head SHA immediately before using Sonnet.
4. Fetches a Guardian-authored inline review finding for that same SHA.
   Does not trust arbitrary comments, paths or code from PR authors.
5. Reads one bounded, UTF-8 source file from GitHub. Rejects secret markers,
   protected paths, symlinks and oversized content.
6. Sonnet proposes an exact substring replacement with structured JSON.
   Guardian verifies the old substring occurs exactly once, then rechecks SHA.
7. Creates a fresh guardian/fix-prN-SHA branch, writes only that branch,
   and opens a **draft PR targeting the original feature branch**. The user
   reviews/merges it; no automatic approval or merge is permitted.

No code tests are executed by Guardian. Repository CI can run the proposed
draft PR under its own free, ordinary GitHub-hosted workflow policy.

## How the 3-way merge resolver works

1. Fetch head SHA, base SHA, and common ancestor with the GitHub compare API.
2. Compare the Git *trees* in all three commits. Preserve HEAD-only edits,
   copy BASE-only edits and identify genuine conflicting paths.
3. Resolve at most two small text conflicts per request with Sonnet; skip
   symlinks/submodules, deleted-vs-modified files, protected/unsupported
   paths, large files or ambiguous resolutions. Do not choose ours/theirs
   silently.
4. Construct a new Git tree with merged content, then create a merge commit
   with **both parents** [head_sha, base_sha].
5. Put that commit on a new guardian/resolve-prN-SHA branch. Open a
   reviewable **draft PR targeting the original contributor feature branch**.

This allows a human to review the suggested conflict resolution and merge
the draft PR through the normal branch checks. Neither main nor the user's
original branch is pushed by Guardian.

## Safe deployment

1. Merge the separate UI PR, then the maintenance PR, then this fix PR,
   after all required CI checks and human review pass.
2. Update /opt/guardian in LXC 111, run its offline tests and verify the
   webhook/worker service still works; do not touch existing SQLite or keys.
3. Reapprove the GitHub App's new permissions and event subscriptions.
4. Create /etc/guardian/maintenance.env with
   GUARDIAN_MAINTAINER_IDS=YOUR_NUMERIC_GITHUB_USER_ID and
   GUARDIAN_ENABLE_CHECK_ACTIONS=0. Owner root, group guardian, mode 640.
5. Install units with deploy/install-systemd.sh (does NOT enable timers).
6. Test a single triage event with the maintain CLI; provision labels through
   setup-labels; confirm safe GitHub writes before enabling its timer.
7. Before enabling the paid fix worker, confirm the Claude Console hard
   spend limit and disable automatic credit reload. Its tokens share the
   local $20/month ledger with the existing review worker.
8. Only after a review of the new Checks:write permission set
   GUARDIAN_ENABLE_CHECK_ACTIONS=1 and create Check-run buttons.
9. Enable the new maintenance/fix timers explicitly, one at a time.
   Monitor systemd, local SQLite status and Claude Console usage.
10. To stop code changes immediately without interrupting the existing
    Guardian reviewer:
    systemctl disable --now guardian-fixes.timer

## Limitations

- No model is allowed to approve Guardian-authored commits.
- Exact duplicates can be auto-closed after --enable-closure; semantic
  duplicates require human confirmation.
- The fixer proposes **one small, exact correction per command**. Larger
  changes, unavailable inline findings, unsupported files, private code,
  conflicts with more than two files and unverifiable merges are refused.
- A PR from a fork cannot be patched this way, as Guardian never pushes to
  an external contributor's repository.
- GitHub Check Run buttons appear in the Checks tab, not arbitrary Markdown.
- If GitHub or Anthropic fails after a paid request, the operation is not
  automatically retried to avoid duplicate charges or side effects.
