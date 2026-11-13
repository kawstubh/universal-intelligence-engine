from __future__ import annotations

import os
import socket
import time

import pytest

from universal_intelligence_engine import dental_document_scanner as scanner

pytestmark = pytest.mark.skipif(
    os.getenv("ORALYN_CLAMAV_INTEGRATION") != "1",
    reason="requires the isolated ClamAV CI service",
)


def _wait_for_clamav(host: str, port: int, timeout_seconds: int = 240) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2) as connection:
                connection.settimeout(2)
                connection.sendall(b"zPING\\0")
                response = connection.recv(32)
                if b"PONG" in response:
                    return
        except OSError as exc:
            last_error = exc
        time.sleep(2)
    pytest.fail(f"ClamAV did not become ready within {timeout_seconds}s: {last_error}")


@pytest.fixture(scope="module", autouse=True)
def configure_real_clamav() -> None:
    host = os.getenv("ORALYN_CLAMAV_HOST", "127.0.0.1")
    port = int(os.getenv("ORALYN_CLAMAV_PORT", "3310"))
    _wait_for_clamav(host, port)
    os.environ["DENTAL_DOCUMENT_SCANNER_HOST"] = host
    os.environ["DENTAL_DOCUMENT_SCANNER_PORT"] = str(port)
    os.environ["DENTAL_DOCUMENT_SCANNER_TIMEOUT_SECONDS"] = "20"
    os.environ["DENTAL_DOCUMENT_SCANNER_PRIVATE"] = "true"


def test_real_clamav_accepts_a_clean_synthetic_payload() -> None:
    result = scanner.scan_document_bytes(b"Oralyn synthetic clean document scan integration test")
    assert result == {"engine": "clamav", "status": "clean"}


def test_real_clamav_rejects_the_eicar_test_signature() -> None:
    eicar = (
        b"X5O!P%@AP[4\\\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )
    with pytest.raises(scanner.DocumentMalwareDetected):
        scanner.scan_document_bytes(eicar)
