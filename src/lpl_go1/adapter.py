# SPDX-License-Identifier: Apache-2.0
"""The Unitree Go1 adapter for livingproof-sim-mujoco's generic walk-hallway evidence loop.
Only robot-specific code and data live here: the scene build, the published controllers,
the seeded initial pose and the profiles."""

from __future__ import annotations

from pathlib import Path

from lpl_go1 import ROOT


class Go1:
    id = "unitree-go1"
    root: Path = ROOT
    width_decimals = 2
    camera = (2.2, -35.0, 200.0)  # stand and media clips: tracking distance, elevation, azimuth
    actuation = "position"

    def base_body(self, name: str) -> str:
        return "trunk"

    def scene(self, name: str):
        from lpl_go1.scene import build

        return build(self.actuation, self.root)

    def controller(self, name: str, scene):
        from lpl_go1.profiles import controller

        return controller(name, scene, self.root)

    def policy(self, model, ctrl):
        from livingproof_sim_mujoco.policy import OnnxPolicy

        return OnnxPolicy(model, ctrl.policy_path, ctrl.spec)

    def initial_state(self, model, data, ctrl, seed, noise, spawn) -> None:
        from livingproof_sim_mujoco.walk import initial_state

        initial_state(model, data, ctrl, seed, noise, spawn)

    def runner_settings(self) -> dict:
        from livingproof_sim_mujoco.walk import runner_settings

        return runner_settings(self.root)

    def robot(self) -> dict:
        from lpl_go1.profiles import robot

        return robot(self.root)

    def configuration(self, name: str) -> dict:
        from lpl_go1.profiles import configuration

        return configuration(name, self.root)[0]

    def world(self) -> dict:
        from lpl_go1.scene import load_world

        return load_world(self.root)

    def environment(self, name: str) -> dict:
        return {}


ROBOT = Go1()
