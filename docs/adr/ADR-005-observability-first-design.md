# ADR-005: Observability-First Design

**Status:** ACCEPTED (2026-06-26)

## Context

ARGOS ATS automates financial decisions involving real capital. Before deployment, the system must be fully observable across four dimensions: logging for forensic traceability, health checks for automated recovery, inference audit trails for performance analysis, and alerting for incident response. Absence of observability constitutes blind risk.

## Options Considered

- **Basic console.log / print** — rejected; insufficient for forensics and debugging in production.
- **ELK stack** — rejected; overkill for v1 with 3 containers; operational overhead outweighs benefit.
- **Structured JSON logging with rotation** — selected as primary approach.
- **Prometheus / Grafana** — deferred to v2.

## Decision

Implement 6 rotating JSON log files with `structlog`, ULID-based correlation IDs, and forensic inference records. The log categories are:

| Log File | Purpose |
|----------|---------|
| `system.log` | Process lifecycle, configuration, startup validation |
| `health.log` | Health check results, uptime, connectivity status |
| `inference.log` | Each inference with 22+ fields (checksums, probabilities, feature hashes) |
| `orders.log` | Order submission, modification, cancellation events |
| `trades.log` | Filled trade records with P&L attribution |
| `errors.log` | Error conditions with full stack traces and context |

Health endpoints are exposed at `/health`, `/health/exchange`, and `/health/bus`.

## Consequences

**Positive:**
- Full forensic traceability for every inference and trade — critical for post-mortem analysis
- Health monitoring enables automated recovery without human intervention
- ULID correlation IDs link events across log files and services

**Negative:**
- Log storage grows linearly with trade volume; requires rotation management
- JSON serialization adds marginal overhead per log entry

## Alternatives Rejected

- **Console-only logging:** not forensic; lost on process restart or crash.
- **ELK stack:** operational complexity exceeds v1 requirements.
- **Datadog / Sentry:** ongoing cost without commensurate benefit for a 3-container system.

## References

- `reports/active/operational/STARTUP_OBSERVABILITY_VALIDATION.md`
