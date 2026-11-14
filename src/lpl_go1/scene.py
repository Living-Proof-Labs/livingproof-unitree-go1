# SPDX-License-Identifier: Apache-2.0
"""The Playground Go1 scene.

The Playground MJCF is loaded unchanged: its solver options and its feet-only collision
pairs are not touched. This package does not load a world. A caller that runs a scenario
adds that world's geometry to the compiled model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from livingproof_sim_mujoco.pins import load_pins, local
from livingproof_sim_mujoco.scene import actuator_table
import mujoco

from lpl_go1 import ROOT

XMLS = "mujoco_playground/_src/locomotion/go1/xmls"
SCENE = XMLS + "/scene_mjx_feetonly_flat_terrain.xml"
MESHES = "unitree_go1/assets"


@dataclass
class Scene:
    model: mujoco.MjModel
    position_kp: dict[str, float]  # published <position> gains, by actuator name
    ctrlrange: dict[str, tuple[float, float]]
    walls: list[str]  # empty: the robot model has no world geometry


def assets(root: Path = ROOT) -> dict[str, bytes]:
    """File name -> bytes, as Playground's go1 ``base.get_assets`` builds it: the go1 XMLs
    and the menagerie Go1 meshes, looked up by base name."""
    pins = load_pins(root)["groups"]
    out = {}
    for group, prefix in (("playground", XMLS + "/"), ("menagerie", MESHES + "/")):
        for path in pins[group]["files"]:
            if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                out[Path(path).name] = local(group, path, root).read_bytes()
    return out


def build(actuation: str = "position", root: Path = ROOT) -> Scene:
    """Compile the Playground scene. Go1 runs on Playground's own <position> actuators only."""
    if actuation != "position":
        raise ValueError(f"unknown actuation {actuation!r}; Go1 uses position actuators")
    load_pins(root)
    files = assets(root)
    spec = mujoco.MjSpec.from_string(
        local("playground", SCENE, root).read_text(),
        include={k: v for k, v in files.items() if k.endswith(".xml")},
        assets=files,
    )
    compiled = spec.compile()
    position_kp, ctrlrange = actuator_table(compiled)
    return Scene(compiled, position_kp, ctrlrange, [])
