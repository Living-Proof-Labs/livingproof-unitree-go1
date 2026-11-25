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
Offline fixtures cover that contract without a network: plan.mjs refuses when
HEAD is not the expected commit, run.mjs passes GITHUB_SHA into planVersion
unless the run is a candidate, and the only fetch copies one tag onto itself.
A GITHUB_SHA kept in a variable counts only when that variable is the expected
commit. LIVINGPROOF_NETWORK=1 checks the same facts on the pinned tag.
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


# The shared scripts are parsed, not searched as text, so a reflow or a quote
# change still passes and a comment cannot stand in for the check.
_OPEN = {"(", "[", "{"}
_CLOSE = {")", "]", "}"}
_HEAD_MISMATCH = "Checked-out HEAD must match the triggering commit"
_ALLOWED_FETCH_OPTIONS = {"--force", "-f"}
_READ_ONLY_GIT = {"rev-parse"}
_SHELL_GIT = re.compile(
    r"\bgit\s+(fetch|pull|checkout|switch|reset|rebase|merge|clean|am|cherry-pick)\b"
)


def _regex_end(source: str, start: int) -> int | None:
    i = start + 1
    in_class = False
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "\n":
            return None
        if ch == "[" and not in_class:
            in_class = True
        elif ch == "]" and in_class:
            in_class = False
        elif ch == "/" and not in_class:
            i += 1
            while i < len(source) and source[i].isalpha():
                i += 1
            return i
        i += 1
    return None


def _read_string(source: str, start: int) -> tuple[int, str]:
    quote = source[start]
    chars: list[str] = []
    i = start + 1
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            if i + 1 < len(source):
                chars.append(source[i + 1])
            i += 2
            continue
        if ch == quote:
            return i + 1, "".join(chars)
        if ch == "\n":
            break
        chars.append(ch)
        i += 1
    return i, "".join(chars)


