"""Native adaptive strategy selection for the Universal Intelligence Engine.

This layer learns which previously observed strategies perform best for a
capability/context pair. It does not call an external model and does not
mutate source code. Strategy changes remain data-driven and reversible.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable, Mapping


@dataclass(frozen=True)
class StrategyExperience:
    strategy: str
    reward: float
    context: str = "global"


@dataclass(frozen=True)
class StrategyProfile:
    strategy: str
    trials: int
    average_reward: float
    confidence: float


class AdaptiveStrategyEngine:
    """Selects strategies using a confidence-aware exploration policy.

    The score combines observed reward with an uncertainty bonus. This gives
    new strategies a fair opportunity while naturally favoring strategies
    with stronger measured outcomes.
    """

    def __init__(self, exploration: float = 0.35) -> None:
        if exploration < 0:
            raise ValueError("exploration must be non-negative")
        self.exploration = exploration
        self._history: dict[tuple[str, str], list[StrategyExperience]] = {}

    def learn(self, capability: str, experiences: Iterable[StrategyExperience]) -> None:
        for experience in experiences:
            reward = max(0.0, min(1.0, float(experience.reward)))
            key = (capability, experience.context)
            self._history.setdefault(key, []).append(
                StrategyExperience(experience.strategy, reward, experience.context)
            )

    def profiles(self, capability: str, context: str = "global") -> tuple[StrategyProfile, ...]:
        bucket = self._history.get((capability, context), [])
        by_strategy: dict[str, list[float]] = {}
        for item in bucket:
            by_strategy.setdefault(item.strategy, []).append(item.reward)

        profiles = []
        for strategy, rewards in by_strategy.items():
            n = len(rewards)
            mean = sum(rewards) / n
            confidence = 1.0 - 1.0 / sqrt(n + 1.0)
            profiles.append(StrategyProfile(strategy, n, mean, confidence))
        return tuple(sorted(profiles, key=lambda p: p.average_reward, reverse=True))

    def rank(
        self,
        capability: str,
        strategies: Iterable[str],
        context: str = "global",
    ) -> tuple[str, ...]:
        profiles = {p.strategy: p for p in self.profiles(capability, context)}
        total_trials = sum(p.trials for p in profiles.values())

        def score(strategy: str) -> float:
            profile = profiles.get(strategy)
            if profile is None:
                return 1.0 + self.exploration
            uncertainty = sqrt(
                2.0 * (total_trials + 1.0) / (profile.trials + 1.0)
            )
            return profile.average_reward + self.exploration * uncertainty

        return tuple(sorted(set(strategies), key=score, reverse=True))
