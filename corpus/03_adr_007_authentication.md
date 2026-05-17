# Architecture Decision Record — ADR-007
# Authentication Strategy: Stateless JWT with RSA-256

**Status:** Accepted  
**Date:** 2022-03-10  
**Author:** Arjun Sharma, Staff Engineer  
**Reviewers:** Kavita Rao, Rohan Mehta  
**Supersedes:** None  
**Superseded By:** None (current)

---

## Context

As InfraCore grew from a monolith to a microservices architecture (Q4 2021), we needed
a unified authentication and authorisation strategy that could:

1. Work across 50+ microservices without per-service session management
2. Support both human users (browser sessions) and machine-to-machine (service accounts)
3. Be horizontally scalable without a shared session store
4. Integrate with our existing internal LDAP directory

The old approach — server-side sessions stored in a Redis cluster — worked for the monolith
but created tight coupling between services and the session Redis instance. Any Redis
downtime caused a full authentication outage.

---

## Decision

We will use **stateless JWT (JSON Web Tokens)** with RSA-256 asymmetric signing as the
primary authentication mechanism for InfraCore.

Key parameters:
- Token expiry: **15 minutes** (short to limit exposure if a token is stolen)
- Signing algorithm: **RS256** (asymmetric — private key signs, any service can verify with public key)
- Key rotation: **Every 30 days**, managed via HashiCorp Vault
- Refresh mechanism: Silent refresh when < 3 minutes remain; hard cap at 8-hour session
- Token payload: user_id, roles[], issued_at, expires_at, service_context

---

## Rationale

### Why not server-side sessions?

Server-side sessions require all services to share a session store (Redis).
This creates a single point of failure and a scaling bottleneck.
If the session store is unavailable, no service can authenticate users.
We experienced this failure mode twice in 2021 (INC-2021-003, INC-2021-019).

### Why not OAuth2/OIDC directly?

OAuth2 + OIDC is the industry gold standard and was seriously considered.
However, for our use case it introduced unnecessary complexity:
- Requires an authorisation server (Keycloak, Auth0, or self-hosted)
- Authorization Code Flow adds 2 round trips for a browser login
- Our services are internal — we don't need the delegated access model OAuth2 was
  designed for

We can migrate to OIDC in the future if we need federated identity (e.g. external
partners). The JWT format is compatible with OIDC claims.

### Why RSA-256 over HMAC-256?

With HMAC-256 (symmetric), every service that verifies tokens also knows the signing
secret — meaning a compromised internal service could forge tokens.
With RSA-256 (asymmetric), only AuthCore holds the private key.
Services receive the public key (via Vault) for verification only.
This reduces the blast radius of a service compromise.

---

## Consequences

### Positive

- No shared session store — each service verifies tokens independently
- Services can go offline without affecting authentication of other services
- Clear separation: only AuthCore can issue tokens
- Easy audit trail: all token issuances logged centrally in AuthCore

### Negative / Risks

**Token revocation is difficult:**  
Because tokens are stateless, there is no way to invalidate a token before it expires
(short of maintaining a blocklist, which reintroduces statefulness).
Mitigation: Short 15-minute TTL limits exposure. 
TODO: Implement a lightweight token blocklist for high-severity revocations (e.g., compromised account).

**Key rotation downtime risk:**  
During key rotation, there is a brief window where tokens signed by the old key fail
verification (if the new public key is distributed before the old tokens expire).
Mitigation: Vault serves both old and new public keys during a 20-minute overlap window.

---

## Alternatives Considered

| Option              | Verdict  | Reason Rejected                                    |
|---------------------|----------|----------------------------------------------------|
| Server-side sessions| Rejected | SPOF, scaling bottleneck, see INC-2021-003/019     |
| OAuth2 + OIDC       | Deferred | Overcomplicated for internal-only use case         |
| HMAC-256 JWT        | Rejected | Symmetric key — too broad a blast radius           |
| API Keys            | Rejected | No expiry, hard to rotate, poor UX for human users |

---

## Review Notes

*Kavita Rao:* "Agree with the decision. I'd like a follow-up task tracked for the token
revocation blocklist — the 15-minute TTL is fine for now but we should revisit this once
we have admin revocation use cases."

*Rohan Mehta:* "Approved. Key rotation runbook needs to be written before we go live."

---

## Status History

| Date       | Status    | Notes                                  |
|------------|-----------|----------------------------------------|
| 2022-03-10 | Proposed  | Initial draft                          |
| 2022-03-18 | Accepted  | Approved by architecture review board  |
| 2022-04-05 | Implemented | AuthCore v1.0 deployed to production |
