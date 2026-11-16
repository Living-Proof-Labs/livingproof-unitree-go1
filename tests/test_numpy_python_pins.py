# SPDX-License-Identifier: Apache-2.0
"""Numpy stays forked at Python 3.12, without freezing the exact releases.

Python below 3.12 stays on a numpy older than 2.5. Python 3.12 and newer stays
on at least 2.5.4. pyproject, the lock's requires-dist, the locked numpy for
that fork, and the installed numpy name the same version. Renovate may move the
2.4 line inside 2.4.x, and may raise the 3.12 line from 2.5.4, without making
this test fail.
"""

import json
import re
import sys
import tomllib

import numpy
from packaging.markers import Marker, default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from lpl_go1 import ROOT

# numpy 2.5 requires Python >= 3.12. 2.5.4 is the floor already resolved for that
# side; later 2.5 releases stay acceptable. These are bounds, not frozen pins.
BELOW_312_CEILING = Version("2.5")
FROM_312_FLOOR = Version("2.5.4")
PYTHON_CUT = Version("3.12")
# One interpreter below the cut, the cut itself, and one newer release.
CHECKED_PYTHONS = ("3.11.0", "3.12.0", "3.13.1")
ROOT_PACKAGE = "livingproof-unitree-go1"
RENOVATE_RULE = {
    "description": (
        "numpy 2.5+ requires Python >=3.12; keep the python_version < '3.12' line on 2.4.x."
    ),
    "matchManagers": ["pep621"],
    "matchPackageNames": ["numpy"],
    "matchCurrentValue": "/^==2\\.4\\./",
    "allowedVersions": "<2.5",
}
_PYTHON_VERSION = re.compile(r"(?m)^[ \t]*python-version:\s*['\"]?([^'\"#\s]+)['\"]?\s*$")


def _environment(python_full: str) -> dict[str, str]:
    env = default_environment()
    env["python_full_version"] = python_full
    env["python_version"] = ".".join(python_full.split(".")[:2])
    return env


def _applies(marker: str | Marker | None, env: dict[str, str]) -> bool:
    if marker is None:
        return True
    if not isinstance(marker, Marker):
        marker = Marker(marker)
    return marker.evaluate(env)


def _fork_applies(markers: list[str] | None, env: dict[str, str]) -> bool:
    """A missing marker list is the collapsed single pin: it matches every Python."""
    if not markers:
        return True
    return any(Marker(marker).evaluate(env) for marker in markers)


def _exact_version(specifier: str | SpecifierSet) -> str | None:
    parsed = specifier if isinstance(specifier, SpecifierSet) else SpecifierSet(specifier or "")
    specs = list(parsed)
    if len(specs) == 1 and specs[0].operator == "==":
        return specs[0].version
    return None


def _numpy_requirements(dependencies: list[str]) -> list[Requirement]:
    return [Requirement(dep) for dep in dependencies if Requirement(dep).name == "numpy"]


def _selected_versions(dependencies: list[str], python_full: str) -> list[str | None]:
    env = _environment(python_full)
    return [
        _exact_version(req.specifier)
        for req in _numpy_requirements(dependencies)
        if _applies(req.marker, env)
    ]


def numpy_pin_problems(
    dependencies: list[str], packages: list[dict], pythons: tuple[str, ...]
) -> list[str]:
    """Problems when a Python does not get exactly one in-bounds numpy that every source shares."""
    distributions = [pkg for pkg in packages if pkg.get("name") == "numpy" and "version" in pkg]
    roots = [pkg for pkg in packages if pkg.get("name") == ROOT_PACKAGE]
    if len(roots) != 1:
        return [f"expected one {ROOT_PACKAGE} lock entry, found {len(roots)}"]
    declared = [
        item
        for item in roots[0].get("metadata", {}).get("requires-dist", [])
        if item.get("name") == "numpy"
    ]
    problems: list[str] = []
    for python_full in pythons:
        env = _environment(python_full)
        where = f"Python {python_full}"
        pinned = _selected_versions(dependencies, python_full)
        if len(pinned) != 1 or pinned[0] is None:
            problems.append(f"{where} pyproject selects {pinned}, expected exactly one numpy pin")
            continue
        version = pinned[0]
        required = [
            _exact_version(item.get("specifier") or "")
            for item in declared
            if _applies(item.get("marker"), env)
        ]
        if required != [version]:
            problems.append(f"{where} requires-dist is {required}, pyproject pin is {version}")
        locked = [
            pkg["version"]
            for pkg in distributions
            if _fork_applies(pkg.get("resolution-markers"), env)
        ]
        if locked != [version]:
            problems.append(f"{where} locked numpy is {locked}, pyproject pin is {version}")
        parsed = Version(version)
        if Version(python_full) < PYTHON_CUT:
            if not parsed < BELOW_312_CEILING:
                problems.append(f"{where} numpy {version} is not < {BELOW_312_CEILING}")
        elif parsed < FROM_312_FLOOR:
            problems.append(f"{where} numpy {version} is below the {FROM_312_FLOOR} floor")
    return problems


