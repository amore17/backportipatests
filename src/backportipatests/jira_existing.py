"""Find open python3-ipatests backport bugs and comment instead of duplicating."""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

from backportipatests.git_compare import CommitRow, format_report_lines
from backportipatests.jira_fetch import description_field_to_plain, jira_credentials
from backportipatests.jira_spec import (
    DEFAULT_JIRA_COMPONENT,
    OPEN_IPATESTS_WORKFLOW_STATUSES,
    TARGET_RHEL_VERSION_FIELD_ID,
)
from backportipatests.report_merge import MISSING_HEADER, commits_not_yet_listed

FIXED_IN_BUILD_FIELD_NAME = "Fixed in Build"

# Resolved at runtime via Jira field search (see ``_fixed_in_build_field_id``).
FIXED_IN_BUILD_FIELD_ID: str | None = None


def _auth_header(user: str, token: str) -> str:
    return base64.b64encode(f"{user}:{token}".encode()).decode()


@lru_cache(maxsize=1)
def _fixed_in_build_field_id(
    base: str,
    auth: str,
    timeout: int,
) -> str | None:
    """Return custom field id for ``Fixed in Build`` (cached per process)."""
    global FIXED_IN_BUILD_FIELD_ID
    if FIXED_IN_BUILD_FIELD_ID:
        return FIXED_IN_BUILD_FIELD_ID

    root = base.rstrip("/")
    query = urllib.parse.quote(FIXED_IN_BUILD_FIELD_NAME)
    url = f"{root}/rest/api/3/field/search?query={query}&maxResults=20"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None

    for field in data.get("values") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "")
        fid = field.get("id")
        if name == FIXED_IN_BUILD_FIELD_NAME and fid:
            FIXED_IN_BUILD_FIELD_ID = str(fid)
            return FIXED_IN_BUILD_FIELD_ID
    return None


def _is_field_value_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def issue_fixed_in_build_empty(fields: dict, *, fixed_in_build_field_id: str | None) -> bool:
    """True when ``Fixed in Build`` is unset on the issue."""
    if not fixed_in_build_field_id:
        return True
    return _is_field_value_empty(fields.get(fixed_in_build_field_id))


def jira_open_ipatests_bugs_jql(
    *,
    project_key: str,
    affects_version_name: str,
    component: str = DEFAULT_JIRA_COMPONENT,
) -> str:
    """
    JQL for open backport bugs matching summary and workflow state.

    Mirrors manual QE query: summary contains ``python3-ipatests``, version on
    affected/fix/target-RHEL fields, Components ``ipa``, status in active workflow states.
    """
    statuses = ", ".join(f'"{s}"' for s in OPEN_IPATESTS_WORKFLOW_STATUSES)
    ver = affects_version_name.replace('"', '\\"')
    comp = component.strip().replace('"', '\\"')
    pk = project_key.strip().upper()
    cf_num = TARGET_RHEL_VERSION_FIELD_ID.removeprefix("customfield_")
    return (
        f'project = {pk} AND type = Bug AND summary ~ "python3-ipatests" '
        f'AND component = "{comp}" '
        f"AND status in ({statuses}) AND "
        f'(affectedVersion = "{ver}" OR fixVersion = "{ver}" OR cf[{cf_num}] = "{ver}") '
        f"ORDER BY updated DESC"
    )


