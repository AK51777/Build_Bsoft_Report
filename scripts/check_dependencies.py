#!/usr/bin/env python3
"""Check local core, authoring, and Word-delivery dependencies without network access."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from knowledge_db import apply_migrations, connect


def module_status(name: str, required_for: str) -> dict:
    available = importlib.util.find_spec(name) is not None
    return {
        "name": name,
        "available": available,
        "required_for": required_for,
    }


def executable_status(name: str, candidates: list[Path], required_for: str) -> dict:
    discovered = shutil.which(name)
    path = Path(discovered) if discovered else next(
        (candidate for candidate in candidates if candidate.is_file()), None
    )
    return {
        "name": name,
        "available": path is not None,
        "path": str(path) if path else "",
        "required_for": required_for,
    }


def check_dependencies(knowledge_mode: str = "disabled") -> dict:
    python_ok = sys.version_info >= (3, 10)
    with sqlite3.connect(":memory:") as conn:
        sqlite_version = conn.execute("SELECT sqlite_version()").fetchone()[0]
        try:
            conn.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(content)")
            fts5 = True
        except sqlite3.OperationalError:
            fts5 = False
    migration_error = ""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            with connect(Path(tmp) / "dependency-check.sqlite") as conn:
                applied = apply_migrations(conn)
    except Exception as exc:  # pragma: no cover - surfaced in result
        migration_error = str(exc)
        applied = []

    modules = [
        module_status("docx", "Word candidate generation"),
        module_status("openpyxl", "Regression fixtures and advanced XLSX handling"),
        module_status("psycopg", "Optional PostgreSQL adapter"),
    ]
    executables = [
        executable_status("node", [], "Excel confirmation-pack helpers"),
        executable_status(
            "soffice",
            [
                Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
                Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
            ],
            "Preferred DOCX render verification",
        ),
        executable_status(
            "winword",
            [
                Path(r"C:\Program Files\Microsoft Office\Office16\WINWORD.EXE"),
                Path(r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"),
            ],
            "Optional DOCX-to-PDF render fallback",
        ),
    ]
    docx_available = next(item["available"] for item in modules if item["name"] == "docx")
    psycopg_available = next(item["available"] for item in modules if item["name"] == "psycopg")
    renderer_available = any(
        item["available"] for item in executables if item["name"] in {"soffice", "winword"}
    )
    core_ready = python_ok and not migration_error
    word_candidate_ready = core_ready and docx_available
    postgres_required = knowledge_mode == "server_required"
    knowledge_ready = core_ready and (psycopg_available or not postgres_required)
    return {
        "platform": {"os": os.name, "python": sys.version.split()[0]},
        "core": {
            "ready": core_ready,
            "python_3_10_or_newer": python_ok,
            "sqlite_version": sqlite_version,
            "fts5_available": fts5,
            "migrations_apply": not migration_error,
            "migration_error": migration_error,
            "applied_migrations": applied,
        },
        "modules": modules,
        "executables": executables,
        "word": {
            "candidate_generation_ready": word_candidate_ready,
            "renderer_installed": renderer_available,
            "render_smoke_required": True,
            "visual_delivery_ready": False,
            "note": "visual_delivery_ready becomes true only after an actual DOCX render and page inspection",
        },
        "knowledge": {
            "mode": knowledge_mode,
            "ready": knowledge_ready,
            "postgres_required": postgres_required,
            "psycopg_available": psycopg_available,
            "blocking_reason": (
                "Install psycopg[binary] in the repository environment for server_required mode."
                if postgres_required and not psycopg_available
                else ""
            ),
        },
        "optional": {
            "postgres_required": postgres_required,
            "rag_required": False,
            "fts5_required": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-mode",
        choices=("server_required", "snapshot_required", "offline_pack", "disabled"),
        default="disabled",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check_dependencies(args.knowledge_mode)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["core"]["ready"] and result["knowledge"]["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
