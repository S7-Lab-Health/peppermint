#!/usr/bin/env python3
"""Reject edits to the reviewed infra release-lock export before using it."""
import hashlib
import json
from pathlib import Path
import re

folder = Path(__file__).resolve().parent / "vendor/release_lock"
manifest = json.loads((folder / "upstream.json").read_text())
assert manifest.get("repository") == "S7-Lab-Health/altair-infra"
assert manifest.get("path") == "scripts/ci/release_lock.sh"
assert re.fullmatch(r"[0-9a-f]{40}", manifest.get("revision", ""))
assert hashlib.sha256((folder / "release_lock.sh").read_bytes()).hexdigest() == manifest.get("sha256")
print("Reviewed release-lock export matches its pinned infra source")
