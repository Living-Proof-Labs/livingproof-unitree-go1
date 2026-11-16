# SPDX-License-Identifier: Apache-2.0
"""The Unitree Go1 adapter for livingproof-sim-mujoco.

Only robot-specific code and data live here: the Playground scene, the published
controller, the nominal initial pose and the configuration profiles. The adapter does
not choose a world or a scenario. The caller passes the place name to ``initial_state``.
"""

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

    def initial_state(self, model, data, ctrl, spawn: str, *, start_offset=None) -> None:
        """Exact nominal pose at ``spawn``. ``start_offset`` is one declared comparison change.

        Signature matches livingproof-sim-mujoco v2.0.3 ``Robot.initial_state``:
        ``(model, data, ctrl, spawn, start_offset=None)``. There is no seed and no noise.
        ``spawn`` is a place the caller has already added to ``model``. A missing place
        raises ``MissingPlace``, the simulator's ``INCOMPATIBLE`` stop (a ``SystemExit``,
        also a ``KeyError``), with the same message as the G1 and T1 adapters.
        """
        from livingproof_sim_mujoco.scene import PLACE
        from livingproof_sim_mujoco.walk import initial_state

        try:
            model.site(PLACE + spawn)
        except KeyError:
            raise MissingPlace(missing_place(spawn)) from None
        initial_state(model, data, ctrl, spawn, start_offset=start_offset)

    def runner_settings(self) -> dict:
        from livingproof_sim_mujoco.walk import runner_settings

        return runner_settings(self.root)

    def robot(self) -> dict:
        from lpl_go1.profiles import robot

        return robot(self.root)

    def configuration(self, name: str) -> dict:
        from lpl_go1.profiles import configuration

        return configuration(name, self.root)[0]

    def environment(self, name: str) -> dict:
        return {}


class MissingPlace(SystemExit, KeyError):
    """A start place the composed world does not have.

    The simulator's own stop for an incompatible run: a ``SystemExit`` whose text starts
    with ``INCOMPATIBLE`` and says no trial was recorded, so a run reports it the same way
    as the simulator's other start-place checks rather than as a traceback. It is also a
    ``KeyError`` for callers that catch a missing place. ``str()`` is the message.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


def missing_place(spawn: str) -> str:
    """The error for a start place that is not on the compiled model."""
    from livingproof_sim_mujoco.scene import PLACE

    return (
        f"INCOMPATIBLE: start place {spawn!r} is not a place in the composed world "
        f"(no site {PLACE + spawn!r} on the compiled model). No trial was recorded. "
        "The scenario start must name a pose in that world, such as 'spawn-entrance'."
    )


ROBOT = Go1()
