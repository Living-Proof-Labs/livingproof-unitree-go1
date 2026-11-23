# SPDX-License-Identifier: Apache-2.0
"""Release gate, race model and workflow contract for the workflow_run release.

Proposed by Vera in the booster-t1#63 review (issue #59) and adopted here for
issue #53. Under `workflow_run`, GITHUB_SHA is the *default-branch tip* when the
Test run completes, not the commit Test ran on (GitHub docs, "Events that trigger
workflows", workflow_run: GITHUB_SHA = "Last commit on default branch"). The
release `if:` compares the two, so a Test run for a commit that is no longer the
tip is skipped, and the Test run for the newer tip releases everything since the
last tag in one version.

Offline:
- every workflow declares a name, triggers and jobs, and the Test workflow keeps
  the push-on-main run that the gate needs;
- only the shared release call holds contents: write; the exact dependent docs
  call has separate Pages permissions, no steps and no secrets;
- the real `if:` is evaluated over a timeline of pushes and Test completions.
LIVINGPROOF_NETWORK=1 also checks the pinned shared workflow still checks out
GITHUB_SHA and still refuses to run when HEAD is not GITHUB_SHA, which is what
makes "release the commit that Test passed on" hold without an explicit `ref:`.
"""

from __future__ import annotations

import os
import re
import urllib.request
from pathlib import Path

import pytest

from test_release_gate import _code, _if_expr, _jobs, _top_section, _workflow_name

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
TEST = WORKFLOWS / "test.yml"
GUIDELINES = "Living-Proof-Labs/collaboration-guidelines"
REPO = "Living-Proof-Labs/livingproof-unitree-go1"


def _triggers(text: str) -> dict[str, str]:
    # Values are the raw YAML body of each top-level trigger key.
    code = _code(text)
    block = _top_section(code, "on")
    assert block is not None, "workflow must define an on: mapping"
    headers = list(re.finditer(r"^  ([A-Za-z0-9_]+):[ \t]*(.*)$", block, re.M))
    triggers: dict[str, str] = {}
    for index, match in enumerate(headers):
        start = match.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(block)
        rest = (match.group(2) or "").strip()
        triggers[match.group(1)] = rest + block[start:end]
    return triggers


# --- a second, independent evaluator for the release job's `if:` -----------------------

_TOKEN = re.compile(r"'(?:[^']|'')*'|[A-Za-z_][\w.-]*|&&|\|\||==|!=|[()!]|\s+")


def gate_allows(expr: str, context: dict) -> bool:
    """Evaluate a restricted GitHub `if:` expression by translating it to Python."""
    text = expr.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()

    def lookup(path: str) -> object:
        cur: object = context
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return ""  # GitHub: a missing property is null, which == '' is true for
            cur = cur[part]
        return cur

    out: list[str] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        assert match, f"unsupported expression syntax near {text[pos:]!r}"
        tok = match.group(0)
        pos = match.end()
        if tok.isspace():
            continue
        if tok.startswith("'"):
            out.append(repr(tok[1:-1].replace("''", "'")))
        elif tok == "&&":
            out.append(" and ")
        elif tok == "||":
            out.append(" or ")
        elif tok == "!":
            out.append(" not ")
        elif tok in ("(", ")", "==", "!="):
            out.append(tok)
        elif tok in ("true", "false"):
            out.append(tok.capitalize())
        else:
            assert not text[pos:].lstrip().startswith("("), (
                f"function calls are not allowed: {tok}()"
            )
            out.append(f"lookup({tok!r})")
    return bool(eval("".join(out), {"__builtins__": {}}, {"lookup": lookup}))  # noqa: S307


def release_if() -> str:
    jobs = _jobs(_code(RELEASE.read_text(encoding="utf-8")))
    body = jobs["release"]
    cond = _if_expr(body)
    assert isinstance(cond, str) and cond.strip(), "the release job needs an explicit if:"
    return cond


def completion(*, tip: str, tested: str, conclusion: str = "success", **overrides: object) -> dict:
    """The github context of the Release run that a completed Test run starts."""
    run: dict = {
        "event": "push",
        "head_branch": "main",
        "head_repository": {"full_name": REPO},
        "conclusion": conclusion,
        "head_sha": tested,
    }
    run.update(overrides)
    return {
        "github": {
            "event_name": "workflow_run",
            "ref": "refs/heads/main",
            "sha": tip,  # workflow_run: last commit on the default branch
            "repository": REPO,
            "event": {"workflow_run": run},
        }
    }


# --- timeline model: which Test completions release, and what each release contains ---------


