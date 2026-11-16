# SPDX-License-Identifier: Apache-2.0
"""The numpy pin keeps main's per-Python resolution.

Main's lock (6292c1ff) resolves numpy 2.4.6 for Python < 3.12 and numpy 2.5.4
for Python >= 3.12. A single ``numpy==2.4.6`` requirement collapses that fork
and downgrades the Python 3.12 path, which is the interpreter CI runs.
"""

import tomllib

from lpl_go1 import ROOT

# Versions main's uv.lock resolves at the fork. uv records python_version
# markers as python_full_version; both name the same 3.12 cut.
BELOW_312 = "2.4.6"
FROM_312 = "2.5.4"
LOCK_MARKERS = {
    "python_full_version < '3.12'": BELOW_312,
    "python_full_version >= '3.12'": FROM_312,
}


def _requirement_name(dep: str) -> str:
    name = dep.split(";", 1)[0].strip().split("[", 1)[0]
    for separator in ("=", ">", "<", "!", "~"):
        name = name.split(separator, 1)[0]
    return name.strip()


def _numpy_requirements() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return [dep for dep in project["project"]["dependencies"] if _requirement_name(dep) == "numpy"]


def _lock() -> dict:
    return tomllib.loads((ROOT / "uv.lock").read_text())


def test_pyproject_pins_numpy_separately_below_and_from_python_312():
    normalized = {req.replace('"', "'") for req in _numpy_requirements()}
    assert normalized == {
        "numpy==2.4.6; python_version < '3.12'",
        "numpy==2.5.4; python_version >= '3.12'",
    }


def test_lock_agrees_and_python_312_stays_on_mains_numpy():
    lock = _lock()
    resolved = {}
    for package in lock["package"]:
        if package["name"] != "numpy":
            continue
        markers = package.get("resolution-markers")
        assert markers, "a numpy pin with no Python marker downgrades the 3.12 path"
        assert len(markers) == 1, markers
        marker = markers[0]
        assert marker not in resolved, marker
        resolved[marker] = package["version"]
    assert resolved == LOCK_MARKERS
    assert resolved["python_full_version >= '3.12'"] == FROM_312

    project = next(pkg for pkg in lock["package"] if pkg["name"] == "livingproof-unitree-go1")
    declared = [
        (item.get("marker"), item["specifier"])
        for item in project["metadata"]["requires-dist"]
        if item["name"] == "numpy"
    ]
    expected = [(marker, f"=={version}") for marker, version in LOCK_MARKERS.items()]
    assert sorted(declared) == sorted(expected)
