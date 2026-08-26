#!/usr/bin/env python3
"""Local stdio MCP adapter for construction alignment and Word generation.

The server deliberately has no network listener.  It exposes existing deterministic
Skill functions over JSON-RPC/MCP while keeping all source files, databases and
generated documents below explicitly allowed local project roots.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from build_report_docx import build_docx, resolve_format_authority
from construction_alignment import (
    _candidate_markdown,
    apply_decisions,
    assemble,
    capture_scope_snapshot,
    match_scope,
    render_scope_fragment,
    render_solution_fragment,
    validate_manifest,
)
from extract_xlsx_scope import build_payload as build_scope_payload
from ingest_scope_items_sqlite import ingest_scope_payload
from knowledge_db import sha256_file
from lint_docx_format import lint_docx


SERVER_NAME = "medical-it-feasibility-local"
SERVER_VERSION = "0.2.0"
LATEST_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    LATEST_PROTOCOL_VERSION,
}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _write_stdout_json(value: Any, *, compact: bool = False) -> None:
    separators = (",", ":") if compact else None
    payload = json.dumps(value, ensure_ascii=False, indent=None if compact else 2, separators=separators)
    sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_text(value) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _run_id(label: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{label}-{uuid.uuid4().hex[:8]}"


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


@dataclass(frozen=True)
class ServerConfig:
    allowed_project_roots: tuple[Path, ...]
    response_mode: str = "review_metadata"
    allow_delivery_mode: bool = False

    @classmethod
    def load(cls, path: Path) -> "ServerConfig":
        config_path = path.resolve()
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if payload.get("schema_version") != "1.0":
            raise ValueError("unsupported MCP service config schema_version")
        roots = payload.get("allowed_project_roots")
        if not isinstance(roots, list) or not roots:
            raise ValueError("allowed_project_roots must contain at least one absolute path")
        resolved_roots: list[Path] = []
        for raw in roots:
            root = Path(str(raw))
            if not root.is_absolute():
                raise ValueError("allowed_project_roots entries must be absolute")
            root = root.resolve()
            if not root.is_dir():
                raise FileNotFoundError(root)
            resolved_roots.append(root)
        response_mode = str(payload.get("response_mode") or "review_metadata")
        if response_mode not in {"review_metadata", "paths_only"}:
            raise ValueError("response_mode must be review_metadata or paths_only")
        return cls(
            allowed_project_roots=tuple(resolved_roots),
            response_mode=response_mode,
            allow_delivery_mode=bool(payload.get("allow_delivery_mode", False)),
        )


class PathPolicy:
    def __init__(self, config: ServerConfig):
        self.config = config

    def project_root(self, raw: Any) -> Path:
        path = self._absolute(raw, "project_root").resolve()
        if not path.is_dir():
            raise FileNotFoundError(path)
        if not any(_inside(path, root) for root in self.config.allowed_project_roots):
            raise PermissionError("project_root is outside allowed_project_roots")
        return path

    def input_file(
        self,
        raw: Any,
        project_root: Path,
        label: str,
        suffixes: tuple[str, ...] = (),
    ) -> Path:
        path = self._absolute(raw, label).resolve()
        self._require_project_path(path, project_root, label)
        if not path.is_file():
            raise FileNotFoundError(path)
        if suffixes and path.suffix.lower() not in suffixes:
            raise ValueError(f"{label} must use one of these suffixes: {', '.join(suffixes)}")
        return path

    def output_file(
        self,
        raw: Any,
        project_root: Path,
        label: str,
        suffix: str,
    ) -> Path:
        path = self._absolute(raw, label).resolve()
        self._require_project_path(path, project_root, label)
        if path.suffix.lower() != suffix:
            raise ValueError(f"{label} must end with {suffix}")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def output_dir(self, raw: Any, project_root: Path, run_id: str) -> Path:
        if raw in (None, ""):
            path = (project_root / "mcp-output" / run_id).resolve()
        else:
            path = self._absolute(raw, "output_dir").resolve()
        self._require_project_path(path, project_root, "output_dir")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _absolute(raw: Any, label: str) -> Path:
        value = str(raw or "").strip()
        if not value:
            raise ValueError(f"{label} is required")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError(f"{label} must be an absolute path")
        return path

    @staticmethod
    def _require_project_path(path: Path, project_root: Path, label: str) -> None:
        if not _inside(path, project_root):
            raise PermissionError(f"{label} is outside project_root")


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _review_items(match_result: dict[str, Any]) -> list[dict[str, Any]]:
    review: list[dict[str, Any]] = []
    for item in match_result.get("items", []):
        if item.get("state") == "auto_confirmed_exact":
            continue
        review.append(
            {
                "scope_row_id": item.get("scope_row_id"),
                "source_ordinal": item.get("source_ordinal"),
                "original_name": item.get("original_name"),
                "hierarchy": item.get("hierarchy", []),
                "state": item.get("state"),
                "question": item.get("question"),
                "candidates": [
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "product_name": candidate.get("product_name"),
                        "module_name": candidate.get("module_name"),
                        "match_class": candidate.get("match_class"),
                        "module_name_exact": candidate.get("module_name_exact"),
                        "hierarchy_exact": candidate.get("hierarchy_exact"),
                        "name_score": candidate.get("name_score"),
                        "parent_score": candidate.get("parent_score"),
                        "root_heading_path": candidate.get("root_heading_path", []),
                        "root_match_type": candidate.get("root_match_type"),
                        "candidate_status": candidate.get("candidate_status"),
                        "reason": candidate.get("reason"),
                    }
                    for candidate in item.get("candidates", [])
                ],
            }
        )
    return review


def _has_report_chapter(markdown: str) -> bool:
    return bool(
        re.search(
            r"(?m)^#{1,7}\s+第\d+章(?:\s|$)",
            markdown,
        )
    )


TOOLS: list[dict[str, Any]] = [
    {
        "name": "service_status",
        "description": "检查本机医疗信息化可研MCP服务状态和隐私边界，不读取项目正文。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "construction_prepare_review",
        "description": (
            "从本机XLSX提取建设清单，写入项目SQLite，生成模块级精确/相似/缺失匹配结果。"
            "只返回人工核对所需名称和候选元数据，不返回标准方案正文。"
        ),
        "inputSchema": {
            "type": "object",
            "required": [
                "project_root", "database_path", "scope_xlsx_path", "project_code", "package_id"
            ],
            "properties": {
                "project_root": {"type": "string"},
                "database_path": {"type": "string"},
                "scope_xlsx_path": {"type": "string"},
                "project_code": {"type": "string", "minLength": 1},
                "package_id": {"type": "string", "minLength": 1},
                "output_dir": {"type": "string"},
                "sheet_names": {"type": "array", "items": {"type": "string"}},
                "header_row": {"type": "integer", "minimum": 1},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 50000},
                "similar_threshold": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "construction_apply_and_assemble",
        "description": (
            "应用一轮人工确认结果，按原清单顺序装配完整标准方案子树；标准正文逐字复用，"
            "缺失项只生成【待补充】，未确认项只能生成阻断型工作预览。"
        ),
        "inputSchema": {
            "type": "object",
            "required": [
                "project_root",
                "database_path",
                "project_code",
                "match_run_id",
                "reviewed_by",
                "decisions",
            ],
            "properties": {
                "project_root": {"type": "string"},
                "database_path": {"type": "string"},
                "project_code": {"type": "string", "minLength": 1},
                "match_run_id": {"type": "string", "minLength": 1},
                "reviewed_by": {"type": "string", "minLength": 1},
                "reviewed_at": {"type": "string"},
                "decisions": {"type": "array", "items": {"type": "object"}},
                "allow_unresolved_preview": {"type": "boolean"},
                "output_dir": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "word_generate",
        "description": (
            "使用已确认且带哈希的Word格式权威配置生成DOCX，并自动执行Markdown残留和样式结构检查。"
            "返回结构校验状态；整份渲染复核未完成前不会标记为正式交付就绪。"
        ),
        "inputSchema": {
            "type": "object",
            "required": [
                "project_root",
                "input_markdown_path",
                "output_docx_path",
                "project_name",
                "format_config_path",
                "database_path",
                "project_code",
                "assembly_manifest_path",
                "assembly_validation_path",
            ],
            "properties": {
                "project_root": {"type": "string"},
                "input_markdown_path": {"type": "string"},
                "output_docx_path": {"type": "string"},
                "project_name": {"type": "string", "minLength": 1},
                "owner_name": {"type": "string"},
                "format_config_path": {"type": "string"},
                "markdown_mode": {
                    "type": "string",
                    "enum": ["auto", "full_report", "fragment"],
                },
                "fragment_title": {"type": "string"},
                "mode": {"type": "string", "enum": ["working", "delivery"]},
                "database_path": {"type": "string"},
                "project_code": {"type": "string"},
                "assembly_manifest_path": {"type": "string"},
                "assembly_validation_path": {"type": "string"},
                "summary_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
]


class MedicalReportMCP:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.paths = PathPolicy(config)
        self._lock = threading.RLock()
        self._tool_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "service_status": self.service_status,
            "construction_prepare_review": self.construction_prepare_review,
            "construction_apply_and_assemble": self.construction_apply_and_assemble,
            "word_generate": self.word_generate,
        }

    def service_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments:
            raise ValueError("service_status does not accept arguments")
        return {
            "status": "ready",
            "server_name": SERVER_NAME,
            "server_version": SERVER_VERSION,
            "transport": "stdio",
            "network_listener": False,
            "response_mode": self.config.response_mode,
            "allowed_project_roots": [str(path) for path in self.config.allowed_project_roots],
            "allow_delivery_mode": self.config.allow_delivery_mode,
            "privacy_note": "项目文件和正文不由服务上传；review_metadata模式只向MCP客户端返回人工核对所需名称和候选元数据。",
        }

    def construction_prepare_review(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _run_id("construction-review")
        project_root = self.paths.project_root(arguments.get("project_root"))
        database = self.paths.input_file(
            arguments.get("database_path"), project_root, "database_path", (".sqlite", ".db")
        )
        xlsx = self.paths.input_file(
            arguments.get("scope_xlsx_path"), project_root, "scope_xlsx_path", (".xlsx",)
        )
        output_dir = self.paths.output_dir(arguments.get("output_dir"), project_root, run_id)
        project_code = str(arguments.get("project_code") or "").strip()
        package_id = str(arguments.get("package_id") or "").strip()
        if not project_code or not package_id:
            raise ValueError("project_code and package_id are required")
        sheet_names = arguments.get("sheet_names")
        if sheet_names is not None and not isinstance(sheet_names, list):
            raise ValueError("sheet_names must be an array")

        with self._lock:
            scope_payload = build_scope_payload(
                xlsx,
                requested_sheets={str(value) for value in sheet_names} if sheet_names else None,
                header_row=arguments.get("header_row"),
                max_rows=int(arguments.get("max_rows", 5000)),
            )
            scope_path = output_dir / "scope-extract.json"
            _write_json(scope_path, scope_payload)
            ingest_result = ingest_scope_payload(database, scope_payload, project_code=project_code)
            ingest_path = output_dir / "scope-ingest-result.json"
            _write_json(ingest_path, ingest_result)
            snapshot = capture_scope_snapshot(database, project_code, scope_payload)
            snapshot_path = output_dir / "scope-display-snapshot.json"
            _write_json(snapshot_path, snapshot)
            match_result = match_scope(
                database,
                project_code,
                scope_snapshot_id=str(snapshot.get("scope_snapshot_id") or ""),
                package_id=package_id,
                similar_threshold=float(arguments.get("similar_threshold", 0.6)),
            )
            match_json = output_dir / "construction-match-review.json"
            match_md = output_dir / "construction-match-review.md"
            _write_json(match_json, match_result)
            _write_text(match_md, _candidate_markdown(match_result))

        summary = match_result.get("summary", {})
        needs_review = int(summary.get("needs_human_review", 0)) + int(
            summary.get("content_missing", 0)
        )
        result: dict[str, Any] = {
            "status": "needs_human_confirmation" if needs_review else "ready_to_assemble",
            "run_id": run_id,
            "match_run_id": match_result.get("match_run_id"),
            "scope_snapshot_id": snapshot.get("scope_snapshot_id"),
            "summary": summary,
            "artifacts": {
                "scope_extract": _artifact(scope_path),
                "scope_ingest": _artifact(ingest_path),
                "scope_snapshot": _artifact(snapshot_path),
                "match_review_json": _artifact(match_json),
                "match_review_markdown": _artifact(match_md),
            },
            "next_action": (
                "请把review_items作为一轮人工核对问题；确认后调用construction_apply_and_assemble。"
                if needs_review
                else "无需人工修正，可用空decisions调用construction_apply_and_assemble。"
            ),
        }
        if self.config.response_mode == "review_metadata":
            result["review_items"] = _review_items(match_result)
        self._write_audit(output_dir, run_id, "construction_prepare_review", result)
        return result

    def construction_apply_and_assemble(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _run_id("construction-assembly")
        project_root = self.paths.project_root(arguments.get("project_root"))
        database = self.paths.input_file(
            arguments.get("database_path"), project_root, "database_path", (".sqlite", ".db")
        )
        output_dir = self.paths.output_dir(arguments.get("output_dir"), project_root, run_id)
        project_code = str(arguments.get("project_code") or "").strip()
        match_run_id = str(arguments.get("match_run_id") or "").strip()
        reviewed_by = str(arguments.get("reviewed_by") or "").strip()
        decisions = arguments.get("decisions")
        if not project_code or not match_run_id or not reviewed_by or not isinstance(decisions, list):
            raise ValueError(
                "project_code, match_run_id, reviewed_by and decisions[] are required"
            )
        decision_payload: dict[str, Any] = {
            "match_run_id": match_run_id,
            "reviewed_by": reviewed_by,
            "decisions": decisions,
        }
        if arguments.get("reviewed_at"):
            decision_payload["reviewed_at"] = str(arguments["reviewed_at"])
        decision_path = output_dir / "construction-decisions.json"
        _write_json(decision_path, decision_payload)

        with self._lock:
            applied = apply_decisions(database, decision_payload)
            applied_path = output_dir / "construction-decisions-applied.json"
            _write_json(applied_path, applied)
            manifest, combined = assemble(
                database,
                project_code,
                match_run_id,
                allow_unresolved_preview=bool(arguments.get("allow_unresolved_preview", False)),
            )
            manifest_path = output_dir / "construction-assembly-manifest.json"
            combined_path = output_dir / "construction-assembly.md"
            scope_path = output_dir / "construction-list.md"
            solution_path = output_dir / "application-software-solution.md"
            _write_json(manifest_path, manifest)
            _write_text(combined_path, combined)
            _write_text(scope_path, render_scope_fragment(manifest))
            _write_text(solution_path, render_solution_fragment(manifest))
            validation = validate_manifest(database, manifest)
            validation_path = output_dir / "construction-assembly-validation.json"
            _write_json(validation_path, validation)

        valid = bool(validation.get("valid"))
        result = {
            "status": "validated" if valid else "blocked_working_preview",
            "run_id": run_id,
            "match_run_id": match_run_id,
            "manifest_id": manifest.get("manifest_id"),
            "manifest_hash": manifest.get("manifest_hash"),
            "package_id": manifest.get("package_id"),
            "package_content_hash": manifest.get("package_content_hash"),
            "manifest_status": manifest.get("status"),
            "preview_only": bool(manifest.get("preview_only")),
            "decision_result": {
                key: applied.get(key)
                for key in ("applied", "duplicates", "match_run_id")
                if key in applied
            },
            "validation": validation,
            "artifacts": {
                "decisions": _artifact(decision_path),
                "decisions_applied": _artifact(applied_path),
                "manifest": _artifact(manifest_path),
                "combined_markdown": _artifact(combined_path),
                "construction_list_markdown": _artifact(scope_path),
                "solution_markdown": _artifact(solution_path),
                "validation": _artifact(validation_path),
            },
            "next_action": (
                "装配校验通过，可调用word_generate生成工作Word。"
                if valid
                else "当前仅供人工核对；完成剩余确认或补充知识后重新装配。"
            ),
        }
        self._write_audit(output_dir, run_id, "construction_apply_and_assemble", result)
        return result

    def word_generate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        run_id = _run_id("word")
        project_root = self.paths.project_root(arguments.get("project_root"))
        markdown_path = self.paths.input_file(
            arguments.get("input_markdown_path"),
            project_root,
            "input_markdown_path",
            (".md", ".markdown"),
        )
        output_docx = self.paths.output_file(
            arguments.get("output_docx_path"), project_root, "output_docx_path", ".docx"
        )
        format_config = self.paths.input_file(
            arguments.get("format_config_path"),
            project_root,
            "format_config_path",
            (".json",),
        )
        project_name = str(arguments.get("project_name") or "").strip()
        if not project_name:
            raise ValueError("project_name is required")
        markdown = markdown_path.read_text(encoding="utf-8-sig")
        assembly_markdown = markdown
        markdown_mode = str(arguments.get("markdown_mode") or "auto")
        if markdown_mode not in {"auto", "full_report", "fragment"}:
            raise ValueError("markdown_mode must be auto, full_report or fragment")
        contains_report_chapter = _has_report_chapter(markdown)
        if markdown_mode == "full_report" and not contains_report_chapter:
            raise ValueError("full_report markdown must contain an Arabic-numbered '第1章' heading")
        wrapper_applied = markdown_mode == "fragment" or (
            markdown_mode == "auto" and not contains_report_chapter
        )
        fragment_title = str(
            arguments.get("fragment_title") or "建设清单与应用软件建设方案专项预览"
        ).strip()
        if wrapper_applied:
            if not fragment_title:
                raise ValueError("fragment_title cannot be empty when wrapping a fragment")
            markdown = f"# 第1章 {fragment_title}\n\n{markdown.lstrip()}"
        mode = str(arguments.get("mode") or "working")
        if mode == "delivery" and not self.config.allow_delivery_mode:
            raise PermissionError("delivery mode is disabled by the MCP service config")
        if mode not in {"working", "delivery"}:
            raise ValueError("mode must be working or delivery")

        database = self.paths.input_file(
            arguments.get("database_path"), project_root, "database_path", (".sqlite", ".db")
        )
        project_code = str(arguments.get("project_code") or "").strip()
        if not project_code:
            raise ValueError("project_code is required")
        manifest_path = self.paths.input_file(
            arguments.get("assembly_manifest_path"),
            project_root,
            "assembly_manifest_path",
            (".json",),
        )
        validation_path = self.paths.input_file(
            arguments.get("assembly_validation_path"),
            project_root,
            "assembly_validation_path",
            (".json",),
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        recorded_validation = json.loads(validation_path.read_text(encoding="utf-8-sig"))
        if manifest.get("project_code") != project_code:
            raise ValueError("assembly manifest project_code does not match Word request")
        if manifest.get("preview_only") or manifest.get("status") == "blocked":
            raise ValueError("blocked or preview-only assembly cannot enter Word generation")
        expected_markdown = (
            render_scope_fragment(manifest) + "\n\n" + render_solution_fragment(manifest)
        )
        if assembly_markdown.strip() != expected_markdown.strip():
            raise ValueError("input markdown is not the exact output of the bound assembly manifest")
        live_validation = validate_manifest(database, manifest)
        if not recorded_validation.get("valid") or not live_validation.get("valid"):
            raise ValueError("assembly validation is blocked or stale; rerun assembly validation")
        binding_fields = ("manifest_id", "manifest_hash", "package_id", "package_content_hash")
        if any(
            recorded_validation.get(field) != manifest.get(field)
            or live_validation.get(field) != manifest.get(field)
            for field in binding_fields
        ):
            raise ValueError("assembly manifest, validation and package bindings do not match")
        if recorded_validation.get("validation_hash") != live_validation.get("validation_hash"):
            raise ValueError("assembly validation file is stale or has been modified")

        template, authority = resolve_format_authority(None, format_config)
        if template is None:
            raise ValueError("confirmed format authority did not resolve a template")
        self.paths._require_project_path(template, project_root, "format authority template")
        for label, raw in authority.get("_resolved_evidence", {}).items():
            self.paths._require_project_path(Path(str(raw)), project_root, label)
        style_contract_raw = authority.get("_resolved_evidence", {}).get("style_contract_path")
        style_contract = Path(style_contract_raw) if style_contract_raw else None

        with self._lock:
            summary = build_docx(
                markdown,
                output_docx,
                project_name,
                str(arguments.get("owner_name") or ""),
                mode=mode,
                database=database,
                project_code=project_code,
                format_config=format_config,
            )
            lint = lint_docx(output_docx, style_contract)

        summary_path = (
            self.paths.output_file(
                arguments.get("summary_path"), project_root, "summary_path", ".json"
            )
            if arguments.get("summary_path")
            else output_docx.with_suffix(".build-summary.json")
        )
        self.paths._require_project_path(summary_path.resolve(), project_root, "summary_path")
        lint_path = output_docx.with_suffix(".format-lint.json")
        self.paths._require_project_path(lint_path.resolve(), project_root, "format lint path")
        _write_json(summary_path, summary)
        _write_json(lint_path, lint)

        residue = summary.get("markdown_residue", {})
        residue_count = sum(int(value or 0) for value in residue.values())
        blocking_count = int(lint.get("summary", {}).get("blocking_count", 0))
        structural_pass = residue_count == 0 and blocking_count == 0
        result = {
            "status": "structure_pass_render_required" if structural_pass else "structure_blocked",
            "run_id": run_id,
            "mode": mode,
            "markdown_mode": markdown_mode,
            "fragment_wrapper_applied": wrapper_applied,
            "fragment_title": fragment_title if wrapper_applied else "",
            "format_profile_id": summary.get("format_profile_id"),
            "template_sha256": summary.get("template_sha256"),
            "format_config_sha256": summary.get("format_config_sha256"),
            "assembly_manifest_id": manifest.get("manifest_id"),
            "assembly_manifest_hash": manifest.get("manifest_hash"),
            "assembly_validation_hash": live_validation.get("validation_hash"),
            "heading_levels": summary.get("heading_levels", {}),
            "numbered_heading_levels": summary.get("numbered_heading_levels", []),
            "markdown_residue": residue,
            "format_lint_summary": lint.get("summary", {}),
            "structural_validation_passed": structural_pass,
            "visual_render_review": "not_run",
            "delivery_ready": False,
            "artifacts": {
                "docx": _artifact(output_docx),
                "build_summary": _artifact(summary_path),
                "format_lint": _artifact(lint_path),
            },
            "next_action": "整份渲染并逐页检查标题层级、分页、表格和孤行后，方可标记正式交付。",
        }
        self._write_audit(output_docx.parent, run_id, "word_generate", result)
        return result

    @staticmethod
    def _write_audit(
        output_dir: Path, run_id: str, tool_name: str, result: dict[str, Any]
    ) -> None:
        record = {
            "schema_version": "1.0",
            "run_id": run_id,
            "tool_name": tool_name,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": result.get("status"),
            "artifact_hashes": {
                name: artifact.get("sha256")
                for name, artifact in result.get("artifacts", {}).items()
                if isinstance(artifact, dict)
            },
        }
        _write_json(output_dir / f"mcp-run-{run_id}.json", record)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request")
        if request_id is None and method.startswith("notifications/"):
            return None
        try:
            if method == "initialize":
                params = request.get("params") or {}
                requested = str(params.get("protocolVersion") or "")
                protocol_version = (
                    requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
                )
                return self._result(
                    request_id,
                    {
                        "protocolVersion": protocol_version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                        "instructions": (
                            "先调用service_status；清单流程依次调用construction_prepare_review、"
                            "人工确认、construction_apply_and_assemble、word_generate。"
                        ),
                    },
                )
            if method == "ping":
                return self._result(request_id, {})
            if method == "tools/list":
                return self._result(request_id, {"tools": TOOLS})
            if method == "resources/list":
                return self._result(request_id, {"resources": []})
            if method == "prompts/list":
                return self._result(request_id, {"prompts": []})
            if method == "tools/call":
                params = request.get("params") or {}
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if name not in self._tool_handlers:
                    return self._error(request_id, -32602, f"Unknown tool: {name}")
                if not isinstance(arguments, dict):
                    return self._error(request_id, -32602, "tool arguments must be an object")
                try:
                    value = self._tool_handlers[name](arguments)
                    return self._result(request_id, self._tool_result(value, False))
                except Exception as exc:  # MCP tool errors are data, not protocol failures.
                    error_value = {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                    return self._result(request_id, self._tool_result(error_value, True))
            return self._error(request_id, -32601, "Method not found")
        except Exception as exc:
            return self._error(request_id, -32603, f"Internal error: {type(exc).__name__}")

    @staticmethod
    def _tool_result(value: dict[str, Any], is_error: bool) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
            "structuredContent": value,
            "isError": is_error,
        }

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }


def serve_stdio(server: MedicalReportMCP) -> int:
    for raw_line in sys.stdin.buffer:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line.decode("utf-8"))
            if not isinstance(request, dict):
                response = MedicalReportMCP._error(None, -32600, "Invalid Request")
            else:
                response = server.dispatch(request)
        except json.JSONDecodeError:
            response = MedicalReportMCP._error(None, -32700, "Parse error")
        if response is not None:
            _write_stdout_json(response, compact=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Local MCP service JSON config")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit without starting stdio",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ServerConfig.load(args.config)
    server = MedicalReportMCP(config)
    if args.check_config:
        _write_stdout_json(server.service_status({}))
        return 0
    return serve_stdio(server)


if __name__ == "__main__":
    raise SystemExit(main())
