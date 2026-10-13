"""Public API for the Universal Intelligence Engine."""
from .agent import AgentRun, AgentStep, AutonomousAgent
from .algorithm import AdaptiveReasoningCore
from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from .dental_api import router as dental_router
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .governance import ChangeProposal, GovernanceError, HumanAuthorizationGate
from .knowledge import CompositeKnowledgeProvider, deduplicate_evidence, rank_evidence
from .locale import LocaleContext, resolve_locale
from .memory import LearningEvent, LearningMemory
from .planner import AdaptivePlanner, Plan, PlanStep, topological_steps, validate_plan
from .provenance import ProvenanceRecord
from .providers import (
    BraveSearchProvider,
    NoKnowledgeProvider,
    OpenAIResponsesReasoningProvider,
)
from .reasoning import NativeReasoningProvider
from .service import IntelligenceService

__version__ = "0.5.0"
__all__ = ["AdaptivePlanner", "AdaptiveReasoningCore", "AgentRun", "AgentStep", "AutonomousAgent", "BasicResponseEvaluator", "BraveSearchProvider", "ChangeProposal", "CompositeKnowledgeProvider", "Evidence", "GovernanceError", "HumanAuthorizationGate", "IntelligenceRequest", "IntelligenceResponse", "IntelligenceService", "LearningEvent", "LearningMemory", "LocaleContext", "NativeReasoningProvider", "NoKnowledgeProvider", "OpenAIResponsesReasoningProvider", "Plan", "PlanStep", "ProvenanceRecord", "UniversalIntelligenceEngine", "deduplicate_evidence", "dental_router", "rank_evidence", "resolve_locale", "topological_steps", "validate_plan"]
