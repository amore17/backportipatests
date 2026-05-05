"""Extract freeipa.spec body from a source RPM."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def extract_spec_text(srpm_path: Path) -> str:
    srpm_path = srpm_path.resolve()
    if not srpm_path.is_file():
        raise FileNotFoundError(srpm_path)

    tmp = Path(tempfile.mkdtemp(prefix="backportipatests-srpm-"))
    try:
        # Prefer bsdtar (macOS) / libarchive; falls back to rpm2cpio + cpio on Linux.
        if shutil.which("bsdtar"):
            subprocess.run(
                ["bsdtar", "-xf", str(srpm_path), "-C", str(tmp)],
                check=True,
                capture_output=True,
            )
        elif shutil.which("rpm2cpio") and shutil.which("cpio"):
            proc = subprocess.run(
                ["rpm2cpio", str(srpm_path)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["cpio", "-idmv"],
                check=True,
                cwd=tmp,
                input=proc.stdout,
                capture_output=True,
            )
        else:
            raise RuntimeError(
                "Need bsdtar or (rpm2cpio and cpio) to unpack SRPM"
            )

        specs = list(tmp.glob("*.spec"))
        if not specs:
            specs = list(tmp.rglob("*.spec"))
        if not specs:
            raise RuntimeError("No .spec found after unpacking SRPM")
        # Prefer freeipa.spec
        for p in specs:
            if p.name.lower() == "freeipa.spec":
                return p.read_text(encoding="utf-8", errors="replace")
        return specs[0].read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_upstream_branch(spec_body: str, default: str = "ipa-4-13") -> str:
    """Best-effort: dist-git specs sometimes define upstream branch."""
    m = re.search(r"^\s*%global\s+upstream_branch\s+(\S+)\s*$", spec_body, re.MULTILINE | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"^\s*%global\s+git_branch\s+(\S+)\s*$", spec_body, re.MULTILINE | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return default