def _matching_brace(source: str, open_at: int) -> int:
    depth = 0
    i = open_at
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(source) and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            i = len(source) if end < 0 else end + 2
            continue
        if ch in "'\"":
            i, _value = _read_string(source, i)
            continue
        if ch == "`":
            i, _parts = _read_template(source, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(source)


def _read_template(source: str, start: int) -> tuple[int, tuple]:
    parts: list[tuple[str, str]] = [("static", "")]
    i = start + 1
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            if i + 1 < len(source):
                kind, text = parts[-1]
                parts[-1] = (kind, text + source[i + 1])
            i += 2
            continue
        if ch == "`":
            return i + 1, tuple(parts)
        if ch == "$" and i + 1 < len(source) and source[i + 1] == "{":
            end = _matching_brace(source, i + 1)
            parts.append(("expr", source[i + 2 : end - 1].strip()))
            parts.append(("static", ""))
            i = end
            continue
        kind, text = parts[-1]
        parts[-1] = (kind, text + ch)
        i += 1
    return i, tuple(parts)


def _tokenize(source: str) -> list[tuple]:
    tokens: list[tuple] = []
    i = 0
    prev = ""
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if ch.isspace():
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(source) and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            i = len(source) if end < 0 else end + 2
            continue
        if ch == "/":
            if not (prev.isalnum() or prev in "_$)]"):
                end = _regex_end(source, i)
                if end is not None:
                    prev = ")"
                    i = end
                    continue
            tokens.append(("punct", "/"))
            prev = "/"
            i += 1
            continue
        if ch in "'\"":
            i, value = _read_string(source, i)
            tokens.append(("str", value))
            prev = ")"
            continue
        if ch == "`":
            i, parts = _read_template(source, i)
            tokens.append(("tmpl", parts))
            prev = ")"
            continue
        if ch.isalpha() or ch in "_$":
            j = i + 1
            while j < len(source) and (source[j].isalnum() or source[j] in "_$"):
                j += 1
            tokens.append(("ident", source[i:j]))
            prev = source[j - 1]
            i = j
            continue
        if ch.isdigit():
            j = i + 1
            while j < len(source) and (source[j].isdigit() or source[j] == "."):
                j += 1
            tokens.append(("num", source[i:j]))
            prev = source[j - 1]
            i = j
            continue
        three = source[i : i + 3]
        if three in {"===", "!=="}:
            tokens.append(("punct", three))
            prev = three[-1]
            i += 3
            continue
        two = source[i : i + 2]
        if two in {"=>", "==", "!=", "<=", ">=", "&&", "||", "??", "?.", "++", "--", "**"}:
            tokens.append(("punct", two))
            prev = two[-1]
            i += 2
            continue
        tokens.append(("punct", ch))
        prev = ch
        i += 1
    return tokens


def _consume_brackets(tokens: list[tuple], start: int) -> tuple[list[tuple], int]:
    depth = 1
    i = start + 1
    begin = i
    while i < len(tokens):
        val = tokens[i][1]
        if val in _OPEN:
            depth += 1
        elif val in _CLOSE:
            depth -= 1
            if depth == 0:
                return tokens[begin:i], i + 1
        i += 1
    raise AssertionError("unbalanced bracket in the shared script")


def _parse_list(tokens: list[tuple], start: int) -> tuple[list[list[tuple]], int]:
    inner, end = _consume_brackets(tokens, start)
    items: list[list[tuple]] = []
    current: list[tuple] = []
    depth = 0
    for tok in inner:
        if tok[1] in _OPEN:
            depth += 1
            current.append(tok)
        elif tok[1] in _CLOSE:
            depth -= 1
            current.append(tok)
        elif depth == 0 and tok[1] == ",":
            items.append(current)
            current = []
        else:
            current.append(tok)
    items.append(current)
    return items, end


def _until_semicolon(tokens: list[tuple], start: int) -> tuple[list[tuple], int]:
    depth = 0
    i = start
    while i < len(tokens):
        val = tokens[i][1]
        if val in _OPEN:
            depth += 1
        elif val in _CLOSE:
            if depth == 0:
                break
            depth -= 1
        elif val == ";" and depth == 0:
            return tokens[start:i], i + 1
        i += 1
    return tokens[start:i], i


def _calls(tokens: list[tuple], names: set[str]) -> list[tuple[int, list[list[tuple]]]]:
    found = []
    i = 0
    while i < len(tokens):
        if (
            tokens[i][0] == "ident"
            and tokens[i][1] in names
            and i + 1 < len(tokens)
            and tokens[i + 1][1] == "("
        ):
            args, nxt = _parse_list(tokens, i + 1)
            found.append((i, args))
            i = nxt
            continue
        i += 1
    return found


def _assignments(tokens: list[tuple]) -> list[tuple[int, str, list[tuple]]]:
    found = []
    i = 0
    while i < len(tokens):
        if (
            tokens[i][1] in {"const", "let", "var"}
            and i + 2 < len(tokens)
            and tokens[i + 1][0] == "ident"
            and tokens[i + 2][1] == "="
        ):
            rhs, nxt = _until_semicolon(tokens, i + 3)
            found.append((i, tokens[i + 1][1], rhs))
            i = nxt
            continue
        i += 1
    return found


def _unwrap(tokens: list[tuple]) -> list[tuple]:
    tokens = list(tokens)
    while len(tokens) >= 2 and tokens[0][1] == "(" and tokens[-1][1] == ")":
        depth = 0
        matches = False
        for index, tok in enumerate(tokens):
            if tok[1] in _OPEN:
                depth += 1
            elif tok[1] in _CLOSE:
                depth -= 1
                if depth == 0:
                    matches = index == len(tokens) - 1
                    break
        if not matches:
            break
        tokens = tokens[1:-1]
    return tokens


def _sequence(tokens: list[tuple], pattern: list[tuple]) -> bool:
    width = len(pattern)
    for index in range(len(tokens) - width + 1):
        if [
            (tokens[index + offset][0], tokens[index + offset][1]) for offset in range(width)
        ] == pattern:
            return True
    return False


def _controlling_condition(tokens: list[tuple], throw_at: int) -> list[tuple] | None:
    index = throw_at - 1
    if index >= 0 and tokens[index][1] == "{":
        index -= 1
    if index < 0 or tokens[index][1] != ")":
        return None
    depth = 0
    opener = index
    while opener >= 0:
        val = tokens[opener][1]
        if val in _CLOSE:
            depth += 1
        elif val in _OPEN:
            depth -= 1
            if depth == 0:
                break
        opener -= 1
    else:
        return None
    if opener < 1 or tokens[opener][1] != "(" or tokens[opener - 1][1] != "if":
        return None
    return tokens[opener + 1 : index]


def _error_throws(tokens: list[tuple]) -> list[tuple[int, tuple | str]]:
    found = []
    index = 0
    while index < len(tokens):
        if (
            tokens[index][1] == "throw"
            and index + 3 < len(tokens)
            and tokens[index + 1][1] == "new"
            and tokens[index + 2][1] == "Error"
            and tokens[index + 3][1] == "("
        ):
            args, nxt = _parse_list(tokens, index + 3)
            message: tuple | str = ""
            if args and len(args[0]) == 1 and args[0][0][0] in {"str", "tmpl"}:
                message = args[0][0][1]
            found.append((index, message))
            index = nxt
            continue
        index += 1
    return found


def _rhs_strings(rhs: list[tuple]) -> list[str]:
    values = [tok[1] for tok in rhs if tok[0] == "str"]
    for _at, args in _calls(rhs, {tok[1] for tok in rhs if tok[0] == "ident"}):
        for arg in args:
            if arg and arg[0][1] == "[":
                elements, _end = _parse_list(arg, 0)
                for element in elements:
                    if len(element) == 1 and element[0][0] == "str":
                        values.append(element[0][1])
    return values


def _rhs_is_rev_parse_head(rhs: list[tuple]) -> bool:
    values = _rhs_strings(rhs)
    try:
        rev = values.index("rev-parse")
    except ValueError:
        return False
    return "HEAD" in values[rev + 1 :]


def _bindings_before(
    assignments: list[tuple[int, str, list[tuple]]], index: int
) -> dict[str, list[tuple]]:
    bindings: dict[str, list[tuple]] = {}
    for at, name, rhs in assignments:
        if at < index:
            bindings[name] = rhs
    return bindings


def _is_github_sha(tokens: list[tuple]) -> bool:
    kind = [(tok[0], tok[1]) for tok in _unwrap(tokens)]
    member = [
        ("ident", "process"),
        ("punct", "."),
        ("ident", "env"),
        ("punct", "."),
        ("ident", "GITHUB_SHA"),
    ]
    indexed = [
        ("ident", "process"),
        ("punct", "."),
        ("ident", "env"),
        ("punct", "["),
        ("str", "GITHUB_SHA"),
        ("punct", "]"),
    ]
    return kind == member or kind == indexed


def _split_ternary(tokens: list[tuple]) -> tuple[list[tuple], list[tuple], list[tuple]] | None:
    depth = 0
    question = None
    for index, tok in enumerate(tokens):
        if tok[1] in _OPEN:
            depth += 1
        elif tok[1] in _CLOSE:
            depth -= 1
        elif depth == 0 and tok[1] == "?":
            question = index
            break
    if question is None:
        return None
    depth = 0
    pending = 1
    for index in range(question + 1, len(tokens)):
        tok = tokens[index]
        if tok[1] in _OPEN:
            depth += 1
        elif tok[1] in _CLOSE:
            depth -= 1
        elif depth == 0 and tok[1] == "?":
            pending += 1
        elif depth == 0 and tok[1] == ":":
            pending -= 1
            if pending == 0:
                return tokens[:question], tokens[question + 1 : index], tokens[index + 1 :]
    return None


def _expr_is_github_sha_when_not_candidate(
    tokens: list[tuple], bindings: dict[str, list[tuple]], depth: int = 0
) -> bool:
    if depth > 8:
        return False
    tokens = _unwrap(tokens)
    if _is_github_sha(tokens):
        return True
    split = _split_ternary(tokens)
    if split:
        cond, yes, no = split
        cond = _unwrap(cond)
        kind = [(tok[0], tok[1]) for tok in cond]
        if kind == [("ident", "candidate")]:
            chosen = no
        elif kind == [("punct", "!"), ("ident", "candidate")]:
            chosen = yes
        else:
            return False
        return _expr_is_github_sha_when_not_candidate(chosen, bindings, depth + 1)
    if len(tokens) == 1 and tokens[0][0] == "ident":
        rhs = bindings.get(tokens[0][1])
        if rhs is None:
            return False
        return _expr_is_github_sha_when_not_candidate(rhs, bindings, depth + 1)
    return False


def _imported_names(tokens: list[tuple], exported: str, module: str) -> set[str]:
    names: set[str] = set()
    for index, tok in enumerate(tokens):
        if tok[1] != "import" or index + 1 >= len(tokens) or tokens[index + 1][1] != "{":
            continue
        items, nxt = _parse_list(tokens, index + 1)
        if (
            nxt + 1 >= len(tokens)
            or tokens[nxt][1] != "from"
            or tokens[nxt + 1][0] != "str"
            or tokens[nxt + 1][1] != module
        ):
            continue
        for item in items:
            idents = [part[1] for part in item if part[0] == "ident"]
            if not idents or idents[0] != exported:
                continue
            names.add(idents[-1] if "as" in idents else idents[0])
    return names


def _assert_plan_refuses_head_mismatch(plan: str) -> None:
    tokens = _tokenize(plan)
    assignments = _assignments(tokens)
    throws = [
        (index, message) for index, message in _error_throws(tokens) if message == _HEAD_MISMATCH
    ]
    assert throws, "plan.mjs must throw when checked-out HEAD is not the triggering commit"
    matched = False
    for index, _message in throws:
        condition = _controlling_condition(tokens, index)
        if condition is None:
            continue
        # Last assignment wins: a name counts only while it still holds rev-parse HEAD.
        current = _bindings_before(assignments, index)
        names = {name for name, rhs in current.items() if _rhs_is_rev_parse_head(rhs)}
        for name in names:
            patterns = (
                [("ident", name), ("punct", "!=="), ("ident", "expected")],
                [("ident", "expected"), ("punct", "!=="), ("ident", name)],
                [
                    ("punct", "!"),
                    ("punct", "("),
                    ("ident", name),
                    ("punct", "==="),
                    ("ident", "expected"),
                    ("punct", ")"),
                ],
                [
                    ("punct", "!"),
                    ("punct", "("),
                    ("ident", "expected"),
                    ("punct", "==="),
                    ("ident", name),
                    ("punct", ")"),
                ],
            )
            if any(_sequence(condition, pattern) for pattern in patterns):
                matched = True
    assert matched, "the HEAD-mismatch throw must run when rev-parsed HEAD !== expected"


def _assert_runner_passes_github_sha_to_plan(runner: str) -> None:
    tokens = _tokenize(runner)
    local_names = _imported_names(tokens, "planVersion", "./plan.mjs")
    assert local_names, "run.mjs must import planVersion from ./plan.mjs"
    calls = _calls(tokens, local_names)
    assert calls, "run.mjs must call planVersion"
    assignments = _assignments(tokens)
    for index, args in calls:
        assert len(args) >= 2, "planVersion must receive the expected commit"
        assert _expr_is_github_sha_when_not_candidate(
            args[1], _bindings_before(assignments, index)
        ), "a non-candidate run must pass GITHUB_SHA as the expected commit"


def _static_piece(tokens: list[tuple]) -> tuple[str, object] | None:
    tokens = _unwrap(tokens)
    if len(tokens) == 1 and tokens[0][0] in {"str", "tmpl", "ident"}:
        return tokens[0][0], tokens[0][1]
    return None


def _same_tag_refspec(piece: tuple[str, object]) -> bool:
    kind, value = piece
    if kind == "str" and isinstance(value, str):
        text = value[1:] if value.startswith("+") else value
        src, sep, dst = text.partition(":")
        return (
            bool(sep)
            and src.startswith("refs/tags/")
            and src == dst
            and len(src) > len("refs/tags/")
        )
    if kind == "tmpl" and isinstance(value, tuple):
        if len(value) != 5 or value[0][0] != "static" or value[2][0] != "static":
            return False
        left = value[0][1][1:] if value[0][1].startswith("+") else value[0][1]
        expr_a = value[1][1] if value[1][0] == "expr" else None
        expr_b = value[3][1] if value[3][0] == "expr" else None
        return (
            left == "refs/tags/"
            and value[2][1] == ":refs/tags/"
            and value[4] == ("static", "")
            and bool(expr_a)
            and expr_a == expr_b
        )
    return False


def _git_commands(tokens: list[tuple]) -> list[tuple[int, list[list[tuple]]]]:
    commands = []
    for index, args in _calls(tokens, {"execFileSync", "execFile"}):
        if not args or len(args[0]) != 1 or args[0][0] != ("str", "git"):
            continue
        if len(args) < 2 or not args[1] or args[1][0][1] != "[":
            raise AssertionError("git must be invoked with an argument array, not a shell string")
        elements, _end = _parse_list(args[1], 0)
        commands.append((index, [element for element in elements if element]))
    return commands


def _functions(tokens: list[tuple]) -> list[dict]:
    found = []
    index = 0
    while index < len(tokens):
        if tokens[index] != ("ident", "function"):
            index += 1
            continue
        cursor = index + 1
        name = tokens[cursor][1] if cursor < len(tokens) and tokens[cursor][0] == "ident" else ""
        while cursor < len(tokens) and tokens[cursor][1] != "(":
            cursor += 1
        if cursor >= len(tokens):
            break
        params, after = _parse_list(tokens, cursor)
        if after >= len(tokens) or tokens[after][1] != "{":
            index = after
            continue
        _body, end = _consume_brackets(tokens, after)
        found.append(
            {
                "name": name,
                "params": params,
                "body": tokens[after + 1 : end - 1],
                "start": index,
                "end": end,
            }
        )
        index = end
    return found


def _names_compared_unequal_to(tokens: list[tuple], expected_name: str) -> set[str]:
    """Identifiers a throw's controlling if compares with ``!== expected_name``."""
    found: set[str] = set()
    for throw_at, _message in _error_throws(tokens):
        condition = _controlling_condition(tokens, throw_at)
        if not condition:
            continue
        for index, tok in enumerate(condition):
            if tok != ("punct", "!==") or index == 0 or index + 1 >= len(condition):
                continue
            left, right = condition[index - 1], condition[index + 1]
            if left == ("ident", expected_name) and right[0] == "ident":
                found.add(right[1])
            elif right == ("ident", expected_name) and left[0] == "ident":
                found.add(left[1])
    return found


def _param_name(param: list[tuple]) -> str | None:
    idents = [tok[1] for tok in param if tok[0] == "ident"]
    return idents[0] if idents else None


def _rhs_peels_tag(rhs: list[tuple]) -> bool:
    values = _rhs_strings(rhs)
    if "rev-parse" not in values or "--verify" not in values:
        return False
    for tok in rhs:
        if tok[0] != "tmpl":
            continue
        static = "".join(part[1] for part in tok[1] if part[0] == "static")
        if "refs/tags/" in static and "^{commit}" in static:
            return True
    return False


def _assert_fetch_cannot_change_released_commit(runner: str) -> None:
    """A tag fetch may update refs/tags only, and only to verify the planned commit.

    ``git fetch origin refs/tags/<tag>:refs/tags/<tag> --force`` does not move
    HEAD or the current branch. The fetch that is present has to be that shape,
    and the function that performs it has to throw unless the tag peels to the
    commit ``planVersion`` already accepted. A branch fetch, a pull, or a
    checkout fails this check.
    """
    tokens = _tokenize(runner)
    for tok in tokens:
        if tok[0] == "str" and _SHELL_GIT.search(tok[1]):
            raise AssertionError(f"shell git command is not the reviewed tag fetch: {tok[1]}")
        if tok[0] == "tmpl":
            static = "".join(part[1] for part in tok[1] if part[0] == "static")
            if _SHELL_GIT.search(static):
                raise AssertionError("shell git command is not the reviewed tag fetch")
    commands = _git_commands(tokens)
    assert commands, "run.mjs's git invocations must be visible to the safety check"
    fetches = []
    for index, elements in commands:
        piece = _static_piece(elements[0])
        subcommand = piece[1] if piece and piece[0] == "str" else None
        assert isinstance(subcommand, str), "git subcommand must be a literal"
        if subcommand == "fetch":
            fetches.append((index, elements))
        else:
            assert subcommand in _READ_ONLY_GIT, (
                f"git {subcommand} can change the checkout; only rev-parse and a tag fetch are allowed"
            )
    assert fetches, "the tag verification fetch must be asserted, not ignored"
    for _index, elements in fetches:
        options = []
        positionals = []
        for element in elements[1:]:
            piece = _static_piece(element)
            assert piece is not None, "git fetch arguments must be literals or one tag refspec"
            if piece[0] == "str" and isinstance(piece[1], str) and piece[1].startswith("-"):
                options.append(piece[1])
            else:
                positionals.append(piece)
        assert set(options) <= _ALLOWED_FETCH_OPTIONS, options
        assert positionals, "git fetch without a refspec updates remote branches"
        repository, *refspecs = positionals
        assert repository == ("str", "origin"), repository
        assert refspecs, "git fetch origin without a refspec updates remote branches"
        assert all(_same_tag_refspec(spec) for spec in refspecs), refspecs

    functions = {item["name"]: item for item in _functions(tokens)}
    verifier = functions.get("verifyTagMatchesCommit")
    assert verifier, "tag fetch must live in verifyTagMatchesCommit"
    for index, _elements in fetches:
        assert verifier["start"] < index < verifier["end"], (
            "a fetch outside tag verification is not allowed"
        )
    assert len(verifier["params"]) >= 2
    expected_name = _param_name(verifier["params"][1])
    assert expected_name
    peel_names = [name for _at, name, rhs in _assignments(verifier["body"]) if _rhs_peels_tag(rhs)]
    assert peel_names, (
        "the fetched tag must be peeled with git rev-parse --verify refs/tags/<tag>^{commit}"
    )
    compared = _names_compared_unequal_to(verifier["body"], expected_name)
    assert compared, "tag verification must throw when the peeled tag is not the expected commit"
    # Each of the two asserts above has to be able to fail on its own. This link
    # runs only when both found something, so a missing peel does not also fail
    # the comparison, and a missing comparison does not also fail the peel.
    if peel_names and compared:
        assert set(peel_names) & compared, (
            "the inequality must compare the peeled tag to the expected commit"
        )

    run = functions.get("run")
    assert run, "run() must call the tag verification with the planned commit"
    calls = _calls(run["body"], {"verifyTagMatchesCommit"})
    assert calls, "run() must call verifyTagMatchesCommit"
    for _index, args in calls:
        assert len(args) >= 2 and _unwrap(args[1]) == [("ident", "head")], (
            "tag verification must compare against the planned head, not the fetched tag"
        )
    head = [
        rhs for at, name, rhs in _assignments(run["body"]) if name == "head" and at < calls[0][0]
    ]
    assert any(
        [(tok[0], tok[1]) for tok in _unwrap(rhs)]
        == [("ident", "plan"), ("punct", "."), ("ident", "commit")]
        for rhs in head
    ), "head must be plan.commit, the commit planVersion already checked"
    planned = _calls(run["body"], _imported_names(tokens, "planVersion", "./plan.mjs"))
    assert planned, "planVersion must still run in the publish path"
    assert planned[0][0] < calls[0][0], "planVersion must accept HEAD before the tag fetch is used"


# Minimal copies of the pinned scripts. CI does not set LIVINGPROOF_NETWORK, so
# these fixtures are what actually runs the guard checks.
_PLAN_FIXTURE = """\
export async function planVersion(cwd, expected) {
  const commit = git('rev-parse', 'HEAD').trim();
  if (!shaPattern.test(expected ?? '') || commit !== expected) {
    throw new Error('Checked-out HEAD must match the triggering commit');
  }
  return { commit };
}
"""

_RUN_FIXTURE = """\
import { planVersion } from './plan.mjs';

export async function verifyTagMatchesCommit(gitTag, expectedCommit) {
  execFileSync('git', ['fetch', 'origin', `refs/tags/${gitTag}:refs/tags/${gitTag}`, '--force'], {});
  const taggedCommit = execFileSync('git', ['rev-parse', '--verify', `refs/tags/${gitTag}^{commit}`], {}).trim();
  if (taggedCommit !== expectedCommit) {
    throw new Error(`Tag ${gitTag} resolves to ${taggedCommit}, expected ${expectedCommit}`);
  }
}

export async function run() {
  const candidate = false;
  const expected = candidate ? process.env.RELEASE_EXPECTED_COMMIT : process.env.GITHUB_SHA;
  const plan = await planVersion(cwd, expected, { candidate });
  const head = plan.commit;
  verifyTagMatchesCommit(result.nextRelease.gitTag, head, { cwd, fetchTag: true });
}
"""

_SHA_LINE = (
    "const expected = candidate ? process.env.RELEASE_EXPECTED_COMMIT : process.env.GITHUB_SHA;"
)
_BOUND_SHA_LINE = """\
const triggering = process.env.GITHUB_SHA;
  const expected = candidate ? process.env.RELEASE_EXPECTED_COMMIT : triggering;"""
_UNBOUND_SHA_LINE = """\
const triggering = process.env.GITHUB_SHA;
  const expected = candidate ? process.env.RELEASE_EXPECTED_COMMIT : process.env.RELEASE_EXPECTED_COMMIT;"""


def _run_with_expected_line(line: str) -> str:
    return _RUN_FIXTURE.replace(_SHA_LINE, line)


def test_release_script_fixtures_keep_head_guard_sha_and_tag_fetch() -> None:
    _assert_plan_refuses_head_mismatch(_PLAN_FIXTURE)
    _assert_runner_passes_github_sha_to_plan(_RUN_FIXTURE)
    _assert_fetch_cannot_change_released_commit(_RUN_FIXTURE)


def test_github_sha_variable_passes_when_it_is_the_expected_commit() -> None:
    """An alias counts only because planVersion receives it as the expected commit."""
    _assert_runner_passes_github_sha_to_plan(_run_with_expected_line(_BOUND_SHA_LINE))


def test_github_sha_variable_is_refused_when_it_is_not_the_expected_commit() -> None:
    with pytest.raises(AssertionError):
        _assert_runner_passes_github_sha_to_plan(_run_with_expected_line(_UNBOUND_SHA_LINE))


@pytest.mark.parametrize(
    "plan",
    [
        pytest.param(
            _PLAN_FIXTURE.replace("commit !== expected", "true"),
            id="no-head-comparison",
        ),
        pytest.param(
            _PLAN_FIXTURE.replace(
                "throw new Error('Checked-out HEAD must match the triggering commit');",
                "return;",
            ),
            id="no-head-mismatch-throw",
        ),
        pytest.param(
            _PLAN_FIXTURE.replace(
                "const commit = git('rev-parse', 'HEAD').trim();",
                "const commit = expected;",
            ),
            id="commit-copied-from-expected",
        ),
    ],
)
def test_plan_fixture_missing_the_head_guard_is_refused(plan: str) -> None:
    with pytest.raises(AssertionError):
        _assert_plan_refuses_head_mismatch(plan)


@pytest.mark.parametrize(
    "runner",
    [
        pytest.param(
            _run_with_expected_line(
                "const expected = candidate ? process.env.RELEASE_EXPECTED_COMMIT "
                ": process.env.RELEASE_EXPECTED_COMMIT;"
            ),
            id="non-candidate-is-not-github-sha",
        ),
        pytest.param(
            _run_with_expected_line(
                "const expected = candidate ? process.env.GITHUB_SHA "
                ": process.env.RELEASE_EXPECTED_COMMIT;"
            ),
            id="github-sha-on-the-candidate-arm",
        ),
    ],
)
def test_run_fixture_missing_the_github_sha_binding_is_refused(runner: str) -> None:
    with pytest.raises(AssertionError):
        _assert_runner_passes_github_sha_to_plan(runner)


def test_run_fixture_missing_the_tag_fetch_is_refused() -> None:
    runner = _RUN_FIXTURE.replace(
        "execFileSync('git', ['fetch', 'origin', `refs/tags/${gitTag}:refs/tags/${gitTag}`, '--force'], {});\n  ",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_fetch_cannot_change_released_commit(runner)


def test_branch_fetch_fixture_is_refused() -> None:
    runner = _RUN_FIXTURE.replace(
        "`refs/tags/${gitTag}:refs/tags/${gitTag}`",
        "`refs/heads/main:refs/heads/main`",
    )
    with pytest.raises(AssertionError):
        _assert_fetch_cannot_change_released_commit(runner)


def test_run_fixture_without_the_tag_peel_comparison_is_refused() -> None:
    runner = _RUN_FIXTURE.replace("if (taggedCommit !== expectedCommit)", "if (false)")
    with pytest.raises(AssertionError):
        _assert_fetch_cannot_change_released_commit(runner)


def test_run_fixture_without_the_tag_peel_is_refused() -> None:
    runner = _RUN_FIXTURE.replace(
        "const taggedCommit = execFileSync('git', ['rev-parse', '--verify', "
        "`refs/tags/${gitTag}^{commit}`], {}).trim();\n  ",
        "",
    )
    with pytest.raises(AssertionError):
        _assert_fetch_cannot_change_released_commit(runner)


def test_run_fixture_with_git_checkout_is_refused() -> None:
    runner = _RUN_FIXTURE.replace(
        "execFileSync('git', ['fetch', 'origin', `refs/tags/${gitTag}:refs/tags/${gitTag}`, '--force'], {});\n  ",
        "execFileSync('git', ['fetch', 'origin', `refs/tags/${gitTag}:refs/tags/${gitTag}`, '--force'], {});\n"
        "  execFileSync('git', ['checkout', gitTag], {});\n  ",
    )
    with pytest.raises(AssertionError):
        _assert_fetch_cannot_change_released_commit(runner)


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_pinned_shared_workflow_releases_exactly_github_sha() -> None:
    """With head_sha == github.sha required by the gate, the shared workflow tags that commit.

    Checkout defaults to GITHUB_SHA. plan.mjs refuses a different HEAD, and run.mjs
    passes GITHUB_SHA to planVersion on a non-candidate run. The runner's git fetch
    only updates that same tag ref, then refuses unless the tag peels to the planned
    commit. A guidelines bump that changes any of these must be reviewed with this gate.
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
    plan = _raw(".github/actions/semantic-release/plan.mjs")
    runner = _raw(".github/actions/semantic-release/run.mjs")
    _assert_plan_refuses_head_mismatch(plan)
    _assert_runner_passes_github_sha_to_plan(runner)
    _assert_fetch_cannot_change_released_commit(runner)
