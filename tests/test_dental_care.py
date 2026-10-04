from universal_intelligence_engine.dental_care import (
    draft_care_pathway,
    draft_referral,
)


def test_known_signal_creates_reviewable_care_draft():
    result = draft_care_pathway("rct", tooth_id="16", chart_status="RCT")

    assert result.tooth_id == "16"
    assert result.pathway == "root_canal_care"
    assert result.clinician_review_required is True
    assert "restoration/coronal protection review" in result.requirements


def test_unknown_signal_falls_back_to_clinical_review():
    result = draft_care_pathway("unknown", tooth_id="21")

    assert result.pathway == "clinical_review"
    assert result.requirements == ("clinician assessment",)


def test_referral_draft_does_not_choose_a_facility():
    result = draft_referral(
        "oral surgery",
        facility_requirements=("appropriate surgical capability",),
        available_information={"patient context": {}, "media_projects": {"x": 1}},
    )

    assert result.specialty == "oral surgery"
    assert result.facility_requirements == ("appropriate surgical capability",)
    assert "patient context" in result.information_to_include
    assert "media_projects" not in result.information_to_include
    assert result.clinician_approval_required is True
