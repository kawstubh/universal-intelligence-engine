"""Public API for the Universal Intelligence Engine."""

from .algorithm import AdaptiveReasoningCore
from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .governance import ChangeProposal, GovernanceError, HumanAuthorizationGate
from .knowledge import CompositeKnowledgeProvider, deduplicate_evidence, rank_evidence
from .locale import LocaleContext, resolve_locale
from .memory import LearningEvent, LearningMemory
from .provenance import ProvenanceRecord
from .providers import BraveSearchProvider, OpenAIResponsesReasoningProvider
from .reasoning import NativeReasoningProvider
from .agent import AgentRun, AgentStep, AutonomousAgent
from .service import IntelligenceService

__version__ = "0.4.0"

__all__ = [
    "AdaptiveReasoningCore",
    "AgentRun",
    "AgentStep",
    "AutonomousAgent",
    "BasicResponseEvaluator",
    "BraveSearchProvider",
    "ChangeProposal",
    "CompositeKnowledgeProvider",
    "Evidence",
    "GovernanceError",
    "HumanAuthorizationGate",
    "IntelligenceRequest",
    "IntelligenceResponse",
    "IntelligenceService",
    "LearningEvent",
    "LearningMemory",
    "LocaleContext",
    "NativeReasoningProvider",
    "OpenAIResponsesReasoningProvider",
    "ProvenanceRecord",
    "UniversalIntelligenceEngine",
    "deduplicate_evidence",
    "rank_evidence",
    "resolve_locale",
]
