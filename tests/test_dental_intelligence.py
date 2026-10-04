from universal_intelligence_engine.contracts import IntelligenceResponse
from universal_intelligence_engine.dental import DENTAL_POLICY, dental_request
from universal_intelligence_engine.dental_care import draft_care_pathway, draft_referral
from universal_intelligence_engine.engine import UniversalIntelligenceEngine


class Knowledge:
    def __init__(self):
        self.last_request = None

    def search(self, request):
        self.last_request = request
        return []


class Reasoning:
    def reason(self, request, evidence):
        return IntelligenceResponse(
            answer={"context": request.context},
            actions=(
                {"action": "confirm_treatment"},
                {"action": "unsafe_cross_domain_action"},
            ),
        )


def test_dental_policy_isolates_context_and_actions():
    knowledge = Knowledge()
    result = UniversalIntelligenceEngine(knowledge, Reasoning(), policy=DENTAL_POLICY).run(
        dental_request("review patient", {
            "patient_context": {"id": "p1"},
            "dental_chart": {"teeth": ["16"]},
            "media_projects": {"project": "private"},
            "credentials": {"token": "private"},
            "unknown_domain": {"x": 1},
        })
    )
    assert set(knowledge.last_request.context) == {"patient_context", "dental_chart"}
    assert result.actions[0]["requires_human_approval"] is True
    assert all(a["action"] != "unsafe_cross_domain_action" for a in result.actions)


def test_care_pathway_is_explicitly_reviewable():
    result = draft_care_pathway("rct", tooth_id="16", chart_status="RCT")
    assert result.pathway == "root_canal_care"
    assert result.clinician_review_required is True


def test_unknown_care_signal_falls_back_to_clinical_review():
    result = draft_care_pathway("unknown", tooth_id="21")
    assert result.pathway == "clinical_review"


def test_referral_draft_never_selects_a_facility():
    result = draft_referral(
        "oral surgery",
        facility_requirements=("appropriate surgical capability",),
        available_information={"patient context": {}, "media_projects": {"x": 1}},
    )
    assert result.specialty == "oral surgery"
    assert "patient context" in result.information_to_include
    assert "media_projects" not in result.information_to_include
    assert result.clinician_approval_required is True
