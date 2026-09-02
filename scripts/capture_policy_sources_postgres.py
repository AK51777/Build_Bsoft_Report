#!/usr/bin/env python3
"""Capture policy originals into an unverified PostgreSQL staging layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from knowledge_db import stable_id
from postgres_knowledge_db import (
    add_connection_arguments,
    apply_migrations,
    connect,
    jsonb,
    validate_schema,
)


CAPTURE_VERSION = "policy-source-capture-v1"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
)
TEXT_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml"}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
    "pre", "section", "table", "td", "th", "tr", "ul",
}
SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
REFERENCE_PATTERN = re.compile(r"专家共识|会议|谈话|解读|新闻|答记者问")
DRAFT_PATTERN = re.compile(r"征求意见|内部材料|不予公开|草案|送审稿")
DIRECT_OPENER = build_opener(ProxyHandler({}))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_space(value: str) -> str:
    return re.sub(r"[\t\r ]+", " ", re.sub(r"\n{3,}", "\n\n", value or "")).strip()


def normalize_identity(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", (value or "").casefold())


def normalize_source_url(value: str) -> str:
    url = str(value or "").strip()
    if not url or url.startswith("("):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    elif not re.match(r"^https?://", url, re.IGNORECASE):
        if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}/", url, re.IGNORECASE):
            url = "https://" + url
        else:
            return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url


def classify_entry(entry: dict[str, Any]) -> str:
    combined = " ".join(
        str(entry.get(key) or "")
        for key in ("title", "notes", "authority_level_label", "catalog_group_code")
    )
    if DRAFT_PATTERN.search(combined):
        return "draft_or_internal"
    if str(entry.get("catalog_group_code") or "").upper() == "CNX":
        return "reference_only"
    if str(entry.get("authority_level_label") or "").strip() == "共识":
        return "reference_only"
    if REFERENCE_PATTERN.search(combined) and not str(entry.get("document_no") or "").strip():
        return "reference_only"
    return "formal_candidate"


class VisibleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href.strip())

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if not self.skip_depth and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data)


def decode_body(raw: bytes, content_type_header: str) -> tuple[str, str]:
    charset_match = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type_header, re.I)
    candidates = [charset_match.group(1) if charset_match else "", "utf-8", "gb18030"]
    for charset in dict.fromkeys(item.casefold() for item in candidates if item):
        try:
            return raw.decode(charset), charset
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def extract_html(raw: bytes, final_url: str, content_type_header: str) -> tuple[str, list[str], str]:
    decoded, charset = decode_body(raw, content_type_header)
    parser = VisibleTextExtractor()
    parser.feed(decoded)
    text = normalize_space("".join(parser.parts))
    attachments: list[str] = []
    for href in parser.links:
        absolute = urljoin(final_url, href)
        path = urlparse(absolute).path.casefold()
        if path.endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")):
            attachments.append(absolute)
    return text, list(dict.fromkeys(attachments)), charset


def identity_status(entry: dict[str, Any], text: str) -> str:
    if not text:
        return "not_checked"
    normalized_text = normalize_identity(text)
    title = normalize_identity(str(entry.get("title") or ""))
    document_no = normalize_identity(str(entry.get("document_no") or ""))
    title_match = bool(title and title in normalized_text)
    number_match = bool(document_no and document_no in normalized_text)
    if title_match and (not document_no or number_match):
        return "matched"
    if title_match or number_match:
        return "partial"
    return "mismatch" if len(text) >= 200 else "not_checked"


class HostRateLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval = max(0.0, interval_seconds)
        self._lock = threading.Lock()
        self._last_request: dict[str, float] = {}

    def wait(self, host: str) -> None:
        if not self.interval:
            return
        with self._lock:
            delay = self.interval - (time.monotonic() - self._last_request.get(host, 0.0))
            if delay > 0:
                time.sleep(delay)
            self._last_request[host] = time.monotonic()


def _content_type(headers: Any) -> str:
    value = str(headers.get("Content-Type", "") if headers else "")
    return value.split(";", 1)[0].strip().casefold()


def _archive_raw(
    archive_dir: Path | None,
    entry: dict[str, Any],
    raw: bytes,
    raw_hash: str,
    content_type: str,
    final_url: str,
) -> str:
    if not archive_dir or not raw:
        return ""
    extension = Path(urlparse(final_url).path).suffix.casefold()
    if not extension or len(extension) > 8:
        extension = mimetypes.guess_extension(content_type) or ".bin"
    target = archive_dir / str(entry["catalog_entry_id"]) / f"{raw_hash}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(raw)
    return target.resolve().as_uri()


def _base_capture(entry: dict[str, Any], requested_url: str) -> dict[str, Any]:
    return {
        "catalog_entry_id": entry["catalog_entry_id"],
        "requested_url": requested_url,
        "final_url": "",
        "source_domain": urlparse(requested_url).hostname or "",
        "source_classification": classify_entry(entry),
        "retrieval_status": "network_error",
        "content_readiness": "not_ready",
        "identity_status": "not_checked",
        "http_status": None,
        "content_type": "",
        "content_charset": "",
        "raw_size_bytes": 0,
        "raw_sha256": "",
        "extracted_text": "",
        "extracted_text_sha256": "",
        "extraction_method": "",
        "attachment_urls": [],
        "raw_storage_uri": "",
        "fetched_at": now_iso(),
        "error_code": "",
        "error_message": "",
        "verification_status": "unverified",
        "review_status": "pending",
        "metadata": {"capture_version": CAPTURE_VERSION},
    }


def capture_entry(
    entry: dict[str, Any],
    *,
    timeout: float,
    retries: int,
    max_bytes: int,
    limiter: HostRateLimiter,
    archive_dir: Path | None = None,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    raw_url = str(entry.get("external_url") or "").strip()
    requested_url = normalize_source_url(raw_url)
    result = _base_capture(entry, requested_url)
    if not raw_url:
        result.update(
            retrieval_status="missing_url",
            content_readiness="manual_source_required",
            error_code="missing_url",
        )
    elif not requested_url:
        result.update(
            retrieval_status="invalid_url",
            content_readiness="manual_source_required",
            error_code="invalid_url",
            error_message=raw_url[:500],
        )
    else:
        host = urlparse(requested_url).hostname or ""
        request = Request(
            requested_url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            },
        )
        for attempt in range(retries + 1):
            limiter.wait(host)
            try:
                transport = opener or DIRECT_OPENER.open
                with transport(request, timeout=timeout) as response:
                    status = int(getattr(response, "status", None) or response.getcode())
                    final_url = str(response.geturl())
                    headers = response.headers
                    raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    result.update(
                        final_url=final_url,
                        http_status=status,
                        content_type=_content_type(headers),
                        retrieval_status="too_large",
                        content_readiness="not_ready",
                        error_code="max_bytes_exceeded",
                        error_message=f"response exceeded {max_bytes} bytes",
                    )
                    break
                content_type_header = str(headers.get("Content-Type", ""))
                content_type = _content_type(headers)
                raw_hash = sha256_bytes(raw)
                extracted_text = ""
                attachments: list[str] = []
                charset = ""
                extraction_method = ""
                if content_type in TEXT_CONTENT_TYPES or raw.lstrip().startswith((b"<", b"<!")):
                    extracted_text, attachments, charset = extract_html(
                        raw, final_url, content_type_header
                    )
                    retrieval_status = "fetched_html"
                    readiness = "text_ready" if len(extracted_text) >= 200 else "not_ready"
                    extraction_method = "html_visible_text_v1"
                elif content_type == "application/pdf" or raw.startswith(b"%PDF"):
                    retrieval_status = "fetched_pdf"
                    readiness = "binary_pending_extraction"
                    extraction_method = "raw_pdf_capture_v1"
                else:
                    retrieval_status = "fetched_binary"
                    readiness = "binary_pending_extraction"
                    extraction_method = "raw_binary_capture_v1"
                final_domain = urlparse(final_url).hostname or host
                text_hash = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest() if extracted_text else ""
                result.update(
                    final_url=final_url,
                    source_domain=final_domain.casefold(),
                    retrieval_status=retrieval_status,
                    content_readiness=readiness,
                    identity_status=identity_status(entry, extracted_text),
                    http_status=status,
                    content_type=content_type,
                    content_charset=charset,
                    raw_size_bytes=len(raw),
                    raw_sha256=raw_hash,
                    extracted_text=extracted_text,
                    extracted_text_sha256=text_hash,
                    extraction_method=extraction_method,
                    attachment_urls=attachments,
                    raw_storage_uri=_archive_raw(
                        archive_dir, entry, raw, raw_hash, content_type, final_url
                    ),
                )
                if readiness == "not_ready":
                    result.update(
                        error_code="extracted_text_too_short",
                        error_message=f"extracted text length={len(extracted_text)}",
                    )
                break
            except HTTPError as exc:
                status = int(exc.code)
                retryable = status == 429 or status >= 500
                if retryable and attempt < retries:
                    time.sleep(min(8.0, 2.0 ** attempt))
                    continue
                result.update(
                    final_url=str(exc.geturl() or requested_url),
                    source_domain=(urlparse(str(exc.geturl() or requested_url)).hostname or host).casefold(),
                    http_status=status,
                    content_type=_content_type(exc.headers),
                    retrieval_status="http_error",
                    content_readiness=(
                        "url_revalidation_required" if status in {403, 404, 410, 412} else "not_ready"
                    ),
                    error_code=f"http_{status}",
                    error_message=str(exc)[:1000],
                )
                break
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < retries:
                    time.sleep(min(8.0, 2.0 ** attempt))
                    continue
                result.update(
                    retrieval_status="network_error",
                    content_readiness="not_ready",
                    error_code=exc.__class__.__name__.casefold(),
                    error_message=str(exc)[:1000],
                )
                break
            except Exception as exc:  # defensive parser boundary; persisted for review
                result.update(
                    retrieval_status="parse_error",
                    content_readiness="not_ready",
                    error_code=exc.__class__.__name__.casefold(),
                    error_message=str(exc)[:1000],
                )
                break
    capture_identity = result["raw_sha256"] or result["error_code"] or result["retrieval_status"]
    result["capture_id"] = stable_id(
        "POLICYCAPTURE", result["catalog_entry_id"], result["requested_url"], capture_identity
    )
    return result


def load_entries(
    connection,
    *,
    schema: str,
    catalog_id: str,
    start_after_source_row: int,
    limit: int,
    refresh: bool,
) -> list[dict[str, Any]]:
    validate_schema(schema)
    where = ["catalog.catalog_status='published'", "entry.entry_status='active'", "entry.source_row>%s"]
    params: list[Any] = [start_after_source_row]
    if catalog_id:
        where.append("entry.catalog_id=%s")
        params.append(catalog_id)
    if not refresh:
        where.append("COALESCE(latest.content_readiness,'')<>'text_ready'")
    limit_sql = ""
    if limit > 0:
        limit_sql = " LIMIT %s"
        params.append(limit)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT entry.catalog_entry_id,entry.catalog_id,entry.source_row,entry.source_index_no,
                   entry.catalog_group_code,entry.authority_level_label,entry.title,entry.document_no,
                   entry.issuer,entry.notes,entry.external_url
            FROM {schema}.policy_catalog_entry AS entry
            JOIN {schema}.policy_catalog AS catalog ON catalog.catalog_id=entry.catalog_id
            LEFT JOIN {schema}.review_policy_source_capture_latest AS latest
              ON latest.catalog_entry_id=entry.catalog_entry_id
            WHERE {' AND '.join(where)}
            ORDER BY entry.source_row,entry.catalog_entry_id{limit_sql}
            """,
            params,
        )
        columns = [item.name for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def persist_capture(connection, capture: dict[str, Any], *, schema: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {schema}.policy_source_capture (
              capture_id,catalog_entry_id,requested_url,final_url,source_domain,
              source_classification,retrieval_status,content_readiness,identity_status,http_status,
              content_type,content_charset,raw_size_bytes,raw_sha256,extracted_text,
              extracted_text_sha256,extraction_method,attachment_urls,raw_storage_uri,fetched_at,
              error_code,error_message,verification_status,review_status,metadata
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
              %s,%s,%s,%s,%s
            )
            ON CONFLICT (capture_id) DO UPDATE SET
              final_url=EXCLUDED.final_url,source_domain=EXCLUDED.source_domain,
              source_classification=EXCLUDED.source_classification,
              retrieval_status=EXCLUDED.retrieval_status,
              content_readiness=EXCLUDED.content_readiness,identity_status=EXCLUDED.identity_status,
              http_status=EXCLUDED.http_status,content_type=EXCLUDED.content_type,
              content_charset=EXCLUDED.content_charset,raw_size_bytes=EXCLUDED.raw_size_bytes,
              raw_sha256=EXCLUDED.raw_sha256,extracted_text=EXCLUDED.extracted_text,
              extracted_text_sha256=EXCLUDED.extracted_text_sha256,
              extraction_method=EXCLUDED.extraction_method,attachment_urls=EXCLUDED.attachment_urls,
              raw_storage_uri=EXCLUDED.raw_storage_uri,fetched_at=EXCLUDED.fetched_at,
              error_code=EXCLUDED.error_code,error_message=EXCLUDED.error_message,
              metadata=EXCLUDED.metadata,updated_at=NOW()
            """,
            (
                capture["capture_id"], capture["catalog_entry_id"], capture["requested_url"],
                capture["final_url"], capture["source_domain"], capture["source_classification"],
                capture["retrieval_status"], capture["content_readiness"], capture["identity_status"],
                capture["http_status"], capture["content_type"], capture["content_charset"],
                capture["raw_size_bytes"], capture["raw_sha256"], capture["extracted_text"],
                capture["extracted_text_sha256"], capture["extraction_method"],
                jsonb(capture["attachment_urls"]), capture["raw_storage_uri"], capture["fetched_at"],
                capture["error_code"], capture["error_message"], capture["verification_status"],
                capture["review_status"], jsonb(capture["metadata"]),
            ),
        )


def summarize(captures: list[dict[str, Any]]) -> dict[str, Any]:
    def counts(key: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in captures:
            value = str(item.get(key) or "")
            result[value] = result.get(value, 0) + 1
        return dict(sorted(result.items()))

    return {
        "capture_version": CAPTURE_VERSION,
        "processed": len(captures),
        "retrieval_status": counts("retrieval_status"),
        "content_readiness": counts("content_readiness"),
        "identity_status": counts("identity_status"),
        "source_classification": counts("source_classification"),
        "text_ready": sum(item["content_readiness"] == "text_ready" for item in captures),
        "total_raw_bytes": sum(int(item.get("raw_size_bytes") or 0) for item in captures),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-id", default="")
    parser.add_argument("--start-after-source-row", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--per-host-interval", type=float, default=0.8)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024)
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument(
        "--use-system-proxy",
        action="store_true",
        help="inherit HTTP(S)_PROXY settings; direct connections are the safe default",
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database", default="")
    parser.add_argument("--output", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 12:
        raise ValueError("workers must be between 1 and 12")
    if args.apply and args.confirm_database != args.database:
        raise ValueError("--apply requires --confirm-database to exactly match --database")
    validate_schema(args.schema)
    with connect(args) as connection:
        migrations = apply_migrations(connection, schema=args.schema) if args.apply else []
        if not args.apply:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) FROM {args.schema}.policy_catalog_entry AS entry
                    JOIN {args.schema}.policy_catalog AS catalog ON catalog.catalog_id=entry.catalog_id
                    WHERE catalog.catalog_status='published' AND entry.entry_status='active'
                    """
                )
                available = cursor.fetchone()[0]
            result = {
                "mode": "dry_run",
                "available_catalog_entries": available,
                "message": "No network requests or database writes were performed.",
            }
        else:
            entries = load_entries(
                connection,
                schema=args.schema,
                catalog_id=args.catalog_id,
                start_after_source_row=args.start_after_source_row,
                limit=args.limit,
                refresh=args.refresh,
            )
            limiter = HostRateLimiter(args.per_host_interval)
            archive_dir = args.archive_dir.resolve() if args.archive_dir else None
            captures: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {
                    executor.submit(
                        capture_entry,
                        entry,
                        timeout=args.timeout,
                        retries=args.retries,
                        max_bytes=args.max_bytes,
                        limiter=limiter,
                        archive_dir=archive_dir,
                        opener=urlopen if args.use_system_proxy else None,
                    ): entry
                    for entry in entries
                }
                for index, future in enumerate(as_completed(futures), 1):
                    capture = future.result()
                    persist_capture(connection, capture, schema=args.schema)
                    captures.append(capture)
                    if index % 25 == 0:
                        connection.commit()
            connection.commit()
            result = {
                "mode": "applied",
                "catalog_id": args.catalog_id or "published",
                "applied_migrations": migrations,
                **summarize(captures),
            }
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
