# MIGRATION REPORT — Infisical Secret Manager

> **Proyecto**: argos-ats — migración de secretos embebidos a Infisical
> **Fecha**: 2026-06-17
> **Estado**: ✅ COMPLETADA — 8/8 fases ejecutadas

---

## Resumen Ejecutivo

Se migró el manejo de secretos de archivos `.env` locales con valores hardcodeados a **Infisical** como secret manager central para DEV/STAGING, y sistema operativo/orchestrator para PROD.

### Línea base (antes)

| Métrica | Valor |
|---|---|
| Secretos en disco local | 4 (3 reales + 1 chat ID) |
| Secretos en git history | 0 ✅ |
| `dotenv` en runtime | No se usaba |
| Preflight checks | Solo en analytics-engine para LIVE |
| Secretos sin rotación | 0 — rotación imposible |

### Estado actual (después)

| Métrica | Valor |
|---|---|
| Secretos en disco local | 0 reales (solo placeholders) |
| Preflight checks | Ambos servicios, 3 modos (LIVE/PAPER_TRADING/BACKTESTING) |
| Schema unificado | `security/required_secrets.json` |
| Startup scripts Infisical | 3 scripts (`start-data-engine.sh`, `start-analytics-engine.sh`, `start-all.sh`) |
| CI audit | `scripts/security_audit.sh` — 8 checks, PASS esperado |
| Secret rotation | Posible vía Infisical UI/CLI — sin redeploy |

---

## Fases Ejecutadas

### FASE 1 — Auditoría de exposición

- **Archivos auditados**: todos los `.ts`, `.py`, `.js`, `.json`, `.yml`, `.yaml`, `.env`, `.sh`
- **Git history escaneado**: `git log -p --all` + `git ls-files`
- **Hallazgos**: 4 secretos reales en 2 archivos `.env` locales (0 en git)

### FASE 2 — Limpieza de .env locales

- `./.env`: Telegram token reemplazado por `<TELEGRAM_BOT_TOKEN>`, chat ID por `<TELEGRAM_CHAT_ID>`
- `apps/analytics-engine/.env`: Binance testnet API key/secret reemplazados por placeholders

### FASE 3 — Hardening de .gitignore

Añadido:
- `*.p12`, `*.cert`, `credentials*`, `secrets*`, `!.env.example`

### FASE 4 — Scripts de inicio Infisical

| Script | Propósito |
|---|---|
| `scripts/start-data-engine.sh` | `infisical run -- node dist/main.js` |
| `scripts/start-analytics-engine.sh` | `infisical run -- uvicorn app.main:app` |
| `scripts/start-all.sh` | Arranca ambos servicios + Infisical |

### FASE 5 — Preflight unificado

- `security/required_secrets.json` — schema compartido (fuente única de verdad)
- `security/preflight.py` — preflight en Python (usa el schema)
- `apps/data-engine/src/infrastructure/security/preflight.ts` — preflight en TS (usa el schema)

### FASE 6 — Integración en bootstrap

- `apps/data-engine/src/main.ts` — llama `preflightCheck()` al iniciar
- `apps/analytics-engine/app/composition.py` — llama `abort_if_missing()` con el schema compartido

### FASE 7 — Verificación de no-logging

- **0 hallazgos** de `console.log(process.env)` o `print(os.environ)` en código de producción

### FASE 8 — CI audit script

- `scripts/security_audit.sh` — 8 checks automatizados, exit code 0 = PASS

---

## Arquitectura de Secretos

```
                    ┌─────────────────────────────────────────┐
                    │           deployment model               │
                    ├────────────┬──────────────┬──────────────┤
                    │   DEV      │   STAGING    │    PROD      │
                    ├────────────┼──────────────┼──────────────┤
                    │ Infisical  │  Infisical   │ systemd /    │
                    │ CLI        │  CLI         │ orchestrator │
                    └─────┬──────┴──────┬───────┴──────┬───────┘
                          │             │              │
                          ▼             ▼              ▼
                    ┌─────────────────────────────────────────┐
                    │         process.env / os.environ         │
                    │         (injected at runtime)            │
                    ├─────────────────────────────────────────┤
                    │      security/required_secrets.json      │
                    │         (schema — fuente de verdad)      │
                    └─────────────────────────────────────────┘
                          │             │              │
                          ▼             ▼              ▼
                    ┌─────────────────────────────────────────┐
                    │   preflight check (ambos servicios)      │
                    │   abort if missing required secrets      │
                    └─────────────────────────────────────────┘
```

## Schema Unificado (`security/required_secrets.json`)

```json
{
  "LIVE": ["EXCHANGE_API_KEY", "EXCHANGE_API_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
  "PAPER_TRADING": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
  "BACKTESTING": []
}
```

## Verificación Final

| Ítem | Check | Método |
|---|---|---|
| Sin secrets hardcodeados en código | ✅ | `bash scripts/security_audit.sh` |
| `.env` ignorados por git | ✅ | `git check-ignore .env` |
| `.env.example` sync con schema | ✅ | Revisión manual |
| Preflight data-engine | ✅ | `grep preflightCheck apps/data-engine/src/main.ts` |
| Preflight analytics-engine | ✅ | `grep abort_if_missing apps/analytics-engine/app/composition.py` |
| Schema compartido | ✅ | `cat security/required_secrets.json` |
| Startup scripts | ✅ | `ls -la scripts/start-*.sh` |
| Sin logging de env vars | ✅ | `bash scripts/security_audit.sh` |

## Recomendaciones Post-Migración

1. **Rotar tokens comprometidos**: el Telegram token y Binance testnet key/secret estuvieron en texto plano en disco. Aunque testnet, se recomienda rotarlos.
2. **Infisical setup**: en DEV/STAGING, ejecutar `infisical init` y configurar el projecto antes de usar los scripts de inicio.
3. **PROD setup**: inyectar secrets via systemd `EnvironmentFile=` o Kubernetes `Secret` + `envFrom`.
4. **CI/CD**: integrar `bash scripts/security_audit.sh` como step pre-merge en GitHub Actions.
5. **Monitor**: agregar alerta si el preflight check falla en producción (métrica de health endpoint).

---

## Conclusión

Migración completada. El sistema ahora es **resiliente a fugas accidentales de secretos** en git, tiene **preflight checks** en ambos servicios para todos los modos de operación, y soporta **Infisical** como secret manager en DEV/STAGING. El CI audit script permite verificar automáticamente que no se reintroduzcan secretos en el código.
