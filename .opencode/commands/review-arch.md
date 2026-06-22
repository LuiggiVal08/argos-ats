---
description: Revisa código del argos-ats para detectar violaciones a la arquitectura hexagonal
agent: plan
---

You are performing a read-only architecture review of the argos-ats project.

Scope: $1 (a file path or glob, default `apps/`).

Re-read @spec.md section 1.3 (Hexagonal Architecture rules) and section 1.2 (Stack constraints) before judging.

Check for, and report on:
1. **Direction of dependencies**: Domain must not import from Application or Infrastructure. Application must not import concrete adapters — only port interfaces.
2. **Engine isolation**: `apps/data-engine/**` must not import from `apps/analytics-engine/**` and vice versa. Shared types go through the message broker contract.
3. **Stack leakage**: No `ioredis`/`ws`/`ccxt` outside `apps/data-engine/src/infrastructure/`. No `pandas`/`ta`/`tensorflow`/`ccxt` outside `apps/analytics-engine/app/infrastructure/`.
4. **Spec compliance**: Any new file should fit one of the user stories in @spec.md section 5, or justify a new one.

Output a structured report:
- Verdict: PASS / NEEDS CHANGES
- Violations: file:line + rule broken + suggested fix
- Suggestions: non-blocking improvements
Do not edit any files.
