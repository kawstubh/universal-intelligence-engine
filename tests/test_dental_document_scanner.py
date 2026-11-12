from __future__ import annotations

import socket
import struct

import pytest

from universal_intelligence_engine import dental_document_scanner as scanner


class FakeScannerSocket:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = bytearray()
        self.timeout = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)

    def recv(self, size: int) -> bytes:
        if not self.response:
            return b""
        result, self.response = self.response[:size], self.response[size:]
        return result

    def close(self) -> None:
        self.closed = True


def _configure_private_scanner(monkeypatch, fake_socket: FakeScannerSocket):
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_HOST", "clamav.internal")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_PORT", "3310")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_PRIVATE", "true")
    monkeypatch.setattr(
        scanner.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.30.40", port))
        ],
    )
    connected = {}

    def connect(address, timeout=None):
        connected["address"] = address
        connected["timeout"] = timeout
        return fake_socket

    monkeypatch.setattr(scanner.socket, "create_connection", connect)
    return connected


def test_clamav_instream_sends_bounded_chunks_to_a_private_address(monkeypatch):
    fake_socket = FakeScannerSocket(b"stream: OK\0")
    connected = _configure_private_scanner(monkeypatch, fake_socket)
    payload = b"A" * (scanner.STREAM_CHUNK_BYTES + 5)

    result = scanner.scan_document_bytes(payload)

    expected = (
        b"zINSTREAM\0"
        + struct.pack("!I", scanner.STREAM_CHUNK_BYTES)
        + payload[: scanner.STREAM_CHUNK_BYTES]
        + struct.pack("!I", 5)
        + payload[scanner.STREAM_CHUNK_BYTES :]
        + b"\x00\x00\x00\x00"
    )
    assert result == {"engine": "clamav", "status": "clean"}
    assert bytes(fake_socket.sent) == expected
    assert connected["address"] == ("10.20.30.40", 3310)
    assert connected["timeout"] == 20
    assert fake_socket.timeout == 20
    assert fake_socket.closed is True


def test_scanner_fails_closed_when_private_attestation_is_missing(monkeypatch):
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_HOST", "clamav.internal")
    monkeypatch.delenv("DENTAL_DOCUMENT_SCANNER_PRIVATE", raising=False)

    with pytest.raises(scanner.DocumentScannerNotConfigured):
        scanner.scan_document_bytes(b"safe test bytes")


def test_scanner_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.delenv("DENTAL_DOCUMENT_SCANNER_HOST", raising=False)
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_PRIVATE", "true")

    with pytest.raises(scanner.DocumentScannerNotConfigured):
        scanner.scan_document_bytes(b"safe test bytes")


def test_public_scanner_address_is_rejected_before_connect(monkeypatch):
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_HOST", "public.example")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_PRIVATE", "true")
    monkeypatch.setattr(
        scanner.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port))
        ],
    )
    connected = []
    monkeypatch.setattr(
        scanner.socket,
        "create_connection",
        lambda *args, **kwargs: connected.append(args),
    )

    with pytest.raises(scanner.DocumentScannerConfigurationError):
        scanner.scan_document_bytes(b"safe test bytes")
    assert connected == []


def test_malware_signature_blocks_the_document(monkeypatch):
    fake_socket = FakeScannerSocket(b"stream: Eicar-Test-Signature FOUND\0")
    _configure_private_scanner(monkeypatch, fake_socket)

    with pytest.raises(scanner.DocumentMalwareDetected):
        scanner.scan_document_bytes(b"synthetic test payload")


def test_unknown_scanner_response_fails_closed(monkeypatch):
    fake_socket = FakeScannerSocket(b"stream: ERROR\0")
    _configure_private_scanner(monkeypatch, fake_socket)

    with pytest.raises(scanner.DocumentScannerUnavailable):
        scanner.scan_document_bytes(b"synthetic test payload")


def test_non_clamav_reply_cannot_be_mistaken_for_a_clean_scan(monkeypatch):
    fake_socket = FakeScannerSocket(b"untrusted-prefix stream: OK\\0")
    _configure_private_scanner(monkeypatch, fake_socket)

    with pytest.raises(scanner.DocumentScannerUnavailable):
        scanner.scan_document_bytes(b"synthetic test payload")


def test_scanner_timeout_fails_closed(monkeypatch):
    class TimeoutSocket(FakeScannerSocket):
        def recv(self, size: int) -> bytes:
            raise TimeoutError("synthetic timeout")

    fake_socket = TimeoutSocket(b"")
    _configure_private_scanner(monkeypatch, fake_socket)

    with pytest.raises(scanner.DocumentScannerUnavailable):
        scanner.scan_document_bytes(b"synthetic test payload")
    assert fake_socket.closed is True


def test_scanner_rejects_payload_above_the_document_limit(monkeypatch):
    with pytest.raises(scanner.DocumentScannerConfigurationError):
        scanner.scan_document_bytes(b"A" * (scanner.MAX_SCAN_BYTES + 1))
