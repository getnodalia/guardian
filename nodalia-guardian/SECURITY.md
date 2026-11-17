# Security

Report vulnerabilities privately through the repository's GitHub Security advisory interface
once this project is hosted at `getnodalia/guardian`. Do not publish exploitable findings in issues.

Guardian is read-only by design. It sends a bounded summary of a PR's **title, body, file names and
check descriptions** to Claude only with explicit `--ai`. Do not use `--ai` on confidential/private
work without the organization's approval. Text redaction cannot guarantee secrets are removed.

Never store GitHub/Anthropic keys in the repository or in PR comments. Use least-privilege tokens,
managed secrets and Claude Console workspace limits. Keep credits auto-reload disabled to avoid
unexpected bills. Review the Foundation security standard for additional requirements.
