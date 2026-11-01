# SPDX-License-Identifier: Apache-2.0
import io
import json
import re

import mujoco
import numpy as np
import pytest

from lpl_go1 import ROOT
from lpl_go1 import scenario as sc
from lpl_go1.clearance import Clearance
from lpl_go1.fetch import ChecksumError, fetch, local, sha256
from lpl_go1.profiles import Unsupported, configuration, controller, robot, validate
from lpl_go1.scene import SCENE, assets, build, place_pose
from lpl_go1.stats import wilson
from lpl_go1.walk import initial_state, runner_settings

CONFIGS = ["playground"]
SCENARIO_README = "specs/scenarios/walk-hallway/README.md"
SIM2SIM = "mujoco_playground/experimental/sim2sim/play_go1_joystick.py"
JOYSTICK = "mujoco_playground/_src/locomotion/go1/joystick.py"


def test_profiles_match_the_pinned_v1_5_0_schemas():
    pins = json.loads((ROOT / "pins.json").read_text())["groups"]
    assert pins["specs"]["tag"] == "v1.5.0"
    assert pins["specs"]["commit"].startswith("1df6eaa")
    rob = robot()  # validates
    assert rob["id"] == "unitree-go1" and rob["class"] == "quadruped"
    assert [c["name"] for c in rob["configurations"]] == CONFIGS
    assert rob["default_configuration"] == "playground"
    conf, digest = configuration("playground")  # validates
    assert digest == sha256(ROOT / "configurations/playground.json")
    with pytest.raises(ValueError, match="robot does not match"):
        validate("robot", {**rob, "extra": 1})


def test_artifacts_and_model_source_are_pinned_files():
    ms = robot()["model_source"]
    assert sha256(local("playground", ms["path"])) == ms["sha256"]
    c = configuration("playground")[0]
    art = c["controller"]["artifact"]
    assert (c["controller"]["format"], c["controller"]["publisher"]) == ("onnx", "Google DeepMind")
    assert sha256(local("playground", art["path"])) == art["sha256"]
    for key in ("reference", "training_env"):
        ref = c["settings"][key]
        assert sha256(local("playground", ref["path"])) == ref["sha256"]


def test_observation_and_action_layout_follow_play_go1_joystick():
    """The sources of truth: play_go1_joystick.py get_obs / get_control, joystick.py."""
    src = local("playground", SIM2SIM).read_text()
    stack = re.search(r"obs = np\.hstack\(\[(.*?)\]\)", src, re.S).group(1)
    names = [n.strip() for n in stack.split(",") if n.strip()]
    assert names == [
        "linvel",
        "gyro",
        "gravity",
        "joint_angles",
        "joint_velocities",
        "self._last_action",
        "self._joystick.get_command()",
    ]
    for line in (
        'data.sensor("local_linvel")',
        'data.sensor("gyro")',
        'data.site_xmat[model.site("imu").id]',
        "imu_xmat.T @ np.array([0, 0, -1])",
        "data.qpos[7:] - self._default_angles",
        "data.qvel[6:]",
        "self._counter += 1\n    if self._counter % self._n_substeps == 0:",
        "onnx_pred * self._action_scale + self._default_angles",
        "ctrl_dt = 0.02\n  sim_dt = 0.004",
        "mujoco.mj_resetDataKeyframe(model, data, 0)",
        'default_angles=np.array(model.keyframe("home").qpos[7:])',
        "action_scale=0.5",
    ):
        assert line in src, line
    env = local("playground", JOYSTICK).read_text()
    for line in ("Kp=35.0", "Kd=0.5", "action_scale=0.5", "ctrl_dt=0.02", "sim_dt=0.004"):
        assert line in env, line
    s = configuration("playground")[0]["settings"]
    assert s["observation"]["order"] == [
        "local_linvel",
        "gyro",
        "gravity",
        "joint_pos",
        "joint_vel",
        "last_action",
        "command",
    ]
    c = controller("playground", build())
    assert [t.kind for t in c.spec.obs] == [
        "sensor",
        "sensor",
        "gravity",
        "joint_pos",
        "joint_vel",
        "last_action",
        "command",
    ]
    assert c.spec.decimation == 5 and c.spec.first_tick == 4 and c.spec.action_scale == 0.5
    m = build().model
    assert m.key(0).name == "home"
    np.testing.assert_array_equal(c.spec.default_pose, m.key_qpos[0][7:])


