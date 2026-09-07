"""Compare consumed SQL rows with the reviewed embedded payload, including v1 packs."""

from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from pathlib import Path

from import_policy_catalog_sqlite import import_catalog
from import_standard_knowledge_pack import import_pack
from ingest_document_standards import ingest as ingest_standards
from ingest_policies import ingest as ingest_policies
from knowledge_db import connect_readonly, dump_json, sha256_text, stable_id


# These are the business tables consumed by queries and project synchronization.
# Audit/FTS implementation tables are not substitutes for their source rows.
TABLES = (
    "source_document", "corpus_document", "corpus_block", "corpus_tag",
    "corpus_block_tag", "product_capability", "capability_solution_block_relation",
    "policy_catalog", "policy_catalog_entry", "policy_document", "policy_clause",
    "document_standard",
)
IMPORT_TIMESTAMPS = {
    "source_document": {"imported_at"},
    "corpus_document": {"created_at", "updated_at"},
    "corpus_block": {"created_at", "updated_at"},
    "policy_catalog": {"imported_at"},
}


def _fingerprints(database: Path, payloads: dict) -> dict:
    policies = {
        p.get("policy_id") or stable_id("POLICY", p.get("title"), p.get("document_no"), p.get("issuer")): p
        for p in payloads.get("verified_policies", {}).get("policies", [])
    }
    result = {}
    with connect_readonly(database) as conn:
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("local knowledge SQL foreign key mismatch")
        for table in TABLES:
            rows = []
            for raw in conn.execute(f'SELECT * FROM "{table}"'):
                row = dict(raw)
                ignored = set(IMPORT_TIMESTAMPS.get(table, ()))
                if table == "policy_document":
                    policy = policies.get(row["policy_id"], {})
                    # Only legacy, generated ingestion times are non-deterministic;
                    # explicit evidence/verification dates must still match.
                    if not policy.get("retrieved_at"):
                        ignored.add("retrieved_at")
                        if "last_verified_at" not in policy:
                            ignored.add("last_verified_at")
                for key in ignored:
                    row.pop(key, None)
                for key, value in row.items():
                    if key.endswith("_json") and isinstance(value, str):
                        row[key] = json.loads(value)
                rows.append(dump_json(row))
            result[table] = (len(rows), sha256_text(dump_json(sorted(rows))))
    return result


@lru_cache(maxsize=4)
def _expected_fingerprints(payload_json: str) -> dict:
    """Cache only expectations from immutable payload bytes, never actual SQL state."""
    payloads = json.loads(payload_json)
    with tempfile.TemporaryDirectory(prefix="medical-kb-integrity-") as folder:
        database = Path(folder) / "expected.sqlite"
        for name, importer in (
            ("standard_pack", import_pack), ("policy_catalog", import_catalog),
            ("verified_policies", ingest_policies), ("document_standards", ingest_standards),
        ):
            if name in payloads:
                importer(database, payloads[name])
        return _fingerprints(database, payloads)


def validate_sql_payload(database: Path, payloads: dict) -> None:
    unknown = set(payloads) - {"standard_pack", "policy_catalog", "verified_policies", "document_standards"}
    if unknown:
        raise ValueError("unsupported local knowledge payload names")
    expected = _expected_fingerprints(dump_json(payloads))
    actual = _fingerprints(database, payloads)
    mismatches = [table for table in TABLES if actual[table] != expected[table]]
    if mismatches:
        raise ValueError("local knowledge SQL content mismatch: " + ", ".join(mismatches))
