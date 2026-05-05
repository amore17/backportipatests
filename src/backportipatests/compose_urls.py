"""Resolve compose repo URLs for RHEL nightly CRB trees."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComposePaths:
    """Host + path templates for a major RHEL release."""

    latest_host: str
    latest_path_template: str  # uses {minor}
    zstream_host: str
    zstream_path_template: str


PATHS: dict[int, ComposePaths] = {
    10: ComposePaths(
        latest_host="download.devel.redhat.com",
        latest_path_template=(
            "/rhel-10/nightly/RHEL-10/latest-RHEL-10.{minor}/compose/CRB/x86_64/os/"
        ),
        zstream_host="download.eng.pnq.redhat.com",
        zstream_path_template=(
            "/rhel-10/nightly/updates/RHEL-10/latest-RHEL-10.{minor}/compose/CRB/x86_64/os/"
        ),
    ),
    9: ComposePaths(
        latest_host="download.devel.redhat.com",
        latest_path_template=(
            "/rhel-9/nightly/RHEL-9/latest-RHEL-9.{minor}/compose/CRB/x86_64/os/"
        ),
        zstream_host="download.devel.redhat.com",
        zstream_path_template=(
            "/rhel-9/nightly/updates/RHEL-9/latest-RHEL-9.{minor}/compose/CRB/x86_64/os/"
        ),
    ),
}


def default_os_repo_url(
    *,
    major: int,
    minor: int,
    track: str,
    compose_minor: int | None = None,
) -> str:
    """
    Build default CRB x86_64 os/ repo URL.

    track: 'latest' or 'zstream'
    compose_minor: optional override for latest-RHEL-{major}.{x} segment (defaults to minor).
    """
    try:
        cfg = PATHS[major]
    except KeyError as e:
        raise ValueError(f"Unsupported RHEL major version: {major}") from e
    m = str(compose_minor if compose_minor is not None else minor)
    if track == "latest":
        host, path = cfg.latest_host, cfg.latest_path_template.format(minor=m)
    elif track == "zstream":
        host, path = cfg.zstream_host, cfg.zstream_path_template.format(minor=m)
    else:
        raise ValueError("track must be 'latest' or 'zstream'")
    return f"http://{host}{path}"
