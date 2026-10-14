"""Learning components for extracting, querying and safely consolidating evidence."""

from .distiller import ClaimDistiller, DistilledClaim, distill_claims
from .contradiction import ContradictionEngine
from .query_claims import ClaimQueryService, query_claims
from .curiosity_agent import CuriosityAgent
from .heuristic_memory import HeuristicMemory
from .autonomous_learner import AutonomousLearner

__all__ = [
    "ClaimDistiller", "DistilledClaim", "distill_claims",
    "ContradictionEngine", "ClaimQueryService", "query_claims",
    "CuriosityAgent", "HeuristicMemory", "AutonomousLearner",
]
