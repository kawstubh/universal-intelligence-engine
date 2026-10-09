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
from .providers import BraveSearchProvider, NoKnowledgeProvider, OpenAIResponsesReasoningProvider
from .reasoning import NativeReasoningProvider
from .agent import AgentRun, AgentStep, AutonomousAgent
from .planner import AdaptivePlanner, Plan, PlanStep, topological_steps, validate_plan
from .service import IntelligenceService
from .eyes import Observation, WebObserver
from .hands import ActionResult, HttpHand
from .runtime import RuntimeEvent, SenseActRuntime

__version__ = "0.5.0"

__all__ = [
    "AdaptiveReasoningCore",
    "AdaptivePlanner",
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
    "Observation",
    "WebObserver",
    "ActionResult",
    "HttpHand",
    "RuntimeEvent",
    "SenseActRuntime",
    "LearningEvent",
    "LearningMemory",
    "LocaleContext",
    "NativeReasoningProvider",
    "NoKnowledgeProvider",
    "OpenAIResponsesReasoningProvider",
    "ProvenanceRecord",
    "Plan",
    "PlanStep",
    "UniversalIntelligenceEngine",
    "deduplicate_evidence",
    "rank_evidence",
    "resolve_locale",
    "topological_steps",
    "validate_plan",
]
