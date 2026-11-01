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
| `verification.lock.json` | What a verification run uses. Its `releases` block is the one place for the specs, runner, verifier and reporter refs (tag and commit). It also holds the world, the configuration's name, version and sha256, and the plan (20 trials, seeds 1–20, 0.5 m/s, 18 required) |
| `src/lpl_go1/` | Fetching, scene, profiles, the standing check, `evidence` (sim observations for the verifier, no grading) and the Playground reference loop |
| `task repin` (`capability_verifier.repin` at the locked verifier commit) | Fills `PENDING_RELEASE` refs from real release tags and checks that none remain |
| `.github/workflows/verify.yml` | Sim evidence → capability verifier → capability report |

**One configuration.** Unitree publishes no Go1 locomotion policy. unitree_rl_gym (276801e) ships G1, H1 and H1-2 policies only; unitree_rl_lab and unitree_rl_mjlab ship G1 only; unitree_mujoco ships none; unitree_guide is a model-based ROS controller, not a policy artifact. So `stock` is not offered, and a walk-hallway request for it is `UNSUPPORTED` (configuration_offered).

**Source of truth.** The observation and action layout comes from Playground's `play_go1_joystick.py` and the Go1 `joystick.py` env at 12ad7f3:

- Observation: local linvel, gyro, gravity in the IMU frame, q - home, dq, last action, command. There is no gait phase, and the command comes last.
- Action: ctrl = home + 0.5 * action, every 0.02 s (5 steps of 0.004 s), first acting on step index 4.

`task reference` replays that upstream loop verbatim and compares every policy tick with our runner. The test suite keeps a 100-tick bit-exact regression.

**Runner.** livingproof-sim-mujoco, a git dependency. It is still at 3de1a91 (`PENDING_RELEASE`); `task repin -- runner=v1.4.0` moves it to the released tag that has `hallway.observe`. Its policy loop observes the current state (mj_step1, act, mj_step2) and has a configurable first tick.

**Scene.** The scene is Playground's `scene_mjx_feetonly_flat_terrain.xml`, with its solver and feet-only collisions unchanged, plus the pinned hallway world. The walls are visual-only. Wall contacts are measured by the runner's `hallway.observe` on real geometry and assessed by the verifier.

## Tasks

```
task setup          # uv sync + fetch and verify every pin
task check          # ruff, formatting, profiles against the pinned v1.5.0 schemas
task test           # pytest: schemas, gains, parameters, lock, PD
task reference      # tick-by-tick match against play_go1_joystick.py (500 ticks)
task evidence CONFIGURATION=playground   # sim evidence, seeds from the lock
task stand          # zero-command standing check
task repin:check    # fails while any release ref is PENDING_RELEASE
task repin -- runner=v1.4.0 verifier=vX.Y.Z reporter=vX.Y.Z
```

`task evidence` refuses while the lock has `PENDING_RELEASE` refs; for a local run before the
releases exist, pass `-- --allow-pending` (the manifest lists what was pending). Grading is the
capability verifier's and the capability report's job: `PASS` at 18 of 20 under
`all-required-v1`, with a Wilson 95% interval.

## Verification workflow

`verify.yml` reads every ref from `verification.lock.json`. It checks that none is
`PENDING_RELEASE`, records evidence, assesses it with the locked verifier release and grades it
with the locked capability-report image. It uploads `evidence-*`, `verified-*` and
`capability-report-*` artifacts and writes the markdown report into the job summary. A
capability `FAIL` is the result, not a CI failure.

## Rules

- No generated files are committed: no downloaded models or policies, no run output, no evidence.
- Upstream licenses are honoured. The Playground MJCF and policy are Apache-2.0. The menagerie Go1 meshes are BSD-3-Clause (Unitree Robotics). This repository's own code is Apache-2.0.
