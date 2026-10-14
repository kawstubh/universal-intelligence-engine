"""Bounded cross-domain discovery queue for the public-web knowledge corpus.

Discovery is opt-in and batch-limited; this module does not run a background crawler.
"""
from __future__ import annotations

import json
import os
import sqlite3
import xml.etree.ElementTree as ET
from urllib.request import Request
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError

from ..web_knowledge import PublicWebKnowledgeStore
from .security import UnsafeTargetError, safe_urlopen, validate_outbound_url

DEFAULT_SEEDS = Path(__file__).resolve().parents[1] / "config" / "seeds.json"
DOMAIN_REGISTRY = {
    "health_life_sciences": ("who.int", "cdc.gov", "nih.gov", "ncbi.nlm.nih.gov", "europepmc.org", "nhs.uk", "medlineplus.gov"),
    "defence_geopolitics": ("sipri.org", "nato.int", "defense.gov", "rand.org", "csis.org"),
    "politics_government_countries": ("un.org", "worldbank.org", "imf.org", "india.gov.in", "gov.uk", "usa.gov", "europa.eu"),
    "technology_science": ("arxiv.org", "w3.org", "docs.python.org", "developer.mozilla.org", "nature.com", "science.org", "ieee.org", "acm.org"),
    "markets_finance_industry": ("sec.gov", "rbi.org.in", "sebi.gov.in", "nseindia.com", "bseindia.com", "federalreserve.gov", "bls.gov", "bea.gov"),
    "daily_needs_agriculture_environment": ("fao.org", "usda.gov", "unep.org", "noaa.gov", "iea.org", "ipcc.ch", "weather.gov"),
    "news_current_affairs": ("bbc.com", "bbc.co.uk", "reuters.com", "apnews.com", "theguardian.com", "thehindu.com", "indianexpress.com", "dw.com"),
}


def classify_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    for category, domains in DOMAIN_REGISTRY.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return category
    return "general_reference"


