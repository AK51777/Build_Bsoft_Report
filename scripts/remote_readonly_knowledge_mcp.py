#!/usr/bin/env python3
"""Serve published medical-report knowledge through a read-only HTTP MCP endpoint.

This service is intentionally separate from ``medical_report_mcp_server.py``.
It never accepts project paths or project files and only delegates bounded queries
to the existing PostgreSQL ``runtime_*`` views.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from postgres_knowledge_db import DEFAULT_SCHEMA, connect, validate_schema
from query_postgres_knowledge import query as query_published_knowledge


SERVER_NAME = "medical-report-knowledge-readonly"
SERVER_VERSION = "0.1.0"
LATEST_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    LATEST_PROTOCOL_VERSION,
}
QUERY_KINDS = (
    "status",
    "package",
    "corpus",
    "capability",
    "policy-catalog",
    "policy-clause",
)
PAGE_ENTITIES = (
    "knowledge-package",
    "package-source",
    "corpus-document",
    "corpus-block",
    "capability",
    "capability-block",
    "policy-catalog",
    "policy-catalog-entry",
)


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password_env: str
    schema: str = DEFAULT_SCHEMA
    connect_timeout: int = 10
    statement_timeout_ms: int = 15_000

    def as_namespace(self) -> argparse.Namespace:
        return argparse.Namespace(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password_env=self.password_env,
            schema=self.schema,
            connect_timeout=self.connect_timeout,
        )


@dataclass(frozen=True)
class AuthConfig:
    credential_store: Path
    hash_secret_env: str
    access_token_days: int = 90


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    mcp_path: str
    auth: AuthConfig
    max_query_limit: int
    max_request_bytes: int
    database: DatabaseConfig

    @classmethod
    def load(cls, path: Path) -> "ServerConfig":
        payload = json.loads(path.resolve().read_text(encoding="utf-8-sig"))
        if payload.get("schema_version") != "1.0":
            raise ValueError("unsupported remote MCP config schema_version")
        if "token" in payload or "password" in payload:
            raise ValueError("inline secrets are forbidden; use environment variable names")

        server = payload.get("server")
        auth = payload.get("auth")
        database = payload.get("database")
        if not isinstance(server, dict) or not isinstance(auth, dict) or not isinstance(database, dict):
            raise ValueError("server, auth and database objects are required")

        host = str(server.get("host") or "127.0.0.1").strip()
        port = int(server.get("port", 8080))
        mcp_path = str(server.get("mcp_path") or "/mcp").strip()
        credential_store_raw = str(auth.get("credential_store") or "").strip()
        hash_secret_env = str(auth.get("hash_secret_env") or "").strip()
        access_token_days = int(auth.get("access_token_days", 90))
        max_query_limit = int(server.get("max_query_limit", 100))
        max_request_bytes = int(server.get("max_request_bytes", 1_048_576))
        if not host:
            raise ValueError("server.host is required")
        if not 0 <= port <= 65535:
            raise ValueError("server.port must be between 0 and 65535")
        if not mcp_path.startswith("/") or "?" in mcp_path or "#" in mcp_path:
            raise ValueError("server.mcp_path must be an absolute URL path")
        if not credential_store_raw:
            raise ValueError("auth.credential_store is required")
        credential_store = Path(credential_store_raw)
        if not credential_store.is_absolute():
            raise ValueError("auth.credential_store must be an absolute path")
        if not hash_secret_env:
            raise ValueError("auth.hash_secret_env is required")
        if not 1 <= access_token_days <= 365:
            raise ValueError("auth.access_token_days must be between 1 and 365")
        if not 1 <= max_query_limit <= 200:
            raise ValueError("server.max_query_limit must be between 1 and 200")
        if not 1024 <= max_request_bytes <= 10_485_760:
            raise ValueError("server.max_request_bytes must be between 1024 and 10485760")

        schema = validate_schema(str(database.get("schema") or DEFAULT_SCHEMA))
        db_config = DatabaseConfig(
            host=str(database.get("host") or "127.0.0.1"),
            port=int(database.get("port", 5432)),
            database=str(database.get("database") or "").strip(),
            user=str(database.get("user") or "").strip(),
            password_env=str(database.get("password_env") or "").strip(),
            schema=schema,
            connect_timeout=int(database.get("connect_timeout", 10)),
            statement_timeout_ms=int(database.get("statement_timeout_ms", 15_000)),
        )
        if not db_config.database or not db_config.user or not db_config.password_env:
            raise ValueError("database.database, database.user and database.password_env are required")
        if not 1 <= db_config.port <= 65535:
            raise ValueError("database.port must be between 1 and 65535")
        if not 1 <= db_config.connect_timeout <= 60:
            raise ValueError("database.connect_timeout must be between 1 and 60")
        if not 100 <= db_config.statement_timeout_ms <= 120_000:
            raise ValueError("database.statement_timeout_ms must be between 100 and 120000")

        return cls(
            host=host,
            port=port,
            mcp_path=mcp_path,
            auth=AuthConfig(
                credential_store=credential_store.resolve(),
                hash_secret_env=hash_secret_env,
                access_token_days=access_token_days,
            ),
            max_query_limit=max_query_limit,
            max_request_bytes=max_request_bytes,
            database=db_config,
        )

    def require_secrets(self, *, require_database: bool = True) -> None:
        if not os.environ.get(self.auth.hash_secret_env):
            raise RuntimeError(
                "credential hash secret is missing from environment variable "
                f"{self.auth.hash_secret_env}"
            )
        if require_database and not os.environ.get(self.database.password_env):
            raise RuntimeError(
                "database password is missing from environment variable "
                f"{self.database.password_env}"
            )

    def public_summary(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "server_name": SERVER_NAME,
            "server_version": SERVER_VERSION,
            "listen": {"host": self.host, "port": self.port, "mcp_path": self.mcp_path},
            "auth": {
                "mode": "one_time_activation_code",
                "hash_secret_env": self.auth.hash_secret_env,
                "access_token_days": self.auth.access_token_days,
            },
            "database": {
                "host": self.database.host,
                "port": self.database.port,
                "database": self.database.database,
                "user": self.database.user,
                "schema": self.database.schema,
                "password_env": self.database.password_env,
                "read_only": True,
            },
            "limits": {
                "max_query_limit": self.max_query_limit,
                "max_request_bytes": self.max_request_bytes,
            },
        }


class CredentialStore:
    """Small single-process credential store for the initial internal rollout."""

    def __init__(self, config: AuthConfig):
        self.config = config
        secret = os.environ.get(config.hash_secret_env)
        if not secret:
            raise RuntimeError(
                "credential hash secret is missing from environment variable "
                f"{config.hash_secret_env}"
            )
        self._secret = secret.encode("utf-8")
        self._lock = threading.RLock()

    def issue_activation_code(self, *, label: str, valid_hours: int) -> dict[str, Any]:
        if not label.strip():
            raise ValueError("activation code label is required")
        if not 1 <= valid_hours <= 720:
            raise ValueError("activation code valid_hours must be between 1 and 720")
        raw = secrets.token_hex(10).upper()
        code = "MRK-" + "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))
        now = datetime.now(timezone.utc)
        record = {
            "activation_id": f"ACT-{secrets.token_hex(8)}",
            "code_hash": self._digest(code),
            "label": label.strip(),
            "status": "issued",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=valid_hours)).isoformat(),
            "redeemed_at": None,
        }
        with self._lock:
            payload = self._load()
            payload["activation_codes"].append(record)
            self._save(payload)
        return {
            "activation_id": record["activation_id"],
            "activation_code": code,
            "label": record["label"],
            "expires_at": record["expires_at"],
        }

    def activate(self, code: str) -> dict[str, Any]:
        candidate_hash = self._digest(code.strip())
        now = datetime.now(timezone.utc)
        with self._lock:
            payload = self._load()
            matched: dict[str, Any] | None = None
            for record in payload["activation_codes"]:
                if hmac.compare_digest(str(record.get("code_hash") or ""), candidate_hash):
                    matched = record
                    break
            if (
                matched is None
                or matched.get("status") != "issued"
                or self._parse_time(matched.get("expires_at")) <= now
            ):
                raise PermissionError("activation code is invalid or expired")

            token = "mrk_live_" + secrets.token_urlsafe(32)
            token_record = {
                "token_id": f"TOK-{secrets.token_hex(8)}",
                "token_hash": self._digest(token),
                "status": "active",
                "activation_id": matched["activation_id"],
                "label": matched["label"],
                "created_at": now.isoformat(),
                "expires_at": (
                    now + timedelta(days=self.config.access_token_days)
                ).isoformat(),
                "last_used_at": None,
                "revoked_at": None,
            }
            matched["status"] = "redeemed"
            matched["redeemed_at"] = now.isoformat()
            payload["access_tokens"].append(token_record)
            self._save(payload)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_at": token_record["expires_at"],
        }

    def authenticate(self, token: str) -> bool:
        if not token:
            return False
        candidate_hash = self._digest(token)
        now = datetime.now(timezone.utc)
        with self._lock:
            payload = self._load()
            for record in payload["access_tokens"]:
                if not hmac.compare_digest(
                    str(record.get("token_hash") or ""), candidate_hash
                ):
                    continue
                if record.get("status") != "active":
                    return False
                if self._parse_time(record.get("expires_at")) <= now:
                    return False
                record["last_used_at"] = now.isoformat()
                self._save(payload)
                return True
        return False

    def revoke(self, token_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            payload = self._load()
            for record in payload["access_tokens"]:
                if record.get("token_id") != token_id:
                    continue
                if record.get("status") == "revoked":
                    return True
                record["status"] = "revoked"
                record["revoked_at"] = now
                self._save(payload)
                return True
        return False

    def _digest(self, value: str) -> str:
        return hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _load(self) -> dict[str, Any]:
        path = self.config.credential_store
        if not path.exists():
            return {"schema_version": "1.0", "activation_codes": [], "access_tokens": []}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "1.0":
            raise ValueError("unsupported credential store schema_version")
        if not isinstance(payload.get("activation_codes"), list) or not isinstance(
            payload.get("access_tokens"), list
        ):
            raise ValueError("invalid credential store")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        path = self.config.credential_store
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _parse_time(raw: Any) -> datetime:
        try:
            value = datetime.fromisoformat(str(raw))
        except ValueError as exc:
            raise ValueError("invalid credential timestamp") from exc
        if value.tzinfo is None:
            raise ValueError("credential timestamp must include timezone")
        return value.astimezone(timezone.utc)


class KnowledgeRepository(Protocol):
    def query(
        self,
        *,
        kind: str,
        search: str,
        section_role: str,
        module_code: str,
        topic: str,
        limit: int,
    ) -> dict[str, Any]: ...

    def page(
        self,
        *,
        entity: str,
        asset_id: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]: ...


class PostgresPublishedKnowledgeRepository:
    def __init__(self, config: DatabaseConfig):
        self.config = config

    def query(
        self,
        *,
        kind: str,
        search: str,
        section_role: str,
        module_code: str,
        topic: str,
        limit: int,
    ) -> dict[str, Any]:
        connection = connect(self.config.as_namespace())
        try:
            connection.read_only = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{self.config.statement_timeout_ms}ms",),
                )
            result = query_published_knowledge(
                connection,
                schema=self.config.schema,
                kind=kind,
                search=search,
                section_role=section_role,
                module_code=module_code,
                topic=topic,
                limit=limit,
            )
            connection.rollback()
            return result
        finally:
            connection.close()

    def page(
        self,
        *,
        entity: str,
        asset_id: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if entity not in PAGE_ENTITIES:
            raise ValueError(f"unsupported page entity: {entity}")
        schema = validate_schema(self.config.schema)
        statements = {
            "knowledge-package": (
                f"SELECT * FROM {schema}.runtime_knowledge_package "
                "WHERE package_id=%s ORDER BY package_id LIMIT %s OFFSET %s"
            ),
            "package-source": (
                f"SELECT * FROM {schema}.runtime_package_source "
                "WHERE package_id=%s ORDER BY source_role,source_id LIMIT %s OFFSET %s"
            ),
            "corpus-document": (
                f"SELECT * FROM {schema}.runtime_corpus_document "
                "WHERE package_id=%s ORDER BY corpus_document_id LIMIT %s OFFSET %s"
            ),
            "corpus-block": (
                f"SELECT * FROM {schema}.runtime_corpus_block "
                "WHERE package_id=%s "
                "ORDER BY corpus_document_id,block_index,block_id LIMIT %s OFFSET %s"
            ),
            "capability": (
                f"SELECT * FROM {schema}.runtime_product_capability "
                "WHERE package_id=%s ORDER BY capability_id LIMIT %s OFFSET %s"
            ),
            "capability-block": (
                f"SELECT relation.* FROM {schema}.runtime_capability_block AS relation "
                f"JOIN {schema}.runtime_product_capability AS capability "
                "ON capability.capability_id=relation.capability_id "
                "WHERE capability.package_id=%s "
                "ORDER BY relation.capability_id,relation.relation_order,"
                "relation.priority,relation.block_id LIMIT %s OFFSET %s"
            ),
            "policy-catalog": (
                f"SELECT * FROM {schema}.runtime_policy_catalog "
                "WHERE catalog_id=%s ORDER BY catalog_id LIMIT %s OFFSET %s"
            ),
            "policy-catalog-entry": (
                f"SELECT * FROM {schema}.runtime_policy_catalog_entry "
                "WHERE catalog_id=%s ORDER BY source_row,catalog_entry_id LIMIT %s OFFSET %s"
            ),
        }
        connection = connect(self.config.as_namespace())
        try:
            connection.read_only = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (f"{self.config.statement_timeout_ms}ms",),
                )
                cursor.execute(statements[entity], (asset_id, limit + 1, offset))
                columns = [column.name for column in cursor.description]
                fetched = [dict(zip(columns, row)) for row in cursor.fetchall()]
            connection.rollback()
        finally:
            connection.close()
        has_more = len(fetched) > limit
        rows = fetched[:limit]
        return {
            "entity": entity,
            "asset_id": asset_id,
            "offset": offset,
            "limit": limit,
            "count": len(rows),
            "rows": rows,
            "next_offset": offset + len(rows) if has_more else None,
        }


READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def build_tools(max_query_limit: int) -> list[dict[str, Any]]:
    return [
        {
            "name": "knowledge_service_status",
            "description": "检查远程医疗可研知识服务和已发布运行视图状态。只读，不接收项目材料。",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "knowledge_query",
            "description": (
                "对PostgreSQL已发布runtime_*视图执行有数量上限的只读查询。"
                "policy-catalog仅是待核验目录，不能作为正式政策证据。"
            ),
            "inputSchema": {
                "type": "object",
                "required": ["kind"],
                "properties": {
                    "kind": {"type": "string", "enum": list(QUERY_KINDS)},
                    "search": {"type": "string", "maxLength": 500},
                    "section_role": {"type": "string", "maxLength": 100},
                    "module_code": {"type": "string", "maxLength": 100},
                    "topic": {"type": "string", "maxLength": 100},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": max_query_limit,
                    },
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "knowledge_page",
            "description": (
                "按知识包ID或政策目录ID确定性分页读取已发布快照实体。"
                "调用方必须逐页保存并在next_offset为空时结束。"
            ),
            "inputSchema": {
                "type": "object",
                "required": ["entity", "asset_id"],
                "properties": {
                    "entity": {"type": "string", "enum": list(PAGE_ENTITIES)},
                    "asset_id": {"type": "string", "minLength": 1, "maxLength": 200},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 10000000},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": max_query_limit,
                    },
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
    ]


class ReadonlyKnowledgeMCP:
    def __init__(self, config: ServerConfig, repository: KnowledgeRepository):
        self.config = config
        self.repository = repository
        self.tools = build_tools(config.max_query_limit)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request")
        if request_id is None and method.startswith("notifications/"):
            return None
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
                        "本服务仅查询已发布知识且不接收项目文件。先调用knowledge_service_status；"
                        "policy-catalog结果只是待核验线索，不能直接作为正式政策证据。"
                    ),
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": self.tools})
        if method == "resources/list":
            return self._result(request_id, {"resources": []})
        if method == "prompts/list":
            return self._result(request_id, {"prompts": []})
        if method != "tools/call":
            return self._error(request_id, -32601, "Method not found")

        params = request.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "tool arguments must be an object")
        try:
            if name == "knowledge_service_status":
                if arguments:
                    raise ValueError("knowledge_service_status does not accept arguments")
                database_status = self.repository.query(
                    kind="status",
                    search="",
                    section_role="",
                    module_code="",
                    topic="",
                    limit=1,
                )
                value = {
                    "status": "ready",
                    "server_name": SERVER_NAME,
                    "server_version": SERVER_VERSION,
                    "transport": "streamable_http",
                    "authentication": "bearer",
                    "read_only": True,
                    "published_content": database_status,
                }
            elif name == "knowledge_query":
                value = self._knowledge_query(arguments)
            elif name == "knowledge_page":
                value = self._knowledge_page(arguments)
            else:
                return self._error(request_id, -32602, f"Unknown tool: {name}")
            return self._result(request_id, self._tool_result(value, False))
        except Exception as exc:
            error_value = {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            return self._result(request_id, self._tool_result(error_value, True))

    def _knowledge_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {"kind", "search", "section_role", "module_code", "topic", "limit"}
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise ValueError(f"unsupported arguments: {', '.join(unknown)}")
        kind = str(arguments.get("kind") or "")
        if kind not in QUERY_KINDS:
            raise ValueError(f"unsupported query kind: {kind}")
        limit = int(arguments.get("limit", min(50, self.config.max_query_limit)))
        if not 1 <= limit <= self.config.max_query_limit:
            raise ValueError(f"limit must be between 1 and {self.config.max_query_limit}")
        values = {
            field: str(arguments.get(field) or "").strip()
            for field in ("search", "section_role", "module_code", "topic")
        }
        for field, value in values.items():
            maximum = 500 if field == "search" else 100
            if len(value) > maximum:
                raise ValueError(f"{field} exceeds maximum length {maximum}")
        return self.repository.query(kind=kind, limit=limit, **values)

    def _knowledge_page(self, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = {"entity", "asset_id", "offset", "limit"}
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise ValueError(f"unsupported arguments: {', '.join(unknown)}")
        entity = str(arguments.get("entity") or "")
        if entity not in PAGE_ENTITIES:
            raise ValueError(f"unsupported page entity: {entity}")
        asset_id = str(arguments.get("asset_id") or "").strip()
        if not asset_id or len(asset_id) > 200:
            raise ValueError("asset_id is required and must not exceed 200 characters")
        offset = int(arguments.get("offset", 0))
        limit = int(arguments.get("limit", min(100, self.config.max_query_limit)))
        if not 0 <= offset <= 10_000_000:
            raise ValueError("offset must be between 0 and 10000000")
        if not 1 <= limit <= self.config.max_query_limit:
            raise ValueError(f"limit must be between 1 and {self.config.max_query_limit}")
        return self.repository.page(
            entity=entity,
            asset_id=asset_id,
            offset=offset,
            limit=limit,
        )

    @staticmethod
    def _tool_result(value: dict[str, Any], is_error: bool) -> dict[str, Any]:
        text = json.dumps(value, ensure_ascii=False, default=_json_default)
        return {
            "content": [{"type": "text", "text": text}],
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


class MCPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        config: ServerConfig,
        app: ReadonlyKnowledgeMCP,
        credentials: CredentialStore,
    ):
        super().__init__((config.host, config.port), MCPRequestHandler)
        self.config = config
        self.app = app
        self.credentials = credentials


class MCPRequestHandler(BaseHTTPRequestHandler):
    server: MCPHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(
                200,
                {
                    "status": "ready",
                    "server_name": SERVER_NAME,
                    "server_version": SERVER_VERSION,
                    "read_only": True,
                },
            )
            return
        if path == self.server.config.mcp_path:
            self._send_json(405, {"error": "server notifications are not enabled"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        request_path = urlsplit(self.path).path
        if request_path == "/activate":
            self._activate()
            return
        if request_path != self.server.config.mcp_path:
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            # The request body has not been consumed. Close this HTTP/1.1
            # connection so those bytes cannot be parsed as a second request.
            self.close_connection = True
            self._send_json(
                401,
                {"error": "unauthorized"},
                extra_headers={"WWW-Authenticate": 'Bearer realm="medical-report-knowledge"'},
            )
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if length < 1 or length > self.server.config.max_request_bytes:
            self._send_json(413, {"error": "request body size is invalid"})
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(
                200,
                ReadonlyKnowledgeMCP._error(None, -32700, "Parse error"),
            )
            return
        if not isinstance(request, dict):
            self._send_json(
                200,
                ReadonlyKnowledgeMCP._error(None, -32600, "Invalid Request"),
            )
            return
        response = self.server.app.dispatch(request)
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(200, response)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        return scheme.lower() == "bearer" and self.server.credentials.authenticate(
            token.strip()
        )

    def _activate(self) -> None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return
        if length < 1 or length > min(self.server.config.max_request_bytes, 4096):
            self._send_json(413, {"error": "activation request body size is invalid"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid activation request"})
            return
        code = str(payload.get("activation_code") or "") if isinstance(payload, dict) else ""
        try:
            result = self.server.credentials.activate(code)
        except PermissionError:
            self._send_json(401, {"error": "activation code is invalid or expired"})
            return
        self._send_json(200, result)

    def _send_json(
        self,
        status: int,
        payload: Any,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("MCP-Protocol-Version", LATEST_PROTOCOL_VERSION)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        message = format % args
        print(f"remote-readonly-mcp {self.client_address[0]} {message}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and required secret environment variables, then exit",
    )
    actions.add_argument(
        "--issue-activation-code",
        action="store_true",
        help="Issue one activation code and exit; does not connect to PostgreSQL",
    )
    actions.add_argument(
        "--revoke-token-id",
        help="Revoke one access token by its server-side token ID and exit",
    )
    parser.add_argument("--label", default="internal-user", help="Non-secret activation label")
    parser.add_argument("--valid-hours", default=168, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ServerConfig.load(args.config)
    if args.issue_activation_code or args.revoke_token_id:
        config.require_secrets(require_database=False)
        credentials = CredentialStore(config.auth)
        if args.issue_activation_code:
            result = credentials.issue_activation_code(
                label=args.label,
                valid_hours=args.valid_hours,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        revoked = credentials.revoke(args.revoke_token_id)
        print(json.dumps({"token_id": args.revoke_token_id, "revoked": revoked}, indent=2))
        return 0 if revoked else 1
    config.require_secrets()
    if args.check_config:
        print(json.dumps(config.public_summary(), ensure_ascii=False, indent=2))
        return 0
    repository = PostgresPublishedKnowledgeRepository(config.database)
    app = ReadonlyKnowledgeMCP(config, repository)
    credentials = CredentialStore(config.auth)
    server = MCPHTTPServer(config, app, credentials)
    print(
        f"{SERVER_NAME} {SERVER_VERSION} listening on "
        f"http://{server.server_address[0]}:{server.server_address[1]}{config.mcp_path}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
