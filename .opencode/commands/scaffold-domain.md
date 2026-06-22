---
description: Genera una nueva entidad de Dominio (Hexagonal) para el engine indicado
agent: build
---

You are scaffolding a new Domain entity for the argos-ats project.

Target engine: $1 (must be either `data-engine` or `analytics-engine`).
Entity name: $2.

First, re-read @spec.md sections 1.2 and 1.3 to confirm the hexagonal rules.

Engine conventions:
- `data-engine` (NestJS / TypeScript): place under `apps/data-engine/src/domain/<context>/`.
  - Pure TypeScript class or interface. No imports from `@nestjs/*`, no `ioredis`, no `ws`, no `axios`, no `ccxt`.
  - Use strict typing. Export a factory/constructor that validates invariants.
- `analytics-engine` (FastAPI / Python): place under `apps/analytics-engine/app/domain/<context>/`.
  - Pure Python module. No imports from `fastapi`, `redis`, `ccxt`, `pandas` IO, or `tensorflow`/`torch`.
  - Use `dataclasses` or `pydantic.BaseModel` for entities; keep math (ATR, RSI, MACD) here, not in adapters.

Output:
1. The new file(s) with the entity, its invariants, and a docstring explaining the business rule.
2. A short bullet list of tests that should accompany it.
3. Do NOT create the use case or adapter — those are separate commands.
