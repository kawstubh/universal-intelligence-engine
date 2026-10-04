"""Deterministic Dental care-intelligence workflow drafts."""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CareDraft:
    tooth_id: str | None
    signal: str
    pathway: str
    requirements: tuple[str, ...]
    follow_up: tuple[str, ...]
    clinician_review_required: bool = True


@dataclass(frozen=True)
class ReferralDraft:
    specialty: str
    facility_requirements: tuple[str, ...]
    information_to_include: tuple[str, ...]
    clinician_approval_required: bool = True


_SIGNAL_PATHWAYS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "scaling": ("preventive_care", ("post-procedure care information",),
                ("review after the interval selected by the clinician",)),
    "extraction": ("extraction_aftercare",
                   ("post-extraction instructions", "follow-up appointment when indicated"),
                   ("check healing at the clinician-selected interval",)),
    "rct": ("root_canal_care",
            ("restoration/coronal protection review", "post-treatment instructions"),
            ("treatment completion review",)),
    "braces": ("orthodontic_care",
               ("orthodontic hygiene guidance", "scheduled orthodontic review"),
               ("orthodontic follow-up",)),
    "implant": ("implant_care",
                ("implant after-care guidance", "restoration/implant review"),
                ("implant follow-up",)),
    "pediatric": ("pediatric_dental_care",
                  ("age-appropriate oral-care guidance", "parent/guardian instructions"),
                  ("child dental follow-up",)),
}


def draft_care_pathway(signal: str, *, tooth_id: str | None = None,
                       chart_status: str | None = None) -> CareDraft:
    key = signal.strip().lower()
    if key not in _SIGNAL_PATHWAYS:
        return CareDraft(tooth_id, signal, "clinical_review",
                         ("clinician assessment",),
                         ("clinician-selected follow-up",))
    pathway, requirements, follow_up = _SIGNAL_PATHWAYS[key]
    if chart_status:
        requirements = (*requirements, f"review recorded chart status: {chart_status}")
    return CareDraft(tooth_id, signal, pathway, requirements, follow_up)


def draft_referral(specialty: str, *,
                   facility_requirements: list[str] | tuple[str, ...] = (),
                   available_information: Mapping[str, Any] | None = None) -> ReferralDraft:
    info = ("patient context", "relevant dental chart",
            "relevant screening observations", "reason for referral",
            "clinician notes")
    if available_information:
        allowed = tuple(name for name in info if name in available_information)
        if allowed:
            info = allowed
    return ReferralDraft(specialty.strip(), tuple(facility_requirements), info)
