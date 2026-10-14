"""Small JSON-RPC 2.0 MCP client with bounded HTTPS transport and integrity logging."""
from __future__ import annotations

import ipaddress
import json
import socket
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.error import URLError, HTTPError

from ..security.provenance_crypto import append_integrity_event, ensure_integrity_ledger

PROTOCOL_VERSION = "2025-03-26"
MAX_PAYLOAD_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 1_048_576
STRICT_TIMEOUT_SECONDS = 5.0


class MCPError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never follow redirects: they can bypass endpoint validation and enable SSRF.
        return None


def _validate_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MCP endpoint must be an HTTPS URL without embedded credentials")
    if parsed.fragment:
        raise ValueError("MCP endpoint must not contain a URL fragment")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError("MCP endpoint host is not allowed")
    try:
        literal = ipaddress.ip_address(host)
        if not literal.is_global:
            raise ValueError("MCP endpoint IP must be globally routable")
    except ValueError as exc:
        if "globally routable" in str(exc):
            raise
        # DNS names are resolved below; reject any private/reserved answer.
        try:
            answers = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as dns_error:
            raise ValueError("MCP endpoint DNS resolution failed") from dns_error
        for answer in answers:
            address = ipaddress.ip_address(answer[4][0].split("%")[0])
            if not address.is_global:
                raise ValueError("MCP endpoint resolves to a non-public IP address")
    return endpoint


@dataclass(frozen=True)
class MCPToolResult:
    result: Any
    request_id: str
    event_sequence: int | None = None


class MCPClient:
    """MCP JSON-RPC client. Each outbound tool call is hash-chained into the local ledger.

    The transport can be injected for deterministic tests. Production default transport
    only allows public HTTPS destinations and enforces five-second network timeouts.
    """

    def __init__(self, endpoint: str, ledger_db_path: str, *,
                 transport: Callable[[str, bytes, float], bytes] | None = None):
        self.endpoint = _validate_endpoint(endpoint)
        self.ledger_db_path = ledger_db_path
        self.transport = transport or self._http_transport
        self._next_id = 1
        self._initialized = False
        self.server_info: dict[str, Any] = {}

    @staticmethod
    def _http_transport(endpoint: str, body: bytes, timeout: float) -> bytes:
        request = Request(endpoint, data=body, headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }, method="POST")
        try:
            opener = build_opener(_NoRedirect)
            with opener.open(request, timeout=min(STRICT_TIMEOUT_SECONDS, timeout)) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            raise MCPError(f"MCP HTTP transport failed: {type(exc).__name__}") from exc
        if len(payload) > MAX_RESPONSE_BYTES:
            raise MCPError("MCP response exceeded size limit")
        return payload

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_PAYLOAD_BYTES:
            raise MCPError("MCP request exceeded payload size limit")
        response_bytes = self.transport(self.endpoint, body, STRICT_TIMEOUT_SECONDS)
        if len(response_bytes) > MAX_RESPONSE_BYTES:
            raise MCPError("MCP response exceeded size limit")
        try:
            response = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MCPError("MCP returned invalid JSON") from exc
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise MCPError("MCP returned an invalid JSON-RPC envelope")
        if "error" in response:
            raise MCPError(f"MCP JSON-RPC error: {response['error']}")
        if "result" not in response:
            raise MCPError("MCP JSON-RPC response is missing result")
        return response["result"]

    def initialize(self, client_name: str = "uie", client_version: str = "0.5.0") -> dict[str, Any]:
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": client_version},
        })
        if not isinstance(result, dict) or not isinstance(result.get("protocolVersion"), str):
            raise MCPError("MCP initialize result is missing protocolVersion")
        self.server_info = result.get("serverInfo", {}) if isinstance(result.get("serverInfo", {}), dict) else {}
        self._initialized = True
        # MCP lifecycle notification has no JSON-RPC response.
        notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                  separators=(",", ":")).encode("utf-8")
        if len(notification) > MAX_PAYLOAD_BYTES:
            raise MCPError("MCP notification exceeded payload size limit")
        self.transport(self.endpoint, notification, STRICT_TIMEOUT_SECONDS)
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        self._require_initialized()
        result = self._rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise MCPError("MCP tools/list result must contain a tools array")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        self._require_initialized()
        if not name or not isinstance(name, str):
            raise ValueError("tool name must be a non-empty string")
        args = arguments or {}
        request_id = str(uuid.uuid4())
        payload = {"tool_name": name, "arguments": args, "endpoint_host": urlparse(self.endpoint).hostname}
        with sqlite3.connect(self.ledger_db_path, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            ensure_integrity_ledger(conn)
            event = append_integrity_event(conn, "mcp_tool_invocation", request_id, "OUTBOUND",
                                           {"status": "requested", **payload})
        # Audit is persisted before any outbound tool request.
        result = self._rpc("tools/call", {"name": name, "arguments": args})
        return MCPToolResult(result=result, request_id=request_id, event_sequence=event["sequence_id"])

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise MCPError("MCP initialize must complete before tools/list or tools/call")
