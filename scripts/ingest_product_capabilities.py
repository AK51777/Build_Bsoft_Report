#!/usr/bin/env python3
"""Ingest reviewed product capabilities into the local SQLite knowledge base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, stable_id


def ingest_capabilities(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        raise ValueError("input must contain a capabilities array")
    created = 0
    updated = 0
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
        for index, capability in enumerate(capabilities, start=1):
            if not isinstance(capability, dict):
                raise ValueError(f"capability {index} must be an object")
            required = ("product_code", "product_name", "capability_name", "capability_description")
            missing = [field for field in required if not capability.get(field)]
            if missing:
                raise ValueError(
                    f"capability {index} is missing required fields: {', '.join(missing)}"
                )
            capability_id = capability.get("capability_id") or stable_id(
                "CAPABILITY", capability["product_code"], capability["capability_name"]
            )
            exists = conn.execute(
                "SELECT 1 FROM product_capability WHERE capability_id=?", (capability_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO product_capability (
                  capability_id, product_code, product_name, capability_name,
                  capability_description, prerequisites_json,
                  interface_dependencies_json, exclusions_json,
                  applicable_versions_json, standard_block_ids_json, review_status,
                  category,module_name,selection_rules_json,source_location,block_match_scope
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(capability_id) DO UPDATE SET
                  product_code=excluded.product_code,
                  product_name=excluded.product_name,
                  capability_name=excluded.capability_name,
                  capability_description=excluded.capability_description,
                  prerequisites_json=excluded.prerequisites_json,
                  interface_dependencies_json=excluded.interface_dependencies_json,
                  exclusions_json=excluded.exclusions_json,
                  applicable_versions_json=excluded.applicable_versions_json,
                  standard_block_ids_json=excluded.standard_block_ids_json,
                  category=excluded.category,module_name=excluded.module_name,
                  selection_rules_json=excluded.selection_rules_json,
                  source_location=excluded.source_location,
                  block_match_scope=excluded.block_match_scope,
                  review_status=CASE
                    WHEN product_capability.review_status='retired'
                    THEN product_capability.review_status ELSE excluded.review_status END
                """,
                (
                    capability_id,
                    capability["product_code"],
                    capability["product_name"],
                    capability["capability_name"],
                    capability["capability_description"],
                    dump_json(capability.get("prerequisites", [])),
                    dump_json(capability.get("interface_dependencies", [])),
                    dump_json(capability.get("exclusions", [])),
                    dump_json(capability.get("applicable_versions", [])),
                    dump_json(capability.get("standard_block_ids", [])),
                    capability.get("review_status", "pending"),
                    capability.get("category", ""),
                    capability.get("module_name", ""),
                    dump_json(capability.get("selection_rules", [])),
                    capability.get("source_location", ""),
                    capability.get("block_match_scope", ""),
                ),
            )
            created += int(exists is None)
            updated += int(exists is not None)
        conn.commit()
    return {
        "database": str(database.resolve()),
        "capabilities_created": created,
        "capabilities_updated": updated,
        "applied_migrations": applied_migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = ingest_capabilities(args.database, load_json(args.input))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
