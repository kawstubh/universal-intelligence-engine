# Clinical notes and patient communication

## Clinical notes
- Notes are owned by the active clinic workspace and linked to a patient record.
- New notes are private to the clinic by default.
- Only an authenticated doctor in the same workspace can create, read or update notes.
- A doctor may explicitly set `patient_visible=true`; only then does the linked patient account receive the note.
- Patient reads derive the record from the authenticated Supabase user and reject an unrelated patient ID.
- Updates support an optional expected version to prevent overwriting concurrent edits.
- Audit events record note lifecycle actions but never contain note text or patient-identifying content.

## Clinic communication
- Messages are persisted in a clinic/patient thread.
- Doctors must belong to the workspace that owns the patient.
- Patients are resolved from their authenticated Supabase identity and explicit clinic link.
- Patient and doctor messages are visible to the other party in that same linked record only.
- Message bodies are not copied into audit metadata.
- Messages are plain text only; attachments use the separate private-document workflow.

## API
Doctor:
- `GET /v1/dental/doctor/patients/{patient_id}/clinical-notes`
- `POST /v1/dental/doctor/patients/{patient_id}/clinical-notes`
- `PATCH /v1/dental/doctor/clinical-notes/{note_id}`
- `GET /v1/dental/doctor/patients/{patient_id}/messages`
- `POST /v1/dental/doctor/patients/{patient_id}/messages`

Patient:
- `GET /v1/dental/patient/clinical-notes?patient_id={linked_patient_id}`
- `GET /v1/dental/patient/messages?patient_id={linked_patient_id}`
- `POST /v1/dental/patient/messages?patient_id={linked_patient_id}`

No patient AI diagnosis or clinical decision support is added.
