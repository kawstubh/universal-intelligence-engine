"""Controlled, append-only learning memory.

The engine learns from explicit outcomes and feedback. It does not rewrite
source code or silently change application policy.
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class LearningEvent:
    capability: str
    outcome: str
    reward: float
    context: dict[str, Any]


class LearningMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def record(self, event: LearningEvent | Mapping[str, Any]) -> None:
        """Persist a normalized learning event without allowing code mutation."""
        if isinstance(event, LearningEvent):
            normalized = asdict(event)
        else:
            evaluation = event.get("evaluation", {})
            if not isinstance(evaluation, Mapping):
                evaluation = {}
            normalized = {
                "capability": str(event.get("capability") or event.get("goal") or "unknown"),
                "outcome": str(event.get("outcome") or ("success" if evaluation.get("passed") else "completed")),
                "reward": float(event.get("reward", evaluation.get("score", 0.0)) or 0.0),
                "context": dict(event),
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(normalized, ensure_ascii=False) + "\n")

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
