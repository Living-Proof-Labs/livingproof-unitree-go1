# SPDX-License-Identifier: Apache-2.0
"""This repository does not select a world or a scenario.

No recorded scenario evidence is stored here, so there is no legacy reader and
the allowlist is empty. The playground reference trace is a controller rollout,
not scenario evidence. This file is excluded from the name search because it
contains the search terms.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = Path(__file__)
# Names of scenarios and worlds. The search is case-insensitive.
NAMES = re.compile(r"hallway|walk-hallway|hallway-open|corner", re.IGNORECASE)
SELECTING_KEYS = {"world", "worlds", "scenario"}
LEGACY_EVIDENCE: tuple[str, ...] = ()
SKIP_DIRS = {
    ".git",
    ".cache",
    ".venv",
    ".venv-docs",
    "artifacts",
    "downloads",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
BINARY_SUFFIXES = {".npz", ".stl", ".png", ".jpg", ".gif", ".webp", ".pyc"}


def _legacy(path: Path) -> bool:
    relative = path.relative_to(ROOT).as_posix()
    return relative in LEGACY_EVIDENCE or any(
        relative.startswith(allowed.rstrip("/") + "/") for allowed in LEGACY_EVIDENCE
    )


def _tracked_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix in BINARY_SUFFIXES:
            continue
        yield path


def test_scenario_and_world_names_stay_out_of_the_package():
    assert LEGACY_EVIDENCE == ()
    offenders = []
    for path in _tracked_files():
        if path.resolve() == GUARD.resolve() or _legacy(path):
            continue
        relative = path.relative_to(ROOT).as_posix()
        if NAMES.search(path.name):
            offenders.append(relative)
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        if NAMES.search(text):
            offenders.append(relative)
    assert offenders == []


def _keys(value, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            found.append(str(key))
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


def test_pins_runner_and_locks_have_no_world_or_scenario_keys():
    assert not (ROOT / "verification.lock.json").exists()
    documents = [ROOT / "pins.json", ROOT / "runner.json", *ROOT.glob("*.lock.json")]
    documents += list(ROOT.glob("**/*.lock.json"))
    seen = set()
    for path in documents:
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if not path.is_file() or path in seen:
            continue
        seen.add(path)
        found: list[str] = []
        _keys(json.loads(path.read_text(encoding="utf-8")), found)
        selected = sorted(SELECTING_KEYS.intersection(found))
        assert selected == [], f"{path.relative_to(ROOT)} has {selected}"


def test_workflows_do_not_take_a_world_or_scenario():
    workflow_dir = ROOT / ".github/workflows"
    assert workflow_dir.is_dir()
    for path in sorted(workflow_dir.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert NAMES.search(text) is None, path.name
        for line in text.splitlines():
            stripped = line.split("#", 1)[0]
            match = re.match(r"^\s*-?\s*(world|worlds|scenario)\s*:", stripped)
            assert match is None, f"{path.name}: {line}"
