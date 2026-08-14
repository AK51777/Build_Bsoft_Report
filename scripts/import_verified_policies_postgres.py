#!/usr/bin/env python3
"""Import verified policy documents and clauses into PostgreSQL."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from knowledge_db import load_json, now_iso, sha256_text, stable_id
from postgres_knowledge_db import add_connection_arguments, apply_migrations, canonical_json, connect, jsonb, validate_schema


IMPORTER_VERSION = "verified-policy-postgres-v1"


def normalize_title(value: str) -> str:
    return re.sub(r"[《》〈〉\s]+", "", value).lower()


def validate_policies(payload: dict[str, Any], publish: bool) -> None:
    policies = payload.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ValueError("verified policy payload must contain policies")
    for policy in policies:
        for field in ("title", "issuer", "official_url"):
            if not str(policy.get(field, "")).strip():
                raise ValueError(f"policy {field} is missing")
        clauses = policy.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            raise ValueError(f"policy clauses are missing: {policy['title']}")
        if publish and policy.get("verification_status") != "verified":
            raise ValueError(f"published policy is not verified: {policy['title']}")
        for clause in clauses:
            if not str(clause.get("original_text", "")).strip():
                raise ValueError(f"policy clause text is missing: {policy['title']}")
            if publish and clause.get("verification_status", policy.get("verification_status")) != "verified":
                raise ValueError(f"published policy clause is not verified: {policy['title']}")


def import_policies(connection, payload: dict[str, Any], *, publish: bool, schema: str) -> dict[str, Any]:
    validate_policies(payload, publish)
    validate_schema(schema)
    migrations = apply_migrations(connection, schema=schema)
    payload_hash = sha256_text(canonical_json(payload))
    release_id = stable_id("POLICYRELEASE", payload.get("seed_version", ""), payload_hash)
    import_run_id = stable_id("PGIMPORT", IMPORTER_VERSION, release_id, payload_hash)
    policy_count = 0
    clause_count = 0
    topic_codes: set[str] = set()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {schema}.import_run (
              import_run_id,target_type,target_id,input_hash,importer_version,run_status
            ) VALUES (%s,'policy_document',%s,%s,%s,'running')
            ON CONFLICT (target_type,target_id,input_hash,importer_version) DO UPDATE SET
              run_status='running',error_details='{{}}'::jsonb,started_at=NOW(),completed_at=NULL
            """,
            (import_run_id, release_id, payload_hash, IMPORTER_VERSION),
        )
        for policy in payload["policies"]:
            policy_id = policy.get("policy_id") or stable_id(
                "POLICY", policy["title"], policy.get("document_no", ""), policy["issuer"]
            )
            source_hash = policy.get("source_hash") or sha256_text(
                canonical_json(
                    {
                        "title": policy["title"],
                        "document_no": policy.get("document_no", ""),
                        "issuer": policy["issuer"],
                        "official_url": policy["official_url"],
                        "clauses": policy["clauses"],
                    }
                )
            )
            source_id = stable_id("POLICYSOURCE", source_hash)
            catalog_identity = stable_id(
                "POLICYIDENTITY", policy["title"], policy.get("document_no", ""), policy["issuer"]
            )
            cursor.execute(
                f"""
                SELECT catalog_entry_id
                FROM {schema}.runtime_policy_catalog_entry
                WHERE identity_key=%s
                ORDER BY source_row DESC LIMIT 1
                """,
                (catalog_identity,),
            )
            catalog_row = cursor.fetchone()
            catalog_entry_id = catalog_row[0] if catalog_row else None
            cursor.execute(
                f"""
                INSERT INTO {schema}.source_document (
                  source_id,source_scope,file_name,file_type,source_uri,source_sha256,
                  permission_scope,verification_status,contains_personal_data,metadata
                ) VALUES (%s,'public_policy',%s,'WEB',%s,%s,'public_policy_reference','verified',FALSE,%s)
                ON CONFLICT (source_id) DO UPDATE SET
                  source_uri=EXCLUDED.source_uri,verification_status=EXCLUDED.verification_status,
                  metadata=EXCLUDED.metadata,updated_at=NOW()
                """,
                (
                    source_id,
                    policy["title"],
                    policy["official_url"],
                    source_hash,
                    jsonb({"release_id": release_id}),
                ),
            )
            publication_status = "published" if publish else "draft"
            official_domain = policy.get("official_domain") or urlparse(policy["official_url"]).netloc
            retrieved_at = policy.get("retrieved_at") or now_iso()
            last_verified_at = policy.get("last_verified_at") or retrieved_at
            cursor.execute(
                f"""
                INSERT INTO {schema}.policy_document (
                  policy_id,catalog_entry_id,official_source_id,title,normalized_title,document_no,
                  issuer,authority_group,authority_rank,jurisdiction_level,jurisdiction_code,
                  jurisdiction_name,policy_type,publish_date,effective_date,expiry_date,
                  validity_status,official_url,official_domain,source_hash,verification_status,
                  publication_status,permission_scope,retrieved_at,last_verified_at,notes,metadata
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (policy_id) DO UPDATE SET
                  catalog_entry_id=EXCLUDED.catalog_entry_id,official_source_id=EXCLUDED.official_source_id,
                  title=EXCLUDED.title,normalized_title=EXCLUDED.normalized_title,
                  document_no=EXCLUDED.document_no,issuer=EXCLUDED.issuer,
                  authority_group=EXCLUDED.authority_group,authority_rank=EXCLUDED.authority_rank,
                  jurisdiction_level=EXCLUDED.jurisdiction_level,
                  jurisdiction_code=EXCLUDED.jurisdiction_code,
                  jurisdiction_name=EXCLUDED.jurisdiction_name,policy_type=EXCLUDED.policy_type,
                  publish_date=EXCLUDED.publish_date,effective_date=EXCLUDED.effective_date,
                  expiry_date=EXCLUDED.expiry_date,validity_status=EXCLUDED.validity_status,
                  official_url=EXCLUDED.official_url,official_domain=EXCLUDED.official_domain,
                  source_hash=EXCLUDED.source_hash,verification_status=EXCLUDED.verification_status,
                  publication_status=EXCLUDED.publication_status,
                  permission_scope=EXCLUDED.permission_scope,retrieved_at=EXCLUDED.retrieved_at,
                  last_verified_at=EXCLUDED.last_verified_at,notes=EXCLUDED.notes,
                  metadata=EXCLUDED.metadata,updated_at=NOW()
                """,
                (
                    policy_id,
                    catalog_entry_id,
                    source_id,
                    policy["title"],
                    normalize_title(policy["title"]),
                    policy.get("document_no", ""),
                    policy["issuer"],
                    int(policy.get("authority_group", 70)),
                    int(policy.get("authority_rank", 99)),
                    policy.get("jurisdiction_level", "other"),
                    policy.get("jurisdiction_code", ""),
                    policy.get("jurisdiction_name", ""),
                    policy.get("policy_type", "policy"),
                    policy.get("publish_date") or None,
                    policy.get("effective_date") or None,
                    policy.get("expiry_date") or None,
                    policy.get("validity_status", "needs_verification"),
                    policy["official_url"],
                    official_domain,
                    source_hash,
                    policy.get("verification_status", "unverified"),
                    publication_status,
                    policy.get("permission_scope", "public_policy_reference"),
                    retrieved_at,
                    last_verified_at,
                    policy.get("notes", ""),
                    jsonb({"release_id": release_id}),
                ),
            )
            cursor.execute(f"DELETE FROM {schema}.policy_clause WHERE policy_id=%s", (policy_id,))
            for clause_order, clause in enumerate(policy["clauses"], 1):
                original_text = clause["original_text"]
                clause_id = clause.get("clause_id") or stable_id(
                    "CLAUSE", policy_id, clause.get("article_path", ""), original_text
                )
                requirement_type = clause.get("requirement_type", "guiding")
                forbidden_claims = clause.get("forbidden_claims", [])
                if not forbidden_claims and requirement_type != "mandatory":
                    forbidden_claims = ["不得改写为强制要求", "不得写成项目已经达标或通过验收"]
                cursor.execute(
                    f"""
                    INSERT INTO {schema}.policy_clause (
                      clause_id,policy_id,clause_order,article_path,source_location,original_text,
                      normalized_summary,target_objects,requirement_type,applicability_notes,
                      permitted_sections,forbidden_claims,verification_status,review_status,text_hash,metadata
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        clause_id,
                        policy_id,
                        clause_order,
                        clause.get("article_path", ""),
                        clause.get("source_location", clause.get("article_path", "")),
                        original_text,
                        clause.get("normalized_summary", ""),
                        jsonb(clause.get("target_objects", [])),
                        requirement_type,
                        clause.get("applicability_notes", ""),
                        jsonb(clause.get("permitted_sections", ["basis", "policy_background"])),
                        jsonb(forbidden_claims),
                        clause.get("verification_status", policy.get("verification_status", "unverified")),
                        "approved" if publish else "pending",
                        sha256_text(original_text),
                        jsonb({}),
                    ),
                )
                for topic_code in clause.get("topic_tags", []):
                    topic_codes.add(topic_code)
                    topic_id = stable_id("POLICYTOPIC", topic_code)
                    cursor.execute(
                        f"""
                        INSERT INTO {schema}.policy_topic(topic_id,topic_code,topic_name)
                        VALUES (%s,%s,%s)
                        ON CONFLICT (topic_code) DO UPDATE SET topic_name=EXCLUDED.topic_name,status='active'
                        """,
                        (topic_id, topic_code, topic_code),
                    )
                    cursor.execute(
                        f"""
                        INSERT INTO {schema}.policy_clause_topic(clause_id,topic_id)
                        VALUES (%s,%s) ON CONFLICT DO NOTHING
                        """,
                        (clause_id, topic_id),
                    )
                clause_count += 1
            cursor.execute(
                f"""
                INSERT INTO {schema}.policy_verification_event (
                  verification_id,policy_id,catalog_entry_id,checked_url,checked_at,
                  check_method,result,previous_status,current_status,evidence_hash,notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'',%s,%s,%s)
                ON CONFLICT (verification_id) DO NOTHING
                """,
                (
                    stable_id("PVERIFY", policy_id, last_verified_at, policy["official_url"]),
                    policy_id,
                    catalog_entry_id,
                    policy["official_url"],
                    last_verified_at,
                    policy.get("check_method", "official_web_manual"),
                    policy.get("verification_result", policy.get("verification_status", "unverified")),
                    policy.get("validity_status", "needs_verification"),
                    source_hash,
                    policy.get("notes", ""),
                ),
            )
            policy_count += 1
        cursor.execute(
            f"""
            UPDATE {schema}.import_run
            SET run_status='completed',row_counts=%s,completed_at=NOW()
            WHERE import_run_id=%s
            """,
            (
                jsonb(
                    {
                        "policies": policy_count,
                        "clauses": clause_count,
                        "topics": len(topic_codes),
                    }
                ),
                import_run_id,
            ),
        )
    connection.commit()
    return {
        "release_id": release_id,
        "publication_status": "published" if publish else "draft",
        "policies_imported": policy_count,
        "clauses_imported": clause_count,
        "topics_imported": len(topic_codes),
        "import_run_id": import_run_id,
        "applied_migrations": migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policies", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--output", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    payload = load_json(args.policies)
    with connect(args) as connection:
        result = import_policies(connection, payload, publish=args.publish, schema=args.schema)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
