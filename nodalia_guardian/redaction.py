"""Best-effort masking before sending GitHub metadata to an external model.

Not a substitute for GitHub permissions or a classification policy.
"""

import re

_RULES = (
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|authorization|password|secret)\b\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}\b"),
)


def redact(text: object) -> str:
    output = str(text) if text is not None else ""
    for idx, pattern in enumerate(_RULES):
        if idx == 3:
            output = pattern.sub(r"\1[REDACTED]", output)
        else:
            output = pattern.sub("[REDACTED]", output)
    return output


def limit_text(value: object, max_chars: int) -> str:
    s = redact(value)
    return s if len(s) <= max_chars else s[:max_chars] + "\n[TRUNCATED]"
