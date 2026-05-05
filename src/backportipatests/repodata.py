"""Fetch compose repodata and locate python3-ipatests + SRPM name."""

from __future__ import annotations

import gzip
import io
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


_REPO = "http://linux.duke.edu/metadata/repo"
_COMMON = "http://linux.duke.edu/metadata/common"
_RPM = "http://linux.duke.edu/metadata/rpm"


@dataclass(frozen=True)
class BinaryPackageInfo:
    name: str
    version: str
    release: str
    arch: str
    sourcerpm: str
    location_href: str


def _fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "backportipatests/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def primary_href(os_repo_url: str) -> str:
    base = os_repo_url.rstrip("/") + "/"
    repomd = base + "repodata/repomd.xml"
    raw = _fetch(repomd)
    root = ET.fromstring(raw)
    for data in root.findall(f"{{{_REPO}}}data"):
        if data.get("type") == "primary":
            loc = data.find(f"{{{_REPO}}}location")
            if loc is None:
                continue
            href = loc.get("href")
            if href:
                return base + href
    raise RuntimeError("Could not find primary metadata in repomd.xml")


def iter_primary_packages(os_repo_url: str):
    href = primary_href(os_repo_url)
    raw = _fetch(href)
    if href.endswith(".gz"):
        raw = gzip.decompress(raw)
    root = ET.fromstring(raw)
    for pkg in root.findall(f"{{{_COMMON}}}package"):
        name_el = pkg.find(f"{{{_COMMON}}}name")
        arch_el = pkg.find(f"{{{_COMMON}}}arch")
        ver_el = pkg.find(f"{{{_COMMON}}}version")
        fmt_el = pkg.find(f"{{{_COMMON}}}format")
        loc_el = pkg.find(f"{{{_COMMON}}}location")
        if None in (name_el, arch_el, ver_el, fmt_el, loc_el):
            continue
        srpm_el = fmt_el.find(f"{{{_RPM}}}sourcerpm")
        if srpm_el is None or not (srpm_el.text or "").strip():
            continue
        yield BinaryPackageInfo(
            name=(name_el.text or "").strip(),
            arch=(arch_el.text or "").strip(),
            version=ver_el.get("ver") or "",
            release=ver_el.get("rel") or "",
            sourcerpm=srpm_el.text.strip(),
            location_href=(loc_el.get("href") or "").strip(),
        )


def find_python3_ipatests(os_repo_url: str) -> BinaryPackageInfo:
    for pkg in iter_primary_packages(os_repo_url):
        if pkg.name == "python3-ipatests":
            return pkg
    raise RuntimeError("python3-ipatests not found in compose primary metadata")


def compose_root_from_os_url(os_repo_url: str) -> str:
    """.../compose/CRB/x86_64/os/ -> .../compose/"""
    u = os_repo_url.rstrip("/")
    marker = "/compose/"
    idx = u.find(marker)
    if idx == -1:
        raise ValueError(f"Unexpected os repo URL (no /compose/): {os_repo_url}")
    return u[: idx + len(marker)]


def guess_srpm_urls(compose_root: str, sourcerpm: str) -> list[str]:
    """Try common compose layouts for SRPM location."""
    letter = sourcerpm[:1].lower()
    root = compose_root.rstrip("/")
    return [
        f"{root}/SRPMS/{sourcerpm}",
        f"{root}/source/tree/Packages/{letter}/{sourcerpm}",
        f"{root}/crb/source/tree/Packages/{letter}/{sourcerpm}",
        f"{root}/BaseOS/source/tree/Packages/{letter}/{sourcerpm}",
    ]


def download_first_available(urls: list[str], dest_path: str, timeout: int = 600) -> str:
    last_err: Exception | None = None
    for url in urls:
        try:
            data = _fetch(url, timeout=timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            continue
        with open(dest_path, "wb") as f:
            f.write(data)
        return url
    raise RuntimeError(f"Could not download SRPM from candidates; last error: {last_err}")


_VERSION_LINE = re.compile(r"^\s*Version\s*:\s*(\S+)\s*$", re.MULTILINE)


def parse_spec_version(spec_body: str) -> str:
    m = _VERSION_LINE.search(spec_body)
    if not m:
        raise RuntimeError("Could not parse Version from spec")
    return m.group(1)


def freeipa_version_to_tag_candidates(upstream_version: str) -> list[str]:
    """
    Map spec Version like 4.13.6 to likely Pagure tags (ordered).
    """
    parts = upstream_version.strip().split(".")
    if len(parts) >= 3 and parts[0] == "4" and parts[1] == "13":
        micro = parts[2]
        return [f"release-4-13-{micro}", f"ipa-4-13-{micro}"]
    joined = "-".join(parts)
    return [f"release-{joined}", f"ipa-{joined}"]
