# SPDX-License-Identifier: Apache-2.0
"""Robot and configuration profiles (robot-capability-specs v1.5.0), validated against
the pinned schemas, and the runner spec built from the selected configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from livingproof_sim_mujoco.walk import joint_dofs  # noqa: F401
import numpy as np
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from livingproof_sim_mujoco.policy import ObsTerm, PolicySpec

from lpl_go1 import ROOT
from lpl_go1.fetch import load_pins, local, sha256
from lpl_go1.scene import Scene

SCHEMAS = {
    "robot": "specs/robots/robot-profile.schema.json",
    "configuration": "specs/robots/configuration-profile.schema.json",
    "manifest": "specs/robots/run-manifest.schema.json",
}


class Unsupported(Exception):
    """A scenario precondition is not met; ``precondition`` names it."""

    def __init__(self, precondition: str, detail: str):
        super().__init__(f"{precondition}: {detail}")
        self.precondition, self.detail = precondition, detail


def validate(kind: str, document: dict, root: Path = ROOT) -> None:
    loaded = {k: json.loads(local("specs", v, root).read_text()) for k, v in SCHEMAS.items()}
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in loaded.values()
    )  # resolve cross-schema $refs from the pinned files, never the network
    validator = Draft202012Validator(loaded[kind], registry=registry)
    errors = sorted(validator.iter_errors(document), key=str)
    if errors:
        raise ValueError(
            f"{kind} does not match {SCHEMAS[kind]}: "
            + "; ".join(
                f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5]
            )
        )


def robot(root: Path = ROOT) -> dict:
    doc = json.loads((root / "robot.json").read_text())
    validate("robot", doc, root)
    return doc


def configuration_path(name: str, root: Path = ROOT) -> Path:
    offered = {c["name"]: c["path"] for c in robot(root)["configurations"]}
    if name not in offered:
        raise Unsupported(
            "configuration_offered", f"{name!r} is not in robot.json {sorted(offered)}"
        )
    return root / offered[name]


def configuration(name: str, root: Path = ROOT) -> tuple[dict, str]:
    """The configuration profile and the sha256 of its file bytes."""
    path = configuration_path(name, root)
    doc = json.loads(path.read_text())
    validate("configuration", doc, root)
    if doc["name"] != name or doc["robot"] != robot(root)["id"]:
        raise ValueError(f"{path} names {doc['robot']}/{doc['name']}, expected {name}")
    return doc, sha256(path)


def artifact_path(pin: dict, root: Path = ROOT) -> Path:
    """The pins.json file matching a profile's pinned artifact; checks commit and sha256."""
    for group, g in load_pins(root)["groups"].items():
        if pin["repo"].endswith(g["repository"]) and pin["path"] in g["files"]:
            if g["commit"] != pin["commit"] or g["files"][pin["path"]]["sha256"] != pin["sha256"]:
                raise ValueError(f"{pin['path']}: profile pin differs from pins.json")
            path = local(group, pin["path"], root)
            if sha256(path) != pin["sha256"]:
                raise RuntimeError(f"{path} sha256 mismatch; run python -m lpl_go1.fetch")
            return path
    raise KeyError(f"{pin['repo']} {pin['path']} is not in pins.json")


@dataclass
class Controller:
    configuration: dict
    sha256: str
    policy_path: Path
    spec: PolicySpec
    initial_qpos: np.ndarray  # full qpos, base at the origin; spawn pose applied later


def go1_spec(conf: dict, scene: Scene, qpos: np.ndarray) -> PolicySpec:
    """play_go1_joystick.py: obs [local_linvel, gyro, gravity, q - home, dq, last_action,
    command]; ctrl = home + action_scale * action on Playground's <position> actuators."""
    s, model = conf["settings"], scene.model
    obs = s["observation"]
    if obs["order"] != [
        "local_linvel",
        "gyro",
        "gravity",
        "joint_pos",
        "joint_vel",
        "last_action",
        "command",
    ]:
        raise ValueError(f"unsupported observation order {obs['order']}")
    dt = float(model.opt.timestep)
    if not np.isclose(dt, s["physics_dt_s"]):
        raise ValueError(f"model timestep {dt} != profile physics_dt_s {s['physics_dt_s']}")
    decimation = round(s["ctrl_dt_s"] / dt)
    if not np.isclose(decimation * dt, s["ctrl_dt_s"]):
        raise ValueError("ctrl_dt is not a whole number of physics steps")
    acts = [model.actuator(i).name for i in range(model.nu)]
    joints = [model.joint(model.actuator_trnid[i][0]).name for i in range(model.nu)]
    fields = dict(
        joints=joints,
        actuators=acts,
        default_pose=[float(qpos[model.joint(j).qposadr[0]]) for j in joints],
        action_scale=s["action_scale"],
        decimation=decimation,
        obs=[
            ObsTerm("sensor", obs["linvel_sensor"]),
            ObsTerm("sensor", obs["gyro_sensor"]),
            ObsTerm("gravity", obs["imu_site"]),
            ObsTerm("joint_pos"),
            ObsTerm("joint_vel"),
            ObsTerm("last_action"),
            ObsTerm("command"),
        ],
        actuation="position",
        target_range=[s["limits"]["target_range_rad"][j] for j in joints],
    )
    if "first_tick" in PolicySpec.__dataclass_fields__:
        fields["first_tick"] = s["first_tick"]
    elif s["first_tick"] != 0:
        raise RuntimeError(
            f"the profile needs first_tick={s['first_tick']} but this runner has no first_tick"
        )
    return PolicySpec(**fields)


def controller(name: str, scene: Scene, root: Path = ROOT, strict: bool = True) -> Controller:
    """Runner spec for one configuration. ``strict=False`` lets a runner without
    ``first_tick`` build the spec anyway (it then acts on step 0); used only to measure
    that mismatch against the reference."""
    conf, digest = configuration(name, root)
    s = conf["settings"]
    if s["adapter"] != "playground_go1_joystick":
        raise ValueError(f"unknown adapter {s['adapter']!r}")
    path = artifact_path(conf["controller"]["artifact"], root)
    model = scene.model
    qpos = model.key_qpos[model.key(s["initial_keyframe"]).id].copy()
    if strict:
        spec = go1_spec(conf, scene, qpos)
    else:
        spec = go1_spec({**conf, "settings": {**s, "first_tick": 0}}, scene, qpos)
    qpos[0:2] = 0.0
    return Controller(conf, digest, path, spec, qpos)
