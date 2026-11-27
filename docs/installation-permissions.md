# Diagnose GitHub App installation permissions (read-only)

Use `nodalia-guardian doctor` when the App is subscribed to `issues` but
the maintenance queue has no issue events. It performs GET requests authenticated
with a short-lived App JWT; it **does not** print JWTs, installation tokens,
webhook payloads, private-key content, issue bodies, or Anthropic data.

## Run on the installed host

Using the existing virtual environment and service account:

```sh
runuser -u guardian -- env HOME=/var/lib/guardian \
  GITHUB_APP_ID=5794704 \
  GITHUB_APP_PRIVATE_KEY_PATH=/etc/guardian/private-key.pem \
  /opt/guardian/.venv/bin/python -m nodalia_guardian doctor \
  --installation-id 182801897 \
  --db /var/lib/guardian/deliveries.sqlite3 \
  --repo getnodalia/agora --issue 29
```

The command reads `/app`, `/app/installations/{id}`, and a **bounded**
sample of recent webhook deliveries. When a database path and an issue/PR are
supplied, it opens the **existing** SQLite file in read-only mode and shows
the matching maintenance event statuses. It never creates or rebuilds the file.

### Interpret the output

- `app_issues_permission: write` plus
  `installation_issues_permission: none`: permission mismatch. GitHub may
  be awaiting organization owner approval for new permissions. Verify this
  in the organization's GitHub Apps installation page; do **not** assume
  approval is pending without checking the GitHub UI.
- `subscribed_to_issues: false`: subscribe to **Issues** in the GitHub
  App's **Permissions & events** screen.
- `issues_ready: true`: the advertised permission and subscription gates
  are satisfied, but this alone does **not** prove delivery or processing.
- `delivery_sample.issues: 0`: no issue events in the latest 100 sampled
  deliveries; older events might exist.
- `queue.matching_events: []`: no currently stored maintenance events for
  that issue/PR. Check the GitHub delivery details and allowlist.
- A matching `failed` maintenance event means a worker has attempted it:
  diagnose that failure before triggering additional paid work.

## Approval and end-to-end verification

1. As an organization owner, open `getnodalia` **Settings** → **Third-party
   Access** → **GitHub Apps** → **Nodalia Guardian** → **Configure**.
2. Review any pending request for repository **Issues: Read and write**.
   Approve the requested change only after reviewing its scope. Do not
   uninstall or reinstall the App as a shortcut.
3. Confirm the **Issues** webhook event is subscribed in the App
   **Permissions & events** settings.
4. Re-run `doctor` until the installation reports Issues write access.
5. Edit the test issue, inspect the new delivery in GitHub App **Advanced**
   delivery logs, and re-run `doctor` to confirm a new
   `maintenance_events` row. Check status and labels after the
   maintenance timer runs.
6. Repeat with a test PR. No automatic merges or GitHub permissions
   beyond the approved minimum are necessary.

GitHub docs:
- https://docs.github.com/en/apps/using-github-apps/approving-updated-permissions-for-a-github-app
- https://docs.github.com/en/apps/using-github-apps/reviewing-and-modifying-installed-github-apps
