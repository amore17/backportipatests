"""Create Jira issues via REST (Atlassian Cloud–compatible)."""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request

from backportipatests.jira_fetch import jira_credentials

_MAX_TEXT_NODE = 32000


def _project_key_from_issue_key(issue_key: str) -> str:
    """``RHEL-173365`` → ``RHEL`` (RFC issue key ``PROJECT-NUMBER``)."""
    k = issue_key.strip().upper()
    parts = k.rsplit("-", 1)
    return parts[0] if len(parts) == 2 and parts[1].isdigit() else k


def _issue_versions_field_blocked_response(body: str) -> bool:
    """True when Jira rejects the ``versions`` field (Affected version/s) on create/edit."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    errs = data.get("errors") or {}
    msg = errs.get("versions") or errs.get("affectsVersions")
    if msg is None:
        return False
    lower = str(msg).lower()
    return "cannot be set" in lower or "not on the appropriate screen" in lower


def _normalize_extra_fields_versions(extra_fields: dict) -> None:
    """Map legacy ``affectsVersions`` to API ``versions`` if needed."""
    if "affectsVersions" in extra_fields and "versions" not in extra_fields:
        extra_fields["versions"] = extra_fields.pop("affectsVersions")


def _fetch_project_version_ids_by_name(
    *,
    base: str,
    auth: str,
    project_key: str,
    timeout: int,
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Return (exact_name → id, lowered_name → id) for project versions.
    On HTTP/network failure returns empty dicts (caller keeps ``name`` payloads).
    """
    root = base.rstrip("/")
    pk = project_key.strip().upper()
    req = urllib.request.Request(
        f"{root}/rest/api/3/project/{pk}/versions",
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return {}, {}
    exact: dict[str, str] = {}
    lower: dict[str, str] = {}
    if not isinstance(data, list):
        return {}, {}
    for v in data:
        if not isinstance(v, dict):
            continue
        name = v.get("name")
        vid = v.get("id")
        if name is None or vid is None:
            continue
        ns, ids = str(name), str(vid)
        exact.setdefault(ns, ids)
        lower.setdefault(ns.lower(), ids)
    return exact, lower


def _resolve_affects_versions_entries(
    *,
    base: str,
    auth: str,
    project_key: str,
    entries: list,
    timeout: int,
) -> list:
    """Prefer version ``id`` from the project API when ``name`` matches Jira."""
    if not entries:
        return entries
    exact, lower = _fetch_project_version_ids_by_name(
        base=base, auth=auth, project_key=project_key, timeout=timeout
    )
    if not exact and not lower:
        return entries
    out: list = []
    for item in entries:
        if not isinstance(item, dict):
            out.append(item)
            continue
        if item.get("id"):
            out.append({"id": str(item["id"])})
            continue
        name = item.get("name")
        if name is None:
            out.append(item)
            continue
        ns = str(name)
        vid = exact.get(ns) or lower.get(ns.lower())
        if vid:
            out.append({"id": vid})
        else:
            out.append({"name": ns})
    return out


def _try_set_versions_field_edit_only(
    *,
    base: str,
    auth: str,
    issue_key: str,
    field_name: str,
    versions: list,
    timeout: int,
) -> tuple[bool, str]:
    """
    PUT a version multi-select field (``versions`` for Affected version/s or ``fixVersions``) via
    ``fields`` or Jira ``update`` set ops (v3 then v2).

    Used after create/update when the combined payload omitted or rejected that field.
    """
    key = issue_key.strip().upper()
    root = base.rstrip("/")
    bodies = [
        json.dumps({"fields": {field_name: versions}}),
        json.dumps({"update": {field_name: [{"set": versions}]}}),
    ]
    last_err = ""
    for ver in ("3", "2"):
        for body_str in bodies:
            payload = body_str.encode("utf-8")
            url = f"{root}/rest/api/{ver}/issue/{key}"
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "backportipatests/0.1",
                },
                method="PUT",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp.read()
                return True, ""
            except urllib.error.HTTPError as e:
                last_err = e.read().decode("utf-8", errors="replace")
                continue
    return False, last_err


