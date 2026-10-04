"""Public API for the Universal Intelligence Engine."""

from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from .engine import UniversalIntelligenceEngine
from .knowledge import CompositeKnowledgeProvider, deduplicate_evidence, rank_evidence
from .locale import LocaleContext, resolve_locale
from .memory import LearningEvent, LearningMemory
from .provenance import ProvenanceRecord
from .service import IntelligenceService

__version__ = "0.2.0"

__all__ = [
    "CompositeKnowledgeProvider",
    "Evidence",
    "IntelligenceRequest",
    "IntelligenceResponse",
    "IntelligenceService",
    "LearningEvent",
    "LearningMemory",
    "LocaleContext",
    "ProvenanceRecord",
    "UniversalIntelligenceEngine",
    "deduplicate_evidence",
    "rank_evidence",
    "resolve_locale",
]
