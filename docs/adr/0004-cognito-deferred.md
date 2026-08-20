# ADR-0004 — Django auth is the boundary; Cognito is deferred

**Status:** accepted · **Date:** 2026-08-20 · **Spec:** conflict C3, decision D10, §11.2

## Context

The architecture document selected Amazon Cognito as the authentication layer. The
development plan described a working Django auth app and never mentioned Cognito. The
system has roughly **10 known internal users**, all already authenticated on the
corporate network.

## Decision

**Django auth is the system of record** through Phase 5. Cognito is removed from the
near-term stack.

DRF defaults to `SessionAuthentication` and `IsAuthenticated`. There is no anonymous
endpoint.

## Why

Cognito solves a problem this build does not have: federated identity at a scale where
managing users yourself is the bottleneck. At ten internal users it adds a
token-exchange integration, a second user store to keep consistent with
`authentication_user`, and a new failure mode — and it would land in the
highest-risk phase, alongside the traceability contract, which is where attention
should not be divided.

The audit requirement points the same way. FR-13 needs `changed_by` on every review
edit and NFR-3 needs a quote attributable to a person, so a real row in this database
is required regardless of who issues the token. Django needs its user table either
way.

## Why this is not a one-way door

If SSO is later required, Cognito or Entra ID sits **in front of** Django as an OIDC
provider. Django remains the authorisation and audit boundary: the IdP asserts who
someone is, and `authentication_user` still holds the row that `feedback.changed_by`
and the quote approver point at. Adding an OIDC front end later is additive. Building
on Cognito now and then removing it would not be.

## Consequences

- Password policy, session expiry, and account lockout are Django configuration and
  must be set deliberately before go-live.
- No MFA today. If CBC requires it before go-live, that is the trigger to revisit —
  and the answer is an OIDC provider in front, not a rewrite.
- NFR-4 sign-off naming AWS as the approved environment is still outstanding and is
  unaffected by this decision.
