# SPDX-License-Identifier: Apache-2.0
"""A workflow that reads current main at git/ref/heads/main must send the workflow token.

An anonymous call shares the runner rate limit, fails with HTTP 403, and leaves
the published site on the previous release. contents: read is enough; the deploy
job must not gain contents: write.

This repository's guard is the gh api form: GH_TOKEN from github.token, then
`gh api`. The urllib form is authenticated only when the request sends
Authorization: Bearer with that token. A token that is set and not sent still fails.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
FRESHNESS = "git/ref/heads/main"
TOKEN_VALUE = re.compile(r"\$\{\{\s*(?:github\.token|secrets\.GITHUB_TOKEN)\s*\}\}")
DIRECT_HTTP = re.compile(
    r"urlopen|urllib\.request|requests\.(?:get|post|request)"
    r"|(?:^|[^\w])curl\s|http\.client|\bfetch\("
)
GH_API = re.compile(r"\bgh\s+api\b")
EXPECTED_SHA = "a" * 40
BLOCK_SCALARS = {"|", ">", "|-", ">-", "|+", ">+"}

UNAUTHENTICATED = """\
jobs:
  deploy:
    permissions:
      pages: write
      id-token: write
    steps:
      - name: Check current main inside deployment lock
        env:
          EXPECTED_SHA: abcdef
        run: |
          python - <<'PYCODE'
          import json, os, urllib.request
          url = 'https://api.github.com/repos/example/repo/git/ref/heads/main'
          with urllib.request.urlopen(url, timeout=30) as response:
              current = json.load(response)['object']['sha']
          PYCODE
"""

TOKEN_PRESENT_BUT_UNUSED = """\
jobs:
  deploy:
    permissions:
      contents: read
    steps:
      - name: Check current main
        env:
          GITHUB_TOKEN: ${{ github.token }}
        run: |
          python - <<'PYCODE'
          import urllib.request
          url = 'https://api.github.com/repos/example/repo/git/ref/heads/main'
          urllib.request.urlopen(url)
          PYCODE
"""

GH_WITHOUT_TOKEN = """\
permissions:
  contents: read
jobs:
  deploy:
    steps:
      - name: Require current main
        run: |
          current=$(gh api repos/example/repo/git/ref/heads/main --jq .object.sha)
          test "$current" = "$GITHUB_SHA"
"""

AUTHENTICATED_GH = """\
permissions:
  contents: read
jobs:
  deploy:
    permissions:
      contents: read
    steps:
      - name: Require current main
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          ref="repos/${GITHUB_REPOSITORY}/git/ref/heads/main"
          current=$(gh api "$ref" --jq .object.sha)
          test "$current" = "$GITHUB_SHA"
"""

AUTHENTICATED_URLLIB = """\
jobs:
  deploy:
    permissions:
      contents: read
      pages: write
      id-token: write
    steps:
      - name: Require current main
        env:
          GITHUB_TOKEN: ${{ github.token }}
        run: |
          python - <<'PYCODE'
          import json, os, urllib.request
          token = os.environ["GITHUB_TOKEN"]
          repo = os.environ["GITHUB_REPOSITORY"]
          url = f"https://api.github.com/repos/{repo}/git/ref/heads/main"
          request = urllib.request.Request(
              url,
              headers={"Authorization": f"Bearer {token}"},
          )
          with urllib.request.urlopen(request, timeout=30) as response:
              current = json.load(response)["object"]["sha"]
          PYCODE
"""

OUTSIDE_THE_RUN_SCRIPT = """\
jobs:
  deploy:
    permissions:
      contents: read
    steps:
      - name: Check git/ref/heads/main
        env:
          GH_TOKEN: ${{ github.token }}
        run: echo ok
"""


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_value(value: str) -> str:
    value = re.sub(r"\s+#.*$", "", value.strip())
    return re.sub(r"""^['"]|['"]$""", "", value)


def _at_indent(indent: int, rest: str) -> re.Pattern[str]:
    return re.compile("^" + (" " * indent) + rest)


def _envs_at(lines: list[str], env_indent: int) -> list[dict[str, str]]:
    found = []
    env_re = _at_indent(env_indent, r"env:\s*$")
    for index, line in enumerate(lines):
        if not env_re.match(line):
            continue
        for inner in lines[index + 1 :]:
            if not inner.strip() or inner.lstrip().startswith("#"):
                continue
            indent = _indent_of(inner)
            if indent <= env_indent:
                break
            match = re.match(r"^ +([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", inner)
            if match and indent == env_indent + 2:
                found.append({"name": match.group(1), "value": _strip_value(match.group(2))})
    return found


