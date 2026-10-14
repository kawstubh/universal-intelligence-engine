"""Bounded, provenance-preserving public-web knowledge harvesting for UIE.

This is a seed implementation, not a claim to crawl the entire web. It only
fetches explicitly submitted URLs on approved public domains, respects robots.txt,
caps response sizes, stores source metadata, and indexes text for later retrieval.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import sqlite3
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from urllib.request import Request, urlopen

from .contracts import Evidence

# A broad, curated public-source seed allowlist spanning many disciplines.
# Extend through reviewed configuration; do not treat domains as equally authoritative.
DEFAULT_ALLOWED_DOMAINS = {
    # General reference, science and research
    "wikipedia.org", "arxiv.org", "crossref.org", "doaj.org", "zenodo.org",
    "scholar.google.com", "plos.org", "nature.com", "science.org",
    "pubmed.ncbi.nlm.nih.gov", "europepmc.org", "ncbi.nlm.nih.gov",
    "docs.python.org", "developer.mozilla.org", "w3.org",
    # Health and international public health
    "who.int", "www.who.int", "cdc.gov", "www.cdc.gov", "nih.gov",
    "fda.gov", "ema.europa.eu", "nhs.uk", "medlineplus.gov",
    # Countries, law, government, geopolitics and public data
    "un.org", "www.un.org", "worldbank.org", "imf.org", "oecd.org",
    "europa.eu", "india.gov.in", "gov.uk", "usa.gov", "data.gov",
    "law.cornell.edu", "legislation.gov.uk", "supremecourtofindia.nic.in",
    # Defence, aerospace, space and security research (public information only)
    "nasa.gov", "esa.int", "defense.gov", "nato.int", "sipri.org",
    "csis.org", "rand.org", "nist.gov", "www.nist.gov", "cisa.gov",
    # Technology, engineering and standards
    "acm.org", "ieee.org", "ietf.org", "iso.org", "opensource.org",
    "github.com", "gitlab.com", "kubernetes.io", "linuxfoundation.org",
    # Economics, markets and financial regulators/data
    "sec.gov", "federalreserve.gov", "fred.stlouisfed.org", "bls.gov",
    "bea.gov", "finra.org", "rbi.org.in", "sebi.gov.in", "nseindia.com",
    "bseindia.com", "moneycontrol.com", "investor.gov",
    # Energy, agriculture, environment, climate and food
    "iea.org", "energy.gov", "noaa.gov", "ipcc.ch", "fao.org",
    "usda.gov", "unep.org", "unfccc.int", "ourworldindata.org",
    # News and current affairs; still verify claims against primary sources
    "bbc.com", "bbc.co.uk", "reuters.com", "apnews.com", "theguardian.com",
    "dw.com", "aljazeera.com", "thehindu.com", "indianexpress.com",
    # Education, daily life, travel and public services
    "unesco.org", "unicef.org", "consumer.ftc.gov", "consumerfinance.gov",
    "incredibleindia.gov.in", "india.gov.in", "weather.gov",
}
MAX_BYTES = 1_500_000
MAX_TEXT = 80_000
USER_AGENT = "UIE-PublicKnowledgeBot/0.1 (+respectful public research)"


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"}:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "article", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header"} and self.skip_depth:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag in {"p", "br", "div", "li", "h1", "h2", "h3", "article", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.skip_depth:
            self.parts.append(data)


def _is_public_host(host: str) -> bool:
    try:
        addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, None)}
        return bool(addresses) and all(addr.is_global for addr in addresses)
    except (ValueError, OSError):
        return False


class PublicWebKnowledgeStore:
    """SQLite-backed public-source corpus with URL provenance and lexical retrieval."""

    def __init__(self, db_path: str = ".data/web_knowledge.sqlite3",
                 allowed_domains: set[str] | None = None, min_fetch_interval: float = 1.0):
        self.db_path = db_path
        self.allowed_domains = set(allowed_domains or DEFAULT_ALLOWED_DOMAINS)
        self.min_fetch_interval = max(0.0, min_fetch_interval)
        self._last_fetch = 0.0
        import os
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS web_documents (
                url TEXT PRIMARY KEY, canonical_url TEXT NOT NULL, title TEXT NOT NULL,
                content TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                fetched_at TEXT NOT NULL, http_status INTEGER NOT NULL,
                content_type TEXT NOT NULL, source_domain TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_web_docs_domain ON web_documents(source_domain)")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _validate_url(self, url: str) -> tuple[str, str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Only credential-free HTTPS URLs are accepted")
        host = parsed.hostname.lower().rstrip(".")
        if not any(host == d or host.endswith("." + d) for d in self.allowed_domains):
            raise ValueError("URL domain is not in UIE's approved public-source allowlist")
        if not _is_public_host(host):
            raise ValueError("URL host does not resolve exclusively to public IP addresses")
        return host, url

    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            req = Request(robots_url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=8) as response:
                body = response.read(256_000).decode("utf-8", "replace")
            parser.parse(body.splitlines())
            return parser.can_fetch(USER_AGENT, url)
        except (HTTPError, URLError, TimeoutError, OSError):
            # Fail closed if the site's crawl policy cannot be checked.
            return False

    def ingest_url(self, url: str) -> dict:
        host, original_url = self._validate_url(url)
        if not self._robots_allows(original_url):
            return {"url": original_url, "status": "skipped_robots_or_unavailable"}
        wait = self.min_fetch_interval - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        request = Request(original_url, headers={"User-Agent": USER_AGENT,
                                                  "Accept": "text/html,application/xhtml+xml"})
        try:
            with urlopen(request, timeout=15) as response:
                final_url = response.geturl()
                final_host, _ = self._validate_url(final_url)
                content_type = response.headers.get("Content-Type", "").lower()
                if "html" not in content_type:
                    return {"url": original_url, "status": "skipped_non_html"}
                body = response.read(MAX_BYTES + 1)
                status = int(getattr(response, "status", 200))
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return {"url": original_url, "status": "fetch_failed", "error": type(exc).__name__}
        finally:
            self._last_fetch = time.monotonic()
        if len(body) > MAX_BYTES:
            return {"url": original_url, "status": "skipped_too_large"}
        parser = _TextExtractor()
        parser.feed(body.decode("utf-8", "replace"))
        title = re.sub(r"\s+", " ", " ".join(parser.title)).strip()[:500] or final_host
        content = re.sub(r"[ \t]+", " ", "".join(parser.parts))
        content = re.sub(r"\n\s*\n+", "\n\n", content).strip()[:MAX_TEXT]
        if len(content) < 200:
            return {"url": original_url, "status": "skipped_insufficient_text"}
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute("""INSERT INTO web_documents
                (url,canonical_url,title,content,content_sha256,fetched_at,http_status,content_type,source_domain)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET canonical_url=excluded.canonical_url,
                title=excluded.title,content=excluded.content,content_sha256=excluded.content_sha256,
                fetched_at=excluded.fetched_at,http_status=excluded.http_status,
                content_type=excluded.content_type,source_domain=excluded.source_domain""",
                (original_url, final_url, title, content, digest, now, status, content_type, final_host))
        return {"url": original_url, "status": "stored", "title": title,
                "characters": len(content), "sha256": digest, "fetched_at": now}

    def search(self, query: str, limit: int = 5) -> list[Evidence]:
        terms = list(dict.fromkeys(re.findall(r"[a-zA-Z0-9_-]{3,}", query.lower())))[:16]
        if not terms:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM web_documents ORDER BY fetched_at DESC LIMIT 5000").fetchall()
        ranked = []
        for row in rows:
            haystack = (row["title"] + " " + row["content"]).lower()
            score = sum(haystack.count(term) for term in terms)
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda item: (item[0], item[1]["fetched_at"]), reverse=True)
        return [Evidence(source="uie-persistent-web-corpus", title=row["title"],
                         retrieved_at=row["fetched_at"], content=row["content"][:5000],
                         url=row["canonical_url"], confidence=min(0.9, 0.4 + score / 100),
                         metadata={"stored_url": row["url"], "source_domain": row["source_domain"],
                                   "content_sha256": row["content_sha256"],
                                   "retrieval": "lexical", "score": score})
                for score, row in ranked[:max(1, min(limit, 20))]]

    def stats(self) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS documents, COUNT(DISTINCT source_domain) AS domains, MAX(fetched_at) AS last_fetched FROM web_documents").fetchone()
        return dict(row)
