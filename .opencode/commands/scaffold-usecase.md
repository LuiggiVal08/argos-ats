---
description: Genera un Caso de Uso (capa de Aplicación) que orquesta Dominio + puertos
agent: build
---

You are scaffolding a new Application-layer use case for the argos-ats project.

Target engine: $1 (`data-engine` or `analytics-engine`).
Use case name: $2 (e.g. `ProcesarSenalIA`, `EjecutarCompra`).
Port(s) it depends on: $3 (comma-separated, optional).

Re-read @spec.md section 1.3 before writing.

Conventions:
- `data-engine`: `apps/data-engine/src/application/<context>/<name>.usecase.ts`. Inject ports via constructor tokens; no direct Redis/WS imports.
- `analytics-engine`: `apps/analytics-engine/app/application/<context>/<name>.py`. Inject port protocols (Protocol classes) via `__init__`; no direct `ccxt`/`redis`/`ta` imports — those go through adapters.

The use case must:
1. Receive a sanitized input from the infrastructure layer.
2. Invoke Domain logic.
3. Call one or more port interfaces to produce side effects.
4. Return a result DTO; never raise framework-specific exceptions upward.

Output the file plus a 3-bullet unit-test plan. Do not create the adapter.
