#!/usr/bin/env python3
"""Import verified policy documents and relevant clauses into the knowledge base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from knowledge_db import (
    apply_migrations,
    connect,
    dump_json,
    load_json,
    now_iso,
    sha256_text,
    stable_id,
)


def ingest(database: Path, payload: dict) -> dict:
    policy_count = 0
    clause_count = 0
    with connect(database) as conn:
        apply_migrations(conn)
        has_clause_fts = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy_clause_fts'"
            ).fetchone()
        )
        for policy in payload.get("policies", []):
            policy_id = policy.get("policy_id") or stable_id(
                "POLICY", policy.get("title"), policy.get("document_no"), policy.get("issuer")
            )
            official_url = policy.get("official_url", "")
            domain = policy.get("official_domain") or urlparse(official_url).netloc
            retrieved_at = policy.get("retrieved_at") or now_iso()
            source_hash = policy.get("source_hash") or sha256_text(
                json.dumps(
                    {
                        "title": policy.get("title", ""),
                        "document_no": policy.get("document_no", ""),
                        "issuer": policy.get("issuer", ""),
                        "official_url": official_url,
                        "clauses": policy.get("clauses", []),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            conn.execute(
                """
                INSERT INTO policy_document (
                  policy_id,title,document_no,issuer,authority_group,authority_rank,
                  jurisdiction_level,jurisdiction_code,jurisdiction_name,policy_type,
                  publish_date,effective_date,expiry_date,validity_status,official_url,
                  official_domain,source_hash,retrieved_at,last_verified_at,
                  verification_status,source_document_id,notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(policy_id) DO UPDATE SET
                  title=excluded.title,
                  document_no=excluded.document_no,
                  issuer=excluded.issuer,
                  authority_group=excluded.authority_group,
                  authority_rank=excluded.authority_rank,
                  jurisdiction_level=excluded.jurisdiction_level,
                  jurisdiction_code=excluded.jurisdiction_code,
                  jurisdiction_name=excluded.jurisdiction_name,
                  policy_type=excluded.policy_type,
                  publish_date=excluded.publish_date,
                  effective_date=excluded.effective_date,
                  expiry_date=excluded.expiry_date,
                  validity_status=excluded.validity_status,
                  official_url=excluded.official_url,
                  official_domain=excluded.official_domain,
                  source_hash=excluded.source_hash,
                  retrieved_at=excluded.retrieved_at,
                  last_verified_at=excluded.last_verified_at,
                  verification_status=excluded.verification_status,
                  notes=excluded.notes
                """,
                (
                    policy_id,
                    policy["title"],
                    policy.get("document_no", ""),
                    policy.get("issuer", ""),
                    int(policy.get("authority_group", 70)),
                    int(policy.get("authority_rank", 99)),
                    policy.get("jurisdiction_level", "other"),
                    policy.get("jurisdiction_code", ""),
                    policy.get("jurisdiction_name", ""),
                    policy.get("policy_type", "policy"),
                    policy.get("publish_date", ""),
                    policy.get("effective_date", ""),
                    policy.get("expiry_date", ""),
                    policy.get("validity_status", "needs_verification"),
                    official_url,
                    domain,
                    source_hash,
                    retrieved_at,
                    policy.get("last_verified_at", retrieved_at),
                    policy.get("verification_status", "unverified"),
                    policy.get("source_document_id"),
                    policy.get("notes", ""),
                ),
            )
            policy_count += 1
            for clause in policy.get("clauses", []):
                original = clause.get("original_text", "")
                article_path = clause.get("article_path", "")
                clause_id = clause.get("clause_id") or stable_id(
                    "CLAUSE", policy_id, article_path, original
                )
                text_hash = sha256_text(original)
                conn.execute(
                    """
                    INSERT INTO policy_clause (
                      clause_id,policy_id,article_path,original_text,normalized_summary,
                      topic_tags_json,target_objects_json,requirement_type,
                      applicability_notes,permitted_sections_json,forbidden_claims_json,
                      text_hash,verification_status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(clause_id) DO UPDATE SET
                      article_path=excluded.article_path,
                      original_text=excluded.original_text,
                      normalized_summary=excluded.normalized_summary,
                      topic_tags_json=excluded.topic_tags_json,
                      target_objects_json=excluded.target_objects_json,
                      requirement_type=excluded.requirement_type,
                      applicability_notes=excluded.applicability_notes,
                      permitted_sections_json=excluded.permitted_sections_json,
                      forbidden_claims_json=excluded.forbidden_claims_json,
                      text_hash=excluded.text_hash,
                      verification_status=excluded.verification_status
                    """,
                    (
                        clause_id,
                        policy_id,
                        article_path,
                        original,
                        clause.get("normalized_summary", ""),
                        dump_json(clause.get("topic_tags", [])),
                        dump_json(clause.get("target_objects", [])),
                        clause.get("requirement_type", "guiding"),
                        clause.get("applicability_notes", ""),
                        dump_json(clause.get("permitted_sections", ["basis", "policy_background"])),
                        dump_json(clause.get("forbidden_claims", [])),
                        text_hash,
                        clause.get("verification_status", policy.get("verification_status", "unverified")),
                    ),
                )
                if has_clause_fts:
                    conn.execute("DELETE FROM policy_clause_fts WHERE clause_id=?", (clause_id,))
                    conn.execute(
                        "INSERT INTO policy_clause_fts(clause_id,policy_id,original_text,normalized_summary,topic_tags) VALUES(?,?,?,?,?)",
                        (
                            clause_id,
                            policy_id,
                            original,
                            clause.get("normalized_summary", ""),
                            " ".join(clause.get("topic_tags", [])),
                        ),
                    )
                clause_count += 1
            conn.execute(
                """
                INSERT OR IGNORE INTO policy_verification_log (
                  verification_id,policy_id,checked_url,checked_at,check_method,
                  result,previous_status,current_status,notes
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("PVERIFY", policy_id, retrieved_at, official_url),
                    policy_id,
                    official_url,
                    retrieved_at,
                    policy.get("check_method", "official_web_manual"),
                    policy.get("verification_result", policy.get("verification_status", "unverified")),
                    "",
                    policy.get("validity_status", "needs_verification"),
                    policy.get("notes", ""),
                ),
            )
        conn.commit()
    return {"policies": policy_count, "clauses": clause_count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = ingest(args.database, load_json(args.input))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
