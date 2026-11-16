# SPDX-License-Identifier: Apache-2.0
"""The robot manifest declares the scenarios this adapter supports.

robot-capability-specs v2.3.0 (57317e6) puts that list on
livingproof-robot-manifest/0.1. This package declares walk-hallway and does not
declare walk-corner. This file is excluded from the scenario-name search because
it contains those ids.
"""

import json

from jsonschema import Draft202012Validator

from lpl_go1 import ROOT
from livingproof_sim_mujoco.pins import local

SPEC_COMMIT = "57317e617014a6df97bb7fba9d92a890af8d42e5"
SCHEMA = "specs/robots/robot-manifest.schema.json"


def test_manifest_declares_exactly_walk_hallway():
    """Exactly walk-hallway, no walk-corner, and the field validates against the spec."""
    problems = []
    path = ROOT / "manifest.json"
    if not path.is_file():
        problems.append("manifest.json is missing")
        manifest = None
    else:
        text = path.read_text()
        manifest = json.loads(text)
        scenarios = manifest.get("scenarios")
        if scenarios != ["walk-hallway"]:
            problems.append(f"scenarios is {scenarios!r}, expected ['walk-hallway']")
        if "walk-corner" in text:
            problems.append("walk-corner is declared")
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
                if loaded.get("scenarios") != ["walk-hallway"]:
                    problems.append(f"loader returned {loaded.get('scenarios')!r}")
    assert problems == []
