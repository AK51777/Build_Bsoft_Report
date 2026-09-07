"""Compile explicitly approved clause reviews; never write or publish a database."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from build_policy_source_review_workpack import build_workpack
from import_verified_policies_postgres import normalize_title, validate_policies
from knowledge_db import dump_json, load_json, now_iso, sha256_text, stable_id


def compile_reviews(current: dict, decisions: dict) -> dict:
    if current.get("workpack_type") != "policy_source_review":
        raise ValueError("a source review workpack is required")
    source = current["source"]
    records = current["records"]
    rows = [
        {**r["catalog"], **r["capture"], "catalog_entry_id": r["catalog_entry_id"],
         "source_row": r["source_row"], "source_index_no": r.get("source_index_no", "")}
        for r in records
    ]
    rebuilt = build_workpack({
        "catalog_id": source["catalog_id"], "catalog_status": source["catalog_status"],
        "content_hash": source["catalog_content_hash"],
        "record_count": source["catalog_record_count"],
    }, rows)
    for key in ("review_run_id", "input_signature"):
        if current.get(key) != rebuilt[key] or decisions.get(key) != rebuilt[key]:
            raise ValueError("stale or modified policy review workpack: " + key)
    by_id = {r["catalog_entry_id"]: r for r in records}
    policies, seen = [], set()
    for item in decisions.get("records", []):
        entry_id = item["catalog_entry_id"]
        if entry_id not in by_id or entry_id in seen:
            raise ValueError("unknown or duplicate review entry")
        seen.add(entry_id)
        review = item.get("review", {})
        if review.get("decision", "") not in {"", "pending", "defer", "exclude", "approve"}:
            raise ValueError("unsupported review decision")
        if review.get("decision") != "approve":
            continue
        record = by_id[entry_id]
        capture = record["capture"]
        if (capture.get("source_classification") != "formal_candidate"
                or capture.get("content_readiness") != "text_ready"
                or record["catalog"].get("entry_status") != "active"):
            raise ValueError("nonformal, inactive or unavailable source cannot be approved")
        text = capture.get("extracted_text", "")
        if not text or sha256_text(text) != capture.get("extracted_text_sha256"):
            raise ValueError("full source text hash mismatch; export with --include-extracted-text")
        for key in ("reviewer", "reviewed_at", "verified_title", "verified_issuer",
                    "verified_publish_date", "verified_official_url", "review_notes",
                    "validity_evidence_url"):
            if not str(review.get(key, "")).strip():
                raise ValueError("approved review missing " + key)
        datetime.fromisoformat(review["reviewed_at"])
        datetime.fromisoformat(review["verified_publish_date"])
        if normalize_title(review["verified_title"]) not in normalize_title(text):
            raise ValueError("verified title does not match captured source identity")
        document_no = str(review.get("verified_document_no", ""))
        if document_no and normalize_title(document_no) not in normalize_title(text):
            raise ValueError("verified document number does not match captured source")
        if review.get("validity_status") != "current":
            raise ValueError("only explicitly reviewed current policies can be compiled")
        if not all(review.get(key) is True for key in (
            "identity_confirmed", "official_source_confirmed", "permitted_use_confirmed",
            "validity_confirmed",
        )):
            raise ValueError("identity, official source, permitted use and validity require explicit confirmation")
        official_url = review["verified_official_url"]
        if official_url != capture.get("final_url"):
            raise ValueError("changed official URL requires a fresh source capture")
        for url in (official_url, review["validity_evidence_url"]):
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("invalid public official evidence URL")
        jurisdiction = review.get("jurisdiction", {})
        if (jurisdiction.get("level") not in {"national", "province", "prefecture", "county"}
                or len(str(jurisdiction.get("code", ""))) != 6
                or not str(jurisdiction.get("code", "")).isdigit()
                or not jurisdiction.get("name")):
            raise ValueError("reviewed jurisdiction is required")
        if jurisdiction["level"] == "national" and jurisdiction["code"] != "100000":
            raise ValueError("national jurisdiction code must be 100000")
        clauses, clause_hashes = [], set()
        for clause in review.get("clauses", []):
            original = str(clause.get("original_text", ""))
            if not original.strip() or original not in text:
                raise ValueError("clause is not an exact excerpt from the captured source")
            digest = sha256_text(original)
            if digest in clause_hashes:
                raise ValueError("duplicate reviewed clause")
            clause_hashes.add(digest)
            if not clause.get("article_path") or not clause.get("normalized_summary") or not clause.get("topic_tags"):
                raise ValueError("clause location, summary and topics are required")
            sections = clause.get("permitted_sections", [])
            if not sections or set(sections) - {"basis", "policy_background", "construction", "security", "investment", "necessity"}:
                raise ValueError("explicit supported clause uses are required")
            if clause.get("requirement_type") not in {"mandatory", "guiding", "target", "encouraging", "evaluation", "background"}:
                raise ValueError("reviewed requirement strength is required")
            clauses.append({
                **clause, "clause_id": stable_id("CLAUSE", entry_id, clause["article_path"], original),
                "source_location": clause["article_path"], "text_hash": digest,
                "verification_status": "verified",
                "forbidden_claims": clause.get("forbidden_claims") or
                    ["不得改写为项目已经达标或通过验收", "不得超出原文约束力或扩大建设范围"],
            })
        if not clauses:
            raise ValueError("approved policy must have individually reviewed clauses")
        policies.append({
            "policy_id": stable_id("POLICY", review["verified_title"], review.get("verified_document_no", ""), review["verified_issuer"]),
            "title": review["verified_title"], "document_no": review.get("verified_document_no", ""),
            "issuer": review["verified_issuer"], "official_url": official_url,
            "publish_date": review["verified_publish_date"], "validity_status": "current",
            "effective_date": review.get("effective_date", ""), "expiry_date": review.get("expiry_date", ""),
            "policy_type": review.get("policy_type", "policy"),
            "authority_group": int(review["authority_group"]), "authority_rank": int(review["authority_rank"]),
            "jurisdiction_level": jurisdiction["level"], "jurisdiction_code": jurisdiction["code"],
            "jurisdiction_name": jurisdiction["name"], "source_hash": capture["raw_sha256"],
            "retrieved_at": capture["fetched_at"], "last_verified_at": review["reviewed_at"],
            "verification_status": "verified", "permission_scope": "public_policy_reference",
            "check_method": "controlled_clause_review",
            "notes": dump_json({"catalog_entry_id": entry_id, "capture_id": capture["capture_id"],
                                "review_run_id": current["review_run_id"], "reviewer": review["reviewer"],
                                "validity_evidence_url": review["validity_evidence_url"],
                                "review_notes": review["review_notes"]}),
            "clauses": clauses,
        })
    if not policies:
        raise ValueError("no explicitly approved policy reviews; formal publication remains blocked")
    ids = [p["policy_id"] for p in policies]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate formal policy identities require review")
    payload = {"seed_version": "controlled-review-" + current["review_run_id"],
               "policies": policies, "review_input_signature": current["input_signature"],
               "built_at": now_iso(), "production_write_performed": False,
               "required_next_step": "fresh production conflict preflight and explicit publication approval"}
    validate_policies(payload, publish=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current_workpack", type=Path)
    parser.add_argument("review_decisions", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = compile_reviews(load_json(args.current_workpack), load_json(args.review_decisions))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"policies": len(payload["policies"]), "production_write_performed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