def simulate(events: list[tuple]) -> list[tuple[str, list[str]]]:
    """Replay pushes and Test completions. Returns (released commit, commits in the version)."""
    expr = release_if()
    pushed: list[str] = []
    released_upto = 0
    releases: list[tuple[str, list[str]]] = []
    for event in events:
        if event[0] == "push":
            pushed.append(event[1])
            continue
        _, tested, conclusion = event
        tip = pushed[-1]
        if gate_allows(expr, completion(tip=tip, tested=tested, conclusion=conclusion)):
            # The shared workflow checks out github.sha (== tip == tested) and
            # semantic-release releases everything since the last tag.
            assert tested == tip
            releases.append((tip, pushed[released_upto:]))
            released_upto = len(pushed)
    return releases


@pytest.mark.parametrize(
    ("name", "events", "expected"),
    [
        (
            "main moves while Test runs: the older run skips, the newer run batches both",
            [("push", "A"), ("push", "B"), ("done", "A", "success"), ("done", "B", "success")],
            [("B", ["A", "B"])],
        ),
        (
            "the newer Test finishes first: it releases both, the older one is then skipped",
            [("push", "A"), ("push", "B"), ("done", "B", "success"), ("done", "A", "success")],
            [("B", ["A", "B"])],
        ),
        (
            "Test finishes before the next push: two releases, one per commit",
            [("push", "A"), ("done", "A", "success"), ("push", "B"), ("done", "B", "success")],
            [("A", ["A"]), ("B", ["B"])],
        ),
        (
            "a failed Test on the tip never releases",
            [("push", "A"), ("done", "A", "failure")],
            [],
        ),
        (
            "the newer Test fails: nothing releases until a later green run batches all three",
            [
                ("push", "A"),
                ("push", "B"),
                ("done", "A", "success"),
                ("done", "B", "failure"),
                ("push", "C"),
                ("done", "C", "success"),
            ],
            [("C", ["A", "B", "C"])],
        ),
        (
            "a cancelled or skipped Test never releases",
            [("push", "A"), ("done", "A", "cancelled"), ("done", "A", "skipped")],
            [],
        ),
        (
            "a re-run of an old green Test cannot release once main moved on",
            [("push", "A"), ("done", "A", "success"), ("push", "B"), ("done", "A", "success")],
            [("A", ["A"])],
        ),
    ],
)
def test_release_race_model(name: str, events: list[tuple], expected: list) -> None:
    assert simulate(events) == expected, name


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "pull_request"},  # a fork or branch named main
        {"event": "merge_group", "head_branch": "gh-readonly-queue/main/pr-1-abc"},
        {"event": "workflow_dispatch"},
        {"head_branch": "feature"},
        {"head_repository": {"full_name": "someone-else/livingproof-unitree-go1"}},
    ],
)
def test_only_same_repository_push_runs_on_main_can_release(overrides: dict) -> None:
    ctx = completion(tip="a" * 40, tested="a" * 40, **overrides)
    assert not gate_allows(release_if(), ctx)
    assert gate_allows(release_if(), completion(tip="a" * 40, tested="a" * 40))


# --- workflow contract ---------------------------------------------------------------------


def test_every_workflow_parses_and_declares_name_triggers_and_jobs() -> None:
    files = sorted(WORKFLOWS.glob("*.y*ml"))
    assert files
    names = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        code = _code(text)
        assert _workflow_name(text), path.name
        assert _top_section(code, "on"), path.name
        jobs = _jobs(code)
        assert jobs, path.name
        names.append(_workflow_name(text))
    assert len(names) == len(set(names)), f"duplicate workflow names: {names}"


def test_test_workflow_still_runs_on_push_to_main() -> None:
    """The gate needs a Test run with event == push on main, or nothing ever releases."""
    triggers = _triggers(TEST.read_text(encoding="utf-8"))
    assert re.search(r"branches:\s*\[main\]", triggers["push"])
    assert "workflow_run" not in triggers


def _assert_release_job_boundaries(text: str) -> None:
    code = _code(text)
    perms = _top_section(code, "permissions")
    assert perms is not None and perms.strip() == "contents: read"
    jobs = _jobs(code)
    assert set(jobs) == {"release", "deploy-manual"}
    name, body = "release", jobs["release"]
    assert not re.search(r"^    steps:", body, re.M), (
        "no steps: nothing from the repository runs with contents: write"
    )
    job_perms = re.search(r"^    permissions:\n((?:      .+\n?)+)", body + "\n", re.M)
    assert job_perms is not None and job_perms.group(1).strip() == "contents: write"
    assert re.search(
        rf"^    uses:\s*{re.escape(GUIDELINES)}/\.github/workflows/semantic-release\.yml@v\S+\s*$",
        body,
        re.M,
    )
    for forbidden in ("secrets:", "with:", "env:", "continue-on-error:", "needs:"):
        assert not re.search(rf"^    {re.escape(forbidden)}", body, re.M), name
    # Check the entire delivery call: no extra steps, secrets or broader permissions.
    assert (
        jobs["deploy-manual"].strip()
        == """    needs: release
    if: needs.release.outputs.published == 'true'
    permissions:
      contents: read
      pages: write
      id-token: write
    uses: ./.github/workflows/docs.yml
    with:
      release_tag: ${{ needs.release.outputs.git-tag }}
      release_version: ${{ needs.release.outputs.version }}""".strip()
    )
    # A new Test run must never cancel a release that is already tagging.
    assert _top_section(code, "concurrency") is None
    assert "cancel-in-progress" not in text


