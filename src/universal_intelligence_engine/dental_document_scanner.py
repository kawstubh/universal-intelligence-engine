"""Fail-closed private-network ClamAV scanning for dental document uploads.

The scanner accepts bytes already bounded and checksum-verified by the document
upload pipeline. It connects only to private or loopback addresses; link-local,
reserved, multicast, unspecified and public targets are rejected before connect.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import struct
from collections.abc import Mapping

MAX_SCAN_BYTES = 10 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 4096


class DocumentScannerError(RuntimeError):
    """Base error for document malware scanning."""


class DocumentScannerNotConfigured(DocumentScannerError):
    """Raised when a private scanner has not been explicitly configured."""


class DocumentScannerConfigurationError(DocumentScannerError):
    """Raised when scanner configuration is unsafe or invalid."""


class DocumentScannerUnavailable(DocumentScannerError):
    """Raised when the configured scanner cannot complete a scan."""


class DocumentMalwareDetected(DocumentScannerError):
    """Raised when ClamAV reports a malware signature."""


def _configuration(environ: Mapping[str, str] | None = None) -> tuple[str, int, float]:
    env = os.environ if environ is None else environ
    host = str(env.get("DENTAL_DOCUMENT_SCANNER_HOST", "")).strip()
    attestation = str(env.get("DENTAL_DOCUMENT_SCANNER_PRIVATE", "")).strip().lower()

    if not host or attestation != "true":
        raise DocumentScannerNotConfigured(
            "A private ClamAV scanner and private-network attestation are required."
        )
    if any(character.isspace() for character in host) or "/" in host or "://" in host:
        raise DocumentScannerConfigurationError("Scanner host is invalid.")

    try:
        port = int(env.get("DENTAL_DOCUMENT_SCANNER_PORT", "3310"))
        timeout = float(env.get("DENTAL_DOCUMENT_SCANNER_TIMEOUT_SECONDS", "20"))
    except (TypeError, ValueError) as exc:
        raise DocumentScannerConfigurationError("Scanner port or timeout is invalid.") from exc

    if not 1 <= port <= 65535 or not 1 <= timeout <= 60:
        raise DocumentScannerConfigurationError("Scanner port or timeout is outside the allowed range.")
    return host, port, timeout


def _private_target(host: str, port: int) -> tuple[int, int, int, tuple]:
    """Resolve once and connect to the resolved address to prevent DNS rebinding."""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise DocumentScannerUnavailable("Private document scanner could not be resolved.") from exc

    targets: list[tuple[int, int, int, tuple]] = []
    for family, socktype, _protocol, _canonical_name, sockaddr in addresses:
        if socktype != socket.SOCK_STREAM or family not in (socket.AF_INET, socket.AF_INET6):
            continue
        address_text = str(sockaddr[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise DocumentScannerConfigurationError("Scanner resolved to an invalid address.") from exc
        if (
            address.is_multicast
            or address.is_unspecified
            or address.is_link_local
            or address.is_reserved
            or not (address.is_private or address.is_loopback)
        ):
            raise DocumentScannerConfigurationError(
                "Document scanner must resolve exclusively to private network addresses."
            )
        targets.append((family, socktype, _protocol, sockaddr))

    if not targets:
        raise DocumentScannerUnavailable("Private document scanner has no usable address.")
    return targets[0]


def scan_document_bytes(payload: bytes | bytearray | memoryview) -> dict[str, str]:
    """Scan one bounded document using ClamAV's INSTREAM protocol.

    Missing, unsafe or unavailable scanner configuration fails closed. Only
    an explicit ClamAV clean response allows the upload pipeline to publish
    the verified object.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise DocumentScannerConfigurationError("Document scan payload must be bytes.")
    content = bytes(payload)
    if not content or len(content) > MAX_SCAN_BYTES:
        raise DocumentScannerConfigurationError("Document size is outside the scan limit.")

    host, port, timeout = _configuration()
    target = _private_target(host, port)
    family, socktype, protocol, sockaddr = target
    connection = None
    try:
        # Connect to the exact resolved address. create_connection() performs
        # another DNS lookup and cannot preserve the full IPv6 sockaddr tuple.
        connection = socket.socket(family, socktype, protocol)
        connection.settimeout(timeout)
        connection.connect(sockaddr)
        connection.sendall(b"zINSTREAM\0")
        for offset in range(0, len(content), STREAM_CHUNK_BYTES):
            chunk = content[offset : offset + STREAM_CHUNK_BYTES]
            connection.sendall(struct.pack("!I", len(chunk)))
            connection.sendall(chunk)
        connection.sendall(b"\x00\x00\x00\x00")

        response = bytearray()
        while len(response) < MAX_RESPONSE_BYTES:
            chunk = connection.recv(min(1024, MAX_RESPONSE_BYTES - len(response)))
            if not chunk:
                break
            response.extend(chunk)
            if b"\0" in response or b"\n" in response:
                break

        reply_bytes = bytes(response).split(b"\0", 1)[0].split(b"\n", 1)[0]
        reply = reply_bytes.decode("utf-8", errors="replace").strip()
        if reply == "stream: OK":
            return {"engine": "clamav", "status": "clean"}
        if reply.startswith("stream: ") and reply.endswith(" FOUND"):
            raise DocumentMalwareDetected("ClamAV detected a malware signature.")
        raise DocumentScannerUnavailable("ClamAV returned no valid clean result.")
    except DocumentScannerError:
        raise
    except (OSError, TimeoutError) as exc:
        raise DocumentScannerUnavailable("Private document scanner is unavailable.") from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
