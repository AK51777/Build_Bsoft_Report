from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from extract_clean_document_blocks import build_payload  # noqa: E402
from ingest_clean_documents_sqlite import ingest_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402


class CleanCorpusSqliteTests(unittest.TestCase):
    def test_ingests_clean_blocks_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbench = root / "project"
            initialized = initialize_project(workbench, project_code="TEST-CORPUS-001")
            database = Path(initialized["database"])
            source = root / "sample.md"
            source.write_text(
                "# Project overview\n\nHospital baseline text.\n\n## Scope\n\nBuild EMR.\n",
                encoding="utf-8",
            )
            payload = build_payload(source, "TEST-CORPUS-001", "Sample", False)

            first = ingest_payload(database, payload)
            second = ingest_payload(database, payload)

            self.assertTrue(first["source_created"])
            self.assertTrue(first["document_created"])
            self.assertEqual(first["blocks_created"], len(payload["blocks"]))
            self.assertFalse(second["source_created"])
            self.assertFalse(second["document_created"])
            self.assertEqual(second["blocks_created"], 0)
            self.assertEqual(second["blocks_updated"], len(payload["blocks"]))

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM source_document").fetchone()[0], 1
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM corpus_document").fetchone()[0], 1
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM corpus_block").fetchone()[0],
                    len(payload["blocks"]),
                )
                document_status = conn.execute(
                    "SELECT review_status FROM corpus_document"
                ).fetchone()[0]
                self.assertEqual(document_status, "pending")
            finally:
                conn.close()

    def test_reimport_preserves_manual_review_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project", project_code="TEST-CORPUS-REVIEW"
            )
            database = Path(initialized["database"])
            source = root / "sample.txt"
            source.write_text("Reviewed reusable content.\n", encoding="utf-8")
            payload = build_payload(source, "TEST-CORPUS-REVIEW", "Reviewed", False)
            ingest_payload(database, payload)

            conn = sqlite3.connect(database)
            try:
                conn.execute(
                    "UPDATE corpus_document SET review_status='approved'"
                )
                conn.execute(
                    "UPDATE corpus_block SET review_status='prohibited'"
                )
                conn.commit()
            finally:
                conn.close()

            ingest_payload(database, payload, review_status="pending")

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT review_status FROM corpus_document"
                    ).fetchone()[0],
                    "approved",
                )
                self.assertEqual(
                    conn.execute("SELECT review_status FROM corpus_block").fetchone()[0],
                    "prohibited",
                )
            finally:
                conn.close()

    def test_rejects_uninitialized_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(root / "project", project_code="TEST-OTHER")
            source = root / "sample.txt"
            source.write_text("Text.\n", encoding="utf-8")
            payload = build_payload(source, "TEST-MISSING", "Missing", False)

            with self.assertRaises(RuntimeError):
                ingest_payload(Path(initialized["database"]), payload)
