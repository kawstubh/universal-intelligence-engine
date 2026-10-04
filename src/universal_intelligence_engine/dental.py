"""Dental domain adapter for the Universal Intelligence Engine."""

from .contracts import IntelligenceRequest
from .policy import CapabilityPolicy, policy_for


DENTAL_CONTEXT = frozenset({
    "patient_context", "appointments", "dental_chart", "screening",
    "care_pathway", "referral", "practice",
})

DENTAL_TOOLS = frozenset({
    "dental_chart_read", "screening_read", "care_pathway_draft",
    "referral_draft", "patient_message_draft", "practice_analytics_read",
})

DENTAL_HUMAN_APPROVAL = frozenset({
    "confirm_treatment", "approve_referral",
    "send_clinical_instruction", "order_care",
})

DENTAL_BLOCKED_CONTEXT = frozenset({
    "media_projects", "other_patient_records", "finance_private",
    "secrets", "credentials",
})

DENTAL_POLICY = policy_for(
    allowed=DENTAL_TOOLS,
    approval=DENTAL_HUMAN_APPROVAL,
    context=DENTAL_CONTEXT,
    blocked_context=DENTAL_BLOCKED_CONTEXT,
)


def dental_request(goal: str, context: dict | None = None, *, locale: str | None = None,
                   language: str | None = None) -> IntelligenceRequest:
    return IntelligenceRequest(
        goal=goal,
        context=context or {},
        locale=locale,
        language=language,
        constraints={"domain": "dental", "human_in_the_loop": True},
    )


def dental_policy() -> CapabilityPolicy:
    return DENTAL_POLICY
