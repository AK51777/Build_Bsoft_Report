"""Versioned report-outline generation, confirmation, and resolution helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from export_section_task_packages import CHAPTER_ARGUMENT_OUTLINES, ROLE_ARGUMENT_OUTLINES
from knowledge_db import (
    apply_migrations,
    connect,
    dump_json,
    now_iso,
    sha256_text,
    stable_id,
)


OUTLINE_TEMPLATE_VERSION = "2026-08-17.2"


CHAPTER_TITLES = {
    "1": "总论",
    "2": "现状与需求分析",
    "3": "建设必要性与可行性",
    "4": "总体建设方案",
    "5": "建设内容",
    "6": "项目实施与运维",
    "7": "投资估算与资金筹措",
    "8": "效益与绩效评价",
    "9": "风险分析",
    "10": "研究结论与建议",
}

GROUP_TITLES = {
    "1.1": "项目概况",
    "1.2": "编制依据",
    "2.1": "建设单位与现状",
    "2.2": "问题与需求",
    "3.1": "建设必要性",
    "3.2": "建设可行性",
    "4.1": "建设原则与目标",
    "4.2": "总体架构",
    "5.1": "应用、集成与数据建设",
    "5.2": "基础设施与安全建设",
    "6.1": "项目实施",
    "6.2": "运行维护",
    "7.1": "投资估算",
    "7.2": "资金筹措",
    "8.1": "预期效益",
    "8.2": "绩效评价",
    "9.1": "风险与对策",
    "10.1": "结论与建议",
}


def chapter_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _latest_plans(conn, project_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.*,b.section_role
            FROM section_composition_plan p
            LEFT JOIN section_blueprint b ON b.blueprint_id=p.blueprint_id
            WHERE p.project_id=? AND p.applicability_status<>'not_applicable'
              AND p.version_no=(
                SELECT MAX(p2.version_no) FROM section_composition_plan p2
                WHERE p2.project_id=p.project_id AND p2.chapter_code=p.chapter_code
                  AND p2.applicability_status<>'not_applicable'
              )
            """,
            (project_id,),
        )
    ]


