# SPDX-License-Identifier: Apache-2.0
"""Playground's Go1 sim2sim loop (play_go1_joystick.py @ 12ad7f3), and a tick-by-tick
comparison of our runner against it.

``OnnxController`` below is the upstream class with two changes only: the gamepad is a
constant command, and every policy call is recorded. The loop is the viewer's: the control
callback runs inside each ``mj_step``. Both sides start from ``mj_resetDataKeyframe(home)``
on the same compiled model and use the same command, so their observations and actions
must agree at every policy tick.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import mujoco
import numpy as np
import onnxruntime as rt

from lpl_go1 import ROOT
from lpl_go1.profiles import controller
from lpl_go1.scene import build

TERMS = [  # (name, size) in play_go1_joystick.py get_obs order
    ("local_linvel", 3),
    ("gyro", 3),
    ("gravity", 3),
    ("joint_pos", 12),
    ("joint_vel", 12),
    ("last_action", 12),
    ("command", 3),
]


class OnnxController:
    """play_go1_joystick.py OnnxController; the Gamepad is replaced by ``command``."""

    def __init__(self, policy_path, default_angles, n_substeps, command, action_scale=0.5):
        self._output_names = ["continuous_actions"]
        self._policy = rt.InferenceSession(policy_path, providers=["CPUExecutionProvider"])
        self._action_scale = action_scale
        self._default_angles = default_angles
        self._last_action = np.zeros_like(default_angles, dtype=np.float32)
        self._counter = 0
        self._n_substeps = n_substeps
        self._command = np.asarray(command, dtype=np.float64)
        self.log = []  # (physics step index, obs, action)

    def get_obs(self, model, data) -> np.ndarray:
        linvel = data.sensor("local_linvel").data
        gyro = data.sensor("gyro").data
        imu_xmat = data.site_xmat[model.site("imu").id].reshape(3, 3)
        gravity = imu_xmat.T @ np.array([0, 0, -1])
        joint_angles = data.qpos[7:] - self._default_angles
        joint_velocities = data.qvel[6:]
        obs = np.hstack(
            [
                linvel,
                gyro,
                gravity,
                joint_angles,
                joint_velocities,
                self._last_action,
                self._command,
            ]
        )
        return obs.astype(np.float32)

    def get_control(self, model, data) -> None:
        self._counter += 1
        if self._counter % self._n_substeps == 0:
            obs = self.get_obs(model, data)
            onnx_pred = self._policy.run(self._output_names, {"obs": obs.reshape(1, -1)})[0][0]
            self._last_action = onnx_pred.copy()
            data.ctrl[:] = onnx_pred * self._action_scale + self._default_angles
            self.log.append((self._counter - 1, obs, onnx_pred.copy()))


def reference(command, ticks: int, root: Path = ROOT) -> tuple[list, np.ndarray]:
    scene = build("position", root)
    model, data = scene.model, None
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    model.opt.timestep = 0.004  # as load_callback sets it
    ctrl = controller("playground", scene, root, strict=False)  # only its policy path
    pol = OnnxController(
        str(ctrl.policy_path), np.array(model.keyframe("home").qpos[7:]), 5, command
    )
    mujoco.set_mjcb_control(pol.get_control)
    try:
        while len(pol.log) < ticks:
            mujoco.mj_step(model, data)
    finally:
        mujoco.set_mjcb_control(None)
    return pol.log[:ticks], data.qpos.copy()


def ours(
    command, ticks: int, root: Path = ROOT, strict: bool = True, spec_overrides=None
) -> tuple[list, np.ndarray]:
    from livingproof_sim_mujoco.policy import OnnxPolicy, PolicySpec

    scene = build("position", root)
    model = scene.model
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)
    ctrl = controller("playground", scene, root, strict=strict)
    spec = ctrl.spec
    if spec_overrides:
        spec = PolicySpec(**{**spec.__dict__, **spec_overrides})
    policy = OnnxPolicy(model, ctrl.policy_path, spec)
    log, backend = [], policy.backend
    step = {"i": 0}

    def recording(obs):
        out = backend(obs)
        log.append((step["i"], obs.copy(), np.asarray(out).copy()))
        return out

    policy.backend = recording
    while len(log) < ticks:
        policy.step(data, command)
        step["i"] += 1
    return log[:ticks], data.qpos.copy()


def compare(command=(0.5, 0.0, 0.0), ticks: int = 500, root: Path = ROOT, strict=True) -> dict:
    ref, ref_q = reference(command, ticks, root)
    got, got_q = ours(command, ticks, root, strict)
    first, worst = None, {"obs": 0.0, "action": 0.0}
    per_term = {n: 0.0 for n, _ in TERMS}
    for k, ((ri, ro, ra), (gi, go, ga)) in enumerate(zip(ref, got)):
        d_obs, d_act = np.abs(ro - go), np.abs(ra - ga)
        worst["obs"] = max(worst["obs"], float(d_obs.max()))
        worst["action"] = max(worst["action"], float(d_act.max()))
        o = 0
        terms = {}
        for name, n in TERMS:
            terms[name] = float(d_obs[o : o + n].max())
            per_term[name] = max(per_term[name], terms[name])
            o += n
        if first is None and (ri != gi or d_obs.max() > 0 or d_act.max() > 0):
            first = {
                "tick": k,
                "reference_step": ri,
                "runner_step": gi,
                "max_obs_diff_by_term": terms,
                "max_action_diff": float(d_act.max()),
            }
    return {
        "reference": "play_go1_joystick.py OnnxController @ 12ad7f3 (constant command)",
        "command": list(command),
        "ticks": ticks,
        "first_policy_step": {"reference": ref[0][0], "runner": got[0][0]},
        "bit_exact": first is None,
        "first_divergence": first,
        "max_abs_diff": worst,
        "max_obs_diff_by_term": per_term,
        "final_base_xy": {
            "reference": [round(float(v), 4) for v in ref_q[:2]],
            "runner": [round(float(v), 4) for v in got_q[:2]],
        },
    }


def drift(seconds: float = 10.0, root: Path = ROOT) -> dict:
    """Does the reference itself drift at zero command?"""
    _, q = reference((0.0, 0.0, 0.0), int(round(seconds / 0.02)), root)
    return {
        "reference_zero_command_s": seconds,
        "base_xy": [round(float(v), 4) for v in q[:2]],
        "drift_m": round(float(np.hypot(*q[:2])), 4),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticks", type=int, default=500)
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "reference-compare.json")
    ap.add_argument("--no-first-tick", action="store_true", help="runner without first_tick")
    a = ap.parse_args(argv)
    st = not a.no_first_tick
    res = {
        "walk_0.5": compare((0.5, 0.0, 0.0), a.ticks, strict=st),
        "stand_0": compare((0, 0, 0), a.ticks, strict=st),
        "reference_drift": drift(),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: (v.get("bit_exact"), v.get("first_divergence") and v["first_divergence"]["tick"])
                if "bit_exact" in v
                else v
                for k, v in res.items()
            },
            indent=2,
        )
    )
    return 0 if all(v.get("bit_exact", True) for v in res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
