# Design Specification — Real-Time Notification Service (RTNS v1.0)

**Document Type:** Feature Design Spec  
**Author:** Engineering Team — Growth Pod  
**Date:** 2024-03-12  
**Status:** In Review  
**Target Release:** Q2 2024  

---

## 1. Overview

This document describes the design for the Real-Time Notification Service (RTNS),
a new InfraCore capability that delivers instant push notifications to users
across web, iOS, and Android surfaces.

RTNS replaces the current polling-based notification model, which generates
approximately 2.4 million unnecessary HTTP requests per day (measured Q1 2024).

---

## 2. Goals and Non-Goals

### Goals
- Deliver notifications with < 500ms end-to-end latency (P95)
- Support 10,000 concurrent WebSocket connections at launch
- Scale to 100,000 concurrent connections by end of year
- Integrate with InfraCore's AuthCore service for connection authentication
- Provide at-least-once delivery guarantee with deduplication

### Non-Goals
- This version does NOT support rich media notifications (images, action buttons)
- This version does NOT replace email or SMS notification channels
- Offline message queuing beyond 24 hours is out of scope for v1

---

## 3. Architecture

### 3.1 High-Level Design

```
Client (Browser/iOS/Android)
        │
        │  WSS (WebSocket Secure)
        ▼
  RTNS Gateway (Nginx + custom WS handler)
        │
        │  Internal gRPC
        ▼
  RTNS Dispatcher (Go service)
        │          │
        │          └── Kafka Consumer (notification_events topic)
        │
        ▼
  Connection Registry (Redis)
  — maps user_id → [connection_ids]
```

### 3.2 Component Responsibilities

**RTNS Gateway**  
- Handles WebSocket upgrade and TLS termination
- Authenticates connections using AuthCore JWT tokens
  (token provided in the `Authorization` header on the WS upgrade request)
- Maintains connection state in the Connection Registry (Redis)
- Routes incoming Kafka events to the correct connection

**RTNS Dispatcher**  
- Consumes from Kafka topic `notification_events` (partitioned by user_id)
- Looks up connection_ids for target user_id in the Connection Registry
- Delivers the notification payload to the appropriate RTNS Gateway instance
- Implements exponential-backoff retry for transient failures
- Writes undelivered notifications to the DLQ after 3 failed attempts

**Connection Registry (Redis)**  
- Key: `rtns:conn:{user_id}` → Set of `{gateway_instance}:{connection_id}`
- TTL: 1 hour (refreshed on each heartbeat)
- Using the existing InfraCore Redis cluster (Tier 1 hot storage)

---

## 4. Authentication Flow

All WebSocket connections MUST be authenticated via AuthCore before being
admitted to the RTNS Gateway.

Flow:
1. Client obtains a JWT from AuthCore (standard login or token refresh)
2. Client initiates WebSocket upgrade: `wss://rtns.internal/connect`
   with `Authorization: Bearer <jwt>` header
3. RTNS Gateway validates the JWT using AuthCore's public key (fetched from Vault)
4. If valid, the connection is registered in the Connection Registry
5. If the JWT is expired, the client receives a 401 and must refresh

This design re-uses ADR-007's RSA-256 JWT approach — no new authentication
infrastructure is required.

---

## 5. Delivery Guarantees

RTNS provides **at-least-once delivery**:
- Each notification event has a unique `event_id` (UUID v4)
- Clients MUST deduplicate using `event_id` on receipt
- The RTNS Dispatcher retries delivery up to 3 times with exponential backoff
- After 3 failures, events are written to the DLQ for manual inspection

**Why not exactly-once?**  
Exactly-once delivery requires distributed transactions across Kafka, Redis, and the
WebSocket layer — significant complexity for a v1. At-least-once with client-side
deduplication achieves the same user experience at lower system complexity.

---

## 6. Scalability

### 6.1 WebSocket Connection Count

Each RTNS Gateway instance can handle ~5,000 concurrent WebSocket connections
(estimated from Nginx benchmarks on c5.xlarge nodes).

At launch (10,000 connections): 2 gateway instances  
At 100,000 connections: 20 gateway instances (horizontal scale, stateless design)

### 6.2 Kafka Partitioning

The `notification_events` topic will have 24 partitions (2x current consumer count),
partitioned by `user_id` hash. This ensures ordered delivery per user while
allowing parallelism across users.

---

## 7. Failure Modes and Mitigations

| Failure Mode              | Impact                        | Mitigation                             |
|---------------------------|-------------------------------|----------------------------------------|
| Redis cluster unavailable | Cannot route notifications    | Fail-open: log + DLQ; client polls     |
| RTNS Gateway crash        | Active connections lost        | Client auto-reconnects with backoff    |
| Kafka consumer lag        | Notification delay            | Alert on lag > 10s; add consumers      |
| JWT expiry mid-session    | Connection dropped             | Client-side proactive token refresh    |

---

## 8. Open Questions

1. **Heartbeat interval**: Every 30s or 60s? Affects Redis TTL refresh cost.
2. **Payload size limit**: Propose 4KB max. Need confirmation from mobile team.
3. **Multi-tab handling**: Should the same user receive notifications on all open tabs
   simultaneously, or only the "active" tab? Leaning toward all tabs for simplicity.

---

## 9. Dependencies

- AuthCore (ADR-007) — for JWT validation
- InfraCore Redis Cluster — for Connection Registry
- InfraCore Kafka — for `notification_events` topic
- Vault — for AuthCore public key distribution

---

## 10. Success Metrics

| Metric                    | Target          | Measurement                       |
|---------------------------|-----------------|-----------------------------------|
| Notification latency P95  | < 500ms         | Prometheus histogram               |
| Concurrent connections    | 10,000 at launch| Prometheus gauge                  |
| Delivery success rate     | > 99.5%         | Kafka consumer lag + DLQ rate     |
| Daily polling eliminated  | > 90% reduction | Compare HTTP request logs         |