def _permission_block(lines: list[str], block_indent: int) -> dict[str, str] | None:
    pattern = _at_indent(block_indent, r"permissions:\s*(.*)$")
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        inline = _strip_value(match.group(1))
        if inline and inline not in BLOCK_SCALARS:
            return {"_scalar": inline}
        block: dict[str, str] = {}
        for inner in lines[index + 1 :]:
            if not inner.strip() or inner.lstrip().startswith("#"):
                continue
            indent = _indent_of(inner)
            if indent <= block_indent:
                break
            key = re.match(r"^ +([\w-]+):\s*(.*)$", inner)
            if key and indent == block_indent + 2:
                block[key.group(1)] = _strip_value(key.group(2))
        return block
    return None


def _extract_jobs(text: str) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    in_jobs = False
    current: dict[str, object] | None = None
    for line in text.splitlines():
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line.strip() and _indent_of(line) == 0:
            break
        if re.match(r"^  [\w-]+:\s*$", line):
            if current is not None:
                jobs.append(current)
            current = {"name": line.strip()[:-1], "lines": [line]}
            continue
        if current is not None:
            lines = current["lines"]
            assert isinstance(lines, list)
            lines.append(line)
    if current is not None:
        jobs.append(current)
    return jobs


def _extract_steps(job_lines: list[str]) -> list[list[str]]:
    steps: list[list[str]] = []
    in_steps = False
    current: list[str] | None = None
    for line in job_lines:
        if re.match(r"^    steps:\s*$", line):
            in_steps = True
            continue
        if not in_steps:
            continue
        if line.strip() and _indent_of(line) <= 4:
            break
        if line.startswith("      - "):
            if current is not None:
                steps.append(current)
            current = [line]
            continue
        if current is not None:
            current.append(line)
    if current is not None:
        steps.append(current)
    return steps


def _run_script(step_lines: list[str]) -> str:
    scripts = []
    for index, line in enumerate(step_lines):
        match = re.match(r"^( +)run:\s*(.*)$", line)
        if not match:
            continue
        indent = len(match.group(1))
        rest = match.group(2).strip()
        if rest in BLOCK_SCALARS:
            body = []
            for inner in step_lines[index + 1 :]:
                if not inner.strip():
                    body.append("")
                    continue
                if _indent_of(inner) <= indent:
                    break
                body.append(inner)
            scripts.append("\n".join(body))
        else:
            scripts.append(_strip_value(rest))
    return "\n".join(scripts)


