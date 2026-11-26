from universal_intelligence_engine.contracts import IntelligenceRequest, IntelligenceResponse
from universal_intelligence_engine.dental import DENTAL_POLICY, dental_request
from universal_intelligence_engine.engine import UniversalIntelligenceEngine
from universal_intelligence_engine.policy import policy_for


class Knowledge:
    def __init__(self):
        self.last_request = None

    def search(self, request):
        self.last_request = request
        return []


class Reasoning:
    def reason(self, request, evidence):
        return IntelligenceResponse(
            answer={"domain": request.context},
            actions=(
                {"action": "confirm_treatment"},
                {"action": "unsafe_cross_domain_action"},
            ),
        )


def test_policy_filters_context_and_marks_approval():
    knowledge = Knowledge()
    engine = UniversalIntelligenceEngine(
        knowledge,
        Reasoning(),
        policy=DENTAL_POLICY,
    )
    request = dental_request(
        "review patient",
        {
            "patient_context": {"id": "p1"},
            "dental_chart": {"teeth": ["16"]},
            "media_projects": {"project": "secret"},
            "credentials": {"token": "secret"},
            "unknown_domain": {"x": 1},
        },
    )

    result = engine.run(request)

    assert set(knowledge.last_request.context) == {"patient_context", "dental_chart"}
    assert result.actions[0]["requires_human_approval"] is True
    assert all(a["action"] != "unsafe_cross_domain_action" for a in result.actions)


def test_policy_for_supports_least_privilege_context():
    policy = policy_for(context={"dental_chart"}, blocked_context={"secrets"})
    assert policy.filter_context({"dental_chart": 1, "appointments": 2, "secrets": 3}) == {
        "dental_chart": 1
    }


def test_allowlist_rejects_unknown_actions():
    policy = policy_for(allowed={"dental_chart_read"})
    assert policy.allows("dental_chart_read") is True
    assert policy.allows("unknown_action") is False


def test_explicit_deny_overrides_allowlist():
    policy = policy_for(allowed={"dental_chart_read"}, blocked={"dental_chart_read"})
    assert policy.allows("dental_chart_read") is False


def test_blocklist_only_policy_remains_supported():
    policy = policy_for(blocked={"unsafe_action"})
    assert policy.allows("ordinary_action") is True
    assert policy.allows("unsafe_action") is False
