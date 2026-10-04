"""Reference service facade for applications embedding UIE."""

from .contracts import IntelligenceRequest, IntelligenceResponse
from .engine import UniversalIntelligenceEngine


class IntelligenceService:
    def __init__(self, engine: UniversalIntelligenceEngine):
        self.engine = engine

    def execute(self, request: IntelligenceRequest) -> IntelligenceResponse:
        return self.engine.run(request)

    def health(self) -> dict[str, str]:
        return {"status": "ok", "engine": "universal-intelligence-engine"}
