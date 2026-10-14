"""Public PRM and test-time deliberation interface.

The implementation uses bounded evidence trajectories rather than unconstrained
free-form hidden reasoning; each step exposes auditable PRM dimensions.
"""
from .deliberation import DeliberationBudget, DeliberationController, ProcessRewardEvaluator

PRMDeliberationEngine = DeliberationController
PRMDeliberationController = DeliberationController
StepLevelProcessRewardModel = ProcessRewardEvaluator

__all__ = ["DeliberationBudget", "DeliberationController", "ProcessRewardEvaluator",
           "PRMDeliberationEngine", "PRMDeliberationController", "StepLevelProcessRewardModel"]
