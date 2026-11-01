# livingproof-unitree-go1

Living Proof Labs robot package: Unitree Go1 quadruped. It pins an official model and a published, ready-made walking controller so the robot can be validated in the livingproof simulators (MuJoCo first) and in the [livingproof-worlds](https://github.com/Living-Proof-Labs/livingproof-worlds) test worlds.

Living Proof Labs does not train or tune controllers. A robot is validated only with a walking controller or policy its maker or a maintained project has already published.

## What is here

| File | Purpose |
|---|---|
| `robot.json` | Robot profile (robot-capability-specs v1.5.0): quadruped, width 0.282 m and length 0.679 m measured from the mesh vertices, one configuration, MuJoCo |
| `configurations/playground.json` | MuJoCo Playground `go1_policy.onnx`, on Playground's own Go1 MJCF and `<position>` actuators (kp 35, joint damping 0.5) |
| `pins.json` | Every upstream file by repository, commit and sha256. Nothing is vendored |
| `runner.json` | Runner settings (sample rate, seed noise, standing check), not a specification format |
| `src/lpl_go1/` | Fetching, scene, profiles, walk-hallway evaluation, and the Playground reference loop |

**One configuration.** Unitree publishes no Go1 locomotion policy. unitree_rl_gym (276801e) ships G1, H1 and H1-2 policies only; unitree_rl_lab and unitree_rl_mjlab ship G1 only; unitree_mujoco ships none; unitree_guide is a model-based ROS controller, not a policy artifact. So `stock` is not offered, and a walk-hallway request for it is `UNSUPPORTED` (configuration_offered).

**Source of truth.** The observation and action layout comes from Playground's `play_go1_joystick.py` and the Go1 `joystick.py` env at 12ad7f3:

- Observation: local linvel, gyro, gravity in the IMU frame, q - home, dq, last action, command. There is no gait phase, and the command comes last.
- Action: ctrl = home + 0.5 * action, every 0.02 s (5 steps of 0.004 s), first acting on step index 4.

`task reference` replays that upstream loop verbatim and compares every policy tick with our runner. The test suite keeps a 100-tick bit-exact regression.

**Runner.** The runner is livingproof-sim-mujoco `audit/g1-playground-loop-fix` (3de1a91), which is feat/onnx-policy-runner (eb2b0dc) plus the policy-loop fix. With that fix the runner reads observations from the current state (mj_step1, act, mj_step2) and has a configurable first tick. eb2b0dc diverges from the reference at the first tick.

**Scene.** The scene is Playground's `scene_mjx_feetonly_flat_terrain.xml`, with its solver and feet-only collisions unchanged, plus the pinned hallway world. The walls are visual-only. Wall contact is gated on a conservative world-AABB gap, and the real-geometry distance (`mj_geomDistance`) is recorded as a diagnostic.

## Tasks

```
task setup        # uv sync + fetch and verify every pin
task check        # ruff, formatting, profiles against the pinned v1.5.0 schemas
task test         # pytest
task reference    # tick-by-tick match against play_go1_joystick.py (500 ticks)
task walk         # walk-hallway@0.1.0, seeds 1-20, recordings in artifacts/playground/
task stand        # zero-command standing check
```

## Rules

- No generated files are committed: no downloaded models or policies, no run output, no evidence.
- Upstream licenses are honoured. The Playground MJCF and policy are Apache-2.0. The menagerie Go1 meshes are BSD-3-Clause (Unitree Robotics). This repository's own code is Apache-2.0.
