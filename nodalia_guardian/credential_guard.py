"""Conservative preflight against transmitting accidentally committed credentials."""

import re

_CREDENTIALS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{24,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{24,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?im)^\s*(?:ANTHROPIC_API_KEY|AWS_SECRET_ACCESS_KEY|"
               r"GITHUB_TOKEN|CLOUDFLARE_API_TOKEN)\s*=\s*\S+"),
]


def has_credential_markers(text: str) -> bool:
    return isinstance(text, str) and any(p.search(text) for p in _CREDENTIALS)
