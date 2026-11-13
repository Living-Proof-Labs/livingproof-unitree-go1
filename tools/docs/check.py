# SPDX-License-Identifier: Apache-2.0
"""Check built local targets/fragments, brand, and build identity."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2] / "artifacts/docs/html"


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag in ("a", "link") and "href" in attrs:
            self.links.append(attrs["href"])
        if tag in ("script", "img") and "src" in attrs:
            self.links.append(attrs["src"])


def check():
    pages = {}
    for path in ROOT.rglob("*.html"):
        text = path.read_text()
        assert "assets/brand.css" in text, path
        assert 'data-md-color-primary="indigo"' not in text, path
        pages[path.resolve()] = Page(text)
    assert len(pages) >= 7
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
    record = json.loads((ROOT / "build.json").read_text())
    assert record["source_commit"] in (ROOT / "version.html").read_text()
    assert (ROOT / "search.json").is_file()
    print(f"PASS: {len(pages)} branded pages, {checked} local targets/fragments, source identity")


if __name__ == "__main__":
    check()
