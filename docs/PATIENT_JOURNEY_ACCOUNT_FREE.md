# Public, account-free patient journey

The Oralyn patient journey is a public clinic discovery and appointment-request flow. Patients do not create accounts and do not sign in.

## Supported public journey

1. Google Search/Maps/Business Profile can refer visitors to the Oralyn public clinic page.
2. The public clinic page links to the guest appointment request form.
3. The form collects the minimum contact details, service choice and preferred future date/time.
4. The backend creates a request only; the clinic must contact the patient to confirm it.

The public booking API is scoped to the single Dr Pranali master clinic until a verified clinic-slug model exists. It does not expose patient records and does not call patient-facing AI.

## Disabled patient account features

The API now returns HTTP 410 for legacy '/v1/dental/patient/*' account/record routes and Doctor-created '/v1/dental/doctor/patients/{id}/invite' links. Patient-facing AI routes under '/v1/dental/ai/patient/*' return 404. These routes are also omitted from OpenAPI.

This disables the legacy Supabase patient bearer-token path, patient linking, patient record portal, patient billing portal, and patient AI. No patient account, password, OTP, signup, login, invite code or patient authentication provider is required for guest booking.

## Data protection

- Clinical notes, history, odontograms, treatment plans, prescriptions, invoices, receipts and private documents remain protected behind Doctor/staff authorization.
- The public booking endpoint returns request status only and does not return existing patient records.
- No patient-specific no-login record-sharing links are enabled.
- Existing patient data and legacy invite rows are not deleted; only the account and invite API surfaces are disabled.
- Google Business Profile is not modified by this change. Do not claim its booking link was changed without authorized access and confirmation.
