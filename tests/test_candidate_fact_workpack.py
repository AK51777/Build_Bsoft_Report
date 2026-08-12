from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_candidate_fact_workpack import build_workpack  # noqa: E402
from extract_clean_document_blocks import build_payload  # noqa: E402
from ingest_clean_documents_sqlite import ingest_payload  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402


class CandidateFactWorkpackTests(unittest.TestCase):
    def test_workpack_exposes_evidence_without_asserting_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project", project_code="TEST-FACT-WORKPACK"
            )
            source = root / "baseline.md"
            source.write_text("# Baseline\n\nThe hospital has a system.\n", encoding="utf-8")
            ingest_payload(
                Path(initialized["database"]),
                build_payload(source, "TEST-FACT-WORKPACK", "Baseline", False),
            )

            workpack = build_workpack(
                Path(initialized["database"]), "TEST-FACT-WORKPACK"
            )

            self.assertGreater(workpack["counts"]["source_blocks"], 0)
            self.assertEqual(workpack["candidate_fact_schema"]["fact_value"], "")
            self.assertEqual(
                workpack["candidate_fact_schema"]["fact_status"],
                "pending_confirmation",
            )
            self.assertIn("source_id", workpack["source_blocks"][0])
            self.assertIn("source_location", workpack["source_blocks"][0])


if __name__ == "__main__":
    unittest.main()
