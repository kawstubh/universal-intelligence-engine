"""Validated structured spatial and telemetry ingestion into UIE's relational graph."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..learning.graph_store import KnowledgeGraphStore

MAX_ENTITIES = 2_000
MAX_TELEMETRY_FIELDS = 2_000
MAX_LABEL_LENGTH = 256


@dataclass(frozen=True)
class MultimodalIngestResult:
    source_id: str
    frame_type: str
    entities_ingested: int
    relations_created: int
    telemetry_claims_created: int
    rejected_items: list[dict[str, str]] = field(default_factory=list)
    raw_payload_persisted: bool = False


class MultimodalIngestor:
    """Ingest structured features only; raster/binary payloads are neither stored nor returned."""

    def __init__(self, graph_store: KnowledgeGraphStore, *, adjacency_distance: float = 0.15,
                 proximity_distance: float = 0.35):
        if adjacency_distance < 0 or proximity_distance < adjacency_distance:
            raise ValueError("distance bounds must be non-negative and proximity >= adjacency")
        self.graph_store = graph_store
        self.adjacency_distance = float(adjacency_distance)
        self.proximity_distance = float(proximity_distance)

    @staticmethod
    def _bbox(value: Any) -> tuple[float, float, float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError("bounding_box must be [ymin, xmin, ymax, xmax]")
        coords = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in coords):
            raise ValueError("bounding_box coordinates must be finite")
        ymin, xmin, ymax, xmax = coords
        if ymin > ymax or xmin > xmax:
            raise ValueError("bounding_box minima must not exceed maxima")
        return coords

    @staticmethod
    def _center(box: tuple[float, float, float, float]) -> tuple[float, float]:
        ymin, xmin, ymax, xmax = box
        return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)

    @staticmethod
    def _contains(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
        ay0, ax0, ay1, ax1 = a
        by0, bx0, by1, bx1 = b
        return ay0 <= by0 and ax0 <= bx0 and ay1 >= by1 and ax1 >= bx1 and a != b

    def ingest_frame(self, frame: Mapping[str, Any]) -> MultimodalIngestResult:
        if not isinstance(frame, Mapping):
            raise ValueError("frame must be a mapping")
        source_id = frame.get("source_id")
        frame_type = frame.get("frame_type")
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 256:
            raise ValueError("source_id must be a non-empty string up to 256 characters")
        if frame_type not in {"spatial_grid", "sensor_telemetry"}:
            raise ValueError("unsupported frame_type")
        entities = frame.get("entities", [])
        telemetry = frame.get("telemetry", {})
        if not isinstance(entities, list) or len(entities) > MAX_ENTITIES:
            raise ValueError("entities must be a list with at most 2000 entries")
        if not isinstance(telemetry, Mapping) or len(telemetry) > MAX_TELEMETRY_FIELDS:
            raise ValueError("telemetry must be a mapping with at most 2000 fields")

        validated: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        if frame_type == "spatial_grid":
            for index, entity in enumerate(entities):
                try:
                    if not isinstance(entity, Mapping):
                        raise ValueError("entity must be a mapping")
                    entity_id = entity.get("id")
                    label = entity.get("label")
                    confidence = float(entity.get("confidence", 0.5))
                    if not isinstance(entity_id, str) or not entity_id.strip() or len(entity_id) > 256:
                        raise ValueError("id must be a non-empty string up to 256 characters")
                    if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL_LENGTH:
                        raise ValueError("label must be a non-empty string up to 256 characters")
                    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                        raise ValueError("confidence must be finite and between 0 and 1")
                    box = self._bbox(entity.get("bounding_box"))
                    validated.append({"id": entity_id, "label": label.strip(), "confidence": confidence, "box": box})
                except (TypeError, ValueError) as exc:
                    rejected.append({"item": str(index), "reason": str(exc)})
            for item in validated:
                self.graph_store.ingest_claim({
                    "subject": f"{source_id}:{item['id']}", "predicate": "OBSERVED_AS",
                    "object_claim": item["label"], "confidence": item["confidence"],
                    "domain": "spatial_perception", "provenance_url": f"uie-spatial://{source_id}",
                    "status": "unverified_claim",
                })
            relation_count = 0
            for i, first in enumerate(validated):
                for second in validated[i + 1:]:
                    a, b = first["box"], second["box"]
                    distance = math.dist(self._center(a), self._center(b))
                    base = {"confidence": min(first["confidence"], second["confidence"]),
                            "domain": "spatial_perception", "provenance_url": f"uie-spatial://{source_id}",
                            "status": "unverified_claim"}
                    # Both directions of containment are considered, but a pair is emitted once.
                    if self._contains(a, b):
                        self.graph_store.ingest_claim({**base, "subject": f"{source_id}:{first['id']}",
                            "predicate": "CONTAINS", "object_claim": f"{source_id}:{second['id']}"})
                        relation_count += 1
                    elif self._contains(b, a):
                        self.graph_store.ingest_claim({**base, "subject": f"{source_id}:{second['id']}",
                            "predicate": "CONTAINS", "object_claim": f"{source_id}:{first['id']}"})
                        relation_count += 1
                    if distance <= self.adjacency_distance:
                        self.graph_store.ingest_claim({**base, "subject": f"{source_id}:{first['id']}",
                            "predicate": "ADJACENT_TO", "object_claim": f"{source_id}:{second['id']}"})
                        relation_count += 1
                    elif distance <= self.proximity_distance:
                        self.graph_store.ingest_claim({**base, "subject": f"{source_id}:{first['id']}",
                            "predicate": "PROXIMATE_TO", "object_claim": f"{source_id}:{second['id']}"})
                        relation_count += 1
        else:
            # Telemetry is normalized into scalar claims; opaque nested/binary values are discarded.
            relation_count = 0
            for key, value in telemetry.items():
                if not isinstance(key, str) or not key.strip() or len(key) > 256:
                    rejected.append({"item": "telemetry", "reason": "telemetry keys must be non-empty strings up to 256 characters"})
                    continue
                if isinstance(value, bool) or not isinstance(value, (str, int, float)) or (isinstance(value, float) and not math.isfinite(value)):
                    rejected.append({"item": f"telemetry.{key}", "reason": "only finite numeric, string, and boolean-free scalar values are accepted"})
                    continue
                scalar = str(value)
                if len(scalar) > 512:
                    rejected.append({"item": f"telemetry.{key}", "reason": "scalar value exceeds 512 characters"})
                    continue
                self.graph_store.ingest_claim({"subject": f"{source_id}:telemetry", "predicate": f"MEASURED_{key}",
                    "object_claim": scalar, "confidence": 0.7, "domain": "sensor_telemetry",
                    "provenance_url": f"uie-telemetry://{source_id}", "status": "unverified_claim"})
        # Do not retain/return input frame; callers may discard their raster/binary payload now.
        return MultimodalIngestResult(source_id, str(frame_type), len(validated), relation_count,
                                      len(telemetry) if frame_type == "sensor_telemetry" else 0, rejected, False)


def ingest_multimodal_frame(frame: Mapping[str, Any], graph_store: KnowledgeGraphStore, *,
                            adjacency_distance: float = 0.15, proximity_distance: float = 0.35) -> MultimodalIngestResult:
    return MultimodalIngestor(graph_store, adjacency_distance=adjacency_distance,
                              proximity_distance=proximity_distance).ingest_frame(frame)
