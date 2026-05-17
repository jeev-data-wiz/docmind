# Architecture Decision Record — ADR-012
# Deferred Horizontal Write Scaling for PostgreSQL

**Status:** Accepted → REOPENED (2024-01-31, see Addendum)  
**Date:** 2022-08-04  
**Author:** Kavita Rao, Principal Engineer  
**Reviewers:** Arjun Sharma, Rohan Mehta  
**Supersedes:** None  
**Related:** INC-2023-047 (Black Friday outage, November 2023)

---

## Context

As of Q3 2022, the InfraCore platform's PostgreSQL write primary was handling
approximately 1,200 TPS at peak. Database benchmarks showed the current hardware
(db.r6g.4xlarge, 128GB RAM) could support up to approximately 6,000 TPS write
throughput before hitting CPU saturation.

The team evaluated whether to implement horizontal write scaling now (Q3 2022)
or defer until growth required it.

Approaches evaluated:
1. **Citus (sharded PostgreSQL)** — horizontal sharding, requires application-level
   partition key decisions
2. **CockroachDB migration** — distributed SQL with native horizontal scaling;
   significant migration effort
3. **Read replica offloading** — move read-heavy queries to replicas to reduce
   write-primary load (partial mitigation only)
4. **Deferred decision** — monitor growth, revisit when utilisation > 70%

---

## Decision

We will **defer horizontal write scaling** until one of these triggers is met:
- Peak write TPS exceeds 70% of benchmarked capacity (i.e. > 4,200 TPS)
- P99 write latency exceeds 50ms during business hours
- Connection pool saturation events occur in production

The current architecture with PgBouncer connection pooling is sufficient for
projected 12-month growth at the current 35% YoY trajectory.

**Known risks acknowledged:**
- If growth exceeds 35% YoY (e.g. a viral product launch or major seasonal event),
  we may hit the 4,200 TPS threshold unexpectedly.
- Connection pool exhaustion (PgBouncer max_client_conn=300) is a proximate risk
  that should be revisited if concurrent connection count grows.

---

## Rationale

Horizontal write scaling is a significant engineering investment:
- Citus sharding requires schema refactoring (~6 weeks engineering effort)
- CockroachDB migration would require a full data migration (~4 months)

At current growth rates, we have an estimated 14-month runway before hitting limits.
The cost of premature optimisation (engineering time, migration risk) outweighs the
benefit of acting now.

**However: the connection pool limit must be proactively managed.**
Recommendation: revisit PgBouncer max_client_conn setting quarterly.

*(Note: This recommendation was not followed — see Addendum below.)*

---

## Consequences

### Positive

- Saves 6–16 weeks of engineering effort in Q3/Q4 2022
- Avoids migration risk during a period of rapid feature development
- Current system is well-understood and stable

### Negative / Risks

- Single write primary is a scaling ceiling
- If triggers are hit unexpectedly, response time will be days-to-weeks, not hours
- **Known risk:** Connection pool exhaustion under sudden traffic spikes
  (PgBouncer max_client_conn=300 may be insufficient for 3x+ traffic growth)
- No owner assigned for the quarterly PgBouncer review

---

## Addendum — 2024-01-31 (Post INC-2023-047)

This ADR is reopened following the Black Friday 2023 outage (INC-2023-047).

**What happened:**  
The connection saturation trigger identified in this ADR occurred on 2023-11-24.
Peak write TPS reached 4,200 — exactly the 70% capacity threshold identified here.
PgBouncer max_client_conn (300) was never revised despite the recommendation above.

**Learnings:**  
1. ADR risks without assigned owners and tracked deadlines are not mitigations.
2. The quarterly PgBouncer review was never assigned or executed.

**Updated Decision:**  
Proceed with **Read Replica Offloading** as an immediate mitigation (already complete
per AI-003 in INC-2023-047), followed by a formal evaluation of Citus vs CockroachDB
to begin Q2 2024.

Owner for horizontal scaling evaluation: **Arjun Sharma**  
Target decision date: **2024-04-30**

---

## Status History

| Date       | Status    | Notes                                          |
|------------|-----------|------------------------------------------------|
| 2022-08-04 | Proposed  | Initial draft                                  |
| 2022-08-12 | Accepted  | Approved with acknowledged risks               |
| 2023-11-24 | ⚠️ Triggered | INC-2023-047 — connection saturation event    |
| 2024-01-31 | Reopened  | Post-mortem follow-up; new direction set       |
