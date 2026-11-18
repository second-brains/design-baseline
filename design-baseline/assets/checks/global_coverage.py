#!/usr/bin/env python3
"""Example global checker: every registered requirement must reach a story."""
import re
import sys
from pathlib import Path

import yaml


ROOT = Path.cwd()
DESIGN = ROOT / "design"
FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.S)
PIN = re.compile(r"^([A-Z][A-Z0-9]*-\d+)@v\d+$")


requirements = yaml.safe_load((DESIGN / "facts/requirements.yaml").read_text(encoding="utf-8")) or {}
requirements = {key for key in requirements if str(key).startswith("REQ-")}
covered = set()
for path in (DESIGN / "docs").rglob("*.md"):
    match = FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        continue
    metadata = yaml.safe_load(match.group(1)) or {}
    if metadata.get("type") != "story":
        continue
    for reference in metadata.get("based_on") or []:
        pin = PIN.match(str(reference))
        if pin:
            covered.add(pin.group(1))

missing = sorted(requirements - covered)
if missing:
    print("Requirements without a story: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
print(f"Requirement to story coverage: {len(requirements)} requirements checked")
