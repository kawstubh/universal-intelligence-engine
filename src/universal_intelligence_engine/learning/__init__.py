"""Learning components for extracting, querying and safely consolidating evidence."""

from .distiller import ClaimDistiller, DistilledClaim, distill_claims
from .contradiction import ContradictionEngine
from .query_claims import ClaimQueryService, query_claims
from .curiosity_agent import CuriosityAgent
from .heuristic_memory import HeuristicMemory
from .autonomous_learner import AutonomousLearner
from .semantic_matcher import SemanticMatcher
from .graph_store import KnowledgeGraphStore
from .graph_reflexion import ReflexiveGraphValidator

__all__ = [
    "CognitiveMemoryEngine", "DeepReflexionEngine",
    "ClaimDistiller", "DistilledClaim", "distill_claims",
    "ContradictionEngine", "ClaimQueryService", "query_claims",
    "CuriosityAgent", "HeuristicMemory", "AutonomousLearner", "SemanticMatcher",
    "KnowledgeGraphStore", "ReflexiveGraphValidator", "TemporalResolver", "CuriosityScheduler", "EpistemicDiscoveryDispatcher",
]

from .temporal_resolver import TemporalResolver
from .scheduler import CuriosityScheduler

from .cognitive_memory import CognitiveMemoryEngine
from .deep_reflexion import DeepReflexionEngine

from .epistemic_dispatcher import EpistemicDiscoveryDispatcher
