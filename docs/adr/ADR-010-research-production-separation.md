# ADR-010: Research-Production Separation

**Status:** ACCEPTED (2026-06-26)

## Context

Research code (experiments, backtesting, hyperparameter search) uses different dependencies, data paths, and execution patterns than production code. Mixing them risks production instability — a research import change, a notebook dependency, or an experimental path could silently break the live trading pipeline.

## Options Considered

- **Monorepo with shared code** — rejected; research code changes can break production.
- **Research in separate repository** — rejected; creates coordination overhead and makes experiment reproduction difficult.
- **`research/` directory with isolated imports** — selected.
- **Research in Jupyter notebooks** — rejected; non-reproducible and difficult to version control.

## Decision

Research lives in the `/research/qv2/` directory with a strict one-way dependency rule:

- Research code **imports from** `apps/analytics-engine/app/infrastructure/` modules (LabelEngine, FeatureEngine, etc.) to reproduce the production pipeline exactly
- Production code **never imports** from `/research/qv2/`

Key validation phases (35, 375, 38, 39, 4) are standalone Python scripts with their own `main()` entry points, ensuring they can be executed independently without notebook infrastructure.

## Consequences

**Positive:**
- Production stability guaranteed — no research import can affect live code
- Research reproduces the production pipeline exactly — same feature engineering, same label logic, same data flow
- Standalone scripts are easy to run in CI/CD or on remote machines

**Negative:**
- Research cannot use production-only features (e.g., live WebSocket streams)
- Minor duplication of configuration between research and production environments

## Alternatives Rejected

- **Separate repositories:** coordination overhead when updating shared pipeline logic.
- **Shared codebase with feature flags:** complexity cost exceeds value for a two-environment system.
- **Jupyter notebooks:** not reproducible, not version-controllable, not suitable for production validation.

## References

- `/research/qv2/` directory structure
- `/apps/analytics-engine/app/` directory structure
- Git log — feature commits vs research commits
