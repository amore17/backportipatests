"""Shallow git operations against Pagure freeipa."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_URL = os.environ.get("FREEIPA_GIT_URL", "https://pagure.io/freeipa.git")


@dataclass(frozen=True)
class CommitRow:
    short_hash: str
    full_hash: str
    subject: str
    committed_at: datetime


def _run_git(repo: Path, *args: str, input_bytes: bytes | None = None) -> str:
    cmd = ["git", "-C", str(repo), *args]
    proc = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        input=input_bytes,
    )
    return proc.stdout.decode("utf-8", errors="replace")


def ensure_clone(*, branch: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    repo = cache_dir / "freeipa.git"
    if not (repo / "HEAD").exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--bare",
                "--single-branch",
                "--branch",
                branch,
                REPO_URL,
                str(repo),
            ],
            check=True,
            capture_output=True,
        )
    else:
        _run_git(repo, "fetch", "origin", branch)
    _run_git(repo, "fetch", "--tags", "origin")
    return repo


def resolve_baseline_ref(repo: Path, candidates: list[str]) -> str:
    for ref in candidates:
        try:
            out = _run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
            return out.strip()
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError(
        "Could not resolve baseline git ref from candidates: " + ", ".join(candidates)
    )


def nearest_release_4_13_tag(repo: Path, *, max_micro: int | None) -> str | None:
    """
    Pick newest existing release-4-13-<n> tag with n <= max_micro (if provided).
    """
    tags = [t.strip() for t in _run_git(repo, "tag", "-l", "release-4-13-*").splitlines() if t.strip()]
    parsed: list[tuple[int, str]] = []
    for t in tags:
        prefix = "release-4-13-"
        if not t.startswith(prefix):
            continue
        tail = t[len(prefix) :]
        if not tail.isdigit():
            continue
        micro = int(tail)
        if max_micro is not None and micro > max_micro:
            continue
        parsed.append((micro, t))
    if not parsed:
        return None
    parsed.sort(key=lambda x: x[0])
    return parsed[-1][1]


_RE_IPATEST_SUBJECT = re.compile(r"^ipatest", re.IGNORECASE)


def iter_missing_commits(
    repo: Path,
    *,
    branch: str,
    baseline_ref: str,
    since_utc: datetime | None,
) -> list[CommitRow]:
    """
    Commits reachable from branch but not from baseline_ref (baseline inclusive ancestor).
    """
    # Use git's %x00 so argv does not contain embedded NUL bytes (subprocess rejects them).
    fmt = "%H%x00%s%x00%cI%x00"
    log_out = _run_git(
        repo,
        "log",
        "-z",
        "--reverse",
        f"{baseline_ref}..{branch}",
        f"--pretty=format:{fmt}",
        "--no-color",
    )
    rows: list[CommitRow] = []
    chunks = [c for c in log_out.split("\x00") if c]
    # Format repeats: hash, subject, date per commit (final chunk may be incomplete)
    i = 0
    while i + 2 < len(chunks):
        full = chunks[i].strip()
        subject = chunks[i + 1].strip()
        dates = chunks[i + 2].strip()
        i += 3
        if not full or not subject:
            continue
        if not _RE_IPATEST_SUBJECT.match(subject):
            continue
        try:
            dt = _parse_git_date(dates)
        except ValueError:
            dt = datetime.now(timezone.utc)
        if since_utc is not None and dt < since_utc:
            continue
        rows.append(
            CommitRow(
                short_hash=full[:7],
                full_hash=full,
                subject=subject,
                committed_at=dt,
            )
        )
    return rows


def _parse_git_date(s: str) -> datetime:
    s = s.strip()
    if s.endswith(" +0000") or re.search(r" [+-]\d{4}$", s):
        # Git default pretty format
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_report_lines(rows: list[CommitRow], *, pagure_base: str = "https://pagure.io/freeipa/c") -> list[str]:
    lines = []
    for r in rows:
        url = f"{pagure_base}/{r.full_hash}"
        lines.append(f"{r.short_hash}  {r.subject}  {url}")
    return lines


def utc_since_days(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
