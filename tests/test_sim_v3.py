# SPDX-License-Identifier: Apache-2.0
"""livingproof-sim-mujoco v3 contract for this robot package.

v3 evidence and stand require --lock, and v3 owns the world. pins.fetch requires
a byte count on every pin and checks it against the downloaded artifact.
"""

import json
import re
from pathlib import Path

from lpl_go1 import ROOT
from lpl_go1.scene import build
from livingproof_sim_mujoco.pins import local

COMMAND = re.compile(r"python(?:3)?\s+-m\s+livingproof_sim_mujoco\.(?:evidence|walk)\b")
SKIP_DIRS = {
    ".git",
    ".cache",
    ".venv",
    ".venv-docs",
    ".ruff_cache",
    "artifacts",
    "__pycache__",
    ".pytest_cache",
}


def _text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix in {".png", ".jpg", ".gif", ".webp", ".pyc", ".stl", ".npz"}:
            continue
        yield path


def test_every_pin_bytes_matches_the_fetched_artifact():
    groups = json.loads((ROOT / "pins.json").read_text())["groups"]
    mismatches = []
    for name, group in groups.items():
        for path, entry in group["files"].items():
            count = entry.get("bytes")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                mismatches.append(f"{name}:{path} bytes={count!r}")
                continue
            actual = local(name, path, ROOT).stat().st_size
            if actual != count:
                mismatches.append(f"{name}:{path} pin={count} file={actual}")
    assert mismatches == []


def test_evidence_and_stand_commands_pass_lock():
    """A command that runs evidence or stand must pass --lock. None do today."""
    offenders = []
    for path in _text_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(), 1
        ):
            code = line.split("#", 1)[0]
            if COMMAND.search(code) and "--lock" not in code:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert offenders == []


def test_the_robot_scene_does_not_add_a_world():
    scene = build()
    assert scene.walls == []
    source = Path(ROOT / "src/lpl_go1/scene.py").read_text(encoding="utf-8")
    assert "add_world" not in source
    assert "compose.place" not in source
