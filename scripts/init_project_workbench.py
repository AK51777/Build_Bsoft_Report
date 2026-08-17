#!/usr/bin/env python3
"""Initialize an idempotent, local-first feasibility-report project workbench."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, now_iso, upsert_project


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = SKILL_ROOT / "assets" / "project-workbench-template"

DOCUMENT_TYPE_ALIASES = {
    "feasibility_study": "feasibility_study",
    "可行性研究报告": "feasibility_study",
    "可研": "feasibility_study",
    "医疗信息化可研": "feasibility_study",
}
PROJECT_TYPE_ALIASES = {
    "hospital_informationization": "hospital_informationization",
    "医院信息化": "hospital_informationization",
    "医疗信息化": "hospital_informationization",
    "医院信息化建设": "hospital_informationization",
}

DATA_DIRECTORIES = (
    "原始资料",
    "数据包/清洗文本",
    "数据包/结构化数据",
    "数据包/数据库",
    "运行记录",
    "预览",
    "11-正文工作稿",
    "14-交付稿",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def canonical_type(value: str, aliases: dict[str, str], field: str) -> str:
    cleaned = str(value or "").strip()
    canonical = aliases.get(cleaned)
    if canonical:
        return canonical
    supported = "、".join(sorted(aliases))
    raise ValueError(f"unsupported {field}: {cleaned!r}; supported values/aliases: {supported}")


def render_task_book(
    template: str,
    *,
    project_code: str,
    official_name: str | None,
    owner_name: str | None,
    scope_authority: str | None,
    acceptance_targets: list[str],
    unknown_handling: str,
    output_formats: list[str],
    project_directory: Path,
) -> str:
    replacements = {
        "| 项目编号 | 【待补充】 | 【待补充】 | 【待补充】 |": (
            f"| 项目编号 | {project_code} | 【材料明确】 | 初始化参数 |"
        ),
        "| 项目目录 | 【待补充】 | 【待补充】 | 【待补充】 |": (
            f"| 项目目录 | {project_directory} | 【材料明确】 | 初始化参数 |"
        ),
        "| 未知事项处理规则 | 保留统一占位并汇总 | 【待确认】 | 用户任务 |": (
            f"| 未知事项处理规则 | {unknown_handling} | 【待确认】 | 初始化参数 |"
        ),
        "| 输出格式 | Markdown工作稿、Word交付稿 | 【待确认】 | 用户任务 |": (
            f"| 输出格式 | {'、'.join(output_formats)} | 【待确认】 | 初始化参数 |"
        ),
    }
    if official_name:
        replacements["| 正式项目名称 | 【待补充】 | 【待补充】 | 【待补充】 |"] = (
            f"| 正式项目名称 | {official_name} | 【待确认】 | 初始化参数 |"
        )
    if owner_name:
        replacements["| 建设单位 | 【待补充】 | 【待补充】 | 【待补充】 |"] = (
            f"| 建设单位 | {owner_name} | 【待确认】 | 初始化参数 |"
        )
    if scope_authority:
        replacements["| 本期范围最高依据 | 【待补充】 | 【待补充】 | 【待补充】 |"] = (
            f"| 本期范围最高依据 | {scope_authority} | 【待确认】 | 初始化参数 |"
        )
    if acceptance_targets:
        replacements["| 核心验收目标 | 【待补充】 | 【待补充】 | 【待补充】 |"] = (
            f"| 核心验收目标 | {'；'.join(acceptance_targets)} | 【待确认】 | 初始化参数 |"
        )
    for original, replacement in replacements.items():
        template = template.replace(original, replacement)
    return template


def copy_workbench_templates(
    template_dir: Path,
    target_dir: Path,
    task_book_context: dict[str, Any],
) -> tuple[list[str], list[str]]:
    created: list[str] = []
    preserved: list[str] = []
    for source in sorted(template_dir.rglob("*")):
        relative = source.relative_to(template_dir)
        destination = target_dir / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if destination.exists():
            preserved.append(relative.as_posix())
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative.as_posix() == "00-项目任务书.md":
            content = source.read_text(encoding="utf-8")
            destination.write_text(
                render_task_book(content, **task_book_context), encoding="utf-8"
            )
        else:
            shutil.copy2(source, destination)
        created.append(relative.as_posix())
    return created, preserved


def ensure_project_row(database: Path, project_input: dict[str, Any]) -> dict[str, Any]:
    with connect(database) as conn:
        applied = apply_migrations(conn)
        existing = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_input["project_code"],)
        ).fetchone()
        changed = existing is None
        if existing is None:
            upsert_project(conn, project_input)
        else:
            merged = dict(existing)
            fields = (
                "official_name",
                "document_type",
                "owner_name",
                "jurisdiction_code",
                "jurisdiction_name",
                "project_type",
                "scope_authority",
                "baseline_version",
            )
            for field in fields:
                value = project_input.get(field)
                if value not in (None, ""):
                    merged[field] = value
            if project_input.get("acceptance_targets"):
                merged["acceptance_targets"] = project_input["acceptance_targets"]
            else:
                merged["acceptance_targets"] = json.loads(
                    merged.get("acceptance_targets_json", "[]")
                )
            comparison = {
                "official_name": existing["official_name"],
                "document_type": existing["document_type"],
                "owner_name": existing["owner_name"],
                "jurisdiction_code": existing["jurisdiction_code"],
                "jurisdiction_name": existing["jurisdiction_name"],
                "project_type": existing["project_type"],
                "scope_authority": existing["scope_authority"],
                "baseline_version": existing["baseline_version"],
                "acceptance_targets": json.loads(existing["acceptance_targets_json"]),
            }
            expected = {key: merged[key] for key in comparison}
            changed = comparison != expected
            if changed:
                upsert_project(conn, merged)
        conn.commit()
        row = conn.execute(
            "SELECT project_id,project_code,official_name,baseline_version FROM project "
            "WHERE project_code=?",
            (project_input["project_code"],),
        ).fetchone()
        migration_rows = [
            dict(item)
            for item in conn.execute(
                "SELECT version,file_hash,applied_at FROM kb_schema_migration ORDER BY version"
            )
        ]
    return {
        "project": dict(row),
        "database_changed": changed,
        "applied_now": applied,
        "migrations": migration_rows,
    }


def initialize_project(
    target_dir: Path,
    *,
    project_code: str,
    official_name: str | None = None,
    owner_name: str | None = None,
    document_type: str = "feasibility_study",
    jurisdiction_code: str = "",
    jurisdiction_name: str = "",
    project_type: str = "hospital_informationization",
    scope_authority: str | None = None,
    acceptance_targets: list[str] | None = None,
    unknown_handling: str = "保留统一占位并汇总",
    output_formats: list[str] | None = None,
    baseline_version: str = "working",
    template_dir: Path = DEFAULT_TEMPLATE,
) -> dict[str, Any]:
    project_code = project_code.strip()
    if not project_code:
        raise ValueError("project_code must not be empty")
    document_type = canonical_type(document_type, DOCUMENT_TYPE_ALIASES, "document_type")
    project_type = canonical_type(project_type, PROJECT_TYPE_ALIASES, "project_type")
    target_dir = target_dir.resolve()
    if target_dir.exists() and not target_dir.is_dir():
        raise NotADirectoryError(target_dir)
    template_dir = template_dir.resolve()
    if not template_dir.is_dir():
        raise FileNotFoundError(f"workbench template not found: {template_dir}")

    manifest_path = target_dir / "project-manifest.json"
    config_path = target_dir / "project-config.json"
    existing_manifest: dict[str, Any] = {}
    existing_config: dict[str, Any] = {}
    if manifest_path.exists():
        existing_manifest = load_json(manifest_path)
        existing_code = existing_manifest.get("project_code")
        if existing_code and existing_code != project_code:
            raise RuntimeError(
                f"target workbench belongs to project {existing_code}, not {project_code}"
            )
    if config_path.exists():
        existing_config = load_json(config_path)
        existing_code = existing_config.get("project", {}).get("project_code")
        if existing_code and existing_code != project_code:
            raise RuntimeError(
                f"target configuration belongs to project {existing_code}, not {project_code}"
            )

    target_dir.mkdir(parents=True, exist_ok=True)
    for relative in DATA_DIRECTORIES:
        (target_dir / relative).mkdir(parents=True, exist_ok=True)

    acceptance_targets = acceptance_targets or []
    output_formats = output_formats or ["Markdown工作稿", "Word交付稿"]
    task_context = {
        "project_code": project_code,
        "official_name": official_name,
        "owner_name": owner_name,
        "scope_authority": scope_authority,
        "acceptance_targets": acceptance_targets,
        "unknown_handling": unknown_handling,
        "output_formats": output_formats,
        "project_directory": target_dir,
    }
    created_files, preserved_files = copy_workbench_templates(
        template_dir, target_dir, task_context
    )

    initialized_at = existing_manifest.get("initialized_at") or now_iso()
    requested_config = {
        "schema_version": "1.0",
        "project": {
            "project_code": project_code,
            "official_name": official_name or "【待补充】",
            "document_type": document_type,
            "owner_name": owner_name or "",
            "jurisdiction_code": jurisdiction_code,
            "jurisdiction_name": jurisdiction_name,
            "project_type": project_type,
            "scope_authority": scope_authority or "",
            "acceptance_targets": acceptance_targets,
            "baseline_version": baseline_version,
        },
        "workflow": {
            "current_stage": "0",
            "unknown_handling": unknown_handling,
            "output_formats": output_formats,
        },
        "delivery": {
            "word_template": "",
            "require_render_review": True,
        },
        "knowledge": {
            "mode": "server_required",
            "profile": "default",
            "package_ids": [],
            "catalog_ids": [],
            "policy_topics": [],
            "permission_scopes": {
                "knowledge_package": ["internal_company_reuse"],
                "policy_catalog": ["internal_company_reference"],
                "policy_release": ["public_policy_reference"],
            },
            "allow_stale_cache": False,
            "selection": {},
            "standard_packs": [],
            "policy_catalogs": [],
            "mapping_threshold": 0.55,
            "mapping_max_candidates": 3,
        },
        "source_roles": {
            "overrides": {},
            "default_role": "project_material",
        },
        "paths": {
            "source_materials": "原始资料",
            "cleaned_text": "数据包/清洗文本",
            "structured_data": "数据包/结构化数据",
            "database": "数据包/数据库/knowledge.sqlite",
            "run_logs": "运行记录",
            "previews": "预览",
            "drafts": "11-正文工作稿",
            "delivery": "14-交付稿",
        },
    }
    if config_path.exists():
        config = existing_config
        changed = False
        if "knowledge" not in config:
            config["knowledge"] = requested_config["knowledge"]
            changed = True
        else:
            knowledge = config["knowledge"]
            legacy_server = knowledge.get("server") if isinstance(knowledge.get("server"), dict) else None
            if "mode" not in knowledge:
                knowledge["mode"] = (
                    "server_required" if legacy_server and legacy_server.get("enabled") else "disabled"
                )
                changed = True
            if "profile" not in knowledge:
                knowledge["profile"] = "" if legacy_server else "default"
                changed = True
            for key in (
                "package_ids", "catalog_ids", "policy_topics", "permission_scopes",
                "allow_stale_cache", "selection", "standard_packs", "policy_catalogs",
                "mapping_threshold", "mapping_max_candidates",
            ):
                if key not in knowledge:
                    knowledge[key] = requested_config["knowledge"][key]
                    changed = True
            if legacy_server is not None and "catalog_ids" not in legacy_server:
                legacy_server["catalog_ids"] = []
                changed = True
        delivery = config.setdefault("delivery", {})
        if delivery.get("require_render_review") is not True:
            delivery["require_render_review"] = True
            changed = True
        if changed:
            write_json(config_path, config)
    else:
        config = requested_config
        write_json(config_path, config)

    database = target_dir / config["paths"]["database"]
    project_input = dict(config["project"])
    project_input["official_name"] = project_input.get("official_name") or "【待补充】"
    database_result = ensure_project_row(database, project_input)
    db_summary_path = target_dir / "运行记录" / "db-init-summary.json"
    if (
        not db_summary_path.exists()
        or database_result["database_changed"]
        or database_result["applied_now"]
    ):
        write_json(
            db_summary_path,
            {
                "database": str(database),
                **database_result,
            },
        )

    tracked_paths = [
        path
        for path in target_dir.rglob("*")
        if path.is_file()
        and path.name != "project-manifest.json"
        and "-wal" not in path.name
        and "-shm" not in path.name
    ]
    artifacts = [
        {
            "path": path.relative_to(target_dir).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(tracked_paths)
    ]
    manifest = {
        "manifest_version": "1.0",
        "project_code": project_code,
        "initialized_at": initialized_at,
        "skill": "build-medical-it-feasibility-report",
        "storage_mode": "local-first-sqlite",
        "original_material_policy": "原始资料只读保留；清洗、合并和格式处理输出新文件",
        "artifacts": artifacts,
    }
    write_json(manifest_path, manifest)

    result = {
        "project_code": project_code,
        "workbench": str(target_dir),
        "database": str(database),
        "created_files": created_files,
        "preserved_files": preserved_files,
        "artifact_count": len(artifacts),
        "database_changed": database_result["database_changed"],
        "applied_migrations": database_result["applied_now"],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="Target project workbench directory")
    parser.add_argument("--project-code", required=True)
    parser.add_argument("--official-name")
    parser.add_argument("--owner-name")
    parser.add_argument(
        "--document-type",
        default="feasibility_study",
        help="Document type: feasibility_study (aliases: 可行性研究报告, 可研, 医疗信息化可研).",
    )
    parser.add_argument("--jurisdiction-code", default="")
    parser.add_argument("--jurisdiction-name", default="")
    parser.add_argument(
        "--project-type",
        default="hospital_informationization",
        help="Project type: hospital_informationization (aliases: 医院信息化, 医疗信息化, 医院信息化建设).",
    )
    parser.add_argument("--scope-authority")
    parser.add_argument("--acceptance-target", action="append", default=[])
    parser.add_argument("--unknown-handling", default="保留统一占位并汇总")
    parser.add_argument(
        "--output-format",
        action="append",
        dest="output_formats",
        default=[],
    )
    parser.add_argument("--baseline-version", default="working")
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = initialize_project(
        args.target,
        project_code=args.project_code,
        official_name=args.official_name,
        owner_name=args.owner_name,
        document_type=args.document_type,
        jurisdiction_code=args.jurisdiction_code,
        jurisdiction_name=args.jurisdiction_name,
        project_type=args.project_type,
        scope_authority=args.scope_authority,
        acceptance_targets=args.acceptance_target,
        unknown_handling=args.unknown_handling,
        output_formats=args.output_formats or None,
        baseline_version=args.baseline_version,
        template_dir=args.template_dir,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        write_json(args.output, result)
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
