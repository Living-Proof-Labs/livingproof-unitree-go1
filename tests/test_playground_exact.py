# SPDX-License-Identifier: Apache-2.0
"""Exact equality (==, not allclose) with the Playground reference over a fixed number of ticks.

Two anchors:
- the regenerated upstream rollout (play_go1_joystick.py @ mujoco_playground 12ad7f3), run live from the pinned models;
- a committed trace, tests/data/playground-reference-100.npz, which holds the upstream
  rollout's observations, actions and final qpos at command (0.5, 0, 0).
  That file is the x86_64 recording. Regenerate it with
  LPL_REGEN_REFERENCE=1 uv run pytest tests/test_playground_exact.py
  and commit it only after a deliberate upstream pin change.
"""

import os
import platform
from pathlib import Path

import numpy as np

from lpl_go1 import ROOT

TICKS = 100
COMMAND = (0.5, 0.0, 0.0)
TRACE = Path(__file__).parent / "data" / "playground-reference-100.npz"


def _arrays(log, qpos):
    steps = np.array([int(i) for i, _, _ in log])
    obs = np.stack([np.asarray(o) for _, o, _ in log])
    act = np.stack([np.ravel(a) for _, _, a in log])
    return {"steps": steps, "obs": obs, "actions": act, "final_qpos": np.asarray(qpos)}


def test_runner_equals_upstream_rollout_and_committed_trace_exactly():
    from lpl_go1.playground_reference import ours, reference

    ref_log = reference(COMMAND, TICKS, ROOT)
    our_log = ours(COMMAND, TICKS, ROOT, True)
    ref, ours = _arrays(*ref_log), _arrays(*our_log)
    if os.environ.get("LPL_REGEN_REFERENCE"):
        TRACE.parent.mkdir(exist_ok=True)
        np.savez_compressed(TRACE, **ref)
    saved = dict(np.load(TRACE))
    recorded_on_x86 = platform.machine() in {"x86_64", "AMD64"}
    for name in ("steps", "obs", "actions", "final_qpos"):
        assert ours[name].dtype == ref[name].dtype == saved[name].dtype, name
        assert ours[name].shape == ref[name].shape == saved[name].shape, name
        assert (ours[name] == ref[name]).all(), f"{name}: runner != upstream rollout"
        # Policy observations and actions stay bit-exact with the recorded trace on
        # every architecture. The pose after the last physics step was recorded on
        # x86_64 and is not bit-stable elsewhere, so only that field is gated.
        if name == "final_qpos" and not recorded_on_x86:
            continue
        assert (ref[name] == saved[name]).all(), f"{name}: upstream rollout != committed trace"
