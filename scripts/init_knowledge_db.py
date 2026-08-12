#!/usr/bin/env python3
"""Initialize or migrate the local feasibility-report SQLite knowledge base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="SQLite database path")
    parser.add_argument("--migrations", type=Path, help="Optional migration directory")
    parser.add_argument("--output", type=Path, help="Write initialization summary JSON")
    args = parser.parse_args()

    with connect(args.database) as conn:
        applied = apply_migrations(conn, args.migrations)
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            )
        ]
        migration_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT version,file_hash,applied_at FROM kb_schema_migration ORDER BY version"
            )
        ]

    result = {
        "database": str(args.database.resolve()),
        "applied_now": applied,
        "migrations": migration_rows,
        "table_count": len(tables),
        "tables": tables,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
