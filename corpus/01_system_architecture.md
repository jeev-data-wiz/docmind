# System Architecture Overview — InfraCore Platform v2.3

**Document Type:** Architecture Design Record  
**Author:** Platform Engineering Team  
**Date:** 2024-01-15  
**Status:** Approved  

---

## 1. Executive Summary

InfraCore is the core internal platform that underpins all product services at the company.
It provides shared infrastructure capabilities including service discovery, authentication,
distributed caching, and event streaming. As of version 2.3, InfraCore serves approximately
450 internal microservices across 6 engineering teams.

---

## 2. Authentication Service

The primary purpose of the authentication service (AuthCore) is to provide a centralised,
stateless JWT-based identity layer for all internal and external service-to-service
communication.

AuthCore handles three main concerns:
- **Identity Verification**: Validating user credentials against the internal LDAP directory
  and issuing short-lived JWT tokens (15-minute expiry).
- **Service-to-Service Auth**: Mutual TLS (mTLS) for inter-service calls within the cluster,
  eliminating the need for API keys in internal traffic.
- **Token Refresh**: A sliding session mechanism that silently refreshes tokens before expiry
  as long as the user is active, with a hard session cap of 8 hours.

AuthCore is intentionally stateless — it does not store session state. All state is encoded
in the JWT payload and verified via a rotating RSA-256 signing key pair. Keys are rotated
every 30 days via the internal Vault integration.

### 2.1 Architecture Decisions

We evaluated three approaches before selecting stateless JWT:

| Option              | Pros                              | Cons                                       |
|---------------------|-----------------------------------|--------------------------------------------|
| Session cookies     | Simple, browser-native            | Requires session store, not REST-friendly  |
| Stateless JWT       | Scalable, no session store needed | Token revocation requires blocklist        |
| OAuth2 + OIDC       | Industry standard                 | Significant infrastructure overhead        |

The decision to use stateless JWT was documented in ADR-007 (see separate ADR document).

---

## 3. Data Layer

The data layer uses a tiered storage strategy:

**Tier 1 — Hot (Redis Cluster)**  
All read-heavy data with TTL < 1 hour. Current cluster: 3 primary + 3 replica nodes.
Average read latency: 0.8ms P99.

**Tier 2 — Warm (PostgreSQL 15)**  
Transactional data, user profiles, configuration. Primary + 2 read replicas.
Connection pooling via PgBouncer (max 500 connections).

**Tier 3 — Cold (S3-compatible object store)**  
Audit logs, document archives, ML training artefacts. Lifecycle policy: move to Glacier
after 90 days, delete after 7 years (compliance requirement).

### 3.1 Known Limitations

The PostgreSQL primary currently handles all writes. Under peak load (Black Friday 2023),
write throughput hit 4,200 TPS which caused connection saturation on PgBouncer
(configured max was 300 at the time — since raised to 500). This incident is detailed in
INC-2023-047 (see incident report document).

---

## 4. Event Streaming

InfraCore uses Apache Kafka (v3.5) for asynchronous event propagation.

- 12 topic partitions per high-volume topic
- Events retained for 7 days
- Consumer groups use cooperative sticky assignor for minimal rebalancing
- Dead-letter queue (DLQ) pattern for poison-pill events

---

## 5. Deployment Architecture

All services run on Kubernetes (v1.28) across 3 availability zones.
Infrastructure is provisioned via Terraform and Helm. CI/CD runs on GitHub Actions
with ArgoCD for GitOps-style deployment.

**Current node count:** 48 worker nodes (c5.2xlarge)
**Cluster uptime SLA:** 99.95%

---

## 6. Observability Stack

- **Metrics:** Prometheus + Grafana (15-day retention)
- **Logs:** Fluent Bit → OpenSearch (30-day hot, 1-year cold)
- **Traces:** OpenTelemetry → Jaeger
- **Alerts:** PagerDuty integration with severity 1–4 classification

---

## 7. Open Items / Future Work

1. Evaluate migration from self-managed Kafka to AWS MSK to reduce ops burden
2. Implement token revocation blocklist in AuthCore (currently relies on short TTL as mitigation)
3. Horizontal write scaling for PostgreSQL — investigate Citus or CockroachDB
4. gRPC adoption for inter-service calls (currently REST + JSON with mTLS)
