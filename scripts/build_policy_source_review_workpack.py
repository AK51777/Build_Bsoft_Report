#!/usr/bin/env python3
"""Export a deterministic, read-only review workpack from policy source captures."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from knowledge_db import now_iso, sha256_text, stable_id
from postgres_knowledge_db import add_connection_arguments, canonical_json, connect, validate_schema


WORKPACK_VERSION = "policy-source-review-workpack-v1"
QUEUE_RULES: tuple[tuple[str, int, str], ...] = (
    ("ready_identity_matched", 10, "核对正式名称、文号、发布机关、日期、效力和用途后人工切分必要条款"),
    ("review_identity_partial", 20, "核对标题或文号缺失的一侧，确认是否为目录对应正式原文"),
    ("review_identity_mismatch", 30, "人工比对目录与抓取正文；不得在身份不一致时生成正式政策"),
    ("review_identity_unchecked", 40, "人工核对短文本或无法自动判断身份的正文"),
    ("extract_binary", 50, "提取PDF或其他二进制原文后重新执行身份核对"),
    ("revalidate_url", 60, "在官方站点重新定位现行URL并重新采集"),
    ("retry_network", 70, "排查DNS、TLS、超时或站点可达性后重试采集"),
    ("manual_source", 80, "补充或修正官方原文链接，并保存官方原件"),
    ("retry_capture", 90, "复核解析、文件大小或正文过短问题后重新采集"),
    ("capture_missing", 100, "先执行原文采集，不得仅凭目录进入正式核验"),
    ("excluded_nonformal", 900, "草案、内部材料、共识或参考资料不得进入正式政策发布"),
    ("excluded_inactive", 910, "非活动目录项不得进入正式政策发布"),
    ("reviewed_capture", 950, "暂存层已有人工状态；核对审核记录后决定是否需要重新进入工作包"),
)
QUEUE_BY_CODE = {code: (priority, action) for code, priority, action in QUEUE_RULES}


def review_queue(row: dict[str, Any]) -> tuple[str, int, str]:
    if str(row.get("entry_status") or "active") != "active":
        code = "excluded_inactive"
    elif not row.get("capture_id"):
        code = "capture_missing"
    elif str(row.get("source_classification") or "") != "formal_candidate":
        code = "excluded_nonformal"
    elif (
        str(row.get("verification_status") or "unverified") != "unverified"
        or str(row.get("review_status") or "pending") != "pending"
    ):
        code = "reviewed_capture"
    elif str(row.get("content_readiness") or "") == "text_ready":
        identity = str(row.get("identity_status") or "not_checked")
        code = {
            "matched": "ready_identity_matched",
            "partial": "review_identity_partial",
            "mismatch": "review_identity_mismatch",
            "not_checked": "review_identity_unchecked",
        }.get(identity, "review_identity_unchecked")
    elif str(row.get("content_readiness") or "") == "binary_pending_extraction":
        code = "extract_binary"
    elif str(row.get("content_readiness") or "") == "url_revalidation_required":
        code = "revalidate_url"
    elif str(row.get("content_readiness") or "") == "manual_source_required":
        code = "manual_source"
    elif str(row.get("retrieval_status") or "") == "network_error":
        code = "retry_network"
    else:
        code = "retry_capture"
    priority, action = QUEUE_BY_CODE[code]
    return code, priority, action


def select_catalog(connection, *, schema: str, catalog_id: str) -> dict[str, Any]:
    validate_schema(schema)
    where = "WHERE catalog_id=%s" if catalog_id else "WHERE catalog_status='published'"
    params: tuple[Any, ...] = (catalog_id,) if catalog_id else ()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT catalog_id,catalog_status,title,source_file_name,source_sha256,
                   permission_scope,record_count,content_hash,published_at
            FROM {schema}.policy_catalog
            {where}
            ORDER BY catalog_id
            """,
            params,
        )
        columns = [item.name for item in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    if not rows:
        raise ValueError("policy catalog was not found")
    if not catalog_id and len(rows) != 1:
        raise ValueError("published policy catalog selection is not unique; pass --catalog-id")
    if catalog_id and len(rows) != 1:
        raise ValueError("policy catalog selection is not unique")
    return rows[0]


def load_review_rows(connection, *, schema: str, catalog_id: str) -> list[dict[str, Any]]:
    validate_schema(schema)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
              entry.catalog_entry_id,entry.catalog_id,entry.source_row,entry.source_index_no,
              entry.catalog_group_code,entry.catalog_group_name,entry.authority_level_label,
              entry.jurisdiction_level,entry.jurisdiction_code,entry.jurisdiction_name,
              entry.category_name,entry.title,entry.document_no,entry.issuer,
              entry.publish_date,entry.publish_date_raw,entry.notes,
              entry.external_url AS catalog_external_url,entry.entry_status,entry.row_hash,
              latest.capture_id,latest.requested_url,latest.final_url,latest.source_domain,
              latest.source_classification,latest.retrieval_status,latest.content_readiness,
              latest.identity_status,latest.http_status,latest.content_type,
              latest.raw_size_bytes,latest.raw_sha256,latest.extracted_text_sha256,
              latest.extraction_method,latest.attachment_urls,latest.raw_storage_uri,
              latest.fetched_at,latest.error_code,latest.error_message,
              latest.verification_status,latest.review_status,
              capture.extracted_text
            FROM {schema}.policy_catalog_entry AS entry
            LEFT JOIN {schema}.review_policy_source_capture_latest AS latest
              ON latest.catalog_entry_id=entry.catalog_entry_id
            LEFT JOIN {schema}.policy_source_capture AS capture
              ON capture.capture_id=latest.capture_id
            WHERE entry.catalog_id=%s
            ORDER BY entry.source_row,entry.catalog_entry_id
            """,
            (catalog_id,),
        )
        columns = [item.name for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _signature_row(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "catalog_entry_id",
        "source_row",
        "row_hash",
        "capture_id",
        "requested_url",
        "final_url",
        "source_classification",
        "retrieval_status",
        "content_readiness",
        "identity_status",
        "http_status",
        "raw_sha256",
        "extracted_text_sha256",
        "verification_status",
        "review_status",
    )
    return {field: row.get(field) for field in fields}


def _review_template() -> dict[str, Any]:
    return {
        "decision": "",
        "reviewer": "",
        "reviewed_at": "",
        "verified_title": "",
        "verified_document_no": "",
        "verified_issuer": "",
        "verified_publish_date": "",
        "verified_official_url": "",
        "validity_status": "",
        "policy_type": "",
        "check_method": "",
        "review_notes": "",
        "clauses": [],
        "verification_status": "unverified",
    }


def build_workpack(
    catalog: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    include_extracted_text: bool = False,
    preview_chars: int = 500,
) -> dict[str, Any]:
    expected = int(catalog.get("record_count") or 0)
    if expected != len(rows):
        raise ValueError(f"catalog row count mismatch: expected={expected} actual={len(rows)}")
    entry_ids = [str(row.get("catalog_entry_id") or "") for row in rows]
    source_rows = [int(row.get("source_row") or 0) for row in rows]
    if not all(entry_ids) or len(entry_ids) != len(set(entry_ids)):
        raise ValueError("catalog entry IDs are missing or duplicated")
    if not all(source_rows) or len(source_rows) != len(set(source_rows)):
        raise ValueError("catalog source rows are missing or duplicated")

    signature_payload = {
        "workpack_version": WORKPACK_VERSION,
        "catalog_id": catalog["catalog_id"],
        "catalog_content_hash": catalog["content_hash"],
        "records": [
            _signature_row(row)
            for row in sorted(
                rows,
                key=lambda item: (
                    int(item.get("source_row") or 0),
                    str(item.get("catalog_entry_id") or ""),
                ),
            )
        ],
    }
    input_signature = sha256_text(canonical_json(signature_payload))
    run_id = stable_id("POLICYSOURCEREVIEW", catalog["catalog_id"], input_signature)
    output_records: list[dict[str, Any]] = []
    for row in rows:
        queue_code, queue_priority, next_action = review_queue(row)
        extracted_text = str(row.get("extracted_text") or "")
        capture = {
            key: row.get(key)
            for key in (
                "capture_id",
                "requested_url",
                "final_url",
                "source_domain",
                "source_classification",
                "retrieval_status",
                "content_readiness",
                "identity_status",
                "http_status",
                "content_type",
                "raw_size_bytes",
                "raw_sha256",
                "extracted_text_sha256",
                "extraction_method",
                "attachment_urls",
                "raw_storage_uri",
                "fetched_at",
                "error_code",
                "error_message",
                "verification_status",
                "review_status",
            )
        }
        capture["extracted_text_chars"] = len(extracted_text)
        if preview_chars > 0 and extracted_text:
            capture["extracted_text_preview"] = extracted_text[:preview_chars]
        if include_extracted_text:
            capture["extracted_text"] = extracted_text
        output_records.append(
            {
                "catalog_entry_id": row["catalog_entry_id"],
                "source_row": row["source_row"],
                "source_index_no": row.get("source_index_no", ""),
                "queue_code": queue_code,
                "queue_priority": queue_priority,
                "next_action": next_action,
                "formal_publish_eligible": False,
                "catalog": {
                    key: row.get(key)
                    for key in (
                        "catalog_group_code",
                        "catalog_group_name",
                        "authority_level_label",
                        "jurisdiction_level",
                        "jurisdiction_code",
                        "jurisdiction_name",
                        "category_name",
                        "title",
                        "document_no",
                        "issuer",
                        "publish_date",
                        "publish_date_raw",
                        "notes",
                        "catalog_external_url",
                        "entry_status",
                        "row_hash",
                    )
                },
                "capture": capture,
                "review": _review_template(),
            }
        )
    output_records.sort(
        key=lambda item: (item["queue_priority"], item["source_row"], item["catalog_entry_id"])
    )
    queue_counts = Counter(item["queue_code"] for item in output_records)
    classification_counts = Counter(
        str(item["capture"].get("source_classification") or "capture_missing")
        for item in output_records
    )
    readiness_counts = Counter(
        str(item["capture"].get("content_readiness") or "capture_missing")
        for item in output_records
    )
    identity_counts = Counter(
        str(item["capture"].get("identity_status") or "capture_missing")
        for item in output_records
    )
    return {
        "schema_version": "1.0",
        "workpack_type": "policy_source_review",
        "workpack_version": WORKPACK_VERSION,
        "review_run_id": run_id,
        "input_signature": input_signature,
        "generated_at": now_iso(),
        "source": {
            "catalog_id": catalog["catalog_id"],
            "catalog_status": catalog["catalog_status"],
            "catalog_title": catalog.get("title", ""),
            "catalog_content_hash": catalog["content_hash"],
            "catalog_record_count": expected,
            "permission_scope": catalog.get("permission_scope", ""),
        },
        "guardrails": {
            "database_read_only": True,
            "all_records_unfit_for_automatic_formal_publication": True,
            "formal_import_payload_generated": False,
            "required_next_stage": "controlled_manual_verification",
        },
        "queue_definitions": [
            {"queue_code": code, "priority": priority, "next_action": action}
            for code, priority, action in QUEUE_RULES
        ],
        "quality_summary": {
            "records": len(output_records),
            "queue_counts": dict(sorted(queue_counts.items())),
            "source_classification_counts": dict(sorted(classification_counts.items())),
            "content_readiness_counts": dict(sorted(readiness_counts.items())),
            "identity_status_counts": dict(sorted(identity_counts.items())),
            "formal_publish_eligible": 0,
        },
        "records": output_records,
    }


CSV_FIELDS = (
    "catalog_entry_id",
    "source_row",
    "source_index_no",
    "queue_code",
    "queue_priority",
    "next_action",
    "source_classification",
    "retrieval_status",
    "content_readiness",
    "identity_status",
    "http_status",
    "title",
    "document_no",
    "issuer",
    "catalog_external_url",
    "final_url",
    "raw_storage_uri",
    "raw_sha256",
    "extracted_text_sha256",
    "error_code",
    "review_decision",
    "reviewer",
    "reviewed_at",
    "review_notes",
)


def write_csv(workpack: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for item in workpack["records"]:
            catalog = item["catalog"]
            capture = item["capture"]
            writer.writerow(
                {
                    "catalog_entry_id": item["catalog_entry_id"],
                    "source_row": item["source_row"],
                    "source_index_no": item["source_index_no"],
                    "queue_code": item["queue_code"],
                    "queue_priority": item["queue_priority"],
                    "next_action": item["next_action"],
                    "source_classification": capture.get("source_classification") or "",
                    "retrieval_status": capture.get("retrieval_status") or "",
                    "content_readiness": capture.get("content_readiness") or "",
                    "identity_status": capture.get("identity_status") or "",
                    "http_status": capture.get("http_status") or "",
                    "title": catalog.get("title") or "",
                    "document_no": catalog.get("document_no") or "",
                    "issuer": catalog.get("issuer") or "",
                    "catalog_external_url": catalog.get("catalog_external_url") or "",
                    "final_url": capture.get("final_url") or "",
                    "raw_storage_uri": capture.get("raw_storage_uri") or "",
                    "raw_sha256": capture.get("raw_sha256") or "",
                    "extracted_text_sha256": capture.get("extracted_text_sha256") or "",
                    "error_code": capture.get("error_code") or "",
                    "review_decision": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "review_notes": "",
                }
            )


def render_markdown(workpack: dict[str, Any], *, sample_per_queue: int = 10) -> str:
    source = workpack["source"]
    summary = workpack["quality_summary"]
    lines = [
        "# 政策原文复核工作包",
        "",
        f"- 复核运行：`{workpack['review_run_id']}`",
        f"- 政策目录：`{source['catalog_id']}`（{source['catalog_status']}）",
        f"- 输入签名：`{workpack['input_signature']}`",
        f"- 记录数：{summary['records']}",
        "- 正式自动发布资格：0；本工作包不得直接导入正式政策表。",
        "",
        "## 队列汇总",
        "",
        "| 优先级 | 队列 | 数量 | 下一步 |",
        "|---:|---|---:|---|",
    ]
    counts = summary["queue_counts"]
    for code, priority, action in QUEUE_RULES:
        lines.append(f"| {priority} | `{code}` | {counts.get(code, 0)} | {action} |")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in workpack["records"]:
        grouped.setdefault(item["queue_code"], []).append(item)
    for code, _, _ in QUEUE_RULES:
        items = grouped.get(code, [])
        if not items:
            continue
        lines.extend(["", f"## {code}", "", "| 来源行 | 索引号 | 标题 | 状态 |", "|---:|---|---|---|"])
        for item in items[: max(0, sample_per_queue)]:
            title = str(item["catalog"].get("title") or "").replace("|", "\\|")
            status = "/".join(
                str(item["capture"].get(key) or "")
                for key in ("retrieval_status", "content_readiness", "identity_status")
            )
            lines.append(
                f"| {item['source_row']} | {item['source_index_no']} | {title} | {status} |"
            )
        if len(items) > sample_per_queue:
            lines.append(f"\n其余 {len(items) - sample_per_queue} 条见 JSON/CSV。")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-id", default="")
    parser.add_argument("--include-extracted-text", action="store_true")
    parser.add_argument("--preview-chars", type=int, default=500)
    parser.add_argument("--sample-per-queue", type=int, default=10)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--output-md", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    if args.preview_chars < 0:
        raise ValueError("--preview-chars must be zero or greater")
    if args.sample_per_queue < 0:
        raise ValueError("--sample-per-queue must be zero or greater")
    validate_schema(args.schema)
    with connect(args) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        catalog = select_catalog(connection, schema=args.schema, catalog_id=args.catalog_id)
        rows = load_review_rows(connection, schema=args.schema, catalog_id=catalog["catalog_id"])
        workpack = build_workpack(
            catalog,
            rows,
            include_extracted_text=args.include_extracted_text,
            preview_chars=args.preview_chars,
        )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(workpack, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    if args.output_csv:
        write_csv(workpack, args.output_csv)
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(
            render_markdown(workpack, sample_per_queue=args.sample_per_queue),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "review_run_id": workpack["review_run_id"],
                "input_signature": workpack["input_signature"],
                "catalog_id": workpack["source"]["catalog_id"],
                "records": workpack["quality_summary"]["records"],
                "queue_counts": workpack["quality_summary"]["queue_counts"],
                "formal_publish_eligible": 0,
                "database_transaction": "read_only",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