def _apply_versions_after_affects_failed(
    *,
    base: str,
    auth: str,
    issue_key: str,
    resolved_versions: list,
    timeout: int,
    after_create: bool,
) -> None:
    """
    Try edit-time ``versions`` (Affected version/s), then ``fixVersions`` with the same entries.

    RHEL Cloud sometimes exposes Fix versions on the edit/create screen when the
    versions field does not.
    """
    key = issue_key.strip().upper()
    ok_av, err_av = _try_set_versions_field_edit_only(
        base=base,
        auth=auth,
        issue_key=key,
        field_name="versions",
        versions=resolved_versions,
        timeout=timeout,
    )
    if ok_av:
        print(
            "Note: Set Affects versions to "
            f"{_affects_version_names(resolved_versions)!s} via edit API on {key}.",
            file=sys.stderr,
        )
        return

    ok_fx, err_fx = _try_set_versions_field_edit_only(
        base=base,
        auth=auth,
        issue_key=key,
        field_name="fixVersions",
        versions=resolved_versions,
        timeout=timeout,
    )
    if ok_fx:
        print(
            "Warning: Affects versions could not be set via edit API "
            f"({err_av!r}). Set Fix versions to "
            f"{_affects_version_names(resolved_versions)!s} instead.",
            file=sys.stderr,
        )
        return

    opener = (
        "Issue was created but neither"
        if after_create
        else "Description was updated but neither"
    )
    print(
        f"Warning: {opener} Affects versions nor Fix versions could be set via edit "
        "API. versions (Affected) error: "
        f"{err_av!r}; fixVersions error: {err_fx!r}. Set them manually in Jira.",
        file=sys.stderr,
    )


def _affects_version_names(affects_versions: list) -> str:
    names: list[str] = []
    for item in affects_versions:
        if isinstance(item, dict):
            if item.get("name"):
                names.append(str(item["name"]))
            elif item.get("id"):
                names.append(f"id:{item['id']}")
    return ", ".join(names) if names else "(unknown)"


def plain_text_to_adf(text: str) -> dict:
    """Minimal Atlassian Document Format for a multi-line description."""
    content: list[dict] = []
    for line in text.split("\n"):
        chunk = line if len(line) <= _MAX_TEXT_NODE else line[: _MAX_TEXT_NODE - 1] + "…"
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": chunk if chunk.strip() else " "}],
            }
        )
    return {"type": "doc", "version": 1, "content": content}


def create_jira_issue(
    *,
    description_plain: str,
    summary: str,
    project_key: str,
    issue_type: str,
    components_csv: str,
    extra_fields: dict,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    timeout: int = 120,
    _stripped_affects: bool = False,
    _post_create_affects_versions: list | None = None,
) -> dict:
    """
    POST a new issue. Uses API v3 + ADF description; falls back to API v2 string body.

    ``extra_fields`` should match MCP ``additional_fields`` (e.g. ``versions`` for
    Affected version/s, ``customfield_10606``).

    Returns the create response JSON (includes ``key``, ``id``, ``self``).
    """
    base, user, token = jira_credentials(
        base_url=base_url, email=email, api_token=api_token
    )
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()

    extra_fields = dict(extra_fields)
    _normalize_extra_fields_versions(extra_fields)
    av_list = extra_fields.get("versions")
    if isinstance(av_list, list) and av_list:
        extra_fields["versions"] = _resolve_affects_versions_entries(
            base=base,
            auth=auth,
            project_key=project_key,
            entries=av_list,
            timeout=timeout,
        )

    comps = [
        {"name": c.strip()}
        for c in components_csv.split(",")
        if c.strip()
    ]
    fields_v3 = {
        "project": {"key": project_key.strip().upper()},
        "summary": summary,
        "description": plain_text_to_adf(description_plain),
        "issuetype": {"name": issue_type},
        "components": comps,
        **extra_fields,
    }

    body_v3 = json.dumps({"fields": fields_v3}).encode("utf-8")
    req_v3 = urllib.request.Request(
        f"{base}/rest/api/3/issue",
        data=body_v3,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="POST",
    )
    def _finish(result: dict) -> dict:
        key = result.get("key")
        pending = _post_create_affects_versions
        if pending and key:
            _apply_versions_after_affects_failed(
                base=base,
                auth=auth,
                issue_key=key,
                resolved_versions=pending,
                timeout=timeout,
                after_create=True,
            )
        return result

    try:
        with urllib.request.urlopen(req_v3, timeout=timeout) as resp:
            return _finish(json.loads(resp.read().decode("utf-8", errors="replace")))
    except urllib.error.HTTPError as e_v3:
        err_body = e_v3.read().decode("utf-8", errors="replace")
        if e_v3.code not in (400, 415):
            raise RuntimeError(f"Jira create (v3) HTTP {e_v3.code}: {err_body}") from e_v3

    # Fallback: classic string description
    fields_v2 = {
        "project": {"key": project_key.strip().upper()},
        "summary": summary,
        "description": description_plain,
        "issuetype": {"name": issue_type},
        "components": comps,
        **extra_fields,
    }
    body_v2 = json.dumps({"fields": fields_v2}).encode("utf-8")
    req_v2 = urllib.request.Request(
        f"{base}/rest/api/2/issue",
        data=body_v2,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req_v2, timeout=timeout) as resp:
            return _finish(json.loads(resp.read().decode("utf-8", errors="replace")))
    except urllib.error.HTTPError as e_v2:
        err = e_v2.read().decode("utf-8", errors="replace")
        if (
            not _stripped_affects
            and "versions" in extra_fields
            and (
                _issue_versions_field_blocked_response(err_body)
                or _issue_versions_field_blocked_response(err)
            )
        ):
            pending_av = extra_fields["versions"]
            print(
                "Warning: Jira rejected versions (Affected version/s) on create (field "
                "not on create screen). Creating without it, then setting versions via "
                "edit API.",
                file=sys.stderr,
            )
            stripped = {
                k: v for k, v in extra_fields.items() if k != "versions"
            }
            return create_jira_issue(
                description_plain=description_plain,
                summary=summary,
                project_key=project_key,
                issue_type=issue_type,
                components_csv=components_csv,
                extra_fields=stripped,
                base_url=base_url,
                email=email,
                api_token=api_token,
                timeout=timeout,
                _stripped_affects=True,
                _post_create_affects_versions=pending_av,
            )
        raise RuntimeError(
            f"Jira create failed. v3 error body: {err_body!r}; "
            f"v2 HTTP {e_v2.code}: {err}"
        ) from e_v2


