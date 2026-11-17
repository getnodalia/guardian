# Nodalia Guardian GitHub App: identity and installation

Guardian must act as its own GitHub App, not as a maintainer's personal account.
Do not create a GitHub user account to impersonate the application.

## Official avatar — mandatory

**Use the exact Nodalia logo image supplied by the project owner for Guardian.**
Do not generate, redraw, recolor, add text, or replace it with a stock icon.
GitHub App avatars are configured in GitHub's **Display information** settings;
committing a logo to this repository does **not** change the bot's avatar.

1. Save the owner's original image locally as a PNG or JPG (or GIF) under 1 MB. GitHub recommends 200 × 200 pixels; if resizing is needed, keep the original proportions and colors.
2. In GitHub, open `getnodalia` → **Settings** → **Developer settings** → **GitHub Apps** → **Nodalia Guardian** → **Edit**.
3. Under **Display information**, select **Upload a logo** and choose that original image.
4. Use the crop UI only if necessary to fit the full mark; select **Set new avatar**.
5. Verify the image appears beside a Guardian review on a test PR before enabling unattended reviews.

The bot will appear with the GitHub App's actual actor name, conventionally
`nodalia-guardian[bot]` when that app slug is available. The avatar is GitHub-hosted
and GitHub adds its own badge treatment; Guardian cannot override the GitHub UI.

GitHub official instructions: https://docs.github.com/es/apps/creating-github-apps/registering-a-github-app/creating-a-custom-badge-for-your-github-app

## Register and scope the GitHub App

Register the app under the **getnodalia organization**, selecting the app name
`Nodalia Guardian` if available. During the pilot, install it only on `getnodalia/agora`.
Do not allow installation on arbitrary repositories or external organizations.

Use this **minimum permissions** profile for the review-only pilot:

| Repository permission | Access | Purpose |
| --- | --- | --- |
| Metadata | Read | Identify repo and installation |
| Contents | Read | Inspect source when later needed |
| Pull requests | Read and write | Read diffs and publish review events |
| Checks | Read | Inspect workflow check runs |
| Commit statuses | Read | Inspect status checks |

Subscribe to **Pull request**, **Check run**, and **Check suite** webhook events.
A future autofix mode will require further permissions and a separate security review;
**do not grant Contents: write now**.

Configure a HTTPS webhook endpoint such as `https://<YOUR-HOST>/webhook` only after
it exists and is TLS-protected. Generate an unpredictable webhook secret, then store
it as `GUARDIAN_WEBHOOK_SECRET` in your deployment's secret manager.

Set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, and `ANTHROPIC_API_KEY` **outside
source control**. Limit the private key's OS permissions to the service account.
Keep Claude Console auto-reload disabled and verify your workspace spend limit.

## Run safely before first publication

Start with offline checks:

```bash
python3 -m compileall -q nodalia_guardian tests
python3 -m unittest discover -s tests -v
python3 -m nodalia_guardian scan --repo getnodalia/agora --pr 18
```

To test the listener after configuring the webhook secret:

```bash
python3 -m nodalia_guardian serve --host 127.0.0.1 --port 8787 \
  --allowed-repo getnodalia/agora
```

This listener only **queues** signed GitHub webhook events. It does not contact Claude,
post comments or approve PRs by itself. Run the worker separately and only once your
GitHub App permissions, authenticated HTTPS delivery, API credits and budget controls
are verified. The worker makes billable API requests and can post reviews.

```bash
python3 -m nodalia_guardian worker \
  --required-check 'YOUR_EXACT_REQUIRED_CHECK_NAME' \
  --monthly-budget 20 --confirm-paid-publishing
```

For the pilot, leave `--auto-approve` **unset**. An explicit required-check allowlist,
fully available patches and deterministic gates are needed before allowing approvals.
This local reservation ledger is not a provider-enforced monthly spending limit.

## Important boundary

A review **APPROVE** from Guardian does not mean GitHub will count it as an independent
human approval under a repository's branch-protection or ruleset configuration.
Guardian cannot approve its own corrections; never auto-merge based on an AI verdict.
