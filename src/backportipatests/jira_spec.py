"""Defaults for RHEL Jira bugs created from backport-ipa-tests output."""

from __future__ import annotations

import re

# redhat.atlassian.net — field from jira_search_fields("AssignedTeam")
ASSIGNED_TEAM_FIELD_ID = "customfield_10606"

DEFAULT_JIRA_COMPONENT = "ipa"
DEFAULT_ASSIGNED_TEAM_VALUE = "rhel-idm-ipa"

# RHEL Jira — Target RHEL Version (used in QE JQL alongside affected/fix versions)
TARGET_RHEL_VERSION_FIELD_ID = "customfield_10855"

# Active workflow states for open python3-ipatests backport bugs (do not duplicate).
OPEN_IPATESTS_WORKFLOW_STATUSES: tuple[str, ...] = (
    "New",
    "Planning",
    "In Progress",
    "Integration",
)

# Z-stream compose without explicit ".z" in Jira (user override → normalize)
_RHEL_MINOR_ONLY = re.compile(r"^rhel-(?P<maj>\d+)\.(?P<min>\d+)$")


def is_zstream_track(track: str) -> bool:
    """True for ``zstream`` (allows minor normalization / ``.z`` suffix rules)."""
    return (track or "").strip().lower().replace("-", "") == "zstream"


def coerce_jira_affects_version_display_name(
    *,
    prod_major: int,
    prod_minor: int,
    track: str = "latest",
    affects_version: str | None = None,
) -> str:
    """
    Canonical **Affects versions** label for RHEL Jira.

    Default: ``rhel-X.Y`` (latest) or ``rhel-X.Y.z`` (z-stream). When the user passes
    ``--jira-affects-version`` as ``rhel-X.Y`` on z-stream, append ``.z``.
    """
    if affects_version is None:
        return jira_affects_version_name(
            prod_major=prod_major, prod_minor=prod_minor, track=track
        )
    av = affects_version.strip()
    if is_zstream_track(track) and _RHEL_MINOR_ONLY.fullmatch(av):
        return f"{av}.z"
    return av


def jira_summary_line(*, prod_major: int, prod_minor: int) -> str:
    return (
        f"[Cursor Automated] Include latest fixes in python3-ipatests package "
        f"[RHEL{prod_major}.{prod_minor}]"
    )


def jira_affects_version_name(
    *,
    prod_major: int,
    prod_minor: int,
    track: str = "latest",
) -> str:
    """
    Default Affects versions value for RHEL Jira.

    ``latest`` → ``rhel-X.Y`` (minor release). ``zstream`` → ``rhel-X.Y.z``.
    """
    base = f"rhel-{prod_major}.{prod_minor}"
    if is_zstream_track(track):
        return f"{base}.z"
    return base


def jira_create_additional_fields(
    *,
    prod_major: int,
    prod_minor: int,
    track: str = "latest",
    assigned_team: str = DEFAULT_ASSIGNED_TEAM_VALUE,
    affects_version: str | None = None,
    include_affects_versions: bool = True,
) -> dict:
    """
    JSON object for jira_create_issue(..., additional_fields=json.dumps(this)).

    Components are passed separately as components='ipa' on create_issue.
    Set ``include_affects_versions=False`` when the Jira create/edit screen omits
    the ``versions`` field for API callers (see ``--jira-no-affects-version``).
    """
    out: dict = {
        ASSIGNED_TEAM_FIELD_ID: {"value": assigned_team},
    }
    if include_affects_versions:
        ver = coerce_jira_affects_version_display_name(
            prod_major=prod_major,
            prod_minor=prod_minor,
            track=track,
            affects_version=affects_version,
        )
        # REST API field id for UI "Affected version/s" (not affectsVersions / affectedVersion).
        out["versions"] = [{"name": ver}]
    return out


