from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_evidence_bound_initial_drafts import build_initial_drafts  # noqa: E402
from build_reference_corpus_review_pack import build_review_pack  # noqa: E402
from build_reference_corpus_workpack import build_workpack  # noqa: E402
from build_reference_knowledge_pack import build_knowledge_pack  # noqa: E402
from build_section_composition_plan import build_composition_plan  # noqa: E402
from export_section_task_packages import export_packages  # noqa: E402
from extract_clean_document_blocks import build_payload  # noqa: E402
from import_standard_knowledge_pack import import_pack  # noqa: E402
from init_project_workbench import initialize_project  # noqa: E402


TAXONOMY = json.loads(
    (
        SKILL_ROOT
        / "assets"
        / "knowledge-base"
        / "seeds"
        / "reference_corpus_taxonomy_v1.json"
    ).read_text(encoding="utf-8")
)


class HospitalNarrativeCorpusChainTests(unittest.TestCase):
    def _reviewed_pack(self, root: Path) -> tuple[dict, list[str]]:
        source = root / "reference.md"
        source.write_text(
            "# 项目总体设计\n\n"
            "## 项目建设目标、规模与内容\n\n"
            "### 整体目标\n\n"
            "参考医院围绕电子病历五级和互联互通四级甲等目标，形成分层联动诊疗闭环，统筹临床、服务、管理和数据能力。\n\n"
            "### 技术目标\n\n"
            "参考医院采用统一标准、统一身份、统一数据和统一运维机制，支撑跨系统业务协同和全过程留痕。\n",
            encoding="utf-8",
        )
        clean = build_payload(source, "REFERENCE-SOURCE", "Reference", False)
        workpack = build_workpack(
            clean,
            TAXONOMY,
            source_corpus_type="reference_feasibility",
            project_type="smart_hospital",
            variables=[("hospital_name", "参考医院")],
        )
        workpack["blocks"][0]["content_slot"] = "overall_objective_scope"
        workpack_hash = "a" * 64
        review = build_review_pack(
            workpack,
            workpack_sha256=workpack_hash,
            semantic_sections=["overall_objective_scope"],
        )
        review["confirmation"] = {
            "status": "confirmed",
            "confirmed_by": "synthetic-reviewer",
            "confirmed_at": "2026-08-18T00:00:00+08:00",
            "decision_note": "Synthetic regression fixture only.",
        }
        for decision in review["decisions"]:
            decision["decision_status"] = "approved"
        pack = build_knowledge_pack(
            workpack,
            review,
            workpack_sha256=workpack_hash,
            version_label="synthetic-v1",
        )
        return pack, [block["block_id"] for block in pack["corpus"]["blocks"]]

    def test_pending_review_cannot_build_publishable_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "reference.md"
            source.write_text(
                "# 项目总体设计\n\n## 项目建设目标、规模与内容\n\n"
                "### 整体目标\n\n参考医院提出电子病历五级建设目标，并统筹临床业务、数据治理和运营管理能力。\n",
                encoding="utf-8",
            )
            clean = build_payload(source, "REFERENCE-PENDING", "Reference", False)
            workpack = build_workpack(
                clean,
                TAXONOMY,
                source_corpus_type="reference_feasibility",
                project_type="smart_hospital",
                variables=[("hospital_name", "参考医院")],
            )
            review = build_review_pack(
                workpack,
                workpack_sha256="b" * 64,
                semantic_sections=["overall_objective_scope"],
            )
            with self.assertRaisesRegex(ValueError, "confirmation status"):
                build_knowledge_pack(
                    workpack,
                    review,
                    workpack_sha256="b" * 64,
                    version_label="blocked",
                )

    def test_prohibited_candidate_text_is_not_carried_into_runtime_pack(self) -> None:
        approved_id = "REFBLOCK-approved"
        prohibited_id = "REFBLOCK-prohibited-list"
        workpack = {
            "source": {
                "document_id": "DOC-reference",
                "source_sha256": "c" * 64,
                "source_path": "敏感客户医院可研.docx",
                "project_type": "smart_hospital",
            },
            "blocks": [
                {
                    "block_id": approved_id,
                    "clean_text": "项目采用统一标准支撑业务协同。",
                    "semantic_section": "overall_objective_scope",
                    "content_slot": "technical_objective",
                    "content_type": "feasibility_narrative",
                    "reuse_class": "A",
                    "adaptation_mode": "direct",
                    "applicable_project_types": ["smart_hospital"],
                    "variable_slots": [],
                    "forbidden_terms": ["敏感客户医院"],
                },
                {
                    "block_id": prohibited_id,
                    "clean_text": "参考项目软硬件建设清单全文。",
                    "semantic_section": "overall_objective_scope",
                    "content_slot": "list",
                    "content_type": "project_specific",
                    "reuse_class": "D",
                    "adaptation_mode": "prohibited",
                    "applicable_project_types": ["smart_hospital"],
                    "variable_slots": [],
                    "forbidden_terms": [],
                },
            ],
        }
        decisions = {
            "workpack_sha256": "d" * 64,
            "source_document_id": "DOC-reference",
            "confirmation": {
                "status": "confirmed",
                "confirmed_by": "reviewer",
                "confirmed_at": "2026-08-19T00:00:00+08:00",
            },
            "decisions": [
                {
                    **workpack["blocks"][0],
                    "decision_status": "approved",
                },
                {
                    **workpack["blocks"][1],
                    "decision_status": "prohibited",
                },
            ],
        }

        pack = build_knowledge_pack(
            workpack,
            decisions,
            workpack_sha256="d" * 64,
            version_label="privacy-gate",
        )

        self.assertEqual(
            [block["block_id"] for block in pack["corpus"]["blocks"]],
            [approved_id],
        )
        self.assertEqual(pack["review_summary"]["reviewed_block_count"], 2)
        self.assertEqual(pack["review_summary"]["prohibited_or_retired_count"], 1)
        self.assertNotIn(
            "参考项目软硬件建设清单全文",
            json.dumps(pack, ensure_ascii=False),
        )
        self.assertNotIn("敏感客户医院可研", json.dumps(pack, ensure_ascii=False))
        self.assertNotIn("敏感客户医院", json.dumps(pack, ensure_ascii=False))
        self.assertTrue(pack["corpus"]["blocks"][0]["forbidden_term_hashes"])

    def test_canonical_objective_and_summary_share_reviewed_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initialized = initialize_project(
                root / "project",
                project_code="HOSPITAL-NARRATIVE-001",
                official_name="测试医院信息化建设项目",
                owner_name="测试医院",
                project_type="smart_hospital",
                acceptance_targets=["电子病历五级", "互联互通四级甲等"],
            )
            database = Path(initialized["database"])
            pack, block_ids = self._reviewed_pack(root)
            imported = import_pack(database, pack)
            self.assertEqual(imported["package_kind"], "reference_corpus")
            self.assertEqual(imported["capabilities_imported"], 0)

            build_composition_plan(
                database,
                "HOSPITAL-NARRATIVE-001",
                chapter_codes=["1.1.2", "4.1.2"],
            )
            with closing(sqlite3.connect(database)) as connection:
                rows = connection.execute(
                    """
                    SELECT p.chapter_code,s.source_object_id,s.usage_mode
                    FROM section_composition_plan p
                    JOIN section_plan_source s ON s.plan_id=p.plan_id
                    WHERE p.chapter_code IN ('1.1.2','4.1.2') AND s.source_type='corpus'
                    ORDER BY p.chapter_code,s.source_object_id
                    """
                ).fetchall()
            by_chapter = {
                chapter: {object_id for code, object_id, _ in rows if code == chapter}
                for chapter in ("1.1.2", "4.1.2")
            }
            self.assertEqual(by_chapter["1.1.2"], set(block_ids))
            self.assertEqual(by_chapter["4.1.2"], set(block_ids))

            task_dir = root / "tasks"
            draft_dir = root / "drafts"
            export_packages(
                database,
                "HOSPITAL-NARRATIVE-001",
                task_dir,
                chapter_codes=["1.1.2", "4.1.2"],
            )
            build_initial_drafts(
                database,
                "HOSPITAL-NARRATIVE-001",
                task_dir,
                draft_dir,
                adopt=False,
                chapter_codes=["1.1.2", "4.1.2"],
            )
            canonical = next(draft_dir.glob("CH4.1.2-*.md")).read_text(
                encoding="utf-8"
            )
            summary = next(draft_dir.glob("CH1.1.2-*.md")).read_text(
                encoding="utf-8"
            )
            self.assertIn("分层联动诊疗闭环", canonical)
            self.assertIn("测试医院", canonical)
            self.assertNotIn("参考医院", canonical)
            self.assertIn("电子病历五级", summary)
            self.assertIn("项目建设期将结合立项批复", summary)
            self.assertNotRegex(summary, r"【(?:待补充|待确认|冲突|分析建议)[^】]*】")
            self.assertNotIn("事实台账", summary)
            self.assertNotIn("章节复核", summary)
            for block_id in block_ids:
                self.assertIn(block_id, canonical)
                self.assertIn(block_id, summary)
            self.assertNotIn("经核验的相关条款主要包括", canonical + summary)
            self.assertNotIn("上述内容来自", canonical + summary)


if __name__ == "__main__":
    unittest.main()
