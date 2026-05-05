"""Parse Jira/plain descriptions and merge newly found commits into an existing report."""

from __future__ import annotations

import re
from collections.abc import Callable
from backportipatests.git_compare import CommitRow

MISSING_HEADER = "Missing ipatest* commits:"
DEFAULT_BUG_INTRO = "Automated Bug By Cursor"


def automation_intro_before_scan_metadata(text: str) -> str:
    """Lines before the first ``Compose repo:`` line (e.g. automation opener)."""
    lines = text.splitlines()
    buf: list[str] = []
    for ln in lines:
        if ln.startswith("Compose repo:"):
            break
        buf.append(ln)
    return "\n".join(buf).strip()

# Pagure commit URL (full hash)
_PAGURE_HASH = re.compile(
    r"https://pagure\.io/freeipa/c/([0-9a-f]{40})\b",
    re.IGNORECASE,
)

def parse_known_commit_hashes(text: str) -> set[str]:
    """All full commit hashes referenced by Pagure URLs in ``text``."""
    return {m.group(1).lower() for m in _PAGURE_HASH.finditer(text)}


def extract_commit_lines_ordered(text: str) -> list[str]:
    """Unique lines that cite a Pagure commit URL, first-seen order (Jira-safe)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _PAGURE_HASH.search(line)
        if not m:
            continue
        h = m.group(1).lower()
        if h in seen:
            continue
        seen.add(h)
        out.append(line)
    return out


def latest_listed_commit_full_hash(text: str) -> str | None:
    """Full hash from the last Pagure commit URL in document order."""
    lines = extract_commit_lines_ordered(text)
    if not lines:
        last_url_match = None
        for m in _PAGURE_HASH.finditer(text):
            last_url_match = m
        return last_url_match.group(1).lower() if last_url_match else None
    m = _PAGURE_HASH.search(lines[-1])
    return m.group(1).lower() if m else None


def commits_not_yet_listed(
    rows: list[CommitRow],
    *,
    existing_description: str,
) -> list[CommitRow]:
    """Commits from ``rows`` whose hashes are not already mentioned in ``existing_description``."""
    known = parse_known_commit_hashes(existing_description)
    return [r for r in rows if r.full_hash.lower() not in known]


def merge_report_with_existing_description(
    *,
    existing_description: str,
    scan_header_lines: list[str],
    rows_from_scan: list[CommitRow],
    format_lines: Callable[[list[CommitRow]], list[str]],
    refresh_scan_header: bool = True,
) -> tuple[str, int, int]:
    """
    Build an updated description: optional fresh scan header + merged commit list.

    ``format_lines`` is typically ``format_report_lines`` from git_compare.

    Returns ``(merged_text, new_commit_count, total_commit_lines)``.
    """
    prior_lines = extract_commit_lines_ordered(existing_description)
    new_rows = commits_not_yet_listed(
        rows_from_scan, existing_description=existing_description
    )

    merged_commit_lines = prior_lines + format_lines(new_rows)
    total = len(merged_commit_lines)

    if refresh_scan_header:
        intro = automation_intro_before_scan_metadata(existing_description)
        if not intro:
            intro = DEFAULT_BUG_INTRO
        meta = "\n".join(scan_header_lines).rstrip()
        header = f"{intro}\n\n{meta}".strip()
        body = "\n".join(merged_commit_lines)
        if body.strip():
            text = f"{header}\n\n{MISSING_HEADER}\n\n{body}\n"
        else:
            text = f"{header}\n\n{MISSING_HEADER}\n\n(none)\n"
        return text, len(new_rows), total

    # Preserve everything before Missing header; replace tail with merged commits.
    marker = MISSING_HEADER
    idx = existing_description.find(marker)
    if idx == -1:
        appendix = (
            "\n\n"
            + marker
            + "\n\n"
            + ("\n".join(merged_commit_lines) if merged_commit_lines else "(none)")
            + "\n"
        )
        text = existing_description.rstrip() + appendix
        return text, len(new_rows), total

    prefix = existing_description[:idx].rstrip()
    body = "\n".join(merged_commit_lines)
    text = f"{prefix}\n\n{marker}\n\n{body}\n" if body.strip() else f"{prefix}\n\n{marker}\n\n(none)\n"
    return text, len(new_rows), total
