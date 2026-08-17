#!/usr/bin/env python3
"""Normalize extracted XLSX scope rows and ingest them into local SQLite."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from knowledge_db import (
    apply_migrations,
    connect,
    dump_json,
    load_json,
    now_iso,
    sha256_text,
    stable_id,
)


MODE_PATTERNS = (
    ("new", ("新建", "新增")),
    ("upgrade", ("升级", "扩容")),
    ("reuse", ("利旧", "复用")),
    ("replace", ("替换", "更换")),
    ("migrate", ("迁移", "搬迁")),
)
INVESTMENT_PATTERNS = (
    ("software", ("软件", "系统", "平台", "应用")),
    ("hardware", ("硬件", "设备", "服务器", "终端")),
    ("cloud_resource", ("云资源", "云服务")),
    ("interface_migration", ("接口", "迁移")),
    ("security", ("安全", "等保", "密码")),
    ("implementation_service", ("实施", "培训", "测试", "运维", "服务")),
)
TOTAL_NAMES = {"合计", "总计", "小计", "汇总", "总金额"}


def clean_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def standardize_name(value: str, name_map: dict[str, str]) -> str:
    cleaned = clean_cell(value)
    without_sequence = re.sub(
        r"^\s*(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[、.．)）\-—:]\s*",
        "",
        cleaned,
    )
    return clean_cell(name_map.get(cleaned, name_map.get(without_sequence, without_sequence)))


def parse_number(value: Any) -> float | None:
    text = clean_cell(value).replace(",", "")
    text = re.sub(r"^(?:￥|¥)", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def canonical_mode(value: Any) -> str:
    text = clean_cell(value)
    for marker in ("调整为", "变更为", "更正为", "改成", "改为"):
        if marker in text:
            replacement = text.rsplit(marker, 1)[-1]
            replacement_mode = canonical_mode(replacement)
            if replacement_mode != "pending_confirmation":
                return replacement_mode
    has_new_mode = any(term in text for term in ("新建", "新增"))
    if ("利旧升级" in text or ("利旧" in text and "升级" in text)) and not has_new_mode:
        return "upgrade"
    matches = [code for code, terms in MODE_PATTERNS if any(term in text for term in terms)]
    return matches[0] if len(set(matches)) == 1 else "pending_confirmation"


def canonical_investment_category(value: Any, item_type: str, standard_name: str = "") -> str:
    text = f"{clean_cell(value)} {item_type}".strip()
    matches = [
        code for code, terms in INVESTMENT_PATTERNS if any(term in text for term in terms)
    ]
    if len(set(matches)) == 1:
        return matches[0]
    name = clean_cell(standard_name)
    if any(term in name for term in ("咨询", "实施服务", "培训服务", "运维服务", "评级")):
        return "implementation_service"
    if any(term in name for term in ("安全", "等保", "密码", "防火墙", "审计")):
        return "security"
    if any(term in name for term in ("硬件", "设备", "服务器", "存储", "机房", "终端")):
        return "hardware"
    if any(term in name for term in ("接口", "迁移")):
        return "interface_migration"
    return "software" if name else ""


def selected_header(sheet: dict[str, Any], field: str) -> str | None:
    columns = sheet.get("detected_columns", {}).get(field, [])
    return str(columns[0]) if columns else None


def row_value(row: dict[str, Any], sheet: dict[str, Any], field: str) -> str:
    header = selected_header(sheet, field)
    return clean_cell(row.get(header, "")) if header else ""


def row_values(row: dict[str, Any], sheet: dict[str, Any], field: str) -> list[str]:
    values: list[str] = []
    for header in sheet.get("detected_columns", {}).get(field, []):
        value = clean_cell(row.get(str(header), ""))
        if value and value not in values:
            values.append(value)
    return values


def normalize_payload(
    payload: dict[str, Any],
    project_id: str,
    name_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    name_map = name_map or {}
    items_by_id: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    sheets = payload.get("sheets")
    if not isinstance(sheets, list):
        raise ValueError("input must contain a sheets array")

    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        original_name_headers = [
            str(header) for header in sheet.get("detected_columns", {}).get("original_name", [])
        ]
        if not original_name_headers:
            issues.append(
                {
                    "sheet": sheet.get("name", ""),
                    "reason": "missing_original_name_column",
                }
            )
            continue
        for row in sheet.get("rows", []):
            if not isinstance(row, dict):
                continue
            name_path = [
                clean_cell(row.get(header, ""))
                for header in original_name_headers
                if clean_cell(row.get(header, ""))
            ]
            original_name = name_path[-1] if name_path else ""
            location = f"sheet:{sheet.get('name', '')};row:{row.get('_source_row', '')}"
            if not original_name or original_name in TOTAL_NAMES:
                issues.append(
                    {
                        "source_location": location,
                        "reason": "blank_or_total_row",
                        "original_name": original_name,
                    }
                )
                continue

            standard_name = standardize_name(original_name, name_map)
            domain_path = row_values(row, sheet, "domain") + name_path[:-1]
            domain = " / ".join(dict.fromkeys(value for value in domain_path if value))
            item_type = row_value(row, sheet, "item_type")
            mode_values = row_values(row, sheet, "construction_mode")
            for note in row_values(row, sheet, "notes"):
                if any(term in note for _, terms in MODE_PATTERNS for term in terms):
                    mode_values.append(note)
            construction_mode = canonical_mode("；".join(mode_values))
            quantity_text = row_value(row, sheet, "quantity")
            quantity = parse_number(quantity_text)
            unit = row_value(row, sheet, "unit")
            investment_category = canonical_investment_category(
                row_value(row, sheet, "investment_category"), item_type, standard_name
            )
            acceptance_target = row_value(row, sheet, "acceptance_target")
            semantic_key = (
                standard_name.casefold(),
                domain.casefold(),
                item_type.casefold(),
            )
            scope_id = stable_id("SCOPE", project_id, *semantic_key)
            source_record = {
                "source_location": location,
                "row": row,
                "quantity_parse_warning": bool(quantity_text and quantity is None),
                "name_path": name_path,
                "construction_mode_source": mode_values,
            }
            if scope_id in items_by_id:
                items_by_id[scope_id]["source_records"].append(source_record)
                continue
            items_by_id[scope_id] = {
                "scope_id": scope_id,
                "original_name": original_name,
                "standard_name": standard_name,
                "domain": domain,
                "item_type": item_type,
                "construction_mode": construction_mode,
                "quantity": quantity,
                "unit": unit,
                "customer_scope": 1,
                "investment_category": investment_category,
                "acceptance_target": acceptance_target,
                "chapter_location": "",
                "status": "pending_confirmation",
                "source_records": [source_record],
            }
    return sorted(items_by_id.values(), key=lambda item: item["scope_id"]), issues


def ingest_scope_payload(
    database: Path,
    payload: dict[str, Any],
    *,
    project_code: str,
    name_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    source = payload.get("source")
    if not isinstance(source, dict) or not source.get("sha256") or not source.get("path"):
        raise ValueError("input must contain source.path and source.sha256")
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(
                f"project_code {project_code} is not initialized in {database}"
            )
        items, issues = normalize_payload(payload, project["project_id"], name_map)
        source_id = stable_id("SOURCE", project["project_id"], source["sha256"])
        existing_scope_ids = {
            row["scope_id"]
            for row in conn.execute(
                "SELECT scope_id FROM project_scope_item WHERE project_id=?",
                (project["project_id"],),
            )
        }
        source_path = Path(str(source["path"]))
        conn.execute(
            """
            INSERT INTO source_document (
              source_id, project_id, source_scope, source_class, file_name, file_type,
              source_path, sha256, usage_scope, restriction_note,
              contains_personal_data, verification_status, imported_at, metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET
              source_path=excluded.source_path,
              metadata_json=excluded.metadata_json
            """,
            (
                source_id,
                project["project_id"],
                "project",
                "construction_scope_list",
                source_path.name,
                "XLSX",
                str(source["path"]),
                str(source["sha256"]),
                "project_scope_evidence",
                "Mechanically extracted; scope confirmation required.",
                0,
                "registered",
                timestamp,
                dump_json(
                    {
                        "size_bytes": source.get("size_bytes"),
                        "sheet_count": payload.get("sheet_count", len(payload.get("sheets", []))),
                    }
                ),
            ),
        )

        evidence_count = 0
        for item in items:
            conn.execute(
                """
                INSERT INTO project_scope_item (
                  scope_id, project_id, source_id, original_name, standard_name,
                  domain, item_type, construction_mode, quantity, unit,
                  customer_scope, investment_category, acceptance_target,
                  chapter_location, status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scope_id) DO UPDATE SET
                  source_id=excluded.source_id,
                  original_name=excluded.original_name,
                  standard_name=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.standard_name ELSE project_scope_item.standard_name END,
                  domain=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.domain ELSE project_scope_item.domain END,
                  item_type=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.item_type ELSE project_scope_item.item_type END,
                  construction_mode=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.construction_mode ELSE project_scope_item.construction_mode END,
                  quantity=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.quantity ELSE project_scope_item.quantity END,
                  unit=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.unit ELSE project_scope_item.unit END,
                  investment_category=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.investment_category ELSE project_scope_item.investment_category END,
                  acceptance_target=CASE
                    WHEN project_scope_item.status='pending_confirmation'
                    THEN excluded.acceptance_target ELSE project_scope_item.acceptance_target END,
                  updated_at=excluded.updated_at
                """,
                (
                    item["scope_id"],
                    project["project_id"],
                    source_id,
                    item["original_name"],
                    item["standard_name"],
                    item["domain"],
                    item["item_type"],
                    item["construction_mode"],
                    item["quantity"],
                    item["unit"],
                    item["customer_scope"],
                    item["investment_category"],
                    item["acceptance_target"],
                    item["chapter_location"],
                    item["status"],
                    timestamp,
                    timestamp,
                ),
            )
            for source_record in item["source_records"]:
                evidence_id = stable_id(
                    "EVIDENCE",
                    source_id,
                    source_record["source_location"],
                    dump_json(source_record["row"]),
                )
                evidence_text = dump_json(
                    {
                        "scope_id": item["scope_id"],
                        "row": source_record["row"],
                        "quantity_parse_warning": source_record["quantity_parse_warning"],
                    }
                )
                conn.execute(
                    """
                    INSERT INTO evidence_record (
                      evidence_id, source_id, source_location, evidence_text,
                      evidence_hash, extraction_method, reliability_level, notes
                    ) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                      evidence_text=excluded.evidence_text,
                      evidence_hash=excluded.evidence_hash,
                      notes=excluded.notes
                    """,
                    (
                        evidence_id,
                        source_id,
                        source_record["source_location"],
                        evidence_text,
                        sha256_text(evidence_text),
                        "xlsx_scope_extraction",
                        "C",
                        "Candidate scope evidence; semantic confirmation required.",
                    ),
                )
                evidence_count += 1
        conn.commit()

    incoming_scope_ids = {item["scope_id"] for item in items}
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "source_id": source_id,
        "scope_items_created": len(incoming_scope_ids - existing_scope_ids),
        "scope_items_updated": len(incoming_scope_ids & existing_scope_ids),
        "scope_items_total_in_input": len(items),
        "evidence_rows_processed": evidence_count,
        "issues": issues,
        "items": items,
        "review_policy": "all-new-items-pending-confirmation; preserve-confirmed-fields",
        "applied_migrations": applied_migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path, help="JSON produced by extract_xlsx_scope.py")
    parser.add_argument("--project-code", required=True)
    parser.add_argument("--name-map", type=Path, help="Optional JSON object: original name to standard name")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    name_map = load_json(args.name_map) if args.name_map else {}
    if not isinstance(name_map, dict):
        raise ValueError("name-map must be a JSON object")
    result = ingest_scope_payload(
        args.database,
        load_json(args.input),
        project_code=args.project_code,
        name_map={str(key): str(value) for key, value in name_map.items()},
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
