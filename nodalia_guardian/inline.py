"""Build GitHub review comments only on verified added lines of a PR diff.

Claude-supplied paths and line numbers are untrusted. Never construct a GitHub
inline comment from a claimed location unless the corresponding patch contains
that exact new-side line. Suggestions remain human-applied GitHub UI actions.
"""

import re

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# Suggestions in sensitive files require an explicit fix workflow and human review.
_SENSITIVE = re.compile(r"(?:^|/)(?:\.github|auth|oauth|crypto|cipher|mfa|security|secret|token|keys?|deploy|infra|migration)(?:[._/-]|$)", re.IGNORECASE)


def added_lines(patch: str) -> dict[int, str]:
    """Return {new_file_line: source_line} for + lines in a unified patch."""
    if not isinstance(patch, str):
        return {}
    line_number = None
    lines = {}
    for item in patch.splitlines():
        match = _HUNK.match(item)
        if match:
            line_number = int(match.group(1))
        elif line_number is None:
            continue
        elif item.startswith("+"):
            lines[line_number] = item[1:]
            line_number += 1
        elif item.startswith(" "):
            line_number += 1
        elif item.startswith("-") or item.startswith("\\"):
            continue
    return lines


def build_inline_comments(findings: list[dict], files: list[dict], *,
                          limit: int = 12) -> tuple[list[dict], set[int]]:
    """Return GitHub review comments and the indices placed inline.

    The caller retains the non-inline findings in the review summary. Missing,
    untrusted, truncated or invalid patch positions never become inline posts.
    """
    patches = {}
    for file in files:
        path, patch = file.get("filename"), file.get("patch")
        if isinstance(path, str) and isinstance(patch, str) and 0 < len(patch) < 30_000:
            patches[path] = added_lines(patch)

    comments = []
    placed = set()
    for index, item in enumerate(findings):
        if len(comments) >= limit:
            break
        path, line = item.get("path"), item.get("line")
        if not isinstance(path, str) or type(line) is not int or line <= 0:
            continue
        source = patches.get(path, {}).get(line)
        if source is None:
            continue
        message = item.get("message")
        severity = item.get("severity")
        if not isinstance(message, str) or not isinstance(severity, str):
            continue
        title = f"**{severity.upper()} · Nodalia Guardian**"
        safe_message = message.replace("```", "'''")
        body = f"{title}\n\n{safe_message}"
        suggestion = item.get("suggestion")
        if (isinstance(suggestion, str) and suggestion.strip()
                and len(suggestion) <= 400
                and "\n" not in suggestion and "\r" not in suggestion
                and "```" not in suggestion and suggestion != source
                and not _SENSITIVE.search(path)):
            body += ("\n\n**Proposed change** — review before applying:\n\n"
                     f"```suggestion\n{suggestion}\n```")
        comments.append({"path": path, "line": line, "side": "RIGHT", "body": body})
        placed.add(index)
    return comments, placed
