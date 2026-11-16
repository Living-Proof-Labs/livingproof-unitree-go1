# SPDX-License-Identifier: Apache-2.0
"""The robot manifest declares the scenarios this adapter supports.

robot-capability-specs v2.3.0 (57317e6) puts that list on
livingproof-robot-manifest/0.1. This package declares walk-hallway and walk-corner,
which the adapter drives itself (``drive``, robot-capability-specs#40 step 11b). This file is excluded from the scenario-name search because
it contains those ids.
"""

import hashlib
import json

from jsonschema import Draft202012Validator

from lpl_go1 import ROOT
from livingproof_sim_mujoco.pins import local

SPEC_COMMIT = "57317e617014a6df97bb7fba9d92a890af8d42e5"
SCHEMA = "specs/robots/robot-manifest.schema.json"


def test_manifest_declares_walk_hallway_and_walk_corner():
    """walk-hallway and walk-corner, and the field validates against the spec."""
    problems = []
    path = ROOT / "manifest.json"
    if not path.is_file():
        problems.append("manifest.json is missing")
        manifest = None
    else:
        text = path.read_text()
        manifest = json.loads(text)
        scenarios = manifest.get("scenarios")
        if scenarios != ["walk-hallway", "walk-corner"]:
            problems.append(f"scenarios is {scenarios!r}, expected ['walk-hallway', 'walk-corner']")
    spec = json.loads((ROOT / "pins.json").read_text())["groups"]["specs"]
    if spec.get("tag") != "v2.3.0" or spec.get("commit") != SPEC_COMMIT:
        problems.append(
            f"specs pin is {spec.get('tag')} {str(spec.get('commit'))[:7]}, expected v2.3.0 57317e6"
        )
    elif manifest is not None:
        schema = json.loads(local("specs", SCHEMA, ROOT).read_text())
        if schema.get("title") != "livingproof-robot-manifest/0.1":
            problems.append(f"schema title is {schema.get('title')!r}")
        if "scenarios" not in schema.get("properties", {}):
            problems.append("pinned robot-manifest schema has no scenarios field")
        else:
            errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=str)
            if errors:
                problems.append(
                    "manifest does not match robot-manifest.schema.json: "
                    + "; ".join(e.message for e in errors[:5])
                )
            else:
                from lpl_go1.profiles import manifest as load_manifest

                loaded = load_manifest()
                if loaded.get("scenarios") != ["walk-hallway", "walk-corner"]:
                    problems.append(f"loader returned {loaded.get('scenarios')!r}")
    assert problems == []


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Published configuration versions. A byte change needs a new version and a new digest here.
# playground 1.0.0 is the file at main b89bfa5.
PUBLISHED_CONFIGURATION = {
    ("configurations/playground.json", "1.0.0"): (
        "07bcbab38570ddb1c76c8508916298f2dc633be1543a78af7971bec338d6f930"
    ),
}


def test_a_configuration_version_keeps_one_digest():
    """A profile version is one file. playground 1.0.0 keeps the published digest.

    robot-capability-specs v2.3.0: any change to a configuration profile changes its
    sha256 and needs a new version. The manifest cites that version and sha256, and
    its assets match pins.json. The check uses the recorded digest so a shallow
    checkout can run it.
    """
    manifest = json.loads((ROOT / "manifest.json").read_text())
    problems = []
    profile_path = manifest["profile"]["path"]
    profile_bytes = (ROOT / profile_path).read_bytes()
    if _sha256(profile_bytes) != manifest["profile"]["sha256"]:
        problems.append(f"{profile_path} sha256 differs from the manifest")
    for item in manifest["configurations"]:
        path = item["path"]
        current = json.loads((ROOT / path).read_text())
        file_bytes = (ROOT / path).read_bytes()
        digest = _sha256(file_bytes)
        if current["version"] != item["version"]:
            problems.append(
                f"{path} version is {current['version']}, manifest says {item['version']}"
            )
        if digest != item["sha256"]:
            problems.append(f"{path} sha256 differs from the manifest")
        published = PUBLISHED_CONFIGURATION.get((path, current["version"]))
        if published is not None and digest != published:
            problems.append(
                f"{path} version {current['version']} changed bytes without a new version"
            )
    pins = json.loads((ROOT / "pins.json").read_text())["groups"]
    expected = []
    for name, group in pins.items():
        for path, entry in group["files"].items():
            expected.append(
                {
                    "group": name,
                    "repository": group["repository"],
                    "commit": group["commit"],
                    "path": path,
                    "sha256": entry["sha256"],
                    "bytes": entry["bytes"],
                }
            )
    if manifest["assets"] != expected:
        problems.append("manifest assets differ from pins.json")
    assert problems == []
