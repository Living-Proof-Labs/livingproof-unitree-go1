# Verification and publication

## Build the manual

Use Python 3.11 or newer and Task 3.54.0 on the host. The docs environment is
independent of the simulation environment; it does not install the robot package.

```sh
python3 -m venv .venv-docs
.venv-docs/bin/python -m pip install --require-hashes -r tools/docs/requirements.lock
.venv-docs/bin/python tools/docs/build.py
python3 -m http.server --directory artifacts/docs/html 8000
```

Open `http://localhost:8000/`. The build stages canonical Markdown, generates
Task descriptions with native `task --list --json` and `task --summary` (without
executing task bodies), verifies the pinned brand digest, builds Zensical, and
checks local HTML links/fragments, brand loading, and source identity.
Network access is needed for dependency installation and the immutable brand fetch.

## Evidence and limits

The [existing verification workflow](https://github.com/Living-Proof-Labs/livingproof-unitree-go1/actions/workflows/verify.yml)
remains the source for simulation/evaluator results. This manual build does not
run the robot, verify the README's simulation commands, or measure software coverage.
No scenario pass count or package-release identity is inferred from documentation CI.

This first documentation baseline preserves all existing README content and
anchors. Task reference is generated. Generated CLI/API references and embedded
scenario/coverage reports are deferred explicitly: importing the runtime would
require simulation dependencies and downloaded assets. Use the existing package
guide, exact source, and verification artifacts for now. Full recipe adoption is
not claimed. No historical rendered manuals are maintained; use source tags.

## Publication prerequisites

After review and a separately authorized merge, the Documentation workflow builds
on main and deploys with GitHub Pages. Repository Pages must use **GitHub Actions**,
and the `github-pages` environment must permit only main. An administrator must
confirm any required settings changes; this PR does not change them. No CNAME or
DNS change is needed. Pull requests build a 14-day preview artifact and cannot deploy.

The intended URL is `https://docs.livingprooflabs.com/livingproof-unitree-go1/`.
It returned HTTP 404 during preparation on 2026-11-13; this is not a live-manual claim.
After deployment, record the actual Pages URL and workflow run, match `build.json`
to the deployed source SHA, and verify README navigation, nested page reload,
search/result opening, desktop/mobile/keyboard navigation, and root/source/help/license
links. Update the README and organization directory only after that acceptance.
Independent review and live browser acceptance remain required before declaring
publication complete.
