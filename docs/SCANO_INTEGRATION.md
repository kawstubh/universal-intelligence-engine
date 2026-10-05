# scanO Integration Readiness

## Purpose

Prepare the Universal Intelligence Engine / Dental platform for a future
authenticated scanO integration without claiming that a live scanO connection
exists today.

Public scanO material describes **scanKit API** as an API that returns
structured oral-health signals and supporting evidence, and describes an
integration model where intelligence can dock into existing practice software.

## Current boundary

Our side provides:

1. A vendor adapter boundary.
2. Normalized scan result objects.
3. Provenance preservation of the original payload.
4. Safe handling of unknown fields.
5. No clinical reinterpretation of scanO findings.
6. A testable path using fixture/mock payloads.

## Required from scanO before live integration

- Auth method and credential flow
- Base URL and API version
- Exact request schema
- Exact response schema
- Scan/upload lifecycle
- Webhook/event contract, if applicable
- Tenant/clinic identifiers
- Rate limits and retry guidance
- Error codes
- Data-retention and permitted-use requirements
- Sandbox/test credentials
- Approved integration use cases

## Proposed flow

scanO scan -> scanO API -> adapter -> provenance -> Dental/UIE intelligence
-> clinician-facing workflow -> audit trail

The adapter must remain a boundary: the application must never present an
unverified third-party signal as an independent diagnosis.

## Demo conversation for Vidhi

We can show that our platform is already structured to accept scanO's
structured screening output and route it into patient journey, clinician
workflow, follow-up and intelligence layers.

The honest statement is:

> "We have built the integration boundary and normalization layer. Give us
> the scanKit sandbox contract and credentials, and we can wire the live API
> without redesigning the Dental intelligence stack."

## Not claimed

This branch does **not** contain a live scanO API integration, real scanO
credentials, or production clinical validation.
