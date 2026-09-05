#!/usr/bin/env python3
"""Fail public-release checks if tracked files contain private deployment details."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# These are deliberate indicators of a private environment, not generic app docs.
FORBIDDEN = (
    r"\b(?:truenas|hexos|tailscale|tailnet)\b",
    r"\b(?:192\.168\.|172\.(?:1[6-9]|2[0-9]|3[0-1])\.|10\.\d{1,3}\.|100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.)\d{1,3}\.\d{1,3}\b",
    r"(?:/opt/data|/mnt/HDDs|root@|\.ssh/truenas_key)",
    r"github_pat_[A-Za-z0-9_]+",
    r"\bghp_[A-Za-z0-9]+",
    r"-----BEGIN (?:(?:RSA|OPENSSH|EC|DSA|ENCRYPTED) )?PRIVATE KEY-----",
)
PATTERN = re.compile("|".join(FORBIDDEN), re.IGNORECASE)
EXCLUDED = {"scripts/public_safety_scan.py", ".github/workflows/public-safety.yml"}


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
    return [Path(value) for value in result.stdout.decode().split("\0") if value]


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        if path.as_posix() in EXCLUDED:
            continue
        try:
            if '--staged' in sys.argv:
                raw = subprocess.run(['git', 'show', ':' + path.as_posix()], check=True, capture_output=True).stdout
                text = raw.decode('utf-8')
            else:
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"{path}: binary files are not permitted in the public source repository")
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                findings.append(f"{path}:{line_number}: private deployment detail or credential-like value")
    if findings:
        print("Public-safety scan failed:", *findings, sep="\n", file=sys.stderr)
        return 1
    print("Public-safety scan passed: no private topology or credential markers in tracked source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
