#!/usr/bin/env python3
"""Local stdio credential bridge for the remote read-only knowledge MCP.

The one-time activation code is sent directly to the remote activation endpoint.
The returned long-lived bearer token is stored in a user-only local file and is
never returned through MCP tool content.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

BRIDGE_NAME = "medical-report-knowledge-bridge"
BRIDGE_VERSION = "0.1.0"
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
DEFAULT_CREDENTIAL_FILE = Path.home() / ".codex" / "credentials" / "medical-report-knowledge.json"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _write_stdout_json(value: Any) -> None:
    sys.stdout.buffer.write(_json_bytes(value) + b"\n")
    sys.stdout.buffer.flush()


@dataclass(frozen=True)
class BridgeConfig:
    remote_url: str
    activation_url: str
    credential_file: Path
    request_timeout: int = 60
    max_query_limit: int = 100

    @classmethod
    def load(cls, path: Path) -> "BridgeConfig":
        payload = json.loads(path.resolve().read_text(encoding="utf-8-sig"))
        if payload.get("schema_version") != "1.0":
            raise ValueError("unsupported bridge config schema_version")
        if "access_token" in payload or "activation_code" in payload:
            raise ValueError("credentials are forbidden in the bridge config")
        remote_url = str(payload.get("remote_url") or "").strip()
        if not remote_url:
            raise ValueError("remote_url is required")
        parsed = urlsplit(remote_url)
        allow_insecure = bool(payload.get("allow_insecure_http", False))
        if parsed.scheme not in ({"https", "http"} if allow_insecure else {"https"}):
            raise ValueError("remote_url must use https")
        if not parsed.netloc or parsed.path != "/mcp" or parsed.query or parsed.fragment:
            raise ValueError("remote_url must be an origin plus the exact /mcp path")
        activation_url = urlunsplit((parsed.scheme, parsed.netloc, "/activate", "", ""))

        raw_credential_file = str(payload.get("credential_file") or DEFAULT_CREDENTIAL_FILE)
        credential_file = Path(raw_credential_file).expanduser()
        if not credential_file.is_absolute():
            raise ValueError("credential_file must resolve to an absolute path")
        request_timeout = int(payload.get("request_timeout", 60))
        max_query_limit = int(payload.get("max_query_limit", 100))
        if not 1 <= request_timeout <= 120:
            raise ValueError("request_timeout must be between 1 and 120")
        if not 1 <= max_query_limit <= 200:
            raise ValueError("max_query_limit must be between 1 and 200")
        return cls(
            remote_url=remote_url,
            activation_url=activation_url,
            credential_file=credential_file.resolve(),
            request_timeout=request_timeout,
            max_query_limit=max_query_limit,
        )

    def public_summary(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "bridge_name": BRIDGE_NAME,
            "bridge_version": BRIDGE_VERSION,
            "remote_url": self.remote_url,
            "credential_file": str(self.credential_file),
            "request_timeout": self.request_timeout,
            "max_query_limit": self.max_query_limit,
        }


class LocalCredentialFile:
    def __init__(self, path: Path):
        self.path = path

    def status(self) -> dict[str, Any]:
        payload = self._load(required=False)
        if payload is None:
            return {"activated": False, "status": "activation_required"}
        expires_at = self._parse_time(payload.get("expires_at"))
        if expires_at <= datetime.now(timezone.utc):
            return {
                "activated": False,
                "status": "credential_expired",
                "expires_at": expires_at.isoformat(),
            }
        return {
            "activated": True,
            "status": "ready",
            "expires_at": expires_at.isoformat(),
        }

    def token(self) -> str:
        status = self.status()
        if not status["activated"]:
            raise PermissionError(status["status"])
        payload = self._load(required=True)
        token = str(payload.get("access_token") or "")
        if not token:
            raise ValueError("local credential file is missing access_token")
        return token

    def save(self, *, access_token: str, expires_at: str, remote_url: str) -> None:
        if not access_token.startswith("mrk_live_"):
            raise ValueError("remote activation returned an invalid access token")
        self._parse_time(expires_at)
        payload = {
            "schema_version": "1.0",
            "remote_url": remote_url,
            "access_token": access_token,
            "expires_at": expires_at,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(6)}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _load(self, *, required: bool) -> dict[str, Any] | None:
        if not self.path.exists():
            if required:
                raise PermissionError("activation_required")
            return None
        if os.name != "nt" and self.path.stat().st_mode & 0o077:
            raise PermissionError("local credential file permissions must be 0600")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "1.0":
            raise ValueError("unsupported local credential schema_version")
        return payload

    @staticmethod
    def _parse_time(raw: Any) -> datetime:
        try:
            value = datetime.fromisoformat(str(raw))
        except ValueError as exc:
            raise ValueError("invalid credential expires_at") from exc
        if value.tzinfo is None:
            raise ValueError("credential expires_at must include timezone")
        return value.astimezone(timezone.utc)


class RemoteKnowledgeClient:
    def __init__(self, config: BridgeConfig, credentials: LocalCredentialFile):
        self.config = config
        self.credentials = credentials

    def activate(self, activation_code: str) -> dict[str, Any]:
        code = activation_code.strip()
        if not code or len(code) > 64:
            raise ValueError("activation_code is required and must not exceed 64 characters")
        result = self._post(
            self.config.activation_url,
            {"activation_code": code},
            access_token=None,
        )
        token = str(result.get("access_token") or "")
        expires_at = str(result.get("expires_at") or "")
        self.credentials.save(
            access_token=token,
            expires_at=expires_at,
            remote_url=self.config.remote_url,
        )
        return {
            "status": "activated",
            "activated": True,
            "expires_at": expires_at,
            "credential_storage": "local_user_only_file",
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._post(
            self.config.remote_url,
            {
                "jsonrpc": "2.0",
                "id": secrets.token_hex(8),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            access_token=self.credentials.token(),
        )
        if "error" in response:
            error = response["error"]
            raise RuntimeError(str(error.get("message") or "remote MCP error"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("remote MCP returned an invalid result")
        return result

    def _post(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        request = urllib.request.Request(
            url,
            data=_json_bytes(payload),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError("remote knowledge access is unauthorized") from exc
            raise RuntimeError(f"remote knowledge service returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError("remote knowledge service is unreachable") from exc


READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def build_tools(max_query_limit: int) -> list[dict[str, Any]]:
    return [
        {
            "name": "knowledge_access_status",
            "description": "检查本机是否已保存远程只读知识服务凭据，不访问项目材料。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "activate_knowledge_access",
            "description": "使用一次性激活码开通远程只读知识访问；长期凭据只保存在本机，不返回给模型。",
            "inputSchema": {
                "type": "object",
                "required": ["activation_code"],
                "properties": {"activation_code": {"type": "string", "minLength": 1, "maxLength": 64}},
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        },
        {
            "name": "knowledge_service_status",
            "description": "检查远程只读知识服务和已发布内容状态。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "knowledge_query",
            "description": "查询远程服务中的已发布医疗可研知识；不提供任何写入能力。",
            "inputSchema": {
                "type": "object",
                "required": ["kind"],
                "properties": {
                    "kind": {"type": "string", "enum": list(QUERY_KINDS)},
                    "search": {"type": "string", "maxLength": 500},
                    "section_role": {"type": "string", "maxLength": 100},
                    "module_code": {"type": "string", "maxLength": 100},
                    "topic": {"type": "string", "maxLength": 100},
                    "limit": {"type": "integer", "minimum": 1, "maximum": max_query_limit},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
        {
            "name": "knowledge_page",
            "description": "按确定顺序分页读取远程已发布知识快照，用于本地只读缓存同步。",
            "inputSchema": {
                "type": "object",
                "required": ["entity", "asset_id"],
                "properties": {
                    "entity": {"type": "string", "enum": list(PAGE_ENTITIES)},
                    "asset_id": {"type": "string", "minLength": 1, "maxLength": 200},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 10000000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": max_query_limit},
                },
                "additionalProperties": False,
            },
            "annotations": READ_ONLY_ANNOTATIONS,
        },
    ]


class CredentialBridgeMCP:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self.credentials = LocalCredentialFile(config.credential_file)
        self.remote = RemoteKnowledgeClient(config, self.credentials)
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
            protocol_version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            return self._result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": BRIDGE_NAME, "version": BRIDGE_VERSION},
                    "instructions": (
                        "先调用knowledge_access_status；未激活时只向用户索取一次性激活码并调用"
                        "activate_knowledge_access。不得回显激活码。激活后调用knowledge_service_status，"
                        "其余知识工具均为远程只读查询。"
                    ),
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": self.tools})
        if method in {"resources/list", "prompts/list"}:
            key = "resources" if method == "resources/list" else "prompts"
            return self._result(request_id, {key: []})
        if method != "tools/call":
            return self._error(request_id, -32601, "Method not found")
        params = request.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "tool arguments must be an object")
        try:
            if name == "knowledge_access_status":
                if arguments:
                    raise ValueError("knowledge_access_status does not accept arguments")
                value = self.credentials.status()
                return self._result(request_id, self._tool_result(value, False))
            if name == "activate_knowledge_access":
                unknown = set(arguments) - {"activation_code"}
                if unknown:
                    raise ValueError("activate_knowledge_access received unsupported arguments")
                value = self.remote.activate(str(arguments.get("activation_code") or ""))
                return self._result(request_id, self._tool_result(value, False))
            if name not in {"knowledge_service_status", "knowledge_query", "knowledge_page"}:
                return self._error(request_id, -32602, f"Unknown tool: {name}")
            result = self.remote.call_tool(name, arguments)
            return self._result(request_id, result)
        except Exception as exc:
            value = {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            return self._result(request_id, self._tool_result(value, True))

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
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_stdio(app: CredentialBridgeMCP) -> int:
    for raw_line in sys.stdin.buffer:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line.decode("utf-8"))
            response = (
                app.dispatch(request)
                if isinstance(request, dict)
                else CredentialBridgeMCP._error(None, -32600, "Invalid Request")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = CredentialBridgeMCP._error(None, -32700, "Parse error")
        if response is not None:
            _write_stdout_json(response)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check-config", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = BridgeConfig.load(args.config)
    if args.check_config:
        print(json.dumps(config.public_summary(), ensure_ascii=False, indent=2))
        return 0
    return serve_stdio(CredentialBridgeMCP(config))


if __name__ == "__main__":
    raise SystemExit(main())