class CrossDomainIngestionPipeline:
    """SQLite-backed discovery queue, sharing the approved corpus fetch policy."""

    def __init__(self, store: PublicWebKnowledgeStore, queue_path: str = ".data/ingestion_queue.sqlite3", seed_path: str | None = None):
        self.store = store
        self.queue_path = queue_path
        os.makedirs(os.path.dirname(queue_path) or ".", exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS ingestion_queue (
                url TEXT PRIMARY KEY, category TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0, last_status TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        self.seed_path = Path(seed_path) if seed_path else DEFAULT_SEEDS

    def _connect(self):
        conn = sqlite3.connect(self.queue_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, urls: list[str], category: str | None = None) -> dict:
        added = 0
        rejected = 0
        for url in urls:
            try:
                host, _ = self.store._validate_url(url)
            except (ValueError, OSError):
                rejected += 1
                continue
            tagged = category or classify_domain(url)
            with self._connect() as conn:
                cur = conn.execute("INSERT OR IGNORE INTO ingestion_queue(url,category) VALUES (?,?)", (url, tagged))
                added += cur.rowcount
        return {"queued": added, "rejected": rejected, "total": self.stats()["queued"]}

    def seed(self) -> dict:
        data = json.loads(self.seed_path.read_text(encoding="utf-8-sig"))
        added = rejected = 0
        for category, urls in data.items():
            for url in urls:
                try:
                    self.store._validate_url(url)
                except (ValueError, OSError):
                    rejected += 1
                    continue
                with self._connect() as conn:
                    added += conn.execute(
                        "INSERT OR IGNORE INTO ingestion_queue(url,category) VALUES (?,?)",
                        (url, category),
                    ).rowcount
        return {"queued": added, "rejected": rejected, "total": self.stats()["queued"], "seed_categories": len(data)}

    def discover(self, max_sources: int = 3, max_urls_per_source: int = 20) -> dict:
        """Read a tiny bounded set of sitemap/feed seeds and enqueue approved page URLs."""
        max_sources = max(1, min(int(max_sources), 5))
        max_urls_per_source = max(1, min(int(max_urls_per_source), 50))
        data = json.loads(self.seed_path.read_text(encoding="utf-8-sig"))
        sources = [(category, url) for category, urls in data.items() for url in urls][:max_sources]
        discovered, rejected, checked = [], 0, 0
        for category, source_url in sources:
            try:
                self.store._validate_url(source_url)
                if not self.store._robots_allows(source_url):
                    continue
                request = Request(source_url, headers={"User-Agent": "UIE-PublicKnowledgeBot/0.1 (+respectful public research)", "Accept": "application/xml, application/rss+xml, application/atom+xml, text/xml"})
                with safe_urlopen(request, timeout=5, allowed_domains=self.store.allowed_domains) as response:
                    final_url = response.geturl()
                    validate_outbound_url(final_url, allowed_domains=self.store.allowed_domains)
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > 2_000_000:
                        continue
                    body = response.read(2_000_001)
                    if len(body) > 2_000_000:
                        continue
                root = ET.fromstring(body)
                root_tag = root.tag.rsplit("}", 1)[-1].lower()
                if root_tag not in {"urlset", "sitemapindex", "rss", "feed", "rdf"}:
                    continue
                candidates = []
                for element in root.iter():
                    tag = element.tag.rsplit("}", 1)[-1].lower()
                    if tag == "loc" and element.text:
                        candidates.append(element.text.strip())
                    elif tag == "link":
                        href = element.attrib.get("href")
                        rel = element.attrib.get("rel", "alternate")
                        if href and rel in {"alternate", "canonical"}:
                            candidates.append(href.strip())
                for candidate in candidates:
                    if len(discovered) >= max_urls_per_source:
                        break
                    try:
                        self.store._validate_url(candidate)
                    except (ValueError, OSError):
                        rejected += 1
                        continue
                    discovered.append((category, candidate))
                checked += 1
            except (ValueError, OSError, HTTPError, URLError, TimeoutError, ET.ParseError, UnsafeTargetError):
                # One malformed/unavailable feed must not halt the batch.
                continue
        added = 0
        for category, url in discovered:
            with self._connect() as conn:
                added += conn.execute("INSERT OR IGNORE INTO ingestion_queue(url,category) VALUES (?,?)", (url, category)).rowcount
        return {"sources_checked": checked, "discovered_candidates": len(discovered), "rejected_candidates": rejected, "queued_new": added, "queue": self.stats()}

    def run_batch(self, limit: int = 5) -> dict:
        limit = max(1, min(int(limit), 10))
        with self._connect() as conn:
            rows = conn.execute("SELECT url,category FROM ingestion_queue WHERE status='queued' ORDER BY rowid LIMIT ?", (limit,)).fetchall()
        results = []
        for row in rows:
            url = row["url"]
            try:
                result = self.store.ingest_url(url)
            except (ValueError, OSError) as exc:
                result = {"url": url, "status": "rejected", "reason": type(exc).__name__}
            status = result.get("status", "failed")
            with self._connect() as conn:
                current = conn.execute("SELECT attempts FROM ingestion_queue WHERE url=?", (url,)).fetchone()
                attempts = int(current["attempts"]) + 1
                retryable = status in {"fetch_failed", "skipped_robots_or_unavailable", "failed"}
                # Retry transient failures up to three attempts; no endless hot loops.
                next_state = "queued" if retryable and attempts < 3 else ("done" if status == "stored" else "skipped")
                conn.execute("UPDATE ingestion_queue SET status=?,attempts=?,last_status=?,updated_at=CURRENT_TIMESTAMP WHERE url=?", (next_state, attempts, status, url))
            results.append({**result, "category": row["category"], "attempts": attempts, "retry_scheduled": next_state == "queued"})
        return {"processed": len(results), "results": results, "queue": self.stats()}

    def stats(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT status,COUNT(*) AS n FROM ingestion_queue GROUP BY status").fetchall()
        counts = {row["status"]: row["n"] for row in rows}
        return {"queued": counts.get("queued", 0), "done": counts.get("done", 0), "skipped": counts.get("skipped", 0), "total": sum(counts.values())}
