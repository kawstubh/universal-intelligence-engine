import asyncio

from universal_intelligence_engine.learning.scheduler import CuriosityScheduler


class FakeLearner:
    def __init__(self): self.calls = []
    def run_cycle(self, query):
        self.calls.append(query)
        return {"domains_processed": 3, "elapsed_seconds": 0.1, "timed_out": False,
                "discovery": [{"domain": "a", "queued_new": 5}, {"domain": "b", "queued_new": 5}, {"domain": "c", "queued_new": 5}]}


def test_scheduler_respects_bounded_budget_and_exits_cleanly(tmp_path):
    learner = FakeLearner()
    scheduler = CuriosityScheduler(learner, state_path=str(tmp_path / "scheduler.sqlite3"), interval_seconds=60, max_cycles=1)
    result = scheduler.run_once()
    assert result["status"] == "completed"
    assert result["duration_seconds"] <= 60
    assert result["result"]["domains_processed"] <= 3
    assert all(item["queued_new"] <= 5 for item in result["result"]["discovery"])
    health = scheduler.health()
    assert health["metrics"]["cycles"] == 1
    assert health["metrics"]["successes"] == 1
    async def lifecycle():
        await scheduler.start()
        await asyncio.sleep(0.02)
        await scheduler.stop()
        assert scheduler._task is None
    asyncio.run(lifecycle())


def test_scheduler_uses_shared_lease_to_prevent_overlapping_processes(tmp_path):
    path = str(tmp_path / "shared-scheduler.sqlite3")
    first = CuriosityScheduler(FakeLearner(), state_path=path)
    second = CuriosityScheduler(FakeLearner(), state_path=path)
    assert first._acquire_lease() is True
    try:
        assert second._acquire_lease() is False
    finally:
        first._release_lease()
    assert second._acquire_lease() is True
    second._release_lease()
