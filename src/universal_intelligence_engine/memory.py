"""Controlled, append-only learning memory.

The engine learns from explicit outcomes and feedback. It does not rewrite
source code or silently change application policy.
"""

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LearningEvent:
    capability: str
    outcome: str
    reward: float
    context: dict[str, Any]


class LearningMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def record(self, event: LearningEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def summarize(self) -> dict[str, Any]:
        totals: dict[str, dict[str, float]] = {}
        if not self.path.is_file():
            return totals
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                bucket = totals.setdefault(event["capability"], {"count": 0, "reward": 0.0})
                bucket["count"] += 1
                bucket["reward"] += float(event.get("reward", 0.0))
            except (ValueError, KeyError, TypeError):
                continue
        for bucket in totals.values():
            bucket["average_reward"] = bucket["reward"] / max(1, bucket["count"])
        return totals
