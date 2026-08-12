from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from knowledge_db import sha256_file  # noqa: E402
from record_word_render_review import record_review  # noqa: E402


class WordRenderReviewTests(unittest.TestCase):
    def test_pass_requires_all_pages_and_binds_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docx = root / "candidate.docx"
            render_dir = root / "rendered"
            render_dir.mkdir()
            docx.write_bytes(b"synthetic-docx-for-hash-binding")
            (render_dir / "page-1.png").write_bytes(b"page-one")
            (render_dir / "page-2.png").write_bytes(b"page-two")

            with self.assertRaisesRegex(RuntimeError, "checked-all-pages"):
                record_review(
                    docx,
                    render_dir,
                    reviewed_by="reviewer",
                    result="pass",
                    checked_all_pages=False,
                )

            review = record_review(
                docx,
                render_dir,
                reviewed_by="reviewer",
                result="pass",
                checked_all_pages=True,
            )
            self.assertEqual(review["docx_sha256"], sha256_file(docx))
            self.assertEqual(review["docx"], "candidate.docx")
            self.assertEqual(review["render_dir"], "rendered")
            self.assertEqual(review["page_count"], 2)
            self.assertTrue(review["checked_all_pages"])
            self.assertTrue(all(page["sha256"] for page in review["pages"]))
            self.assertEqual(
                [page["path"] for page in review["pages"]],
                ["rendered/page-1.png", "rendered/page-2.png"],
            )


if __name__ == "__main__":
    unittest.main()