def test_release_workflow_keeps_one_contents_writer_and_exact_gated_docs_call() -> None:
    _assert_release_job_boundaries(RELEASE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "mutation",
    ["extra-job", "writer-steps", "delivery-write", "ungated", "no-dependency", "wrong-workflow"],
)
def test_release_job_boundary_mutations_are_rejected(mutation):
    text = RELEASE.read_text(encoding="utf-8")
    if mutation == "extra-job":
        text += "\n  extra:\n    runs-on: ubuntu-latest\n"
    elif mutation == "writer-steps":
        text = text.replace("  release:\n", "  release:\n    steps: []\n", 1)
    elif mutation == "delivery-write":
        text = text.replace("      contents: read", "      contents: write", 1)
    elif mutation == "ungated":
        text = text.replace("if: needs.release.outputs.published == 'true'", "if: always()", 1)
    elif mutation == "no-dependency":
        text = text.replace("    needs: release\n", "", 1)
    else:
        text = text.replace(
            "uses: ./.github/workflows/docs.yml", "uses: ./.github/workflows/other.yml", 1
        )
    with pytest.raises(AssertionError):
        _assert_release_job_boundaries(text)


@pytest.mark.parametrize("published", ["true", "false", "", "missing", "failed", "skipped"])
def test_docs_delivery_requires_published_release(published):
    job = _jobs(_code(RELEASE.read_text(encoding="utf-8")))["deploy-manual"]
    assert re.search(r"^    needs: release$", job, re.M)
    assert gate_allows(
        _if_expr(job), {"needs": {"release": {"outputs": {"published": published}}}}
    ) == (published == "true")


def test_release_workflow_listens_only_to_the_test_workflow_by_its_exact_name() -> None:
    triggers = _triggers(RELEASE.read_text(encoding="utf-8"))
    assert set(triggers) == {"workflow_run"}
    body = triggers["workflow_run"]
    test_name = _workflow_name(TEST.read_text(encoding="utf-8"))
    assert re.search(rf"^    workflows:\s*\[{re.escape(test_name)}\]\s*$", body, re.M)
    assert re.search(r"^    types:\s*\[completed\]\s*$", body, re.M)
    assert re.search(r"^    branches:\s*\[main\]\s*$", body, re.M)


# --- the shared workflow (network) ---------------------------------------------------------


def _pinned_ref() -> str:
    match = re.search(
        rf"uses:\s*{re.escape(GUIDELINES)}/\.github/workflows/semantic-release\.yml@(\S+)",
        RELEASE.read_text(encoding="utf-8"),
    )
    assert match
    return match.group(1)


def _raw(path: str) -> str:
    url = f"https://raw.githubusercontent.com/{GUIDELINES}/{_pinned_ref()}/{path}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode()


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_pinned_shared_workflow_releases_exactly_github_sha() -> None:
    """With head_sha == github.sha required by the gate, these two facts mean: tag the tested commit.

    A Renovate bump of the guidelines tag that changes either one must be reviewed together
    with this gate.
    """
    workflow = _raw(".github/workflows/semantic-release.yml")
    code = _code(workflow)
    jobs = _jobs(code)
    release = jobs["release"]
    steps = re.split(r"^      - ", release, flags=re.M)
    checkout_steps = [step for step in steps if re.match(r"uses:\s*actions/checkout@", step)]
    (checkout,) = checkout_steps
    assert not re.search(r"^\s+ref:", checkout, re.M), (
        "checkout must default to GITHUB_REF at GITHUB_SHA"
    )
    assert re.search(r"persist-credentials:\s*false", checkout)
    assert re.search(r"^    if:\s*github\.ref == 'refs/heads/main'\s*$", release, re.M)
    runner = _raw(".github/actions/semantic-release/run.mjs")
    assert "process.env.GITHUB_SHA" in runner
    assert "Checked-out HEAD must match the triggering commit" in runner
    assert "git fetch" not in runner and "git pull" not in runner
