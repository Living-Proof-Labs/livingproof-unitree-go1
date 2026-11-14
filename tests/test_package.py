# SPDX-License-Identifier: Apache-2.0
import io
import json
import math
import re

import mujoco
import numpy as np
import pytest

from lpl_go1 import ROOT
from livingproof_sim_mujoco import pins
from livingproof_sim_mujoco.pins import ChecksumError, sha256
from livingproof_sim_mujoco.scene import actuator_table
from lpl_go1.profiles import Unsupported, configuration, controller, robot, validate
from lpl_go1.scene import SCENE, Scene, assets, build
from livingproof_sim_mujoco.walk import initial_state

from lpl_go1.adapter import ROBOT


def local(group: str, path: str):
    return pins.local(group, path, ROOT)


def fetch(root=ROOT, opener=None):
    return pins.fetch(root, opener) if opener else pins.fetch(root)


CONFIGS = ["playground"]
SIM2SIM = "mujoco_playground/experimental/sim2sim/play_go1_joystick.py"
JOYSTICK = "mujoco_playground/_src/locomotion/go1/joystick.py"


def test_profiles_match_the_pinned_v2_0_2_schemas():
    pins = json.loads((ROOT / "pins.json").read_text())["groups"]
    assert pins["specs"]["tag"] == "v2.0.2"
    assert pins["specs"]["commit"].startswith("16524cb")
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


def test_configuration_states_the_published_controller_and_refuses_stock():
    """The profile is the contract: published controller, offered names, and no stock policy."""
    conf, digest = configuration("playground")
    assert digest == sha256(ROOT / "configurations/playground.json")
    assert conf["controller"]["interface"] == ["velocity_command"]
    assert conf["controller"]["rate_hz"] == 50
    loco = conf["locomotion"]
    assert loco["max_speed"]["value"] == 1.5
    assert loco["nominal_base_height"]["value"] == 0.278
    assert loco["fall_base_height"]["value"] == 0.18
    assert "seeds" not in conf and "required_passes" not in conf
    with pytest.raises(Unsupported, match="configuration_offered"):
        configuration("stock")  # Unitree publishes no Go1 policy


def test_runner_settings_are_robot_timing_only():
    settings = ROBOT.runner_settings()
    assert settings["sample_hz"] == 50
    assert settings["stand_seconds"] == 10.0
    assert "scenario" not in settings and "world" not in settings


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
    assert scene.walls == []
    assert m.ngeom == raw.ngeom and m.nsite == raw.nsite


def _scene_with_place(name: str, xy: tuple[float, float], yaw_deg: float = 0.0) -> Scene:
    """The Playground model plus one caller-supplied place. Not a pinned world."""
    files = assets()
    spec = mujoco.MjSpec.from_string(
        local("playground", SCENE).read_text(),
        include={k: v for k, v in files.items() if k.endswith(".xml")},
        assets=files,
    )
    site = spec.worldbody.add_site()
    site.name = "place:" + name
    site.pos = [xy[0], xy[1], 0.0]
    yaw = math.radians(yaw_deg)
    site.quat = [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]
    model = spec.compile()
    position_kp, ctrlrange = actuator_table(model)
    return Scene(model, position_kp, ctrlrange, [])


def test_scene_adds_no_world_places():
    raw = mujoco.MjModel.from_xml_string(local("playground", SCENE).read_text(), assets())
    scene = build()
    assert scene.walls == []
    assert not hasattr(scene, "world")
    assert scene.model.nsite == raw.nsite
    assert scene.model.ngeom == raw.ngeom


def test_nominal_pose_is_exact_and_start_offset_is_one_declared_change():
    """The caller names the place. The adapter puts the nominal pose there."""
    scene = _scene_with_place("mark", (0.0, 0.0))
    m = scene.model
    for name in CONFIGS:
        c = controller(name, scene)
        d = mujoco.MjData(m)
        ROBOT.initial_state(m, d, c, "mark")
        nominal = d.qpos.copy()
        np.testing.assert_allclose(d.qpos[0:2], [0.0, 0.0])
        np.testing.assert_allclose(d.qvel, 0.0)
        again = mujoco.MjData(m)
        initial_state(m, again, c, "mark")
        np.testing.assert_array_equal(again.qpos, nominal)
        shifted = mujoco.MjData(m)
        ROBOT.initial_state(m, shifted, c, "mark", start_offset={"base_xy_m": [0.0, 0.05]})
        np.testing.assert_allclose(shifted.qpos[0], nominal[0])
        np.testing.assert_allclose(shifted.qpos[1], nominal[1] + 0.05)
        np.testing.assert_allclose(shifted.qpos[2:], nominal[2:])
        np.testing.assert_allclose(shifted.qvel, 0.0)


def test_initial_state_holds_home_until_the_first_tick():
    """mj_resetDataKeyframe(home) in play_go1_joystick.py also loads the keyframe ctrl."""
    scene = _scene_with_place("mark", (0.0, 0.0))
    m, d = scene.model, mujoco.MjData(scene.model)
    c = controller("playground", scene)
    initial_state(m, d, c, "mark")
    np.testing.assert_array_equal(d.ctrl, m.key_ctrl[m.key("home").id])
