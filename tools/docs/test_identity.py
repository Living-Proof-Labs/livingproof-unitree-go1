# SPDX-License-Identifier: Apache-2.0
"""Focused regressions for the static documentation identity checker."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("docs_identity", Path(__file__).with_name("check.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IdentityTests(unittest.TestCase):
    def test_visible_commit_identity_is_detected(self):
        commit = "a1b2c3d4e5f6789012345678901234567890abcdef"
        for length in (7, 12, 40):
            with self.subTest(length=length):
                page = module.Page(f"<footer>Docs: development · {commit[:length]}</footer>")
                self.assertRegex(" ".join(page.footer_text), module.commit_pattern(commit))

    def test_hidden_provenance_and_ordinary_words_are_allowed(self):
        commit = "a1b2c3d4e5f6789012345678901234567890abcdef"
        for wrapper in (
            "<span hidden><em>{sha}</em><br></span>",
            '<span style="display: none">{sha}</span>',
            '<span style="visibility: hidden">{sha}</span>',
            "<script>{sha}</script>",
            "<style>{sha}</style>",
            "<template>{sha}</template>",
        ):
            with self.subTest(wrapper=wrapper):
                href = f"https://github.com/example/repo/tree/{commit}"
                page = module.Page(
                    "<footer>defaced "
                    + wrapper.format(sha=commit)
                    + f'<a href="{href}" title="{commit}">Docs: development</a> after</footer>'
                )
                visible = " ".join(page.visible_text)
                self.assertIn("defaced", visible)
                self.assertIn("after", visible)
                self.assertNotRegex(visible, module.commit_pattern(commit))
                self.assertEqual(page.docs_links, [("Docs: development", href, commit)])
