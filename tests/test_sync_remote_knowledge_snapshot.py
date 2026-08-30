from __future__ import annotations

import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from sync_remote_knowledge_snapshot import RemotePageConnection  # noqa: E402
from sync_postgres_knowledge_snapshot import build_pack_snapshot  # noqa: E402


class FakeRemoteClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call_tool(self, name: str, arguments: dict) -> dict:
        self.calls.append({"name": name, "arguments": arguments})
        offset = arguments["offset"]
        rows = (
            [{"block_id": "B-1", "clean_text": "one"}]
            if offset == 0
            else [{"block_id": "B-2", "clean_text": "two"}]
        )
        return {
            "isError": False,
            "structuredContent": {
                "rows": rows,
                "next_offset": 1 if offset == 0 else None,
            },
        }


class RemoteSnapshotAdapterTests(unittest.TestCase):
    def test_fetch_all_follows_next_offset_until_last_page(self) -> None:
        client = FakeRemoteClient()
        connection = RemotePageConnection(client, page_size=25)

        rows = connection.fetch_all("corpus-block", "PACK-1")

        self.assertEqual([row["block_id"] for row in rows], ["B-1", "B-2"])
        self.assertEqual(
            [call["arguments"]["offset"] for call in client.calls],
            [0, 1],
        )
        self.assertTrue(all(call["name"] == "knowledge_page" for call in client.calls))

    def test_capability_relation_query_maps_to_package_scoped_page(self) -> None:
        client = FakeRemoteClient()
        connection = RemotePageConnection(client, page_size=10)
        connection.package_id = "PACK-REL"

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM remote_mcp.runtime_capability_block "
                "WHERE capability_id = ANY(%s)",
                (["CAP-1"],),
            )

        first_call = client.calls[0]
        self.assertEqual(first_call["arguments"]["entity"], "capability-block")
        self.assertEqual(first_call["arguments"]["asset_id"], "PACK-REL")

    def test_non_advancing_page_is_rejected(self) -> None:
        class StalledClient:
            def call_tool(self, name: str, arguments: dict) -> dict:
                return {
                    "isError": False,
                    "structuredContent": {"rows": [], "next_offset": arguments["offset"]},
                }

        connection = RemotePageConnection(StalledClient(), page_size=10)
        with self.assertRaisesRegex(RuntimeError, "did not advance"):
            connection.fetch_all("corpus-block", "PACK-1")

    def test_existing_snapshot_builder_accepts_remote_paged_rows(self) -> None:
        rows_by_entity = {
            "package-source": [
                {
                    "package_id": "PACK-1",
                    "source_role": "reference_report",
                    "file_name": "reviewed.docx",
                    "source_sha256": "source-hash",
                }
            ],
            "corpus-document": [
                {
                    "corpus_document_id": "DOC-1",
                    "source_corpus_type": "reference_report",
                    "document_type": "feasibility_report",
                    "project_type": "smart_hospital",
                    "quality_level": "reviewed",
                    "version": "1.0",
                    "metadata": {},
                }
            ],
            "corpus-block": [
                {
                    "block_id": "BLOCK-1",
                    "source_location": "第1章",
                    "heading_path": ["第1章"],
                    "section_role": "project_background",
                    "module_code": "",
                    "clean_text": "已审核参考内容。",
                    "reuse_class": "structure_only",
                    "quality_level": "reviewed",
                    "prerequisites": [],
                    "variable_slots": [],
                    "forbidden_terms": [],
                    "length_band": "short",
                    "text_hash": "block-hash",
                    "block_index": 1,
                }
            ],
            "capability": [],
        }

        class MappedClient:
            def call_tool(self, name: str, arguments: dict) -> dict:
                return {
                    "isError": False,
                    "structuredContent": {
                        "rows": rows_by_entity[arguments["entity"]],
                        "next_offset": None,
                    },
                }

        connection = RemotePageConnection(MappedClient(), page_size=25)
        package = {
            "package_id": "PACK-1",
            "schema_version": "1.0",
            "title": "脱敏参考知识",
            "permission_scope": "internal_readonly",
            "content_hash": "package-hash",
            "review_summary": {},
        }

        payload = build_pack_snapshot(connection, "remote_mcp", package)

        self.assertEqual(payload["package_id"], "PACK-1")
        self.assertEqual(payload["corpus"]["blocks"][0]["block_id"], "BLOCK-1")
        self.assertEqual(payload["server_content_hash"], "package-hash")


if __name__ == "__main__":
    unittest.main()
