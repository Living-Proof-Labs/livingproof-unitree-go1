# SPDX-License-Identifier: Apache-2.0
"""Releases on main are gated on a passing Test run for the exact same commit."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
TEST = WORKFLOWS / "test.yml"


def _code(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line.split(" #", 1)[0].rstrip())
    return "\n".join(lines)


def _top_section(code: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\n((?:(?:[ \t]+.*|\s*)\n)+)", code + "\n", re.M)
    return match.group(1) if match else None


def _jobs(code: str) -> dict[str, str]:
    block = _top_section(code, "jobs")
    if not block:
        return {}
    headers = list(re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", block, re.M))
    jobs: dict[str, str] = {}
    for index, match in enumerate(headers):
        start = match.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(block)
        jobs[match.group(1)] = block[start:end]
    return jobs


def _needs(job_body: str) -> list[str]:
    inline = re.search(r"^    needs:\s*\[([^\]]*)\]\s*$", job_body, re.M)
    if inline:
        return [item.strip() for item in inline.group(1).split(",") if item.strip()]
    scalar = re.search(r"^    needs:\s*([A-Za-z0-9_-]+)\s*$", job_body, re.M)
    if scalar:
        return [scalar.group(1)]
    block = re.search(r"^    needs:\n((?:      -\s+.+\n?)+)", job_body + "\n", re.M)
    if block:
        return re.findall(r"^      -\s+([A-Za-z0-9_-]+)\s*$", block.group(1), re.M)
    return []


def _job_permissions(job_body: str) -> dict[str, str]:
    block = re.search(r"^    permissions:\n((?:      .+\n?)+)", job_body + "\n", re.M)
    if not block:
        return {}
    return dict(re.findall(r"^      ([A-Za-z0-9_-]+):\s*(\S+)", block.group(1), re.M))


def release_gate_problems(release_text: str, test_text: str) -> list[str]:
    """Return problems if release.yml is not strictly gated on Test for the same commit."""
    problems: list[str] = []
    release_code = _code(release_text)
    test_code = _code(test_text)

    top_on = _top_section(release_code, "on")
    if not top_on or not re.search(r"^  push:\n    branches: \[main\]\s*$", top_on, re.M):
        problems.append("release.yml must trigger on push to [main]")
    if top_on and ("pull_request" in top_on or "workflow_run" in top_on):
        problems.append("release.yml must not trigger on pull_request or unpinned workflow_run")

    top_perms = _top_section(release_code, "permissions")
    if not top_perms or top_perms.strip() != "contents: read":
        problems.append("release.yml top-level permissions must be contents: read")

    jobs = _jobs(release_code)
    release_jobs = [
        (name, body)
        for name, body in jobs.items()
        if name == "release" or "semantic-release.yml" in body
    ]
    if not release_jobs:
        problems.append("release.yml has no release job")
        return problems

    for name, body in release_jobs:
        needs = _needs(body)
        if not needs:
            problems.append(f"release job {name} must declare needs on the test job")
            continue

        if_match = re.search(r"^    if:\s*(.+)$", body, re.M)
        if_expr = if_match.group(1).strip() if if_match else ""
        if any(fn in if_expr for fn in ("always()", "failure()", "cancelled()")):
            problems.append(f"release job {name} must not run on failure or cancellation")
        if "github.event_name == 'push'" not in if_expr:
            problems.append(f"release job {name} must require github.event_name == 'push'")
        if "github.ref == 'refs/heads/main'" not in if_expr:
            problems.append(f"release job {name} must require github.ref == 'refs/heads/main'")

        for needed in needs:
            if f"needs.{needed}.result == 'success'" not in if_expr:
                problems.append(
                    f"release job {name} must require needs.{needed}.result == 'success' "
                    "so skipped, cancelled and failed tests all block release"
                )
            test_job = jobs.get(needed)
            if test_job is None:
                problems.append(f"release job {name} needs unknown job {needed}")
                continue
            if not re.search(r"^    uses:\s*\./\.github/workflows/test\.yml\s*$", test_job, re.M):
                problems.append(
                    f"gated job {needed} must call ./.github/workflows/test.yml on the same commit"
                )
            if re.search(r"^    if:", test_job, re.M):
                problems.append(f"gated job {needed} must not have an if condition that can skip")
            if "continue-on-error" in test_job:
                problems.append(f"gated job {needed} must not set continue-on-error")
            perms = _job_permissions(test_job)
            if perms and perms != {"contents": "read"}:
                problems.append(f"gated job {needed} must keep contents: read only")

    test_on = _top_section(test_code, "on")
    if not test_on or not re.search(r"^  workflow_call:\s*$", test_on, re.M):
        problems.append("test.yml must declare workflow_call so release.yml runs it on main")
    if "continue-on-error" in test_code:
        problems.append("test.yml must not set continue-on-error")
    if re.search(r"^\s+ref:\s*", test_code, re.M):
        problems.append("test.yml checkout must check out the triggering commit, not a custom ref")

    test_jobs = _jobs(test_code)
    gate = test_jobs.get("verification-gate")
    if gate is None:
        problems.append("test.yml must define verification-gate over the test matrix")
    else:
        if "test" not in _needs(gate):
            problems.append("verification-gate must depend on the test matrix job")
        if not re.search(r"^    if:\s*always\(\)\s*$", gate, re.M):
            problems.append("verification-gate must run with if: always()")
        if 'test "$TEST_RESULT" = success' not in gate:
            problems.append("verification-gate must fail unless TEST_RESULT is success")

    return problems


def test_release_workflow_is_gated_on_passing_test_for_same_commit():
    problems = release_gate_problems(
        RELEASE.read_text(encoding="utf-8"),
        TEST.read_text(encoding="utf-8"),
    )
    assert problems == [], "\n".join(problems)


def test_ungated_release_workflow_like_v2_2_10_fails():
    ungated_release = """\