def update_jira_issue(
    *,
    issue_key: str,
    description_plain: str,
    extra_fields: dict,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    timeout: int = 120,
    _stripped_affects: bool = False,
    _post_apply_affects_versions: list | None = None,
) -> dict:
    """
    PUT field updates on an existing issue (API v3 + ADF description; v2 fallback).

    ``extra_fields`` typically includes ``versions`` (Affected version/s) and custom
    fields such as AssignedTeam (same shape as :func:`create_jira_issue`).
    """
    base, user, token = jira_credentials(
        base_url=base_url, email=email, api_token=api_token
    )
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    key = issue_key.strip().upper()

    extra_fields = dict(extra_fields)
    _normalize_extra_fields_versions(extra_fields)
    av_list = extra_fields.get("versions")
    if isinstance(av_list, list) and av_list:
        extra_fields["versions"] = _resolve_affects_versions_entries(
            base=base,
            auth=auth,
            project_key=_project_key_from_issue_key(key),
            entries=av_list,
            timeout=timeout,
        )

    fields_v3 = {
        "description": plain_text_to_adf(description_plain),
        **extra_fields,
    }
    body_v3 = json.dumps({"fields": fields_v3}).encode("utf-8")
    req_v3 = urllib.request.Request(
        f"{base}/rest/api/3/issue/{key}",
        data=body_v3,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="PUT",
    )
    err_body = ""

    def _finish_update(result: dict) -> dict:
        key = issue_key.strip().upper()
        pending = _post_apply_affects_versions
        if pending:
            _apply_versions_after_affects_failed(
                base=base,
                auth=auth,
                issue_key=key,
                resolved_versions=pending,
                timeout=timeout,
                after_create=False,
            )
        return result

    try:
        with urllib.request.urlopen(req_v3, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return _finish_update(json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e_v3:
        err_body = e_v3.read().decode("utf-8", errors="replace")
        if e_v3.code not in (400, 415):
            raise RuntimeError(
                f"Jira update (v3) HTTP {e_v3.code}: {err_body}"
            ) from e_v3

    fields_v2 = {
        "description": description_plain,
        **extra_fields,
    }
    body_v2 = json.dumps({"fields": fields_v2}).encode("utf-8")
    req_v2 = urllib.request.Request(
        f"{base}/rest/api/2/issue/{key}",
        data=body_v2,
        headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "backportipatests/0.1",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req_v2, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return _finish_update(json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e_v2:
        err = e_v2.read().decode("utf-8", errors="replace")
        if (
            not _stripped_affects
            and "versions" in extra_fields
            and (
                _issue_versions_field_blocked_response(err_body)
                or _issue_versions_field_blocked_response(err)
            )
        ):
            pending_av = extra_fields["versions"]
            print(
                "Warning: Jira rejected versions (Affected version/s) on combined "
                "update. Updating description only, then setting versions via edit API.",
                file=sys.stderr,
            )
            stripped = {
                k: v for k, v in extra_fields.items() if k != "versions"
            }
            return update_jira_issue(
                issue_key=issue_key,
                description_plain=description_plain,
                extra_fields=stripped,
                base_url=base_url,
                email=email,
                api_token=api_token,
                timeout=timeout,
                _stripped_affects=True,
                _post_apply_affects_versions=pending_av,
            )
        raise RuntimeError(
            f"Jira update failed. v3 error body: {err_body!r}; "
            f"v2 HTTP {e_v2.code}: {err}"
        ) from e_v2


def browse_url(base_url: str, issue_key: str) -> str:
    return f"{base_url.rstrip('/')}/browse/{issue_key}"