def test_gains_and_limits_equal_the_published_mjcf_and_env():
    scene = build()
    m = scene.model
    s = configuration("playground")[0]["settings"]
    for i in range(m.nu):
        a, j = m.actuator(i).name, m.joint(m.actuator_trnid[i][0]).name
        assert s["gains"]["kp"][j] == scene.position_kp[a] == 35.0
        assert s["limits"]["target_range_rad"][j] == list(scene.ctrlrange[a])
        assert s["limits"]["torque_limits_nm"]["value"][j] == list(m.actuator_forcerange[i])
        assert s["gains"]["joint_damping"]["value"][j] == m.dof_damping[m.joint(j).dofadr[0]]
        assert m.dof_damping[m.joint(j).dofadr[0]] == 0.5  # joystick.py Kd


def test_runner_matches_the_playground_reference_tick_by_tick():
    """Regression: observations and actions bit-identical to play_go1_joystick.py."""
    from lpl_go1.playground_reference import compare

    for command in ((0.5, 0.0, 0.0), (0.0, 0.0, 0.0)):
        r = compare(command, ticks=100)
        assert r["first_policy_step"] == {"reference": 4, "runner": 4}
        assert r["bit_exact"], r["first_divergence"]


def test_scenario_parameters_follow_the_pinned_acceptance_table():
    text = local("specs", SCENARIO_README).read_text()
    for row in (
        "| `command_speed` | 0.5 m/s",
        "Route length 5.5 m",
        "| `required_passes` | 18",
        "20, seeds 1 to 20",
        "0.10 m quadruped",
    ):
        assert row in text, row
    p = sc.parameters("playground")
    assert (p["time_limit"], p["exit_x"], p["wall_clearance"]) == (27.0, 3.0, 0.10)
    assert p["fall_height"] == 0.18
    assert p["lateral_limit"] == pytest.approx(0.75 - robot()["dimensions"]["width"]["value"] / 2)
    assert sc.SEEDS == list(range(1, 21)) and sc.REQUIRED_PASSES == 18


def test_manifest_validates():
    m, _ = sc.manifest("playground", "run")
    validate("manifest", m)
    assert m["robot"]["id"] == "unitree-go1"
    assert m["configuration"]["sha256"] == configuration("playground")[1]


def test_preconditions_give_unsupported_before_trials():
    pre = sc.preconditions("playground")
    assert all(c["met"] for c in pre), pre
    pre = {c["name"]: c["met"] for c in sc.preconditions("stock")}
    assert pre["configuration_offered"] is False  # Unitree publishes no Go1 policy
    with pytest.raises(Unsupported):
        configuration("stock")


def test_evaluation_from_a_recorded_trajectory():
    p = sc.parameters("playground")
    ok = [[t * 0.02, -2.5 + 0.01 * t, 0.0, 0.28, 0.4, 0.4] for t in range(560)]
    ev = sc.evaluate(ok, p)
    assert ev["outcome"] == "PASS" and ev["metrics"]["reached_exit"]
    wide = [s[:2] + [0.65 if s[0] > 3 else 0.0] + s[3:] for s in ok]
    assert sc.evaluate(wide, p)["first_failing_metric"] == "max_abs_lateral"
    assert sc.evaluate(ok[:100], p)["first_failing_metric"] == "reached_exit"
    fell = ok[:50] + [[1.02, -2.0, 0.0, 0.1, 0.4, 0.4]]
    assert sc.evaluate(fell, p)["first_failing_metric"] == "min_base_height"
    touch = [s[:4] + [0.0 if 1 < s[0] < 2 else 0.4, 0.4] for s in ok]
    ev = sc.evaluate(touch, p)
    assert ev["metrics"]["wall_contacts"] == 1 and ev["first_failing_metric"] == "wall_contacts"


