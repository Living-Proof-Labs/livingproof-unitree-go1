# livingproof-unitree-go1

Living Proof Labs robot package: Unitree Go1 quadruped. It pins an official model and a published, ready-made walking controller so the robot can be validated in the livingproof simulators (MuJoCo first) and in the [livingproof-worlds](https://github.com/Living-Proof-Labs/livingproof-worlds) test worlds.

Living Proof Labs does not train or tune controllers. A robot is validated only with a walking controller or policy its maker or a maintained project has already published.

## What is here

| File | Purpose |
|---|---|
| `robot.json` | Robot profile (robot-capability-specs v2.0.2): quadruped, width 0.282 m and length 0.679 m measured from the mesh vertices, one configuration, MuJoCo |
| `configurations/playground.json` | MuJoCo Playground `go1_policy.onnx`, on Playground's own Go1 MJCF and `<position>` actuators (kp 35, joint damping 0.5) |
| `pins.json` | Every upstream file by repository, commit and sha256, including the specs schemas and scenario at v2.0.2 (`16524cb`). Nothing is vendored |
| `runner.json` | Runner settings that are not part of the spec, all marked `chosen`: the sample rate and the stand length. Trials start at the exact nominal pose. There is no seed and no noise. |
| `verification.lock.json` | What a verification run uses. Its `releases` block is the one place for the specs, runner, verifier and reporter refs (tag and commit). It also holds the world, the configuration's name, version and sha256, and the trial plan (`trials.count` 3, 0.5 m/s, no seeds and no `required_passes`). Only `playground` is in the lock. |
| `src/lpl_go1/` | Fetching, scene, profiles, the standing check, `evidence` (sim observations for the verifier, no grading) and the Playground reference loop |
| `task repin` (`capability_verifier.repin` at the locked verifier commit) | Fills `PENDING_RELEASE` refs from real release tags and checks that none remain |
| `.github/workflows/verify.yml` | Sim evidence → capability verifier → capability report |

**One configuration.** Unitree publishes no Go1 locomotion policy. unitree_rl_gym (276801e) ships G1, H1 and H1-2 policies only; unitree_rl_lab and unitree_rl_mjlab ship G1 only; unitree_mujoco ships none; unitree_guide is a model-based ROS controller, not a policy artifact. So `stock` is not offered, and a walk-hallway request for it is `UNSUPPORTED` (configuration_offered). The lock's only configuration is `playground`; verification runs that one.

**Trials.** The specification's default count is 1. This package uses `trials.count` 3 so a published run can show that the identical trials agree. Evidence 2.0.0 has no seeds and no `required_passes`. The verifier's k-of-N grade for that format requires every trial to pass. Published runs start at the exact nominal pose at `spawn-entrance` (a `start_offset` would be one declared comparison change).

**Source of truth.** The observation and action layout comes from Playground's `play_go1_joystick.py` and the Go1 `joystick.py` env at 12ad7f3:

- Observation: local linvel, gyro, gravity in the IMU frame, q - home, dq, last action, command. There is no gait phase, and the command comes last.
- Action: ctrl = home + 0.5 * action, every 0.02 s (5 steps of 0.004 s), first acting on step index 4.

`task reference` replays that upstream loop verbatim and compares every policy tick with our runner. The test suite keeps a 100-tick bit-exact regression.

**Runner.** livingproof-sim-mujoco v2.0.1 (`5f6b9d10962eb6108dc55b079806b7a3cf794de7`), a git dependency pinned in `verification.lock.json` and `pyproject.toml`. `Go1.initial_state(model, data, ctrl, spawn, *, start_offset=None)` places the exact nominal pose. There is no seed and no noise. Its policy loop observes the current state (mj_step1, act, mj_step2) and has a configurable first tick.

**Scene.** The scene is Playground's `scene_mjx_feetonly_flat_terrain.xml`, with its solver and feet-only collisions unchanged, plus the pinned hallway world. The walls are visual-only. Wall contacts are measured by the runner's `observations.observe` on real geometry and assessed by the verifier.

## Tasks

```
uv sync && uv run pytest tests          # uv sync + tests (the session fetches and verifies every pin)
uv run ruff check . && uv run ruff format --check .          # ruff, formatting, profiles against the pinned v2.0.2 schemas
uv run pytest tests           # pytest: schemas, gains, parameters, lock, PD
task reference      # tick-by-tick match against play_go1_joystick.py (500 ticks)
uv run python -m livingproof_sim_mujoco.evidence lpl_go1.adapter:ROBOT playground artifacts/evidence/playground   # sim evidence, trials.count from the lock (CI's command)
task stand          # zero-command standing check
task repin:check    # fails while any release ref is PENDING_RELEASE
task repin -- runner=vX.Y.Z verifier=vX.Y.Z reporter=vX.Y.Z   # e.g. runner=v2.0.1, the current pin
```

**Evidence only.** `python -m livingproof_sim_mujoco.evidence lpl_go1.adapter:ROBOT <configuration> <out>` runs `trials.count` identical trials through the runner's `observations.observe` and writes one observation per trial (`trials/trial-NN.json`: base trace, maximum lateral offset, minimum base height, exit time and real-geometry wall contacts). It also writes a `livingproof-hallway-evidence/2.0.0` manifest that echoes the lock and records the preconditions, and a `livingproof-run-manifest/0.1` before the first trial. No thresholds are applied here. Exit 4 means the identical trials disagreed (a test-system reproducibility finding, not a robot grade).

`python -m lpl_go1.evidence` is the same loop. It refuses while the lock has `PENDING_RELEASE` refs; for a local run before the releases exist, pass `--allow-pending` (the manifest lists what was pending).

Every release ref in `verification.lock.json` is currently pinned (specs v2.0.2, runner v2.0.1, verifier v1.16.1, reporter v3.3.0). Evidence goes to `artifacts/evidence/<configuration>/` (`manifest.json`, `run-manifest.json`, `trials/trial-NN.json`). Artifacts are never committed.

**Assessment:** the capability verifier's hallway evaluators (exit reached, lateral limit, wall contact, fall) assess each trial with structured observed/limit/time failures. **Grade:** the capability report applies `all-required-v1` per trial and aggregates k of N with a Wilson 95% interval. For evidence 2.0.0, N is `trials.count` and every trial must pass.

`task repin -- runner=vX.Y.Z` rewrites the runner pin and then runs `uv lock`:

- `pyproject.toml` `[tool.uv.sources]` `livingproof-sim-mujoco` `tag` (currently `"v2.0.1"`)
- `verification.lock.json` `releases.runner.tag` (currently `"v2.0.1"`) and `releases.runner.commit` (currently `"5f6b9d10962eb6108dc55b079806b7a3cf794de7"`)
- `uv.lock` (regenerated; `task repin` runs `uv lock` when the runner pin changes)

The verifier pin is `releases.verifier` (currently v1.16.1, commit `67bb6bbc330645f9fee66b9ab16f6c055cd313f8`, hallway image `ghcr.io/living-proof-labs/capability-verifier-hallway:1.16.1` digest `sha256:b57bf7dde92d930926b1f75b23bddf6fbc310e5913a5f0db9aa501bccaf62e4d`) and the `uses:` commit in `.github/workflows/verify.yml`, which is that same commit. The reporter pin is `releases.reporter` (currently v3.3.0, commit `927d14a38234a9a469423256012226add8844bc3`, image `ghcr.io/living-proof-labs/livingproof-capability-report:3.3.0` digest `sha256:db6086415d9672fcee078432a649e70d93c5d37b0c51ce51bcca74ff2b56c4bd`).

## Verification workflow

`verify.yml` reads every ref from `verification.lock.json`. It checks that none is
`PENDING_RELEASE`, records evidence, assesses it with the locked verifier release and grades it
with the locked capability-report image. It uploads `evidence-*`, `verified-*` and
`capability-report-*` artifacts and writes the markdown report into the job summary. A
capability `FAIL` is the result, not a CI failure.

## Rules

- No generated files are committed: no downloaded models or policies, no run output, no evidence.
- Upstream licenses are honoured. The Playground MJCF and policy are Apache-2.0. The menagerie Go1 meshes are BSD-3-Clause (Unitree Robotics). This repository's own code is Apache-2.0.
