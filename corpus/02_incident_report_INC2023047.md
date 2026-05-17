# Incident Report — INC-2023-047
# Database Connection Saturation — Production Outage

**Severity:** P1 (Critical)  
**Date:** 2023-11-24 (Black Friday)  
**Duration:** 47 minutes (14:32 UTC — 15:19 UTC)  
**Reported By:** On-call SRE — Priya Nair  
**Status:** Resolved / Post-mortem Complete  

---

## 1. Incident Summary

On 24 November 2023 at 14:32 UTC, the InfraCore platform experienced a cascading
database outage affecting all services that depend on the PostgreSQL write primary.
The root cause was connection pool exhaustion on PgBouncer, which had a maximum
connection limit of 300 — insufficient for peak Black Friday traffic.

The outage lasted 47 minutes and affected approximately 78% of user-facing API calls.
Estimated revenue impact: $340,000.

---

## 2. Timeline

| Time (UTC) | Event                                                              |
|------------|--------------------------------------------------------------------|
| 14:28      | Write TPS begins climbing above baseline (normal: ~800 TPS)        |
| 14:32      | PgBouncer reports connection pool exhausted; new connections queued |
| 14:35      | Alert fires: PostgreSQL connection wait time > 2s P99              |
| 14:38      | On-call SRE Priya Nair acknowledges incident                       |
| 14:42      | Initial diagnosis: PgBouncer max_client_conn=300 hit              |
| 14:51      | Decision to increase max_client_conn to 500 and restart PgBouncer  |
| 14:58      | PgBouncer restarted — 6-minute secondary outage during restart     |
| 15:05      | Connections begin recovering; TPS normalising                      |
| 15:19      | All services reporting healthy; incident closed                    |

---

## 3. Root Cause Analysis

### 3.1 Primary Cause

The PgBouncer connection pool maximum was configured at 300 connections, set originally
during the platform's initial deployment in 2021 when peak TPS was approximately 400.
Over two years of growth, write TPS during peak events grew significantly but the
PgBouncer configuration was never revisited.

### 3.2 Contributing Factors

**CF-1: Lack of connection-count monitoring alert**  
There was no alert configured for PgBouncer connection utilisation percentage.
The 300-connection limit was breached before any warning was sent. A utilisation > 80%
alert would have given the team 6 minutes of lead time.

**CF-2: Architecture Decision ADR-012 deferred write scaling**  
ADR-012 (Deferred Horizontal Write Scaling) explicitly deferred PostgreSQL horizontal
write scaling until "post-Series-B growth phase." The document acknowledged connection
pool exhaustion as a known risk but did not assign a mitigation owner or deadline.
This created a gap where a known architectural risk was documented but not tracked.

**CF-3: No load testing at Black Friday volumes**  
Pre-event load tests simulated 2x baseline load (1,600 TPS). Actual Black Friday
peak reached 5.25x baseline (4,200 TPS). The 2x assumption was not validated
against historical year-on-year growth figures.

### 3.3 What Went Well

- On-call rotation was staffed correctly; Priya acknowledged within 6 minutes
- Runbook for PgBouncer reconfiguration existed and was followed correctly
- Customer communications were sent proactively at T+15 minutes

---

## 4. Impact Assessment

| Metric                     | Value                    |
|----------------------------|--------------------------|
| Services affected          | 34 of 45 tracked services |
| User-facing API error rate | Peak 78.3%               |
| Duration                   | 47 minutes               |
| Estimated revenue loss     | $340,000                 |
| Customer support tickets   | 1,247 opened             |

---

## 5. Action Items

| ID     | Action                                              | Owner         | Due Date   | Status   |
|--------|-----------------------------------------------------|---------------|------------|----------|
| AI-001 | Raise PgBouncer max_client_conn to 500              | Priya Nair    | 2023-11-25 | Complete |
| AI-002 | Add PgBouncer utilisation alert (>80% → P3 alert)  | DevOps Team   | 2023-12-01 | Complete |
| AI-003 | Evaluate read replica offloading for write reduction| DB Team       | 2024-01-15 | Complete |
| AI-004 | Re-open ADR-012 with concrete scaling timeline      | Arch Team     | 2024-01-31 | Complete |
| AI-005 | Update load testing protocol to 10x baseline        | QA Team       | 2024-02-15 | In Progress |

---

## 6. Lessons Learned

1. Known architectural risks documented in ADRs must have assigned owners and deadlines
   — documentation alone is not a mitigation.
2. Load testing scenarios must be grounded in historical growth data, not multiples of
   current baseline.
3. Connection pool limits should be treated as capacity metrics and alerting configured
   accordingly.

---

## 7. Sign-off

**Engineering Manager:** Rohan Mehta  
**Sign-off Date:** 2023-12-03  