name: Release
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  release:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    permissions:
      contents: write
    uses: Living-Proof-Labs/collaboration-guidelines/.github/workflows/semantic-release.yml@v9.6.9
"""
    problems = release_gate_problems(ungated_release, TEST.read_text(encoding="utf-8"))
    assert any("needs on the test job" in problem for problem in problems), problems


def test_release_without_explicit_success_result_check_fails():
    release_text = RELEASE.read_text(encoding="utf-8")
    test_text = TEST.read_text(encoding="utf-8")
    no_result_check = release_text.replace(
        "needs.test.result == 'success' && ",
        "",
        1,
    )
    assert no_result_check != release_text
    problems = release_gate_problems(no_result_check, test_text)
    assert any("needs.test.result == 'success'" in problem for problem in problems), problems


def test_release_with_always_or_failure_condition_fails():
    release_text = RELEASE.read_text(encoding="utf-8")
    test_text = TEST.read_text(encoding="utf-8")
    always_release = release_text.replace(
        "needs.test.result == 'success'",
        "always()",
        1,
    )
    assert always_release != release_text
    problems = release_gate_problems(always_release, test_text)
    assert any("failure or cancellation" in problem for problem in problems), problems


def test_release_without_workflow_call_in_test_yml_fails():
    release_text = RELEASE.read_text(encoding="utf-8")
    test_text = TEST.read_text(encoding="utf-8")
    without_call = test_text.replace("  workflow_call:\n", "", 1)
    assert without_call != test_text
    problems = release_gate_problems(release_text, without_call)
    assert any("workflow_call" in problem for problem in problems), problems


def test_release_with_write_permission_on_test_job_fails():
    release_text = RELEASE.read_text(encoding="utf-8")
    test_text = TEST.read_text(encoding="utf-8")
    widened = release_text.replace(
        "  test:\n    permissions:\n      contents: read",
        "  test:\n    permissions:\n      contents: write",
        1,
    )
    assert widened != release_text
    problems = release_gate_problems(widened, test_text)
    assert any("contents: read only" in problem for problem in problems), problems


def test_release_with_custom_checkout_ref_in_test_yml_fails():
    release_text = RELEASE.read_text(encoding="utf-8")
    test_text = TEST.read_text(encoding="utf-8")
    floating_ref = test_text.replace(
        "          persist-credentials: false",
        "          ref: main\n          persist-credentials: false",
        1,
    )
    assert floating_ref != test_text
    problems = release_gate_problems(release_text, floating_ref)
    assert any("triggering commit" in problem for problem in problems), problems
