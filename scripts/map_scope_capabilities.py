#!/usr/bin/env python3
"""Create non-authoritative candidate mappings from scope items to capabilities."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from knowledge_db import apply_migrations, connect, now_iso, stable_id


def normalized_text(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).casefold()


def grams(value: str) -> set[str]:
    text = normalized_text(value)
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def similarity(scope_name: str, capability_name: str, description: str) -> float:
    scope_text = normalized_text(scope_name)
    capability_text = normalized_text(capability_name)
    if not scope_text or not capability_text:
        return 0.0
    if scope_text == capability_text:
        return 1.0
    if scope_text in capability_text or capability_text in scope_text:
        return 0.85
    scope_grams = grams(scope_name)
    capability_grams = grams(f"{capability_name}{description}")
    if not scope_grams:
        return 0.0
    return round(len(scope_grams & capability_grams) / len(scope_grams), 4)


def canonical_variants(value: str) -> set[str]:
    normalized = normalized_text(value)
    variants = {normalized} if normalized else set()
    substitutions = (
        ("基于cdr的医院临床应用", "基于平台的医院临床应用"),
        ("医院决策支持系统bi", "基于平台的管理决策辅助应用"),
        ("电子病历管理", "电子病历"),
    )
    for source, target in substitutions:
        if normalized == normalized_text(source):
            variants.add(normalized_text(target))
    return variants


def hierarchical_candidates(scope_name: str, capabilities) -> list[tuple[float, object, str]]:
    scope_variants = canonical_variants(scope_name)
    if not scope_variants:
        return []

    def normalized_fields(capability) -> tuple[str, str, str]:
        return (
            normalized_text(capability["product_name"]),
            normalized_text(capability["module_name"]),
            normalized_text(capability["capability_name"]),
        )

    exact_capability = [
        (1.0, capability, "exact_capability")
        for capability in capabilities
        if normalized_fields(capability)[2] in scope_variants
    ]
    if exact_capability:
        return exact_capability
    exact_module = [
        (0.98, capability, "exact_module")
        for capability in capabilities
        if normalized_fields(capability)[1] in scope_variants
    ]
    if exact_module:
        return exact_module
    exact_product = [
        (0.95, capability, "exact_product")
        for capability in capabilities
        if normalized_fields(capability)[0] in scope_variants
    ]
    if exact_product:
        return exact_product

    ranked = []
    for capability in capabilities:
        product, module, capability_name = normalized_fields(capability)
        alias_match = any(
            variant and any(
                variant in candidate or candidate in variant
                for candidate in (product, module, capability_name)
                if candidate
            )
            for variant in scope_variants
        )
        score = max(
            similarity(scope_name, capability["capability_name"], capability["capability_description"]),
            similarity(scope_name, capability["product_name"], capability["capability_description"]),
        )
        if alias_match:
            score = max(score, 0.9)
        ranked.append((score, capability, "alias_or_similarity"))
    ranked.sort(key=lambda item: (-item[0], item[1]["capability_id"]))
    return ranked


def map_capabilities(
    database: Path,
    project_code: str,
    *,
    threshold: float = 0.55,
    max_candidates: int = 3,
) -> dict:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        scopes = conn.execute(
            """
            SELECT scope_id,standard_name,investment_category FROM project_scope_item
            WHERE project_id=? AND customer_scope=1 AND status<>'not_applicable'
            ORDER BY scope_id
            """,
            (project["project_id"],),
        ).fetchall()
        capabilities = conn.execute(
            """
            SELECT capability_id,product_code,product_name,capability_name,
                   capability_description,category,module_name,selection_rules_json
            FROM product_capability WHERE review_status='approved'
            ORDER BY capability_id
            """
        ).fetchall()
        created = 0
        updated = 0
        unmapped: list[dict[str, str]] = []
        candidates_output: list[dict[str, object]] = []
        selected_map_ids: set[str] = set()
        for scope in scopes:
            if scope["investment_category"] == "implementation_service":
                unmapped.append(
                    {
                        "scope_id": scope["scope_id"],
                        "standard_name": scope["standard_name"],
                        "reason": "implementation_service_not_product_capability",
                    }
                )
                continue
            ranked = hierarchical_candidates(scope["standard_name"], capabilities)
            exact_basis = ranked[0][2] if ranked else ""
            if exact_basis in {"exact_capability", "exact_module", "exact_product"}:
                selected = ranked
            else:
                selected = [item for item in ranked if item[0] >= threshold][:max_candidates]
            if not selected:
                unmapped.append(
                    {"scope_id": scope["scope_id"], "standard_name": scope["standard_name"]}
                )
                continue
            mapping_type = "one_to_one" if len(selected) == 1 and selected[0][0] == 1 else (
                "one_to_many" if len(selected) > 1 else "partial"
            )
            for score, capability, match_basis in selected:
                map_id = stable_id(
                    "SCOPEMAP", scope["scope_id"], capability["capability_id"]
                )
                selected_map_ids.add(map_id)
                exists = conn.execute(
                    "SELECT 1 FROM scope_product_map WHERE map_id=?", (map_id,)
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO scope_product_map (
                      map_id, project_id, scope_id, capability_id, mapping_type,
                      coverage_note, status, confidence, review_note, reviewed_by,
                      reviewed_at, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(map_id) DO UPDATE SET
                      mapping_type=CASE WHEN scope_product_map.status='candidate'
                        THEN excluded.mapping_type ELSE scope_product_map.mapping_type END,
                      confidence=CASE WHEN scope_product_map.status='candidate'
                        THEN excluded.confidence ELSE scope_product_map.confidence END,
                      coverage_note=CASE WHEN scope_product_map.status='candidate'
                        THEN excluded.coverage_note ELSE scope_product_map.coverage_note END,
                      updated_at=excluded.updated_at
                    """,
                    (
                        map_id,
                        project["project_id"],
                        scope["scope_id"],
                        capability["capability_id"],
                        mapping_type,
                        f"{match_basis} candidate; company capability never creates project scope.",
                        "candidate",
                        score,
                        "",
                        "",
                        None,
                        timestamp,
                        timestamp,
                    ),
                )
                created += int(exists is None)
                updated += int(exists is not None)
                candidates_output.append(
                    {
                        "map_id": map_id,
                        "scope_id": scope["scope_id"],
                        "capability_id": capability["capability_id"],
                        "mapping_type": mapping_type,
                        "confidence": score,
                        "match_basis": match_basis,
                        "status": "candidate",
                    }
                )
        stale_candidates = [
            row["map_id"]
            for row in conn.execute(
                "SELECT map_id FROM scope_product_map WHERE project_id=? AND status='candidate'",
                (project["project_id"],),
            )
            if row["map_id"] not in selected_map_ids
        ]
        if stale_candidates:
            conn.executemany(
                "DELETE FROM scope_product_map WHERE map_id=? AND status='candidate'",
                [(map_id,) for map_id in stale_candidates],
            )
        conn.commit()
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "candidate_mappings_created": created,
        "candidate_mappings_updated": updated,
        "stale_candidates_removed": len(stale_candidates),
        "candidates": candidates_output,
        "unmapped_scope_items": unmapped,
        "boundary_policy": "capabilities-never-create-project-scope",
        "applied_migrations": applied_migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = map_capabilities(
        args.database,
        args.project_code,
        threshold=args.threshold,
        max_candidates=args.max_candidates,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
