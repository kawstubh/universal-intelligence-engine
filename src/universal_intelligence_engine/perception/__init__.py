"""Structured perception adapters. Binary imagery should be reduced to validated features upstream."""
from .multimodal_ingest import MultimodalIngestor, MultimodalIngestResult, ingest_multimodal_frame

__all__ = ["MultimodalIngestor", "MultimodalIngestResult", "ingest_multimodal_frame"]
