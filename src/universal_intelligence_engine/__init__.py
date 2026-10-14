"""Public API for the Universal Intelligence Engine."""

from .agent import AgentRun, AgentStep, AutonomousAgent
from .algorithm import AdaptiveReasoningCore
from .contracts import Evidence, IntelligenceRequest, IntelligenceResponse
from .engine import UniversalIntelligenceEngine
from .evaluation import BasicResponseEvaluator
from .eyes import Observation, WebObserver
from .governance import ChangeProposal, GovernanceError, HumanAuthorizationGate
from .hands import ActionResult, HttpHand
from .knowledge import CompositeKnowledgeProvider, deduplicate_evidence, rank_evidence
from .locale import LocaleContext, resolve_locale
from .memory import LearningEvent, LearningMemory
from .planner import AdaptivePlanner, Plan, PlanStep, topological_steps, validate_plan
from .provenance import ProvenanceRecord
from .providers import FreePublicKnowledgeProvider, OpenAIResponsesReasoningProvider
from .reasoning import DeliberationBudget, DeliberationController, ProcessRewardEvaluator, UIEReasoningCoordinator, ToolReflexionEngine, PRMDeliberationEngine, StepLevelProcessRewardModel, NativeReasoningProvider
from .runtime import RuntimeEvent, SenseActRuntime
from .service import IntelligenceService

__version__ = "0.5.0"

__all__ = [
    "ActionResult",
    "AdaptivePlanner",
    "AdaptiveReasoningCore",
    "AgentRun",
    "AgentStep",
    "AutonomousAgent",
    "BasicResponseEvaluator",
    "ChangeProposal",
    "CompositeKnowledgeProvider",
    "CognitiveMemoryEngine",
    "DeepReflexionEngine",
    "Evidence",
    "DeliberationBudget",
    "DeliberationController",
    "ProcessRewardEvaluator",
    "UIEReasoningCoordinator",
    "ToolReflexionEngine",
    "PRMDeliberationEngine",
    "StepLevelProcessRewardModel",
    "FreePublicKnowledgeProvider",
    "GovernanceError",
    "HttpHand",
    "HumanAuthorizationGate",
    "IntelligenceRequest",
    "IntelligenceResponse",
    "IntelligenceService",
    "LearningEvent",
    "LearningMemory",
    "LocaleContext",
    "NativeReasoningProvider",
    "Observation",
    "OpenAIResponsesReasoningProvider",
    "Plan",
    "PlanStep",
    "ProvenanceRecord",
    "RuntimeEvent",
    "SenseActRuntime",
    "UniversalIntelligenceEngine",
    "WebObserver",
    "deduplicate_evidence",
    "rank_evidence",
    "resolve_locale",
    "topological_steps",
    "validate_plan",
]

from .learning.cognitive_memory import CognitiveMemoryEngine
from .learning.deep_reflexion import DeepReflexionEngine
