# SPDX-License-Identifier: Apache-2.0
"""Render repository Markdown and Task information.

Adapted from collaboration-guidelines tools/manual/build.py at v7.10.9
(c5fa9d9794d0ebd289f51b247fe7872de0db9c27), Apache-2.0.
"""

import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/docs"
REPO = "https://github.com/Living-Proof-Labs/livingproof-unitree-go1"
BASELINE = "v7.10.9"
BASELINE_SHA = "c5fa9d9794d0ebd289f51b247fe7872de0db9c27"


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def build():
    commit = os.environ.get("SOURCE_COMMIT") or run("git", "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected an exact source commit")
    dirty_value = os.environ.get("SOURCE_DIRTY")
    if dirty_value is not None and dirty_value not in ("true", "false"):
        raise ValueError("SOURCE_DIRTY must be true or false")
    dirty = (
        dirty_value == "true"
        if dirty_value is not None
        else bool(run("git", "status", "--porcelain"))
    )
    source = OUT / "source"
    if source.exists():
        shutil.rmtree(source)
    source.mkdir(parents=True)
    mapping = {
        "README.md": "guide.md",
        "docs/index.md": "index.md",
        "docs/contribute.md": "contribute.md",
        "docs/verification.md": "verification.md",
        "LICENSE": "license.md",
    }
    record = {
        "source_commit": commit,
        "source_dirty": dirty,
        "guidelines_baseline": BASELINE,
        "guidelines_baseline_commit": BASELINE_SHA,
        "generator": "zensical==0.0.73",
        "workflow_run": (
            REPO + "/actions/runs/" + os.environ["GITHUB_RUN_ID"]
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
        "sources": {},
    }
    source_link = REPO + "/tree/" + commit
    for path, destination in mapping.items():
        text = (ROOT / path).read_text()
        if path == "LICENSE":
            text = "# License\n\n```text\n" + text + "\n```\n"
        record["sources"][path] = hashlib.sha256(text.encode()).hexdigest()

        def rewrite(match):
            target = match[1]
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
                return match[0]
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), url.path))
            if resolved in mapping:
                relative = posixpath.relpath(
                    mapping[resolved], posixpath.dirname(destination) or "."
                )
                return "](" + urlunsplit(("", "", relative, url.query, url.fragment)) + ")"
            return (
                "]("
                + REPO
                + "/blob/"
                + commit
                + "/"
                + resolved
                + (("#" + url.fragment) if url.fragment else "")
                + ")"
            )

        text = re.sub(r"\]\(([^)\s]+)\)", rewrite, text)
        target = source / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        version = posixpath.relpath("version.md", posixpath.dirname(destination) or ".")
        license_path = posixpath.relpath("license.md", posixpath.dirname(destination) or ".")
        text += (
            f"\n\n---\n\n[Documentation home](https://docs.livingprooflabs.com/) · "
            f"[Docs: development · {commit[:12]}]({source_link}) · "
            f"[Version and source]({version}) · [Help / report an issue]({REPO}/issues) · "
            f"[License]({license_path})\n"
        )
        target.write_text(text)
    task_data = json.loads(run("task", "--list", "--json"))
    tasks = [
        "# Contributor tasks",
        "",
        "Generated from this repository’s real Taskfile.",
        "Host tasks require the prerequisites stated in their guide. Container tasks",
        "are host entry points; do not call their Docker wrappers inside the container.",
        "",
    ]
    for task in task_data["tasks"]:
        tasks += [
            f"## {task['name']}",
            "",
            "```text",
            run("task", "--summary", task["name"]),
            "```",
            "",
        ]
    (source / "tasks.md").write_text("\n".join(tasks))
    record["sources"]["Taskfile.yml"] = hashlib.sha256(
        (ROOT / "Taskfile.yml").read_bytes()
    ).hexdigest()
    from urllib.request import urlopen

    brand = json.loads((ROOT / "docs-brand.lock.json").read_text())
    url = (
        f"https://raw.githubusercontent.com/{brand['repository']}/{brand['commit']}/{brand['path']}"
    )
    data = urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != brand["sha256"]:
        raise ValueError("Brand checksum mismatch")
    (source / "assets").mkdir()
    (source / "assets/brand.css").write_bytes(data)
    record["brand"] = brand
    (source / "build.json").write_text(json.dumps(record, indent=2) + "\n")
    (source / "version.md").write_text(
        "# Version and source\n\nThis is development documentation, not a package release. "
        "Historical rendered manuals are not maintained; use the matching "
        f"[published source tag]({REPO}/tags) for an adopted release.\n\n"
        f"[Docs: development · {commit[:12]}]({source_link}); dirty checkout: **{str(dirty).lower()}**. "
        "A dirty preview has changes beyond that commit; source hashes below identify its inputs.\n\n"
        f"Recipe baseline: [{BASELINE}](https://github.com/Living-Proof-Labs/collaboration-guidelines/tree/{BASELINE}), resolved to `{BASELINE_SHA}`. "
        "This is a released guidance dependency, not the version of this development manual. "
        "[Published guidelines releases](https://github.com/Living-Proof-Labs/collaboration-guidelines/releases) are separate.\n\n"
        "```json\n" + json.dumps(record, indent=2) + "\n```\n\n[Build record](build.json)\n"
    )
    config = yaml.safe_load((ROOT / "tools/docs/mkdocs.yml").read_text())
    config.update(
        docs_dir="source",
        site_dir="html",
        repo_url=source_link,
        repo_name=f"Source · dev {commit[:12]}",
    )
    (OUT / "mkdocs.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    if (OUT / "html").exists():
        shutil.rmtree(OUT / "html")
    subprocess.run(
        [sys.executable, "-m", "zensical", "build", "--clean", "-f", str(OUT / "mkdocs.yml")],
        cwd=ROOT,
        check=True,
    )

    subprocess.run([sys.executable, str(ROOT / "tools/docs/check.py")], check=True)


if __name__ == "__main__":
    build()