def test_grade_rule():
    assert sc.grade(["PASS"] * 18 + ["FAIL"] * 2) == "PASS"
    assert sc.grade(["PASS"] * 17 + ["FAIL"] * 3) == "FAIL"
    assert sc.grade(["PASS"] * 17 + ["ERROR"] + ["FAIL"] * 2) == "INSUFFICIENT_EVIDENCE"


def test_fetch_rejects_a_download_with_the_wrong_checksum(tmp_path):
    pins = {
        "groups": {
            "g": {
                "repository": "o/r",
                "commit": "c",
                "dest": "d",
                "files": {"f.txt": {"sha256": "0" * 64}},
            }
        }
    }
    (tmp_path / "pins.json").write_text(json.dumps(pins))
    with pytest.raises(ChecksumError, match="f.txt"):
        fetch(tmp_path, opener=lambda url: io.BytesIO(b"tampered"))
    assert not (tmp_path / "d" / "f.txt").exists()


def test_wilson_interval():
    assert wilson(20, 20)[0] == pytest.approx(0.8389, abs=1e-4)
    lo, hi = wilson(10, 20)
    assert (round(lo, 4), round(hi, 4)) == (0.2993, 0.7007)


def test_scene_keeps_playground_solver_and_collisions():
    files = assets()
    raw = mujoco.MjModel.from_xml_string(local("playground", SCENE).read_text(), files)
    scene = build()
    m = scene.model
    for attr in ("timestep", "iterations", "ls_iterations", "integrator", "disableflags"):
        assert getattr(m.opt, attr) == getattr(raw.opt, attr), attr
    assert m.opt.timestep == 0.004

    def collide(model):  # by name: added world geoms shift ids
        return sorted(
            (model.geom(g).name, int(model.geom_contype[g]), int(model.geom_conaffinity[g]))
            for g in range(model.ngeom)
            if model.geom_contype[g] or model.geom_conaffinity[g]
        )

    assert collide(m) == collide(raw) and m.npair == raw.npair
    assert scene.walls == ["wall-left", "wall-right"]
    for w in scene.walls:
        g = m.geom(w).id
        assert m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0


def test_spawn_comes_from_the_world_place():
    scene = build()
    world = {p["name"]: p for p in scene.world["places"]}
    pos, yaw = place_pose(scene.model, "spawn-entrance")
    np.testing.assert_allclose(pos, world["spawn-entrance"]["position"])
    assert yaw == pytest.approx(np.radians(world["spawn-entrance"]["yaw_deg"]))


def test_clearance_sees_the_walls():
    scene = build()
    m, d = scene.model, mujoco.MjData(scene.model)
    c = controller("playground", scene)
    zero = {k: 0.0 for k in runner_settings()["initial_noise"]}
    initial_state(m, d, c, 1, zero, "spawn-entrance")
    cl = Clearance(m, scene.walls)
    gaps, real = cl.gaps(d), cl.distances(d)
    assert 0.5 < gaps["wall-left"] < 0.75 and 0.5 < gaps["wall-right"] < 0.75
    for w in scene.walls:  # the box gap is conservative: never larger than the real gap
        assert gaps[w] <= real[w] + 1e-9
    d.qpos[1] = 0.70
    mujoco.mj_forward(m, d)
    assert cl.gaps(d)["wall-left"] <= 0.0 and cl.distances(d)["wall-left"] <= 0.0


def test_runner_is_the_pinned_commit():
    from lpl_go1.scenario import runner_source

    assert runner_source() == ("3de1a917788629a8e60f19a7b6d629edb25e17de", False)


def test_initial_state_holds_home_until_the_first_tick():
    """mj_resetDataKeyframe(home) in play_go1_joystick.py also loads the keyframe ctrl."""
    scene = build()
    m, d = scene.model, mujoco.MjData(scene.model)
    c = controller("playground", scene)
    initial_state(m, d, c, 3, runner_settings()["initial_noise"], "spawn-entrance")
    np.testing.assert_array_equal(d.ctrl, m.key_ctrl[m.key("home").id])
