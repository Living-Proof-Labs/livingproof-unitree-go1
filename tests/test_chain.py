# SPDX-License-Identifier: Apache-2.0
"""The robot package keeps an adapter and configurations.

Integration pins live in livingproof-verification. This repository does not
keep a verification lock, a repin task, or a scenario evidence entry point.
"""

import importlib.util
import re
import tomllib
from pathlib import Path

import lpl_go1
from lpl_go1 import ROOT
from lpl_go1.adapter import ROBOT


def test_adapter_library_is_a_released_tag():
    """livingproof-sim-mujoco is the library this adapter imports, pinned by tag."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    source = pyproject["tool"]["uv"]["sources"]["livingproof-sim-mujoco"]
    assert set(source) == {"git", "tag"}
    assert re.fullmatch(r"v\d+\.\d+\.\d+", source["tag"])
    assert source["git"].endswith("livingproof-sim-mujoco.git")
    uv_lock = (ROOT / "uv.lock").read_text()
    assert f"livingproof-sim-mujoco.git?tag={source['tag']}#" in uv_lock
    assert pyproject["tool"]["livingproof"]["adapter"] == "lpl_go1.adapter:ROBOT"


def test_there_is_no_verification_lock_or_evidence_entry_point():
    assert not (ROOT / "verification.lock.json").exists()
    assert not (ROOT / "tools/repin.py").exists() and not (ROOT / "tools/pr_comment.py").exists()
    assert not (ROOT / "src/lpl_go1/scenario.py").exists()
    assert not (ROOT / "src/lpl_go1/evidence.py").exists()
    assert importlib.util.find_spec("lpl_go1.evidence") is None
    assert importlib.util.find_spec("lpl_go1.scenario") is None


def test_test_workflow_runs_on_pull_request():
    """CI runs the suite on pull requests. The old scenario workflow is gone."""
    path = ROOT / ".github/workflows/test.yml"
    body = "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert re.search(r"^on:\n(?:[ \t].*\n)*?[ \t]*pull_request:\n", body, re.M)
    assert re.search(
        r"^on:\n(?:[ \t].*\n)*?[ \t]*push:\n(?:[ \t].*\n)*?[ \t]*branches: \[main\]\n", body, re.M
    )
    assert "ubuntu-24.04" in body and "ubuntu-24.04-arm" in body
    permissions = re.search(r"^permissions:\n((?:  .+\n)+)", body, re.M)
    assert permissions is not None and permissions.group(1).strip() == "contents: read"
    assert "uv run --no-sync ruff check ." in body
    assert "uv run --no-sync ruff format --check ." in body
    assert "uv run --no-sync pytest tests" in body
    assert "upload" not in body and "secrets." not in body
    docs = (ROOT / ".github/workflows/docs.yml").read_text()
    for action in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@e797f83bcb11b83ae66e0230d6156d7c80228e7c",
    ):
        assert action in body and action in docs
    assert "workflow_call" not in body
    assert "uses: Living-Proof-Labs/capability-verifier/" not in body
    assert not (ROOT / ".github/workflows/verify.yml").exists()
    assert sorted(p.name for p in (ROOT / ".github/workflows").iterdir()) == [
        "docs.yml",
        "release.yml",
        "test.yml",
    ]


def test_robot_repository_does_no_grading():
    package = Path(lpl_go1.__file__).parent
    assert not (package / "clearance.py").exists() and not (package / "stats.py").exists()
    for name in ("evaluate", "grade", "compare", "interval", "world"):
        assert not hasattr(ROBOT, name), name


def test_ci_runs_through_the_adapter_without_repin_or_evidence_tasks():
    assert ROBOT.root == ROOT and ROBOT.id == "unitree-go1"
    taskfile = (ROOT / "Taskfile.yml").read_text()
    for generic in ("setup", "fetch", "check", "test", "evidence", "repin", "repin:check", "stand"):
        assert f"\n  {generic}:\n" not in taskfile, generic
