# SPDX-License-Identifier: Apache-2.0
"""collaboration-guidelines bumps move every consumer pin together and keep the release interface.

Proposed by Vera in the capability-verifier#72 review (guidelines bump checklist items 2, 3 and 6),
adapted for this repository. SHARED_RELEASE_INPUTS and SHARED_RELEASE_OUTPUTS are the keys the
pinned semantic-release.yml must still declare. The pin may add keys; do not treat this floor as
the whole interface. Offline, this repository's call stays within that floor. LIVINGPROOF_NETWORK=1
fetches the pinned workflow, parses its workflow_call inputs and outputs, requires the floor to be
present, and checks callers against that parsed interface.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

REPO = "Living-Proof-Labs/collaboration-guidelines"
TAG = r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
USES = re.compile(
    rf"^\s+uses:\s*{re.escape(REPO)}/\.github/workflows/"
    rf"(?P<file>[\w.-]+\.ya?ml)@(?P<ref>\S+)\s*$",
    re.MULTILINE,
)
PRESET = re.compile(rf"^github>{REPO}:renovate-preset#(?P<ref>.+)$")
# Floor the pinned workflow must keep. A later guidelines release may add keys.
SHARED_RELEASE_INPUTS = {
    "dry-run",
    "draft-release",
    "expected-version",
    "expected-commit",
}
SHARED_RELEASE_OUTPUTS = {
    "draft-created",
    "published",
    "version",
    "git-tag",
    "commit",
    "release-url",
}


def _root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "renovate.json").is_file():
            return candidate
    raise AssertionError("could not find the repository root (renovate.json)")


ROOT = _root()


def workflow_path() -> Path | None:
    for name in (".github/workflows/verify.yml", ".github/workflows/release.yml"):
        path = ROOT / name
        if path.is_file() and USES.search(path.read_text()):
            return path
    return None


def guideline_calls(text: str | None = None) -> dict[str, re.Match[str]]:
    path = workflow_path()
    if text is None:
        if path is None:
            return {}
        text = path.read_text()
    calls: dict[str, re.Match[str]] = {}
    for match in USES.finditer(text):
        headers = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", text[: match.start()], re.MULTILINE)
        assert headers, "expected a job name above the guidelines workflow call"
        calls[headers[-1]] = match
    return calls


def preset_refs(config: dict | None = None) -> list[str]:
    config = config or json.loads((ROOT / "renovate.json").read_text())
    return [m.group("ref") for m in map(PRESET.match, config.get("extends", [])) if m]


def pinned_versions(text: str | None = None, config: dict | None = None) -> set[str]:
    refs = set(preset_refs(config))
    if text is not None or workflow_path() is not None:
        refs |= {match.group("ref") for match in guideline_calls(text).values()}
    return refs


def _job_block(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n", text, re.MULTILINE)
    assert match, f"job {job} not found"
    start = match.start()
    end = len(text)
    for later in re.finditer(r"^  [A-Za-z0-9_-]+:\s*$", text[match.end() :], re.MULTILINE):
        end = match.end() + later.start()
        break
    return text[start:end]


def _mapping(block: str, key: str, indent: int = 4) -> dict[str, str]:
    pad = " " * indent
    inner = " " * (indent + 2)
    found = re.search(rf"^{pad}{re.escape(key)}:\n((?:{inner}.*\n)*)", block, re.MULTILINE)
    if not found:
        return {}
    return dict(re.findall(rf"^{inner}([A-Za-z0-9_-]+):\s*(\S+)", found.group(1), re.MULTILINE))


def _workflow_call_keys(shared: str, section: str) -> set[str]:
    call = re.search(r"^  workflow_call:\n((?:    .*\n)+)", shared, re.MULTILINE)
    assert call, "expected a workflow_call interface"
    body = call.group(1)
    found = re.search(rf"^    {re.escape(section)}:\n((?:      .*\n)+)", body, re.MULTILINE)
    if not found:
        return set()
    return set(re.findall(r"^      ([A-Za-z0-9_-]+):", found.group(1), re.MULTILINE))


def shared_release_interface(shared: str) -> tuple[set[str], set[str]]:
    return _workflow_call_keys(shared, "inputs"), _workflow_call_keys(shared, "outputs")


def recorded_interface_problems(inputs: set[str], outputs: set[str]) -> list[str]:
    """The recorded set is a floor. Keys the pin adds are not drift."""
    problems: list[str] = []
    missing_inputs = sorted(SHARED_RELEASE_INPUTS - inputs)
    missing_outputs = sorted(SHARED_RELEASE_OUTPUTS - outputs)
    if missing_inputs:
        problems.append(f"pinned workflow is missing inputs {missing_inputs}")
    if missing_outputs:
        problems.append(f"pinned workflow is missing outputs {missing_outputs}")
    return problems


def release_call_interface_problems(text: str, inputs: set[str], outputs: set[str]) -> list[str]:
    """Caller inputs and outputs must be names on the interface passed in."""
    calls = guideline_calls(text)
    if not calls:
        return ["expected a job that uses the shared semantic-release workflow"]
    problems: list[str] = []
    for name, match in calls.items():
        if match.group("file") != "semantic-release.yml":
            problems.append(f"{name} uses {match.group('file')}, not semantic-release.yml")
        block = _job_block(text, name)
        unknown_inputs = sorted(set(_mapping(block, "with")) - inputs)
        if unknown_inputs:
            problems.append(f"{name} passes unknown inputs {unknown_inputs}")
        used = set(re.findall(rf"needs\.{re.escape(name)}\.outputs\.([\w-]+)", text))
        unknown_outputs = sorted(used - outputs)
        if unknown_outputs:
            problems.append(f"{name} reads unknown outputs {unknown_outputs}")
    return problems


def shared_release_problems(caller_text: str, shared: str) -> list[str]:
    inputs, outputs = shared_release_interface(shared)
    return [
        *recorded_interface_problems(inputs, outputs),
        *release_call_interface_problems(caller_text, inputs, outputs),
    ]


def _shared_workflow(inputs: set[str], outputs: set[str]) -> str:
    def section(names: set[str], body: str) -> str:
        return "".join(f"      {name}:\n{body}" for name in sorted(names))

    input_body = "        type: string\n"
    output_body = "        value: placeholder\n"
    return (
        "on:\n"
        "  workflow_call:\n"
        "    inputs:\n"
        f"{section(inputs, input_body)}"
        "    outputs:\n"
        f"{section(outputs, output_body)}"
        "jobs:\n"
        "  release:\n"
        "    runs-on: ubuntu-latest\n"
    )


def _caller_workflow(passed: dict[str, str], outputs: list[str]) -> str:
    with_block = ""
    if passed:
        rows = "".join(f"      {key}: {value}\n" for key, value in passed.items())
        with_block = f"    with:\n{rows}"
    reads = "".join(f"    # needs.release.outputs.{name}\n" for name in outputs)
    return (
        "jobs:\n"
        "  release:\n"
        "    permissions:\n"
        "      contents: write\n"
        "    uses: Living-Proof-Labs/collaboration-guidelines/.github/workflows/"
        "semantic-release.yml@v1.2.3\n"
        f"{with_block}"
        f"{reads}"
    )


def test_every_guidelines_pin_is_a_full_release_tag():
    refs = pinned_versions()
    assert refs, "expected a Renovate preset pin and/or a release workflow call"
    for ref in refs:
        assert re.fullmatch(TAG, ref), f"{ref} is not a full vX.Y.Z release tag"


def test_workflow_call_and_renovate_preset_name_one_guidelines_release():
    # A partial bump (one pin moved, the other left behind) fails here.
    # Repos that only pin the preset still need exactly one release tag.
    assert len(pinned_versions()) == 1, sorted(pinned_versions())


@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_mismatched_pins_are_reported():
    text = workflow_path().read_text()
    mutated = USES.sub(lambda m: m.group(0).replace(m.group("ref"), "v2.2.0", 1), text, count=1)
    preset = {"extends": [f"github>{REPO}:renovate-preset#v9.2.0"]}
    assert len(pinned_versions(mutated, preset)) == 2


@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_release_call_uses_only_the_shared_interface():
    path = workflow_path()
    text = path.read_text()
    calls = guideline_calls(text)
    assert calls, "expected a job that uses the shared semantic-release workflow"
    for name, match in calls.items():
        assert match.group("file") == "semantic-release.yml"
        block = _job_block(text, name)
        assert _mapping(block, "permissions").get("contents") == "write"
    # The pin is fetched only under LIVINGPROOF_NETWORK. Offline, the call stays
    # inside the recorded floor; the network test checks it against the parse.
    assert (
        release_call_interface_problems(text, SHARED_RELEASE_INPUTS, SHARED_RELEASE_OUTPUTS) == []
    )


@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_an_unknown_release_output_is_reported():
    text = workflow_path().read_text()
    mutated = (
        text + "\n# needs.release.outputs.tag\n"
        if "needs.release.outputs" in text
        else text.replace(
            "permissions:", "needs.release.outputs.tag: placeholder\n  permissions:", 1
        )
    )
    shared = _shared_workflow(SHARED_RELEASE_INPUTS, SHARED_RELEASE_OUTPUTS)
    problems = shared_release_problems(mutated, shared)
    assert "release reads unknown outputs ['tag']" in problems, problems


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_recorded_interface_matches_the_pinned_shared_workflow():
    refs = {match.group("ref") for match in guideline_calls().values()}
    (ref,) = refs
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/.github/workflows/semantic-release.yml"
    with urllib.request.urlopen(url, timeout=30) as response:
        shared = response.read().decode()
    caller = workflow_path().read_text()
    problems = shared_release_problems(caller, shared)
    assert problems == [], problems
    # A later guidelines release may add a key. That must not fail this check,
    # and this repository's call is still judged against the parsed names.
    widened = shared.replace(
        "    outputs:\n",
        "      future-input:\n        type: string\n    outputs:\n",
        1,
    )
    widened_inputs, _widened_outputs = shared_release_interface(widened)
    assert "future-input" in widened_inputs
    assert shared_release_problems(caller, widened) == []


def test_recorded_floor_requires_expected_inputs_and_commit():
    # Spelled out so dropping expected-version, expected-commit, or commit from
    # the floor fails even when the pinned workflow still declares them.
    assert SHARED_RELEASE_INPUTS == {
        "dry-run",
        "draft-release",
        "expected-version",
        "expected-commit",
    }
    assert SHARED_RELEASE_OUTPUTS == {
        "draft-created",
        "published",
        "version",
        "git-tag",
        "commit",
        "release-url",
    }


def test_parsed_interface_is_workflow_call_names_only():
    shared = _shared_workflow({"dry-run", "expected-commit"}, {"commit", "published"})
    assert shared_release_interface(shared) == (
        {"dry-run", "expected-commit"},
        {"commit", "published"},
    )


def test_added_pinned_keys_remain_compatible():
    shared = _shared_workflow(
        SHARED_RELEASE_INPUTS | {"future-input"},
        SHARED_RELEASE_OUTPUTS | {"future-output"},
    )
    caller = _caller_workflow({}, ["published"])
    assert shared_release_problems(caller, shared) == []


def test_callers_are_checked_against_the_parsed_interface():
    shared = _shared_workflow(
        SHARED_RELEASE_INPUTS | {"future-input"},
        SHARED_RELEASE_OUTPUTS | {"future-output"},
    )
    caller = _caller_workflow({"future-input": "true"}, ["future-output"])
    assert shared_release_problems(caller, shared) == []
    floor_only = release_call_interface_problems(
        caller, SHARED_RELEASE_INPUTS, SHARED_RELEASE_OUTPUTS
    )
    assert "release passes unknown inputs ['future-input']" in floor_only
    assert "release reads unknown outputs ['future-output']" in floor_only


def test_a_missing_recorded_key_is_reported():
    caller = _caller_workflow({}, ["published"])
    missing_commit = _shared_workflow(SHARED_RELEASE_INPUTS, SHARED_RELEASE_OUTPUTS - {"commit"})
    problems = shared_release_problems(caller, missing_commit)
    assert "pinned workflow is missing outputs ['commit']" in problems, problems
    missing_input = _shared_workflow(
        SHARED_RELEASE_INPUTS - {"expected-commit"}, SHARED_RELEASE_OUTPUTS
    )
    problems = shared_release_problems(caller, missing_input)
    assert "pinned workflow is missing inputs ['expected-commit']" in problems, problems


def test_an_unknown_release_input_is_reported():
    caller = _caller_workflow({"not-an-input": "true"}, [])
    shared = _shared_workflow(SHARED_RELEASE_INPUTS, SHARED_RELEASE_OUTPUTS)
    problems = shared_release_problems(caller, shared)
    assert "release passes unknown inputs ['not-an-input']" in problems, problems


def test_this_repository_does_not_opt_in_to_docs_brand_updates():
    """v9.4.0's docs-brand manager stays off until a consumer opts in.

    Proposed by Vera in the guidelines v9.4.0 bump review. This repository
    enables custom.regex and has a docs-brand.lock.json, so the new manager
    would match, but the pinned preset disables that dep type by default.
    A later consumer packageRule that sets enabled: true fails here.
    """
    config = json.loads((ROOT / "renovate.json").read_text())
    assert "custom.regex" in config.get("enabledManagers", [])
    assert (ROOT / "docs-brand.lock.json").is_file()
    for rule in config.get("packageRules") or []:
        dumped = json.dumps(rule)
        if "docs-brand" in dumped or rule.get("matchDepTypes") == ["docs-brand"]:
            assert rule.get("enabled") is False, rule


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_pinned_preset_keeps_docs_brand_disabled_by_default():
    # Proposed by Vera in the guidelines v9.4.0 bump review.
    refs = pinned_versions()
    assert refs, "expected a Renovate preset pin"
    (ref,) = refs
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/renovate-preset.json"
    with urllib.request.urlopen(url, timeout=30) as response:
        preset = json.loads(response.read().decode())
    brand_rules = [
        rule
        for rule in preset.get("packageRules", [])
        if "docs-brand" in json.dumps(rule) or rule.get("matchDepTypes") == ["docs-brand"]
    ]
    assert brand_rules, f"pinned {ref} preset has no docs-brand packageRule"
    for rule in brand_rules:
        assert rule.get("enabled") is False, rule
        assert rule.get("automerge") is False
    managers = [
        manager
        for manager in preset.get("customManagers", [])
        if manager.get("depTypeTemplate") == "docs-brand"
        or "docs-brand.lock.json" in json.dumps(manager)
    ]
    assert managers, f"pinned {ref} preset has no docs-brand.lock.json manager"
