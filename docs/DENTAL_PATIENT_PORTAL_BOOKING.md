# Patient portal linking and appointment requests

The responsive Oralyn web portal uses patient-authenticated routes outside the
AI namespace. These routes do not provide diagnosis, triage, treatment
recommendations or other patient-facing clinical decision support.

## Authentication and record ownership

Every route below requires the Supabase patient bearer token through
`require_patient`. A patient record is selected only when its
`dental_patients.user_id` matches the authenticated Supabase user. When an
account has more than one linked clinic record, the client must pass the
selected `patient_id`; otherwise the API returns
`PATIENT_RECORD_SELECTION_REQUIRED`.

The portal must never send a trusted patient identity in the request body.
The API resolves the patient record from the authenticated user and the
optional `patient_id` query parameter.

## Routes

| Method | Route | Purpose |
|---|---|---|
| POST | `/v1/dental/doctor/patients/{patient_id}/invite` | Create a one-time invite for a patient in the doctor’s active clinic |
| POST | `/v1/dental/patient/link` | Consume a one-time clinic invite and link the matching clinic record |
| GET | `/v1/dental/patient/linked-records` | List only clinic records already linked to the authenticated user |
| GET | `/v1/dental/patient/appointments?patient_id=...` | Read safe appointment fields for the selected linked record |
| POST | `/v1/dental/patient/appointments?patient_id=...` | Create an appointment request for the selected linked record |
| GET | `/v1/dental/patient/chart?patient_id=...` | Read the safe tooth/status projection for the selected linked record |

Appointment request payload:

```json
{
  "starts_at": "2026-11-16T10:30:00+05:30",
  "treatment_type": "Dental consultation",
  "note": "Please call before confirming."
}
```

The timestamp must include an explicit timezone and is normalized to UTC.
The API sets status to `requested`; clients cannot set a confirmed, scheduled
or completed status. The patient identity is resolved server-side and
cross-patient identifiers are rejected. A request is audited without copying
patient names, phone numbers or clinical notes into audit metadata.

## Compatibility and clinical safety

Legacy patient account data routes under `/v1/dental/ai/patient/*` remain
available for compatibility, but they are not AI actions. The API middleware
continues to disable patient chat, triage, treatment explanation, aftercare,
AI consent and AI history. The web portal uses the non-AI routes above.

Public guest appointment intake remains a separate workflow and does not
attach an unverified request to an existing patient record.


Doctor invite creation is a clinic-management action, not an AI action. It is exposed under the authenticated `/v1/dental/doctor/*` namespace, checks the patient against the doctor's active clinic, and returns only the one-time code and its 24-hour expiry. The legacy `/v1/dental/ai/doctor/patients/{patient_id}/invite` route remains for compatibility; the Oralyn portal now uses the non-AI route.
