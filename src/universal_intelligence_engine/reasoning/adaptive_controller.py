"""Reactive, bounded feedback controller for simulated UIE environments.

This controller only re-scores plans; it never actuates physical systems. Incoming
observations are untrusted and all control decisions are auditable ledger events.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from ..security.provenance_crypto import append_integrity_event, ensure_integrity_ledger, verify_integrity_ledger

EVENT_TYPES = frozenset({"sensor_telemetry", "spatial_shift", "adversarial_alert"})


@dataclass(frozen=True)
class ControlAdjustment:
    event_id: str
    event_type: str
    premise_invalidated: bool
    trajectory_rescored: bool
    compute_budget: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    ledger_sequence: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "event_type": self.event_type,
                "premise_invalidated": self.premise_invalidated,
                "trajectory_rescored": self.trajectory_rescored,
                "compute_budget": self.compute_budget, "reasons": list(self.reasons),
                "ledger_sequence": self.ledger_sequence}


class AdaptiveEnvironmentController:
    """Bounded feedback loop that invalidates premises and invokes a re-score callback.

    Premises are explicitly registered as ``{name: expected_value}``; events only
    invalidate a premise when their ``observations`` mapping disagrees. Compute
    budget is a logical deliberation allowance, not a wall-clock or hardware promise.
    """

    def __init__(self, db_path: str | Path, *, rescore: Callable[[Mapping[str, Any]], Any] | None = None,
                 base_compute_budget: int = 4, max_compute_budget: int = 16,
                 rapid_spatial_threshold: float = 0.15, high_causal_risk_threshold: float = 0.65):
        if base_compute_budget < 1 or max_compute_budget < base_compute_budget:
            raise ValueError("compute budgets must satisfy 1 <= base <= max")
        if not 0 <= rapid_spatial_threshold <= 1 or not 0 <= high_causal_risk_threshold <= 1:
            raise ValueError("risk and spatial thresholds must be between 0 and 1")
        self.db_path = Path(db_path)
        self.rescore = rescore
        self.base_compute_budget = base_compute_budget
        self.max_compute_budget = max_compute_budget
        self.rapid_spatial_threshold = rapid_spatial_threshold
        self.high_causal_risk_threshold = high_causal_risk_threshold
        self._premises: dict[str, Any] = {}
        self._last_spatial_delta = 0.0

    def register_active_premises(self, premises: Mapping[str, Any]) -> None:
        if not isinstance(premises, Mapping) or len(premises) > 2_000:
            raise ValueError("premises must be a bounded mapping")
        if any(not isinstance(key, str) or not key.strip() or len(key) > 256 for key in premises):
            raise ValueError("premise names must be non-empty strings up to 256 characters")
        # Snapshot values through JSON so callers cannot mutate the active premise set.
        self._premises = json.loads(json.dumps(dict(premises), allow_nan=False))

    def process_event(self, event: Mapping[str, Any]) -> ControlAdjustment:
        if not isinstance(event, Mapping):
            raise ValueError("event must be a mapping")
        event_type = event.get("event_type")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"event_type must be one of {sorted(EVENT_TYPES)}")
        observations = event.get("observations", {})
        if not isinstance(observations, Mapping) or len(observations) > 2_000:
            raise ValueError("observations must be a bounded mapping")
        risk = event.get("causal_risk", 0.0)
        delta = event.get("spatial_delta", 0.0)
        if isinstance(risk, bool) or not isinstance(risk, (int, float)) or not 0 <= risk <= 1:
            raise ValueError("causal_risk must be between 0 and 1")
        if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not 0 <= delta <= 1:
            raise ValueError("spatial_delta must be between 0 and 1")
        invalidated = []
        for name, actual in observations.items():
            if name in self._premises and self._premises[name] != actual:
                invalidated.append(name)
                self._premises.pop(name, None)
        rapid = event_type == "spatial_shift" and float(delta) >= self.rapid_spatial_threshold
        high_risk = float(risk) >= self.high_causal_risk_threshold
        adversarial = event_type == "adversarial_alert"
        budget = self.base_compute_budget
        reasons = []
        if invalidated:
            reasons.append("active_premise_invalidated:" + ",".join(sorted(invalidated)))
        if rapid:
            budget += self.base_compute_budget
            reasons.append("rapid_spatial_change")
        if high_risk:
            budget += self.base_compute_budget * 2
            reasons.append("high_causal_risk")
        if adversarial:
            budget += self.base_compute_budget
            reasons.append("adversarial_alert_requires_revalidation")
        budget = min(self.max_compute_budget, budget)
        should_rescore = bool(invalidated or rapid or high_risk or adversarial)
        event_id = str(event.get("event_id") or uuid.uuid4())
        result = {"event_id": event_id, "event_type": event_type,
                  "observations": dict(observations), "invalidated_premises": sorted(invalidated),
                  "trajectory_rescored": should_rescore, "compute_budget": budget,
                  "reasons": reasons, "observed_at": event.get("observed_at", time.time()),
                  "actuation_permitted": False}
        if should_rescore and self.rescore is not None:
            self.rescore(result)
        with sqlite3.connect(str(self.db_path), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            ensure_integrity_ledger(conn)
            valid, error = verify_integrity_ledger(conn)
            if not valid:
                raise RuntimeError(f"integrity ledger invalid before control event: {error}")
            row = append_integrity_event(conn, "adaptive_controller", event_id, "CONTROL_ADJUSTMENT", result)
            valid, error = verify_integrity_ledger(conn)
            if not valid:
                raise RuntimeError(f"integrity ledger invalid after control event: {error}")
        return ControlAdjustment(event_id, str(event_type), bool(invalidated), should_rescore,
                                 budget, tuple(reasons), int(row["sequence_id"]))
