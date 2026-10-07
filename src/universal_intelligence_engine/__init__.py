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
from .planner import AdaptivePlanner, Plan, PlanStep, topological_steps, validate_plan
from .service import IntelligenceService
from .self_improvement_runtime import (
    SandboxPolicy,
    SandboxResult,
    SelfImprovementSandbox,
    RuntimeSelfImprovementEngine,
)

__version__ = "0.4.1"

__all__ = [
    "AdaptiveReasoningCore", "AdaptivePlanner", "AgentRun", "AgentStep",
    "AutonomousAgent", "BasicResponseEvaluator", "BraveSearchProvider",
    "ChangeProposal", "CompositeKnowledgeProvider", "Evidence", "GovernanceError",
    "HumanAuthorizationGate", "IntelligenceRequest", "IntelligenceResponse",
    "IntelligenceService", "LearningEvent", "LearningMemory", "LocaleContext",
    "NativeReasoningProvider", "OpenAIResponsesReasoningProvider",
    "ProvenanceRecord", "Plan", "PlanStep", "UniversalIntelligenceEngine",
    "SandboxPolicy", "SandboxResult", "SelfImprovementSandbox",
    "RuntimeSelfImprovementEngine", "deduplicate_evidence", "rank_evidence",
    "resolve_locale", "topological_steps", "validate_plan",
]