def _step_scalar(step_lines: list[str], key: str) -> str | None:
    first = re.match(rf"^\s+- {re.escape(key)}:\s*(.*)$", step_lines[0])
    if first:
        return _strip_value(first.group(1))
    for line in step_lines[1:]:
        match = re.match(rf"^ {{8}}{re.escape(key)}:\s*(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value in BLOCK_SCALARS:
            return None
        return _strip_value(value)
    return None


def _workflow_tokens(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        entry
        for entry in entries
        if entry["name"] in {"GH_TOKEN", "GITHUB_TOKEN"} and TOKEN_VALUE.search(entry["value"])
    ]


def _sends_bearer(script: str, token_names: list[str]) -> bool:
    if "Authorization" not in script or "Bearer" not in script:
        return False
    return any(name in script for name in token_names)


def _contents(job_lines: list[str], workflow_text: str) -> str | None:
    job = _permission_block(job_lines, 4)
    if job is not None:
        return job.get("contents")
    workflow = _permission_block(workflow_text.splitlines(), 0)
    if workflow is None:
        return None
    return workflow.get("contents")


def freshness_problems(filename: str, text: str) -> list[str]:
    """Problems for one workflow. An empty list means every freshness read is authenticated."""
    problems = []
    workflow_env = _workflow_tokens(_envs_at(text.splitlines(), 0))
    for job in _extract_jobs(text):
        job_lines = job["lines"]
        assert isinstance(job_lines, list)
        job_env = _workflow_tokens(_envs_at(job_lines, 4))
        for step in _extract_steps(job_lines):
            if FRESHNESS not in "\n".join(step):
                continue
            script = _run_script(step)
            tokens = _workflow_tokens([*workflow_env, *job_env, *_envs_at(step, 8)])
            token_names = [entry["name"] for entry in tokens]
            gh_api = GH_API.search(script) is not None
            direct_http = DIRECT_HTTP.search(script) is not None
            where = f"{filename} job {job['name']}"
            if FRESHNESS not in script:
                problems.append(
                    f"{where}: {FRESHNESS} is outside the run script, "
                    "so the call is not an authenticated read"
                )
                continue
            if not gh_api and not direct_http:
                problems.append(
                    f"{where}: {FRESHNESS} is called without authentication. "
                    "Pass GH_TOKEN or GITHUB_TOKEN from github.token and use gh api, "
                    "or send Authorization: Bearer."
                )
                continue
            if gh_api and not token_names:
                problems.append(
                    f"{where}: gh api reads {FRESHNESS} without authentication. "
                    "Set GH_TOKEN or GITHUB_TOKEN to ${{ github.token }} in the step env."
                )
            if direct_http and (not token_names or not _sends_bearer(script, token_names)):
                problems.append(
                    f"{where}: {FRESHNESS} is called without authentication. "
                    "Pass GITHUB_TOKEN from github.token in the step env and send "
                    "Authorization: Bearer with that token."
                )
            if _contents(job_lines, text) != "read":
                problems.append(
                    f"{where}: the freshness read must set permissions contents: read. "
                    "That scope is enough; do not grant contents: write."
                )
    return problems


def _workflow_texts() -> list[tuple[str, str]]:
    paths = sorted(path for path in WORKFLOWS.glob("*.y*ml") if path.is_file())
    return [(path.name, path.read_text(encoding="utf-8")) for path in paths]


def _select_docs_guard(steps: list[list[str]]) -> list[str]:
    guards = [
        step
        for step in steps
        if _step_scalar(step, "name") == "Require current main inside deployment lock"
    ]
    assert len(guards) == 1, "exactly one freshness guard is required"
    guard = guards[0]
    assert steps[-2] == guard, "freshness must be the final check before deployment"
    assert _step_scalar(steps[-1], "uses").startswith("actions/deploy-pages@")
    return guard


def _docs_guard() -> tuple[list[str], str]:
    text = (WORKFLOWS / "docs.yml").read_text(encoding="utf-8")
    jobs = {job["name"]: job for job in _extract_jobs(text)}
    deploy = jobs["deploy"]
    lines = deploy["lines"]
    assert isinstance(lines, list)
    steps = _extract_steps(lines)
    guard = _select_docs_guard(steps)
    return guard, _run_script(guard)


def test_repository_workflows_authenticate_every_main_freshness_read():
    calls = []
    problems = []
    for name, text in _workflow_texts():
        if FRESHNESS in text:
            calls.append(name)
        problems.extend(freshness_problems(name, text))
    assert problems == [], "\n".join(problems)
    assert calls == ["docs.yml"]

    text = (WORKFLOWS / "docs.yml").read_text(encoding="utf-8")
    deploy = next(job for job in _extract_jobs(text) if job["name"] == "deploy")
    lines = deploy["lines"]
    assert isinstance(lines, list)
    assert _permission_block(lines, 4) == {
        "contents": "read",
        "pages": "write",
        "id-token": "write",
    }
    assert re.search(r"^      group: go1-pages$", "\n".join(lines), re.M)
    assert re.search(r"^      cancel-in-progress: false$", "\n".join(lines), re.M)
    steps = _extract_steps(lines)
    assert any(
        _step_scalar(step, "name") == "Require matching ID and name readback of the exact tar"
        for step in steps[:-2]
    )
    guard, script = _docs_guard()
    assert steps[-2] == guard
    assert _step_scalar(guard, "name") == "Require current main inside deployment lock"
    env = {entry["name"]: entry["value"] for entry in _envs_at(guard, 8)}
    assert env["GH_TOKEN"] == "${{ github.token }}"
    assert env["CANDIDATE"] == "${{ github.sha }}"
    assert "gh api " in script
    assert FRESHNESS in script
    assert "urlopen" not in script
    assert 'test "$current" = "$CANDIDATE"' in script
    assert _step_scalar(steps[-1], "uses").startswith("actions/deploy-pages@")


def test_unauthenticated_urllib_freshness_read_fails():
    problems = freshness_problems("sample.yml", UNAUTHENTICATED)
    assert any("without authentication" in problem for problem in problems), problems
    assert any("contents: read" in problem for problem in problems), problems
    assert any("contents: write" in problem for problem in problems), problems


def test_token_that_is_not_sent_on_the_request_still_fails():
    problems = freshness_problems("sample.yml", TOKEN_PRESENT_BUT_UNUSED)
    assert any("without authentication" in problem for problem in problems), problems


def test_gh_api_without_a_token_fails():
    problems = freshness_problems("sample.yml", GH_WITHOUT_TOKEN)
    assert any("without authentication" in problem for problem in problems), problems


def test_freshness_url_outside_the_run_script_fails():
    problems = freshness_problems("sample.yml", OUTSIDE_THE_RUN_SCRIPT)
    assert any("outside the run script" in problem for problem in problems), problems


def test_gh_api_with_gh_token_and_urllib_with_authorization_bearer_pass():
    assert freshness_problems("gh.yml", AUTHENTICATED_GH) == []
    assert freshness_problems("urllib.yml", AUTHENTICATED_URLLIB) == []


def test_contents_write_fails_even_when_the_read_sends_a_token():
    text = AUTHENTICATED_URLLIB.replace("contents: read", "contents: write", 1)
    problems = freshness_problems("sample.yml", text)
    assert any("contents: read" in problem and "contents: write" in problem for problem in problems)


def _run_guard(
    tmp_path: Path, *, remote_sha: str, api_exit: int
) -> subprocess.CompletedProcess[str]:
    _guard, script = _docs_guard()
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" > "$MOCK_ARGS"\n'
        'if [[ -z "${GH_TOKEN:-}" ]]; then\n'
        '  echo "missing GH_TOKEN" >&2\n'
        "  exit 98\n"
        "fi\n"
        'printf "%s\\n" "$GH_TOKEN" > "$MOCK_TOKEN"\n'
        'printf "%s\\n" "$MOCK_SHA"\n'
        'exit "$MOCK_EXIT"\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    # Actions runs run: blocks with bash -eo pipefail. Match that so a failed
    # gh api fails the step before deploy-pages, instead of falling through.
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        env={
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "GH_TOKEN": "workflow-token",
            "CANDIDATE": EXPECTED_SHA,
            "MOCK_SHA": remote_sha,
            "MOCK_EXIT": str(api_exit),
            "MOCK_ARGS": str(tmp_path / "args"),
            "MOCK_TOKEN": str(tmp_path / "token"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("remote_sha", "api_exit", "expected_code"),
    [
        (EXPECTED_SHA, 0, 0),
        ("b" * 40, 0, 1),
        ("", 0, 1),
        (EXPECTED_SHA, 1, 1),
    ],
)
def test_deploy_guard_still_aborts_when_main_moved_or_the_read_fails(
    tmp_path: Path, remote_sha: str, api_exit: int, expected_code: int
):
    result = _run_guard(tmp_path, remote_sha=remote_sha, api_exit=api_exit)
    assert result.returncode == expected_code, result.stderr
    assert "workflow-token" not in result.stdout
    assert "workflow-token" not in result.stderr
    assert (tmp_path / "token").read_text(encoding="utf-8") == "workflow-token\n"
    args = (tmp_path / "args").read_text(encoding="utf-8")
    assert "repos/Living-Proof-Labs/livingproof-unitree-go1/git/ref/heads/main" in args


@pytest.mark.parametrize("mutation", ["moved", "missing", "duplicate"])
def test_freshness_guard_selection_rejects_invalid_position_or_count(mutation):
    text = (WORKFLOWS / "docs.yml").read_text(encoding="utf-8")
    deploy = next(job for job in _extract_jobs(text) if job["name"] == "deploy")
    steps = _extract_steps(deploy["lines"])
    guard = _select_docs_guard(steps)
    if mutation == "moved":
        steps.remove(guard)
        steps.insert(0, guard)
    elif mutation == "missing":
        steps.remove(guard)
    else:
        steps.insert(0, guard)
    with pytest.raises(AssertionError):
        _select_docs_guard(steps)


def test_removing_the_actual_freshness_guard_token_fails():
    text = (WORKFLOWS / "docs.yml").read_text(encoding="utf-8")
    guard, _ = _docs_guard()
    original = "\n".join(guard)
    stripped = original.replace("          GH_TOKEN: ${{ github.token }}\n", "", 1)
    assert stripped != original
    mutated = text.replace(original, stripped, 1)
    assert mutated != text
    problems = freshness_problems("docs.yml", mutated)
    assert any("without authentication" in problem for problem in problems), problems
