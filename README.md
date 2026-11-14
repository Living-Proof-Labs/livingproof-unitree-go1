# livingproof-unitree-go1

**Start here: documentation** — [manual source and preview instructions](docs/index.md).
The rendered manual is awaiting publication and live verification.

Living Proof Labs robot package: Unitree Go1 quadruped. It pins an official model and a published, ready-made walking controller so the robot can be validated in the livingproof simulators (MuJoCo first).

This package is the adapter and the configuration profiles. It does not select a world or a scenario. Those choices, and the release pins for a verification run, live in [livingproof-verification](https://github.com/Living-Proof-Labs/livingproof-verification).

Living Proof Labs does not train or tune controllers. A robot is validated only with a walking controller or policy its maker or a maintained project has already published.

## What is here

| File | Purpose |
|---|---|
| `robot.json` | Robot profile (robot-capability-specs v2.0.2): quadruped, width 0.282 m and length 0.679 m measured from the mesh vertices, one configuration, MuJoCo |
| `configurations/playground.json` | MuJoCo Playground `go1_policy.onnx`, on Playground's own Go1 MJCF and `<position>` actuators (kp 35, joint damping 0.5) |
| `pins.json` | Upstream model, policy and robot-profile schema files by repository, commit and sha256. Nothing is vendored. No world and no scenario |
| `runner.json` | Runner settings that are not part of a specification, all marked `chosen`: the sample rate and the stand length. Trials start at the exact nominal pose. There is no seed and no noise |
| `src/lpl_go1/` | Scene (the Playground model only), profiles and the Playground reference loop. This package does not record a scenario |
| `pyproject.toml` | Pins livingproof-sim-mujoco v2.0.3 (`292589ad15582c26cff2bffcc851e3abaf4ea708`) as the library the adapter imports. That tag is not a verification-run pin |
| `.github/workflows/test.yml` | Runs ruff and the full test suite on pull requests and on main, on ubuntu-24.04 and ubuntu-24.04-arm |

**One configuration.** Unitree publishes no Go1 locomotion policy. unitree_rl_gym (276801e) ships G1, H1 and H1-2 policies only; unitree_rl_lab and unitree_rl_mjlab ship G1 only; unitree_mujoco ships none; unitree_guide is a model-based ROS controller, not a policy artifact. So `stock` is not offered, and asking for it raises `Unsupported` (`configuration_offered`). The only configuration is `playground`.

**Source of truth.** The observation and action layout comes from Playground's `play_go1_joystick.py` and the Go1 `joystick.py` env at 12ad7f3:

- Observation: local linvel, gyro, gravity in the IMU frame, q - home, dq, last action, command. There is no gait phase, and the command comes last.
- Action: ctrl = home + 0.5 * action, every 0.02 s (5 steps of 0.004 s), first acting on step index 4.

`task reference` replays that upstream loop verbatim and compares every policy tick with our runner. The test suite keeps a 100-tick bit-exact regression.

**Runner library.** livingproof-sim-mujoco v2.0.3, a git dependency pinned by tag in `pyproject.toml` and `uv.lock`. `Go1.initial_state(model, data, ctrl, spawn, *, start_offset=None)` places the exact nominal pose at a place the caller has added to the model. There is no seed and no noise. Its policy loop observes the current state (mj_step1, act, mj_step2) and has a configurable first tick.

**Scene.** The scene is Playground's `scene_mjx_feetonly_flat_terrain.xml`, with its solver and feet-only collisions unchanged. The adapter does not add a world.

## Tasks

```
uv sync && uv run pytest tests          # uv sync + tests (the session fetches and verifies every pin)
uv run ruff check . && uv run ruff format --check .          # ruff, formatting, profiles against the pinned v2.0.2 schemas
uv run pytest tests           # pytest: schemas, gains, adapter, configurations
task reference      # tick-by-tick match against play_go1_joystick.py (500 ticks)
task profile:measure   # width, length and mass from the pinned model
```

## Verification

Scenario runs are not started from this repository. [livingproof-verification](https://github.com/Living-Proof-Labs/livingproof-verification) selects the robot tag, the configuration, the world and the scenario, and it holds the release pins for that run. `test.yml` in this repository runs ruff and the adapter and configuration tests. A capability result is produced there, not here.

## Rules

- No generated files are committed: no downloaded models or policies, no run output.
- Upstream licenses are honoured. The Playground MJCF and policy are Apache-2.0. The menagerie Go1 meshes are BSD-3-Clause (Unitree Robotics). This repository's own code is Apache-2.0.
