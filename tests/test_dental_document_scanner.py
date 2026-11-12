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
        self.address = None

    def connect(self, address) -> None:
        self.address = address

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

    def make_socket(family, socktype, protocol):
        connected["socket_args"] = (family, socktype, protocol)
        return fake_socket

    monkeypatch.setattr(scanner.socket, "socket", make_socket)
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
    assert connected["socket_args"] == (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    assert fake_socket.address == ("10.20.30.40", 3310)
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
        "socket",
        lambda *args, **kwargs: connected.append(args),
    )

    with pytest.raises(scanner.DocumentScannerConfigurationError):
        scanner.scan_document_bytes(b"safe test bytes")
    assert connected == []


@pytest.mark.parametrize(
    ("family", "sockaddr"),
    [
        (socket.AF_INET, ("169.254.169.254", 3310)),
        (socket.AF_INET6, ("fe80::1", 3310, 0, 0)),
    ],
)
def test_link_local_scanner_addresses_are_rejected_before_connect(
    monkeypatch, family, sockaddr
):
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_HOST", "clamav.internal")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_PRIVATE", "true")
    monkeypatch.setattr(
        scanner.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)
        ],
    )
    connected = []
    monkeypatch.setattr(
        scanner.socket,
        "socket",
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


def test_private_ipv6_ula_scanner_connects_using_the_full_sockaddr(monkeypatch):
    fake_socket = FakeScannerSocket(b"stream: OK\\0")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_HOST", "clamav.internal")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_PORT", "3310")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("DENTAL_DOCUMENT_SCANNER_PRIVATE", "true")
    sockaddr = ("fd12:3456:789a::10", 3310, 0, 0)
    monkeypatch.setattr(
        scanner.socket,
        "getaddrinfo",
        lambda host, port, type: [
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)
        ],
    )
    socket_args = {}

    def make_socket(family, socktype, protocol):
        socket_args["value"] = (family, socktype, protocol)
        return fake_socket

    monkeypatch.setattr(scanner.socket, "socket", make_socket)

    result = scanner.scan_document_bytes(b"synthetic IPv6 test bytes")

    assert result == {"engine": "clamav", "status": "clean"}
    assert socket_args["value"] == (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    assert fake_socket.address == sockaddr
    assert fake_socket.timeout == 20
    assert fake_socket.closed is True
