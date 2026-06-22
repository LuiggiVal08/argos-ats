---
description: Ejecuta la suite de tests de data-engine y analytics-engine, con reporte unificado
agent: build
---

Run the test suite for the argos-ats engines and produce a unified report.

Target engine: $1 (optional — `data-engine`, `analytics-engine`, or empty for both).

Re-read @spec.md section 1.2 (Stack) to confirm the test toolchain: `jest` for the NestJS data-engine, `pytest` for the FastAPI analytics-engine.

Discovery (run from repo root):
```
!`ls apps/data-engine/package.json apps/data-engine/src 2>/dev/null | head -5 || echo "data-engine not scaffolded"`
!`ls apps/analytics-engine/pyproject.toml apps/analytics-engine/app 2>/dev/null | head -5 || echo "analytics-engine not scaffolded"`
```

Then, depending on `$1`:

### If `$1` is `data-engine` or empty
```
cd apps/data-engine && [ -f package.json ] && npm test -- --colors=false 2>&1 || echo "SKIP: data-engine not scaffolded yet"
```

### If `$1` is `analytics-engine` or empty
```
cd apps/analytics-engine && [ -f pyproject.toml ] && (python -m pytest -v --tb=short 2>&1 || pytest -v --tb=short 2>&1) || echo "SKIP: analytics-engine not scaffolded yet"
```

Rules:
1. If an engine is not scaffolded, report `SKIP` with the path that needs scaffolding — do not fail the overall command.
2. For the data-engine, prefer `npm test` (Jest is the standard NestJS preset per spec §1.2). If a custom script like `test:cov` or `test:watch` exists, do NOT use it — only the default test runner.
3. For the analytics-engine, run from `apps/analytics-engine/` so `pyproject.toml` / `pytest.ini` are picked up. Always use `-v --tb=short` for compact, line-numbered failures.
4. Do not modify source files. If tests fail because the implementation is missing, report the failure and suggest the matching `scaffold-*` command from `.opencode/commands/`.
5. Do not commit or stage anything.

Output a unified report at the end:
- `data-engine`: PASS / FAIL / SKIP (with count: passed, failed, skipped)
- `analytics-engine`: PASS / FAIL / SKIP (with count: passed, failed, skipped)
- Overall: PASS only if both engines PASS; otherwise FAIL with the first 5 failing test file:line references.
