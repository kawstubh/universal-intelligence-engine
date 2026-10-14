import ipaddress
import socket
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from universal_intelligence_engine.ingestion.security import (
    UnsafeTargetError, ValidatingRedirectHandler, is_forbidden_ip,
    resolve_public_addresses, safe_urlopen, validate_outbound_url,
)
from universal_intelligence_engine.web_knowledge import PublicWebKnowledgeStore


def _resolver_for(ip):
    def resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0) if ":" in ip else (ip, 0))]
    return resolver


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.20.1.9", "10.1.2.3", "172.16.0.1", "172.31.255.254",
    "192.168.1.1", "169.254.169.254", "::1", "fc00::1", "fe80::1",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254",
])
def test_forbidden_ip_ranges_are_rejected(ip):
    assert is_forbidden_ip(ip) is True
    with pytest.raises(UnsafeTargetError):
        resolve_public_addresses(ip)


def test_hostname_with_mixed_public_and_private_answers_is_rejected():
    resolver = lambda host, port, type=socket.SOCK_STREAM: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    with pytest.raises(UnsafeTargetError, match="prohibited"):
        resolve_public_addresses("example.test", resolver=resolver)


def test_url_validation_rejects_metadata_literal_before_dispatch():
    with pytest.raises(UnsafeTargetError):
        validate_outbound_url("https://169.254.169.254/latest/meta-data/")


def test_redirect_to_loopback_is_blocked_before_redirect_dispatch():
    handler = ValidatingRedirectHandler(allowed_domains={"arxiv.org"}, resolver=_resolver_for("8.8.8.8"))
    req = Request("https://arxiv.org/sitemap.xml")
    with pytest.raises(HTTPError, match="Blocked unsafe redirect target"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://127.0.0.1/admin")


def test_redirect_to_metadata_service_is_blocked_cleanly():
    handler = ValidatingRedirectHandler(allowed_domains={"arxiv.org"})
    req = Request("https://arxiv.org/sitemap.xml")
    with pytest.raises(HTTPError):
        handler.redirect_request(req, None, 302, "Found", {}, "https://169.254.169.254/latest/meta-data/")


def test_safe_urlopen_preflights_dns_before_open():
    with patch("universal_intelligence_engine.ingestion.security.build_opener") as opener:
        with pytest.raises(UnsafeTargetError):
            safe_urlopen(Request("https://arxiv.org/sitemap.xml"), timeout=5,
                         allowed_domains={"arxiv.org"}, resolver=_resolver_for("127.0.0.1"))
        opener.assert_not_called()


def test_pipeline_discovery_rejects_malicious_redirect_without_raising(tmp_path):
    store = PublicWebKnowledgeStore(str(tmp_path / "web.sqlite3"), min_fetch_interval=0)
    seed = tmp_path / "seeds.json"
    seed.write_text('{"technology_science":["https://arxiv.org/sitemap.xml"]}', encoding="utf-8")
    from universal_intelligence_engine.ingestion.pipeline import CrossDomainIngestionPipeline
    pipeline = CrossDomainIngestionPipeline(store, str(tmp_path / "queue.sqlite3"), str(seed))
    with patch.object(store, "_robots_allows", return_value=True), \
         patch("universal_intelligence_engine.ingestion.pipeline.safe_urlopen", side_effect=HTTPError("https://arxiv.org/sitemap.xml", 302, "Blocked unsafe redirect target", {}, None)):
        result = pipeline.discover(max_sources=1, max_urls_per_source=10)
    assert result["sources_checked"] == 0
    assert result["queue"]["total"] == 0
