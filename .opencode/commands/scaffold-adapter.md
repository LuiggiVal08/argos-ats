---
description: Genera un Adaptador de Infraestructura que implementa un puerto existente
agent: build
---

You are scaffolding a new Infrastructure adapter for the argos-ats project.

Target engine: $1 (`data-engine` or `analytics-engine`).
Port to implement: $2.
Underlying tech: $3 (e.g. `ccxt`, `redis-streams`, `binance-ws`, `pandas-ta`, `tensorflow`).

Re-read @spec.md sections 1.3 and 1.2 for stack constraints.

Conventions:
- `data-engine`: `apps/data-engine/src/infrastructure/<tech>/<port>.adapter.ts`. Use NestJS providers; implement the port interface; encapsulate retry/timeout logic here, not in the use case.
- `analytics-engine`: `apps/analytics-engine/app/infrastructure/<tech>/<port>.py`. Use FastAPI dependency injection; implement the Protocol; keep `asyncio` concerns here.

The adapter must:
1. Implement the exact port signature from the Domain/Application layer.
2. Handle errors with the project's retry/containment policy (spec section 4 OWASP phases).
3. Never leak exchange/library-specific types outside the adapter.

Output the file plus a short integration-test outline. Do not modify the domain or use case.
