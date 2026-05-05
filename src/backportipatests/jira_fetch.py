"""Fetch Jira issue description (plain text) for merge workflows."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def _adf_to_plain(node: dict | list | str | None) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_plain(x) for x in node)
    if not isinstance(node, dict):
        return ""
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    inner = "".join(_adf_to_plain(c) for c in node.get("content") or [])
    if t == "paragraph":
        return inner + "\n"
    if t in ("hardBreak", "bulletList", "orderedList", "listItem", "doc"):
        return inner
    return inner


def _cursor_mcp_jira_env_block() -> dict[str, str]:
    """
    Env dict from Cursor MCP config for the Jira server (same creds as jira-mcp / mcp-atlassian).

    Path: ``CURSOR_MCP_JSON`` or ``~/.cursor/mcp.json``. Server key:
    ``JIRA_MCP_SERVER_NAME`` (default ``jira-mcp``).
    """
    raw_path = os.environ.get("CURSOR_MCP_JSON")
    path = Path(raw_path).expanduser() if raw_path else Path.home() / ".cursor" / "mcp.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    servers = data.get("mcpServers") or {}
    name = os.environ.get("JIRA_MCP_SERVER_NAME", "jira-mcp")
    entry = servers.get(name) or {}
    env = entry.get("env") or {}
    if not isinstance(env, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in env.items():
        if isinstance(k, str) and isinstance(v, str):
            out[k] = v
    return out


def description_field_to_plain(description_field: str | dict | None) -> str:
    if description_field is None:
        return ""
    if isinstance(description_field, str):
        return description_field
    if isinstance(description_field, dict):
        if description_field.get("type") == "doc":
            return _adf_to_plain(description_field).strip() + "\n"
        return json.dumps(description_field)
    return str(description_field)


def jira_credentials(
    *,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
) -> tuple[str, str, str]:
    """
    Return ``(base_url, email, api_token)`` for REST calls.

    Uses env ``JIRA_URL`` (default ``https://redhat.atlassian.net``),
    ``JIRA_EMAIL``, ``JIRA_API_TOKEN`` when arguments omitted.

    If email/token are still unset, fills gaps from the Cursor MCP config
    (``~/.cursor/mcp.json`` → ``mcpServers.<JIRA_MCP_SERVER_NAME>.env``), matching
    ``mcp-atlassian`` / ``jira-mcp``: ``JIRA_URL``, ``JIRA_USERNAME``, ``JIRA_API_TOKEN``.
    """
    mcp = _cursor_mcp_jira_env_block()

    base = (
        base_url
        or os.environ.get("JIRA_URL")
        or mcp.get("JIRA_URL")
        or "https://redhat.atlassian.net"
    ).rstrip("/")
    user = (
        email
        or os.environ.get("JIRA_EMAIL")
        or os.environ.get("ATLASSIAN_EMAIL")
        or mcp.get("JIRA_EMAIL")
        or mcp.get("JIRA_USERNAME")
    )
    token = (
        api_token
        or os.environ.get("JIRA_API_TOKEN")
        or os.environ.get("ATLASSIAN_API_TOKEN")
        or mcp.get("JIRA_API_TOKEN")
    )
    if not user or not token:
        raise RuntimeError(
            "Jira credentials missing: set JIRA_EMAIL and JIRA_API_TOKEN "
            "(or ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN), optional JIRA_URL; "
            "or configure the same keys under jira-mcp in ~/.cursor/mcp.json "
            "(see CURSOR_MCP_JSON / JIRA_MCP_SERVER_NAME)."
        )
    return base, user, token


def fetch_jira_issue_description(
    issue_key: str,
    *,
    base_url: str | None = None,
    email: str | None = None,
    api_token: str | None = None,
    timeout: int = 120,
) -> str:
    """GET issue description as plain text."""
    base, user, token = jira_credentials(
        base_url=base_url, email=email, api_token=api_token
    )

    url = f"{base}/rest/api/3/issue/{issue_key.strip().upper()}?fields=description"
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
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
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jira HTTP {e.code}: {e.reason}") from e

    data = json.loads(raw)
    desc = data.get("fields", {}).get("description")
    return description_field_to_plain(desc)