def search_open_ipatests_bugs(
    *,
    project_key: str,
    affects_version_name: str,
    component: str = DEFAULT_JIRA_COMPONENT,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    timeout: int = 120,
    max_results: int = 5,
) -> list[dict]:
    """
    Search Jira for matching open bugs.

    Each result is the issue object from the search API (``key``, ``fields``, …).
    """
    base, user, token = jira_credentials(
        base_url=base_url, email=email, api_token=api_token
    )
    auth = _auth_header(user, token)
    fib_id = _fixed_in_build_field_id(base, auth, timeout)

    fields = ["summary", "status", "description", "updated"]
    if fib_id:
        fields.append(fib_id)

    jql = jira_open_ipatests_bugs_jql(
        project_key=project_key,
        affects_version_name=affects_version_name,
        component=component,
    )
    body = json.dumps(
        {
            "jql": jql,
            "maxResults": max_results,
            "fields": fields,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/api/3/search/jql",
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        # Older Cloud: POST /rest/api/3/search
        if e.code != 404:
            raise RuntimeError(f"Jira search HTTP {e.code}: {err}") from e
        req = urllib.request.Request(
            f"{base.rstrip('/')}/rest/api/3/search",
            data=body,
            headers={
                "Authorization": f"Basic {auth}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "backportipatests/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e2:
            raise RuntimeError(
                f"Jira search HTTP {e2.code}: {e2.read().decode('utf-8', errors='replace')}"
            ) from e2

    issues = data.get("issues") or []
    return [i for i in issues if isinstance(i, dict)]


def fetch_issue_comment_bodies(
    issue_key: str,
    *,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    timeout: int = 120,
) -> str:
    """Concatenate all comment bodies on an issue (plain text)."""
    base, user, token = jira_credentials(
        base_url=base_url, email=email, api_token=api_token
    )
    auth = _auth_header(user, token)
    key = issue_key.strip().upper()
    url = f"{base.rstrip('/')}/rest/api/3/issue/{key}/comment?maxResults=100"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Jira comments HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"
        ) from e

    parts: list[str] = []
    for c in data.get("comments") or []:
        if not isinstance(c, dict):
            continue
        body = c.get("body")
        if body is None:
            continue
        parts.append(description_field_to_plain(body))
    return "\n".join(parts)


def build_commit_list_comment(
    *,
    scan_meta_lines: list[str],
    commit_lines: list[str],
) -> str:
    """Plain-text body for a Jira comment listing commits."""
    meta = "\n".join(scan_meta_lines).rstrip()
    body = "\n".join(commit_lines) if commit_lines else "(none)"
    return (
        "Automated comment from backport-ipa-tests\n\n"
        f"{meta}\n\n"
        f"{MISSING_HEADER}\n\n"
        f"{body}\n"
    )


def maybe_comment_on_existing_open_bug(
    *,
    project_key: str,
    affects_version_name: str,
    component: str = DEFAULT_JIRA_COMPONENT,
    scan_meta_lines: list[str],
    rows: list[CommitRow],
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    timeout: int = 120,
) -> str | None:
    """
    If an open matching bug exists, optionally add a comment and return its key.

    Returns the issue key when an existing bug was found (create skipped).
    Returns ``None`` when no matching open bug exists (caller should create).
    """
    from backportipatests.jira_create import add_jira_comment, browse_url

    base, user, token = jira_credentials(
        base_url=base_url, email=email, api_token=api_token
    )
    auth = _auth_header(user, token)
    fib_id = _fixed_in_build_field_id(base, auth, timeout)

    matches = search_open_ipatests_bugs(
        project_key=project_key,
        affects_version_name=affects_version_name,
        component=component,
        base_url=base_url,
        email=email,
        api_token=api_token,
        timeout=timeout,
        max_results=1,
    )
    if not matches:
        return None

    issue = matches[0]
    key = str(issue.get("key") or "").strip().upper()
    if not key:
        return None

    fields = issue.get("fields") or {}
    summary = fields.get("summary", "")
    print(
        f"Found existing open bug {key} ({summary!r}); skipping create.",
        file=sys.stderr,
    )

    if not issue_fixed_in_build_empty(fields, fixed_in_build_field_id=fib_id):
        print(
            f"Note: {key} has Fixed in Build set; not adding a commit-list comment.",
            file=sys.stderr,
        )
        print(f"Use existing bug: {browse_url(base, key)}")
        return key

    desc = description_field_to_plain(fields.get("description"))
    try:
        comments = fetch_issue_comment_bodies(
            key, base_url=base_url, email=email, api_token=api_token, timeout=timeout
        )
    except RuntimeError as e:
        print(f"Warning: could not read comments on {key}: {e}", file=sys.stderr)
        comments = ""

    known_text = desc + "\n" + comments
    new_rows = commits_not_yet_listed(rows, existing_description=known_text)
    commit_lines = format_report_lines(new_rows)

    if not commit_lines:
        print(
            f"No new commits to add on {key} (all already listed in description/comments).",
            file=sys.stderr,
        )
        print(f"Use existing bug: {browse_url(base, key)}")
        return key

    comment_body = build_commit_list_comment(
        scan_meta_lines=scan_meta_lines,
        commit_lines=commit_lines,
    )
    add_jira_comment(
        issue_key=key,
        body_plain=comment_body,
        base_url=base_url,
        email=email,
        api_token=api_token,
        timeout=timeout,
    )
    print(
        f"Added comment on {key} (+{len(commit_lines)} commits): {browse_url(base, key)}"
    )
    return key
