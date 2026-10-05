from universal_intelligence_engine.adaptive_learning import (
    AdaptiveStrategyEngine,
    StrategyExperience,
)


def test_unseen_strategy_gets_exploration_priority():
    engine = AdaptiveStrategyEngine(exploration=0.2)
    engine.learn(
        "research",
        [StrategyExperience("old", 0.95), StrategyExperience("old", 0.90)],
    )
    ranked = engine.rank("research", ["old", "new"])
    assert ranked[0] == "new"


def test_strong_strategy_becomes_preferred_after_evidence():
    engine = AdaptiveStrategyEngine(exploration=0.0)
    engine.learn(
        "dental",
        [
            StrategyExperience("A", 0.9),
            StrategyExperience("A", 0.8),
            StrategyExperience("B", 0.3),
            StrategyExperience("B", 0.4),
        ],
    )
    assert engine.rank("dental", ["A", "B"])[0] == "A"


def test_profiles_are_context_specific():
    engine = AdaptiveStrategyEngine(exploration=0.0)
    engine.learn("video", [StrategyExperience("A", 0.9, "short-form")])
    engine.learn("video", [StrategyExperience("B", 0.9, "long-form")])

    assert engine.profiles("video", "short-form")[0].strategy == "A"
    assert engine.profiles("video", "long-form")[0].strategy == "B"
