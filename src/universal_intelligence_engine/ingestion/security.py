"""SSRF-resistant outbound URL checks and redirect validation."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

FORBIDDEN_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),
)


class UnsafeTargetError(ValueError):
    """Raised when a URL resolves to a non-public network or invalid target."""


def is_forbidden_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    if address.is_loopback or address.is_private or address.is_link_local or address.is_multicast or address.is_unspecified or not address.is_global:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in FORBIDDEN_NETWORKS if network.version == 4)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped and is_forbidden_ip(str(address.ipv4_mapped)):
            return True
        return any(address in network for network in FORBIDDEN_NETWORKS if network.version == 6)
    return True


def resolve_public_addresses(host: str, resolver=socket.getaddrinfo) -> set[str]:
    """Resolve a hostname and reject it unless every answer is public.

    Inject a resolver in tests. Rejecting mixed public/private DNS answers prevents
    an attacker from relying on clients choosing a different returned address.
    """
    if not host or host.lower() == "localhost" or host.endswith(".localhost"):
        raise UnsafeTargetError("Loopback hostnames are prohibited")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if is_forbidden_ip(str(literal)):
            raise UnsafeTargetError("Non-public IP address is prohibited")
        return {str(literal)}
    try:
        records = resolver(host, None, type=socket.SOCK_STREAM)
        addresses = {str(ipaddress.ip_address(record[4][0].split("%", 1)[0])) for record in records}
    except (OSError, ValueError, IndexError) as exc:
        raise UnsafeTargetError("Hostname could not be safely resolved") from exc
    if not addresses:
        raise UnsafeTargetError("Hostname has no address records")
    if any(is_forbidden_ip(address) for address in addresses):
        raise UnsafeTargetError("Hostname resolves to a prohibited or non-public address")
    return addresses


def validate_outbound_url(url: str, resolver=socket.getaddrinfo, allowed_domains: set[str] | None = None) -> tuple[str, set[str]]:
    """Validate scheme/authority, optional host allowlist, and public DNS answers."""
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeTargetError("URL must be a string of at most 2048 characters")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeTargetError("Only credential-free HTTPS URLs are accepted")
    try:
        if parsed.port not in (None, 443):
            raise UnsafeTargetError("Only standard HTTPS port 443 is accepted")
    except ValueError as exc:
        raise UnsafeTargetError("Invalid URL port") from exc
    host = parsed.hostname.lower().rstrip(".")
    if allowed_domains is not None and not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
        raise UnsafeTargetError("URL domain is not in the approved allowlist")
    addresses = resolve_public_addresses(host, resolver=resolver)
    return host, addresses


from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, build_opener


class ValidatingRedirectHandler(HTTPRedirectHandler):
    """Check every redirect target before urllib is allowed to dispatch it."""
    def __init__(self, allowed_domains: set[str] | None = None, resolver=None, max_redirects: int = 5):
        super().__init__()
        self.allowed_domains = allowed_domains
        self.resolver = resolver or socket.getaddrinfo
        self.max_redirections = max_redirects
        self.max_repeats = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validate_outbound_url(newurl, resolver=self.resolver, allowed_domains=self.allowed_domains)
        except UnsafeTargetError as exc:
            raise HTTPError(req.full_url, code, "Blocked unsafe redirect target", headers, fp) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_urlopen(request, timeout: float, allowed_domains: set[str], resolver=None):
    """Resolve/check the first URL then use validated redirects for all hops."""
    resolver = resolver or socket.getaddrinfo
    initial_url = request.full_url if hasattr(request, "full_url") else str(request)
    validate_outbound_url(initial_url, resolver=resolver, allowed_domains=allowed_domains)
    opener = build_opener(ValidatingRedirectHandler(allowed_domains=allowed_domains, resolver=resolver))
    return opener.open(request, timeout=timeout)
