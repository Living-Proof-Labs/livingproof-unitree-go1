# SPDX-License-Identifier: Apache-2.0
"""Check built local targets/fragments, brand, and build identity."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2] / "artifacts/docs/html"


class VisibleHTMLParser(HTMLParser):
    """Ignore non-rendered HTML; CSS visibility is checked in the browser tests."""

    VOID_TAGS = (
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    )

    def __init__(self):
        super().__init__()
        self.hidden_elements = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        hidden = (
            tag in ("script", "style", "template")
            or "hidden" in attrs
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if tag not in self.VOID_TAGS:
            self.hidden_elements.append((tag, hidden))

    def handle_endtag(self, tag):
        for index in range(len(self.hidden_elements) - 1, -1, -1):
            if self.hidden_elements[index][0] == tag:
                del self.hidden_elements[index:]
                break


def commit_pattern(commit):
    """Match this build's full or abbreviated identity, not arbitrary hex words."""
    prefixes = "|".join(re.escape(commit[:length]) for length in range(7, 41))
    return re.compile(r"\b(?:" + prefixes + r")\b", re.IGNORECASE)


class Page(VisibleHTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = set()
        self.links = []
        self.in_source_repo = 0
        self.in_footer = 0
        self.in_link = False
        self.link_attrs = {}
        self.link_text = []
        self.source_repos = []
        self.source_links = []
        self.docs_links = []
        self.footer_text = []
        self.visible_text = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag not in self.VOID_TAGS and (
            "md-source__repository" in classes or self.in_source_repo
        ):
            self.in_source_repo += 1
        if tag not in self.VOID_TAGS and (tag == "footer" or self.in_footer):
            self.in_footer += 1
        if tag == "a" and "md-source" in classes:
            self.source_links.append(attrs.get("href", ""))
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag in ("a", "link") and "href" in attrs:
            self.links.append(attrs["href"])
        if tag == "a":
            self.in_link = True
            self.link_attrs = attrs
            self.link_text = []
        if tag in ("script", "img") and "src" in attrs:
            self.links.append(attrs["src"])

    def handle_endtag(self, tag):
        if tag in self.VOID_TAGS:
            return
        super().handle_endtag(tag)
        if self.in_source_repo:
            self.in_source_repo -= 1
        if self.in_footer:
            self.in_footer -= 1
        if tag == "a" and self.in_link:
            text = "".join(self.link_text).strip()
            if text.startswith("Docs: development"):
                self.docs_links.append(
                    (text, self.link_attrs.get("href", ""), self.link_attrs.get("title", ""))
                )
            self.in_link = False

    def handle_data(self, data):
        if any(hidden for _, hidden in self.hidden_elements):
            return
        self.visible_text.append(data)
        if self.in_source_repo:
            self.source_repos.append(data)
        if self.in_footer:
            self.footer_text.append(data)
        if self.in_link:
            self.link_text.append(data)


def check():
    pages = {}
    for path in ROOT.rglob("*.html"):
        text = path.read_text()
        assert "assets/brand.css" in text, path
        assert 'data-md-color-primary="indigo"' not in text, path
        pages[path.resolve()] = Page(text)
    assert len(pages) >= 7
    record = json.loads((ROOT / "build.json").read_text())
    commit = record["source_commit"]
    sha_pattern = commit_pattern(commit)
    checked = 0
    for path, page in pages.items():
        for link in page.links:
            url = urlsplit(link)
            if url.scheme or url.netloc or url.path.startswith("/"):
                continue
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            if target.is_dir():
                target /= "index.html"
            assert target.exists(), (path, link)
            if url.fragment and target in pages:
                assert unquote(url.fragment) in pages[target].ids, (path, link)
            checked += 1
        repo_text = "".join(page.source_repos).strip()
        assert "Source · development" in repo_text, path
        assert not sha_pattern.search(repo_text), (path, repo_text)
        assert any(commit in href for href in page.source_links), path
        footer_text = " ".join(page.footer_text)
        assert not sha_pattern.search(footer_text), (path, footer_text)
        for link_text, href, title in page.docs_links:
            assert link_text == "Docs: development", (path, link_text)
            assert not sha_pattern.search(link_text), (path, link_text)
            assert commit in href, (path, href)
            assert commit in title, (path, title)
        if path.name != "version.html":
            visible = " ".join(page.visible_text)
            assert not sha_pattern.search(visible), (path, visible)
    assert commit in (ROOT / "version.html").read_text()
    assert (ROOT / "search.json").is_file()
    print(f"PASS: {len(pages)} branded pages, {checked} local targets/fragments, source identity")


if __name__ == "__main__":
    check()