def _project_dependencies() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return project["project"]["dependencies"]


def _lock_packages() -> list[dict]:
    return tomllib.loads((ROOT / "uv.lock").read_text())["package"]


def _lock_numpy_forks() -> tuple[str, str]:
    """Versions the lock itself resolves below 3.12 and from 3.12."""
    below: list[str] = []
    at_least: list[str] = []
    for package in _lock_packages():
        if package.get("name") != "numpy" or "version" not in package:
            continue
        markers = package.get("resolution-markers") or []
        if markers == ["python_full_version < '3.12'"]:
            below.append(package["version"])
        elif markers == ["python_full_version >= '3.12'"]:
            at_least.append(package["version"])
    assert len(below) == 1 and len(at_least) == 1, (below, at_least)
    return below[0], at_least[0]


def _pythons_under_test() -> tuple[str, ...]:
    running = ".".join(str(part) for part in sys.version_info[:3])
    if running in CHECKED_PYTHONS:
        return CHECKED_PYTHONS
    return (*CHECKED_PYTHONS, running)


def test_each_python_gets_one_numpy_pin_and_sources_agree():
    dependencies = _project_dependencies()
    packages = _lock_packages()
    pythons = _pythons_under_test()
    assert numpy_pin_problems(dependencies, packages, pythons) == []
    running = ".".join(str(part) for part in sys.version_info[:3])
    assert numpy.__version__ == _selected_versions(dependencies, running)[0]


def test_renovate_keeps_the_python_below_312_numpy_line_on_2_4():
    config = json.loads((ROOT / "renovate.json").read_text())
    assert RENOVATE_RULE in config.get("packageRules", [])


def test_single_pin_of_the_older_lock_fork_downgrades_python_312():
    """The pin Vera rejected, built from this lock's own fork versions.

    One ``numpy==<the Python < 3.12 lock version>`` with no marker resolves that
    older release on Python >= 3.12. The lock's Python >= 3.12 release meets the
    floor; the collapsed pin does not.
    """
    below, at_least = _lock_numpy_forks()
    assert Version(below) < Version(at_least)
    assert Version(below) < BELOW_312_CEILING
    assert Version(at_least) >= FROM_312_FLOOR
    packages = [
        {"name": "numpy", "version": below},
        {
            "name": ROOT_PACKAGE,
            "metadata": {"requires-dist": [{"name": "numpy", "specifier": f"=={below}"}]},
        },
    ]
    problems = numpy_pin_problems([f"numpy=={below}"], packages, CHECKED_PYTHONS)
    assert not any(problem.startswith("Python 3.11.0") for problem in problems)
    downgraded = [
        problem
        for problem in problems
        if problem.startswith("Python 3.12.0") or problem.startswith("Python 3.13.1")
    ]
    assert [problem.split()[1] for problem in downgraded] == ["3.12.0", "3.13.1"]
    floor = f"numpy {below} is below the {FROM_312_FLOOR} floor"
    assert all(floor in problem for problem in downgraded)


def test_workflow_python_versions_agree():
    found: dict[str, list[str]] = {}
    workflows = ROOT / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")):
        text = path.read_text()
        versions = _PYTHON_VERSION.findall(text)
        if "setup-python" in text:
            assert versions, f"{path.name} uses setup-python without python-version"
        if versions:
            found[path.name] = versions
    assert found
    values = {version for versions in found.values() for version in versions}
    assert len(values) == 1, found