def _dynamic_nodes(conn, plan_ids: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for plan_id in plan_ids:
        result.extend(
            dict(row)
            for row in conn.execute(
                "SELECT * FROM section_outline_node WHERE plan_id=? ORDER BY ordinal",
                (plan_id,),
            )
        )
    return result


def _source_snapshot(
    plans: list[dict[str, Any]],
    dynamic: list[dict[str, Any]],
    fixed_by_plan: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    return {
        "outline_template_version": OUTLINE_TEMPLATE_VERSION,
        "plans": [
            {
                "plan_id": row["plan_id"],
                "chapter_code": row["chapter_code"],
                "section_title": row["section_title"],
                "section_role": row.get("section_role") or "",
                "version_no": int(row["version_no"]),
                "applicability_status": row["applicability_status"],
            }
            for row in sorted(plans, key=lambda item: chapter_key(item["chapter_code"]))
        ],
        "dynamic_nodes": [
            {
                "outline_node_id": row["outline_node_id"],
                "plan_id": row["plan_id"],
                "parent_node_id": row["parent_node_id"],
                "chapter_code": row["chapter_code"],
                "heading_level": int(row["heading_level"]),
                "title": row["title"],
                "node_kind": row["node_kind"],
                "source_type": row["source_type"],
                "source_object_id": row["source_object_id"],
                "usage_mode": row["usage_mode"],
                "status": row["status"],
                "ordinal": int(row["ordinal"]),
                "metadata_json": row["metadata_json"],
            }
            for row in dynamic
        ],
        "fixed_subsections": fixed_by_plan,
    }


def _is_standard_policy(policy_type: str, title: str) -> bool:
    return policy_type.casefold() in {
        "evaluation_rule",
        "standard",
        "technical_standard",
        "specification",
    } or any(term in title for term in ("标准", "规范", "评价", "测评", "指南"))


def _policy_outline_items(conn, plan: dict[str, Any]) -> list[dict[str, Any]]:
    if plan["chapter_code"] not in {"1.2.1", "1.2.2"}:
        return []
    standard_mode = plan["chapter_code"] == "1.2.2"
    rows = conn.execute(
        """
        SELECT s.source_object_id,m.policy_id,m.basis_order,d.title,d.policy_type
        FROM section_plan_source s
        JOIN project_policy_match m ON m.match_id=s.source_object_id
        JOIN policy_document d ON d.policy_id=m.policy_id
        JOIN policy_clause c ON c.clause_id=m.clause_id
        WHERE s.plan_id=? AND s.source_type='policy' AND s.usage_mode='evidence'
          AND m.background_use=1 AND d.validity_status='current'
          AND d.verification_status='verified' AND c.verification_status='verified'
        ORDER BY COALESCE(m.basis_order,999999),d.title,m.policy_id,m.match_id
        """,
        (plan["plan_id"],),
    ).fetchall()
    result = []
    seen: set[str] = set()
    for row in rows:
        if row["policy_id"] in seen:
            continue
        if _is_standard_policy(row["policy_type"] or "", row["title"] or "") != standard_mode:
            continue
        seen.add(row["policy_id"])
        result.append(
            {
                "title": f"《{row['title']}》的适用关系",
                "policy_id": row["policy_id"],
                "match_id": row["source_object_id"],
            }
        )
    return result


def _fixed_subsections(conn, plans: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        role = plan.get("section_role") or ""
        if role == "construction_content" or role not in ROLE_ARGUMENT_OUTLINES:
            result[plan["plan_id"]] = []
            continue
        aspects = CHAPTER_ARGUMENT_OUTLINES.get(
            plan["chapter_code"],
            ROLE_ARGUMENT_OUTLINES.get(role, ["资料边界", "实施机制", "验收取证"]),
        )
        nodes = [
            {
                "node_code": f"{plan['chapter_code']}.{index}",
                "heading_level": 4,
                "title": aspect,
                "node_kind": "feature",
                "source_type": "plan",
                "source_object_id": plan["plan_id"],
                "parent_code": plan["chapter_code"],
                "metadata": {
                    "content_mode": "standard_argument_heading",
                    "argument_index": index,
                },
            }
            for index, aspect in enumerate(aspects, start=1)
        ]
        if plan["chapter_code"] in {"1.2.1", "1.2.2"}:
            policy_parent_code = f"{plan['chapter_code']}.3"
            for index, item in enumerate(_policy_outline_items(conn, plan), start=1):
                nodes.append(
                    {
                        "node_code": f"{policy_parent_code}.{index}",
                        "heading_level": 5,
                        "title": item["title"],
                        "node_kind": "subfeature",
                        "source_type": "plan",
                        "source_object_id": plan["plan_id"],
                        "parent_code": policy_parent_code,
                        "metadata": {
                            "content_mode": "verified_policy_heading",
                            "policy_id": item["policy_id"],
                            "match_id": item["match_id"],
                        },
                    }
                )
        result[plan["plan_id"]] = nodes
    return result


def _candidate_nodes(
    plans: list[dict[str, Any]],
    dynamic: list[dict[str, Any]],
    fixed_by_plan: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_plan: dict[str, list[dict[str, Any]]] = {}
    for node in dynamic:
        by_plan.setdefault(node["plan_id"], []).append(node)
    result: list[dict[str, Any]] = []
    ordinal = 0
    seen_chapters: dict[str, str] = {}
    seen_groups: dict[str, str] = {}
    code_to_temp_id: dict[str, str] = {}

    def add(
        *,
        parent_temp_id: str | None,
        code: str,
        level: int,
        title: str,
        kind: str,
        source_type: str,
        source_object_id: str,
        applicability_status: str = "applicable",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        nonlocal ordinal
        ordinal += 1
        temp_id = f"TEMP-{ordinal:05d}"
        result.append(
            {
                "temp_id": temp_id,
                "parent_temp_id": parent_temp_id,
                "node_code": code,
                "heading_level": level,
                "title": title,
                "node_kind": kind,
                "source_type": source_type,
                "source_object_id": source_object_id,
                "ordinal": ordinal,
                "applicability_status": applicability_status,
                "metadata": metadata or {},
            }
        )
        code_to_temp_id[code] = temp_id
        return temp_id

    for plan in sorted(plans, key=lambda item: chapter_key(item["chapter_code"])):
        parts = plan["chapter_code"].split(".")
        if len(parts) < 3:
            raise RuntimeError(f"section chapter_code must have three levels: {plan['chapter_code']}")
        chapter = parts[0]
        group = ".".join(parts[:2])
        if chapter not in seen_chapters:
            seen_chapters[chapter] = add(
                parent_temp_id=None,
                code=chapter,
                level=1,
                title=CHAPTER_TITLES.get(chapter, f"第{chapter}章"),
                kind="chapter",
                source_type="blueprint",
                source_object_id=chapter,
            )
        if group not in seen_groups:
            seen_groups[group] = add(
                parent_temp_id=seen_chapters[chapter],
                code=group,
                level=2,
                title=GROUP_TITLES.get(group, group),
                kind="group",
                source_type="blueprint",
                source_object_id=group,
            )
        section_temp_id = add(
            parent_temp_id=seen_groups[group],
            code=plan["chapter_code"],
            level=3,
            title=plan["section_title"],
            kind="section",
            source_type="plan",
            source_object_id=plan["plan_id"],
            applicability_status=plan["applicability_status"],
            metadata={
                "section_role": plan.get("section_role") or "",
                "plan_version": int(plan["version_no"]),
            },
        )
        fixed_code_to_temp = {plan["chapter_code"]: section_temp_id}
        for fixed in fixed_by_plan.get(plan["plan_id"], []):
            parent_temp_id = fixed_code_to_temp.get(fixed["parent_code"])
            if parent_temp_id is None:
                raise RuntimeError(
                    f"fixed outline parent is missing: {fixed['parent_code']} -> {fixed['node_code']}"
                )
            temp_id = add(
                parent_temp_id=parent_temp_id,
                code=fixed["node_code"],
                level=fixed["heading_level"],
                title=fixed["title"],
                kind=fixed["node_kind"],
                source_type=fixed["source_type"],
                source_object_id=fixed["source_object_id"],
                metadata={"plan_id": plan["plan_id"], **fixed["metadata"]},
            )
            fixed_code_to_temp[fixed["node_code"]] = temp_id
        dynamic_id_to_temp: dict[str, str] = {}
        for node in sorted(by_plan.get(plan["plan_id"], []), key=lambda item: int(item["ordinal"])):
            parent_temp_id = (
                dynamic_id_to_temp.get(node["parent_node_id"], section_temp_id)
                if node["parent_node_id"]
                else section_temp_id
            )
            metadata = json.loads(node["metadata_json"] or "{}")
            metadata.update(
                {
                    "plan_id": plan["plan_id"],
                    "dynamic_outline_node_id": node["outline_node_id"],
                    "usage_mode": node["usage_mode"],
                    "dynamic_status": node["status"],
                }
            )
            temp_id = add(
                parent_temp_id=parent_temp_id,
                code=node["chapter_code"],
                level=int(node["heading_level"]),
                title=node["title"],
                kind=node["node_kind"],
                source_type=node["source_type"],
                source_object_id=node["source_object_id"],
                applicability_status=(
                    "applicable" if node["status"] == "ready" else "pending_confirmation"
                ),
                metadata=metadata,
            )
            dynamic_id_to_temp[node["outline_node_id"]] = temp_id
    return result


def _outline_hash(nodes: list[dict[str, Any]]) -> str:
    id_to_code = {
        node.get("temp_id") or node.get("report_outline_node_id"): node["node_code"]
        for node in nodes
    }
    public_nodes = []
    for node in nodes:
        parent_id = node.get("parent_temp_id") or node.get("parent_node_id")
        public_nodes.append(
            {
                "parent_code": id_to_code.get(parent_id, ""),
                "node_code": node["node_code"],
                "heading_level": int(node["heading_level"]),
                "title": node["title"],
                "node_kind": node["node_kind"],
                "source_type": node["source_type"],
                "source_object_id": node["source_object_id"],
                "ordinal": int(node["ordinal"]),
                "applicability_status": node["applicability_status"],
                "metadata": node.get("metadata", {}),
            }
        )
    return sha256_text(canonical_json(public_nodes))


def _load_outline(conn, version_row: Any) -> dict[str, Any]:
    nodes = []
    for row in conn.execute(
        "SELECT * FROM report_outline_node WHERE outline_version_id=? ORDER BY ordinal",
        (version_row["outline_version_id"],),
    ):
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        nodes.append(item)
    return {
        "schema_version": "1.0",
        "outline_version_id": version_row["outline_version_id"],
        "version_no": int(version_row["version_no"]),
        "source_plan_version": int(version_row["source_plan_version"]),
        "source_signature": version_row["source_signature"],
        "outline_hash": version_row["outline_hash"],
        "status": version_row["status"],
        "created_by": version_row["created_by"],
        "created_at": version_row["created_at"],
        "confirmed_by": version_row["confirmed_by"],
        "confirmed_at": version_row["confirmed_at"],
        "confirmation_note": version_row["confirmation_note"],
        "nodes": nodes,
    }


def build_outline_candidate(database: Path, project_code: str) -> dict[str, Any]:
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        plans = _latest_plans(conn, project["project_id"])
        if not plans:
            raise RuntimeError("cannot build report outline before section composition plans exist")
        dynamic = _dynamic_nodes(conn, [row["plan_id"] for row in plans])
        fixed_by_plan = _fixed_subsections(conn, plans)
        source_snapshot = _source_snapshot(plans, dynamic, fixed_by_plan)
        source_signature = sha256_text(canonical_json(source_snapshot))
        confirmed = conn.execute(
            """
            SELECT * FROM report_outline_version
            WHERE project_id=? AND status='confirmed' AND source_signature=?
            """,
            (project["project_id"], source_signature),
        ).fetchone()
        if confirmed is not None:
            conn.execute(
                "UPDATE report_outline_version SET status='superseded' WHERE project_id=? AND status='candidate'",
                (project["project_id"],),
            )
            conn.commit()
            payload = _load_outline(conn, confirmed)
            payload.update(
                {
                    "project_code": project_code,
                    "confirmation_status": "confirmed",
                    "is_current": True,
                }
            )
            return payload
        active_candidate = conn.execute(
            "SELECT * FROM report_outline_version WHERE project_id=? AND status='candidate'",
            (project["project_id"],),
        ).fetchone()
        if active_candidate is not None and active_candidate["source_signature"] == source_signature:
            payload = _load_outline(conn, active_candidate)
            payload.update(
                {
                    "project_code": project_code,
                    "confirmation_status": "pending_confirmation",
                    "is_current": True,
                }
            )
            return payload
        if active_candidate is not None:
            conn.execute(
                "UPDATE report_outline_version SET status='superseded' WHERE outline_version_id=?",
                (active_candidate["outline_version_id"],),
            )
        version_no = int(
            conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM report_outline_version WHERE project_id=?",
                (project["project_id"],),
            ).fetchone()[0]
        )
        source_plan_version = max(int(row["version_no"]) for row in plans)
        outline_version_id = stable_id(
            "REPORTOUTLINE", project["project_id"], version_no, source_signature
        )
        raw_nodes = _candidate_nodes(plans, dynamic, fixed_by_plan)
        outline_hash = _outline_hash(raw_nodes)
        conn.execute(
            """
            INSERT INTO report_outline_version (
              outline_version_id,project_id,version_no,source_plan_version,
              source_signature,outline_hash,status,created_by,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                outline_version_id,
                project["project_id"],
                version_no,
                source_plan_version,
                source_signature,
                outline_hash,
                "candidate",
                "build_report_outline.py",
                timestamp,
            ),
        )
        temp_to_id: dict[str, str] = {}
        for node in raw_nodes:
            node_id = stable_id(
                "REPORTOUTLINENODE",
                outline_version_id,
                node["node_code"],
                node["source_type"],
                node["source_object_id"],
            )
            temp_to_id[node["temp_id"]] = node_id
            conn.execute(
                """
                INSERT INTO report_outline_node (
                  report_outline_node_id,outline_version_id,parent_node_id,node_code,
                  heading_level,title,node_kind,source_type,source_object_id,ordinal,
                  applicability_status,metadata_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    node_id,
                    outline_version_id,
                    temp_to_id.get(node["parent_temp_id"]),
                    node["node_code"],
                    node["heading_level"],
                    node["title"],
                    node["node_kind"],
                    node["source_type"],
                    node["source_object_id"],
                    node["ordinal"],
                    node["applicability_status"],
                    dump_json(node["metadata"]),
                    timestamp,
                ),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM report_outline_version WHERE outline_version_id=?",
            (outline_version_id,),
        ).fetchone()
        payload = _load_outline(conn, row)
    payload.update(
        {
            "project_code": project_code,
            "confirmation_status": "pending_confirmation",
            "is_current": True,
        }
    )
    return payload


def _validate_confirmation_payload(
    candidate: dict[str, Any], edited_payload: dict[str, Any] | None
) -> list[dict[str, Any]]:
    if edited_payload is None:
        return candidate["nodes"]
    if edited_payload.get("outline_version_id") != candidate["outline_version_id"]:
        raise ValueError("edited outline does not target the current candidate version")
    if edited_payload.get("source_signature") != candidate["source_signature"]:
        raise ValueError("edited outline source_signature does not match the current plans")
    original = {node["report_outline_node_id"]: node for node in candidate["nodes"]}
    edited = edited_payload.get("nodes") or []
    if {node.get("report_outline_node_id") for node in edited} != set(original):
        raise ValueError("confirmed outline must carry every current candidate node exactly once")
    if [node.get("report_outline_node_id") for node in edited] != list(original):
        raise ValueError(
            "confirmed outline cannot reorder source-bound nodes; change the composition plan first"
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ordinal, incoming in enumerate(edited, start=1):
        node_id = incoming["report_outline_node_id"]
        base = original[node_id]
        for immutable in (
            "parent_node_id",
            "node_code",
            "heading_level",
            "node_kind",
            "source_type",
            "source_object_id",
            "applicability_status",
        ):
            if incoming.get(immutable) != base.get(immutable):
                raise ValueError(f"confirmed outline cannot change {immutable}: {base['node_code']}")
        if base["parent_node_id"] and base["parent_node_id"] not in seen:
            raise ValueError(f"parent node must appear before child node: {base['node_code']}")
        title = str(incoming.get("title", "")).strip()
        if not title:
            raise ValueError(f"outline title cannot be empty: {base['node_code']}")
        item = dict(base)
        item["title"] = title
        item["ordinal"] = ordinal
        result.append(item)
        seen.add(node_id)
    return result


def confirm_outline(
    database: Path,
    project_code: str,
    *,
    outline_version_id: str | None = None,
    confirmed_by: str,
    confirmed_at: str | None = None,
    confirmation_note: str = "",
    edited_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    confirmed_by = confirmed_by.strip()
    if not confirmed_by:
        raise ValueError("confirmed_by is required")
    candidate = build_outline_candidate(database, project_code)
    if outline_version_id and candidate["outline_version_id"] != outline_version_id:
        raise RuntimeError(
            "reviewed outline version is no longer current; rebuild and review the new candidate"
        )
    if candidate["status"] == "confirmed":
        return candidate
    confirmed_at = confirmed_at or now_iso()
    nodes = _validate_confirmation_payload(candidate, edited_payload)
    outline_hash = _outline_hash(nodes)
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        current = conn.execute(
            "SELECT * FROM report_outline_version WHERE outline_version_id=? AND status='candidate'",
            (candidate["outline_version_id"],),
        ).fetchone()
        if current is None or current["source_signature"] != candidate["source_signature"]:
            raise RuntimeError("outline candidate changed before confirmation; rebuild and review it again")
        conn.execute(
            "UPDATE report_outline_version SET status='superseded' WHERE project_id=? AND status='confirmed'",
            (project["project_id"],),
        )
        conn.execute(
            "DELETE FROM report_outline_node WHERE outline_version_id=?",
            (candidate["outline_version_id"],),
        )
        inserted: set[str] = set()
        for node in nodes:
            parent_id = node["parent_node_id"]
            if parent_id and parent_id not in inserted:
                raise ValueError(f"parent node must appear before child node: {node['node_code']}")
            conn.execute(
                """
                INSERT INTO report_outline_node (
                  report_outline_node_id,outline_version_id,parent_node_id,node_code,
                  heading_level,title,node_kind,source_type,source_object_id,ordinal,
                  applicability_status,metadata_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    node["report_outline_node_id"],
                    candidate["outline_version_id"],
                    parent_id,
                    node["node_code"],
                    node["heading_level"],
                    node["title"],
                    node["node_kind"],
                    node["source_type"],
                    node["source_object_id"],
                    node["ordinal"],
                    node["applicability_status"],
                    dump_json(node.get("metadata", {})),
                    node.get("created_at") or candidate["created_at"],
                ),
            )
            inserted.add(node["report_outline_node_id"])
        conn.execute(
            """
            UPDATE report_outline_version
            SET status='confirmed',outline_hash=?,confirmed_by=?,confirmed_at=?,confirmation_note=?
            WHERE outline_version_id=?
            """,
            (
                outline_hash,
                confirmed_by,
                confirmed_at,
                confirmation_note.strip(),
                candidate["outline_version_id"],
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM report_outline_version WHERE outline_version_id=?",
            (candidate["outline_version_id"],),
        ).fetchone()
        payload = _load_outline(conn, row)
    payload.update(
        {
            "project_code": project_code,
            "confirmation_status": "confirmed",
            "is_current": True,
        }
    )
    return payload


def outline_to_markdown(payload: dict[str, Any]) -> str:
    status_label = "已确认" if payload["status"] == "confirmed" else "待确认候选"
    lines = [
        f"> 目录版本：V{payload['version_no']}｜状态：{status_label}",
        f"> 来源签名：{payload['source_signature']}",
        f"> 目录哈希：{payload['outline_hash']}",
        "",
    ]
    if payload["status"] != "confirmed":
        lines.extend(["> 本文件用于审阅；必须通过确认脚本冻结后才能作为正式组装依据。", ""])
    for node in payload["nodes"]:
        level = int(node["heading_level"])
        code = node["node_code"]
        if node["node_kind"] == "chapter":
            text = f"第{code}章 {node['title']}"
        else:
            text = f"{code} {node['title']}"
        lines.extend([f"{'#' * level} {text}", ""])
    return "\n".join(lines).rstrip() + "\n"


def write_outline_outputs(
    payload: dict[str, Any], *, json_path: Path | None = None, markdown_path: Path | None = None
) -> None:
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(outline_to_markdown(payload), encoding="utf-8")
