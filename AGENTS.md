# AGENTS.md — Reglas para el AI agent de argos-ats

> Cargado al inicio de cada sesión. Define cómo me comporto en este proyecto.
> Si una regla entra en conflicto con un pedido del usuario, gana la regla más restrictiva (la que protege el sistema).

## 1. Contexto del proyecto

**argos-ats** es un Sistema de Trading Autónomo (ATS) de grado de producción para futuros perpetuos de criptomonedas.

- **Arquitectura**: microservicios event-driven, hexagonal internamente, **agnóstica al deployment**.
- **Servicios**:
  - `apps/data-engine` (NestJS, TS) — WebSocket al exchange, inyecta ticks al broker.
  - `apps/analytics-engine` (FastAPI, Py 3.11) — Consume broker, calcula indicadores, emite señales.
  - **Broker**: cualquier implementación RESP-compatibile (Redis 7+, Memurai, Dragonfly, Valkey, KeyDB, Garnet, Redict). El código consume el `MessageBus` port; el adapter concreto es pluggable vía `broker.kind` en `config.json`.
- **Deployment**: agnóstico. El stack puede correr en Docker Compose (recomendado para reproducibilidad y CI) o bare metal (recomendado para dev nativo en Linux/WSL2/macOS/Windows). `docker-compose.yml` es **una** opción, no la única.
- **Fuente de verdad**: `spec.md` en la raíz. Antes de cualquier cambio no trivial, lo leo.
- **Modos de operación** (`ENVIRONMENT_MODE`): `BACKTESTING` | `PAPER_TRADING` | `LIVE`.
- **Tracker de progreso**: `TASKS.md`. Lo actualizo al cerrar cada sesión de trabajo.

## 2. Invariantes duras (no negociables)

Estas vienen de `spec.md` sección 5 y de `spec_spec_invariants`. Antes de cualquier cambio, las valido mentalmente.

1. **Risk cap**: pérdida por trade ≤ 1% del free balance.
2. **SL distance**: derivada de ATR, no de un porcentaje fijo.
3. **Drawdown circuit breaker**: pérdida diaria ≥ 5% → halt total.
4. **Acciones del circuit breaker**: cancelar órdenes, cerrar posiciones a mercado, `ENVIRONMENT_MODE=PASIVO`, log, halt loop.
5. **`ENVIRONMENT_MODE` ∈ {BACKTESTING, PAPER_TRADING, LIVE}** exclusivamente.
6. **LIVE mode**: si falta cualquier secret requerido, abort init (exit 1). Sin excepciones.
7. **Secrets**: SIEMPRE desde env vars. NUNCA hardcoded en código. Si veo una API key en el código, la reporto inmediatamente.
8. **Hexagonal — Domain**: no importa de Application ni Infrastructure. Ni un solo import cruzado.
9. **Hexagonal — Application**: solo importa ports (interfaces), nunca adapters concretos (ccxt, ioredis, ws, etc.).
10. **Hexagonal — data-engine ↔ analytics-engine**: comunicación solo por Redis. Nunca import directo.
11. **Stack leakage**: `ioredis`/`ws`/`ccxt` solo en `data-engine/src/infrastructure/`. `pandas`/`ta`/`tensorflow` solo en `analytics-engine/app/infrastructure/`.
12. **Tick pipeline SLA**: inyección a Redis < 2ms p99.
13. **Order retry**: máx 3 reintentos en 500ms; al fallar, market order para liquidar.
14. **Deployment agnosticism**: el código de aplicación no se acopla a Docker ni a un broker concreto. Hostnames via env vars (`ARGOS_BROKER_URL`, `EXCHANGE_*`), paths via `path.join` / `os.path.join`, broker via `MessageBus` port. `docker-compose.yml` es una opción de deploy, no la única. Cambiar de Redis a Memurai/Dragonfly/Valkey es swap de adapter, no reescritura de código de aplicación.

Si una invariante se va a romper, lo digo ANTES de hacer el cambio.

## 3. Workflow spec-driven

1. **Inicio de historia**: invoco `spec_spec_story <n>` para leer la historia completa.
2. **Pre-flight**: invoco `spec_spec_invariants` como checklist mental.
3. **Scaffold**: uso el comando apropiado (`/scaffold-domain`, `/scaffold-usecase`, `/scaffold-adapter`).
4. **Implementación**: domain → application → infrastructure (nunca al revés).
5. **Tests**: implemento y luego escribo tests; uso `quality_test_data_engine` o `quality_test_analytics_engine` para correrlos.
6. **Validación**: `quality_arch_lint` (verifica hexagonal), `quality_typecheck_data_engine`, `quality_lint_data_engine`, `quality_secret_scan`.
7. **Cierre**: actualizo `TASKS.md` (marco tareas hechas, agrego notas), añado entrada a la bitácora.

## 4. Reglas de arquitectura hexagonal

Para `apps/data-engine/src/`:

| Capa              | Puede importar de                          | NO puede importar de                              |
|-------------------|--------------------------------------------|---------------------------------------------------|
| `domain/`         | (nada externo)                             | application, infrastructure, frameworks           |
| `application/`    | `domain/`, ports (interfaces)              | infrastructure, frameworks, librerías concretas    |
| `infrastructure/` | `domain/`, `application/`, adapters        | — (todo lo externo va aquí)                       |

Mismo patrón para `apps/analytics-engine/app/` con `domain/`, `application/`, `infrastructure/`.

**Entre los dos servicios**: solo comparten contratos via Redis (JSON schemas o Protobuf). Nunca `import` directo cross-service.

## 5. Code style

**TypeScript** (data-engine):
- `strict: true`, `noImplicitAny`, `strictNullChecks`.
- 2 espacios de indent.
- camelCase para variables/funciones, PascalCase para clases, kebab-case para archivos.
- Sin `any` salvo justificación explícita.
- Async/await sobre `.then()` chains.
- Errores tipados con clases (`class ExchangeError extends Error`).

**Python** (analytics-engine):
- 3.11+ (type hints completos).
- 4 espacios de indent.
- snake_case para funciones/variables, PascalCase para clases, snake_case para archivos.
- Type hints en TODA función pública.
- Sin `print()` en código de producción (usar `structlog`).
- Async por defecto si la función hace I/O.

## 6. Reglas de seguridad

- **NUNCA** commiteo secrets. Si `.env` aparece en un diff, lo reviso.
- **NUNCA** commiteo `*.env` ni `*.pem` ni `*.key`.
- **SIEMPRE** verifico `ENVIRONMENT_MODE` antes de acciones destructivas.
- **SIEMPRE** confirmo con el usuario antes de:
  - `config_toggle_mode` a LIVE
  - `docker_docker_restart_service`
  - `redis_redis_flush`
  - `redis_redis_set` (en LIVE)
  - `init_*` (crean archivos)
- **SIEMPRE** que veo algo sospechoso (key hardcoded, config rara), lo digo aunque no me lo pidan.

## 7. Cómo uso las herramientas

| Necesito...                                  | Tool                                          |
|----------------------------------------------|-----------------------------------------------|
| Saber los invariantes                        | `spec_spec_invariants`                        |
| Leer una historia                            | `spec_spec_story <n>`                         |
| Ver el resumen del spec                      | `spec_spec_summary`                           |
| Calcular tamaño de posición                  | `risk_position_size`                          |
| Verificar drawdown                           | `risk_drawdown_check`                         |
| Cambiar modo de operación                    | `config_toggle_mode` (con confirmación)       |
| Ver config actual                            | `config_read_config`                          |
| Health check del proyecto                    | `health_health_check`                         |
| Verificar Redis                              | `redis_redis_get`, `redis_redis_xlen`         |
| Escanear secretos hardcoded                  | `quality_secret_scan`                         |
| Validar arquitectura hexagonal               | `quality_arch_lint`                           |
| Typecheck data-engine                        | `quality_typecheck_data_engine`               |
| Lint data-engine                             | `quality_lint_data_engine`                    |
| Testear data-engine                          | `quality_test_data_engine`                    |
| Testear analytics-engine                     | `quality_test_analytics_engine`               |
| Inicializar un servicio                      | `bootstrap_init_data_engine` / `init_analytics_engine` |
| Generar docker-compose                       | `bootstrap_init_compose`                      |
| Generar config.json                          | `bootstrap_init_config`                       |
| Inicializar git                              | `bootstrap_init_git`                          |
| Ver logs de un servicio                      | `docker_docker_logs <service>` (solo si deploy es Docker) |
| Listar servicios corriendo                   | `docker_docker_ps` (solo si deploy es Docker) |
| Health check agnóstico (Docker o bare metal) | `health_health_check`                         |
| Backtest                                     | `backtest` (con args)                         |
| Indicador técnico                            | `indicators` (con args)                       |
| Ver estado de sesión                         | `session_status`                              |
| Inicializar sesión                           | `session_begin <branch>`                      |
| Bloquear archivo                             | `session_lock <path> <change>`                |
| Liberar archivo                              | `session_unlock <path>`                       |
| Registrar decisión de diseño                 | `session_decision <scope> <decision>`         |

## 8. Comandos slash disponibles

- `/new-story` — Iniciar una nueva historia
- `/scaffold-domain` — Scaffold de una entidad de dominio
- `/scaffold-usecase` — Scaffold de un caso de uso
- `/scaffold-adapter` — Scaffold de un adaptador
- `/test` — Correr tests
- `/review-arch` — Revisar la arquitectura hexagonal
- `/risk-audit` — Auditoría de riesgo
- `/incident-drill` — Simular un incidente OWASP
- `/env-check` — Verificar variables de entorno
- `/commit` — Commitear cambios
- `/backtest` — Correr un backtest

## 9. Anti-patrones (lo que NUNCA hago)

- ❌ Editar `spec.md` sin pedirlo (es la fuente de verdad).
- ❌ Hardcodear API keys, secrets, URLs de producción.
- ❌ Pasar de `BACKTESTING` a `LIVE` sin confirmación explícita.
- ❌ Importar `infrastructure` desde `domain` o `application`.
- ❌ Compartir código entre data-engine y analytics-engine por import directo.
- ❌ Usar `console.log` o `print()` en código de producción.
- ❌ Catchear todas las exceptions con `catch (e) {}` silencioso.
- ❌ Modificar archivos fuera del scope de la historia actual.
- ❌ Saltarme los tests "porque es trivial".
- ❌ Inventar historias o features que no están en `spec.md`.

## 10. Cuando hay ambigüedad

- Si una instrucción del usuario entra en conflicto con una invariante: aviso, propongo alternativa, espero OK.
- Si no sé qué hacer: pregunto, no invento.
- Si dos herramientas podrían servir: elijo la más simple y la documento.
- Si encuentro un bug fuera del scope: lo registro en `TASKS.md` bajo la historia correspondiente, no lo arreglo silenciosamente.

## 11. Al cerrar una sesión de trabajo

1. Actualizar `TASKS.md` (estados, %, notas, bitácora).
2. Si hay cambios sin commitear, mencionarlo.
3. Si hay un bloqueo, registrarlo en la sección Bloqueos.
4. Resumir en 3-5 bullets lo que se hizo.
5. Si hubo cambios en el modelo de deploy (Docker ↔ bare metal) o en el broker, actualizar `README.md` (sección Quick start) y `AGENTS.md` (§1, §2 #14).

## 12. Git workflow (ramificado y commits)

> Regla no negociable para todo cambio al código. Asume git ya inicializado con `main` y `dev` creados.

### Modelo de ramas

```
main          ← producción, deployable, protegido
   ↑
dev           ← integración, base para features
   ↑
feature/*     ← feature / historia, merge → dev
fix/*         ← bug no crítico, merge → dev
hotfix/*      ← bug crítico en producción, merge → main Y dev
```

- **`main`**: solo recibe merges desde `dev` (o `hotfix/*`). Cada merge es un release candidate.
- **`dev`**: integración de features. Base para nuevas feature branches.
- **`feature/<id>-<slug>`**: una rama por historia de `spec.md` §5.
- **`fix/<slug>`**: bug no crítico.
- **`hotfix/<slug>`**: bug crítico en producción.

### Convención de nombres de rama

| Tipo | Formato | Ejemplo |
|---|---|---|
| Feature | `feature/<id>-<slug-kebab>` | `feature/h1-tick-pipeline` |
| Bug | `fix/<slug-kebab>` | `fix/redis-flush-falsy-trigger` |
| Hotfix | `hotfix/<slug-kebab>` | `hotfix/live-mode-secret-leak` |

`<slug-kebab>` = 2-4 palabras en kebab-case, minúsculas. `<id>` referencia directa a IDs de `TASKS.md` (e.g. historia 1 → `h1`).

### Convención de commits (Conventional Commits)

Formato: `<tipo>(<scope>): <descripción corta>`

Tipos: `feat` · `fix` · `chore` · `docs` · `test` · `refactor` · `perf` · `ci`

Scopes útiles: `data-engine`, `analytics-engine`, `tools`, `commands`, `spec`, `tasks`, `agents`.

Ejemplos:
- `feat(data-engine): h1-001 scaffold Redis publisher`
- `fix(tools): regex (?i) no soportado en JS`
- `docs(tasks): update h1 a 50% progreso`
- `chore(deps): bump ioredis to 5.3.2`

### Flujo de trabajo por historia

1. **Inicio** — sync con `dev`:
   ```
   git checkout dev && git pull origin dev
   ```
2. **Crear rama**:
   ```
   git checkout -b feature/h1-tick-pipeline
   ```
3. **Trabajo** — commits frecuentes (idealmente 1 por tarea de `TASKS.md`), con conventional commit messages.
4. **Pre-merge validation** — todos deben pasar:
   - `quality_arch_lint`
   - `quality_typecheck_data_engine`
   - `quality_lint_data_engine`
   - `quality_test_data_engine` o `quality_test_analytics_engine`
   - `quality_secret_scan`
5. **Push + PR manual** — yo hago el push:
   ```
   git push -u origin feature/h1-tick-pipeline
   ```
   El usuario abre el PR en GitHub apuntando a `dev` y lo mergea tras revisar. El título del PR es el resumen de la historia (e.g. `H1: Tick Pipeline (<2ms)`).
6. **Merge a `main`** — solo cuando el batch en `dev` esté validado. PR manual `dev` → `main` con título `Release: H1, H2, H3 (YYYY-MM-DD)`. Tag sugerido: `v0.1.0`.

### Confirmaciones explícitas (te pregunto antes de…)

| Acción | Por qué |
|---|---|
| Crear rama nueva | Confirmo el nombre. |
| `git push` (especialmente con `-u`) | Confirmo. |
| `git push --force` | Rechazo salvo que lo pidas explícito. |
| Borrar rama post-merge | Confirmo. |

> **Importante**: yo **nunca** abro ni mergeo PRs. Eso lo haces tú manualmente en github.com. Yo solo preparo la rama, hago el push y te aviso de que está lista.

### Hotfixes (emergencia)

1. `git checkout main && git pull`
2. `git checkout -b hotfix/<slug>`
3. Fix mínimo + 1 test que reproduzca el bug
4. PR `hotfix/*` → `main` inmediato (tú lo abres y mergeas), tag patch release
5. **También** PR `hotfix/*` → `dev` para no perder el fix
6. Entrada en bitácora de `TASKS.md`

### Anti-patrones Git

- ❌ Commit directo a `main` o `dev` (todo va por feature branch)
- ❌ Commits sin conventional commit message
- ❌ `push --force` a `main` o `dev`
- ❌ Commits con archivos de más (mezclar features en una rama)
- ❌ Commits con secrets en el diff
- ❌ "WIP" commits que quedan en la historia (squash antes de merge)
- ❌ Yo abriendo o mergeando PRs sin que tú me lo pidas (los PRs los haces tú manualmente en GitHub)

### Pre-requisito

Esta regla asume que el repo tiene `git init`, `main` y `dev` creadas con el primer commit. Si no existen, lo hago al arrancar la primera historia:
1. `bootstrap_init_git` (te pedirá permiso por la policy)
2. `git checkout -b dev`
3. `git push -u origin dev`

### Adicional al cierre de sesión (extiende §11)

- Reportar rama actual y cambios sin commitear.
- Si commiteé pero no hice push: decir que la rama está solo local.
- Si hice push pero el PR no se ha mergeado: dar el nombre de la rama para que abras el PR.
- Si el PR ya se mergeó a `dev`: anotarlo en bitácora de `TASKS.md`.
- Si se mergeó a `main`: decirlo **explícitamente** (es deployable).

## 13. Session Semaphore — control de concurrencia entre sesiones

> Sistema de locks por archivo para que múltiples sesiones de AI agent trabajen
> en paralelo sin pisarse. El estado vive en `SESSION.json` (no versionado, en `.gitignore`).
> `SESSION.md` contiene la documentación del sistema.

### Ciclo de vida de un archivo

```
free ──→ in_progress (tomado por sesión X) ──→ done ──→ free (disponible)
                                           ──→ verified ──→ free
                                           ──→ superseded ──→ free
```

### Flujo obligatorio por sesión

1. **Inicio** — leer `SESSION.md` vía `session_status`:
   - Si hay archivos `in_progress` de otra sesión: listarlos y decidir si continuar.
   - Si no hay conflicto → `session_begin(branch)` para registrar la sesión.

2. **Antes de editar cualquier archivo** — `session_lock(path, change, detail, scope)`:
   - Si el archivo está `free` → lo tomás, nadie más lo toca.
   - Si otra sesión ya lo tiene con **mismo scope** → **saltás** ("ya está cubierto").
   - Si otra sesión lo tiene con **distinto scope** → registrás posible conflicto.

3. **Decisiones no triviales** — `session_decision(scope, decision, rationale, alternative)`:
   - Registrar ANTES de implementar. Incluir rationale y alternativa descartada.
   - Esto evita que otra sesión revierta una decisión sin saberlo.

4. **Al terminar de editar** — `session_unlock(path, status?, needs_review?)`:
   - `status=done` (default): cambio terminado, archivo libre.
   - `status=verified`: cambio revisado y aprobado.
   - `status=superseded`: otra solución reemplazó esta.
   - `needs_review=true`: pedís que otra sesión verifique.

5. **Verificación cruzada** — si encontrás un archivo `done` con `needs_review=true`:
   - Lo revisás y lo marcás como `verified` o `superseded`.

6. **Cierre** — no es necesario "cerrar sesión" para liberar archivos.
   - Los archivos se liberan individualmente con `session_unlock`.
   - Cuando todos tus `locked_files` están `done`, la sesión puede cerrar.

### Herramientas disponibles

| Herramienta | Cuándo usarla |
|---|---|
| `session_status` | Al inicio de cada sesión, o para ver el estado actual |
| `session_begin` | Al empezar una sesión (asigna UUID, setea branch) |
| `session_lock` | Antes de editar cualquier archivo |
| `session_unlock` | Al terminar de editar un archivo |
| `session_decision` | Antes de implementar una decisión no trivial |

### Anti-patrones (lo que NUNCA hago)

- ❌ Editar un archivo sin antes llamar `session_lock`.
- ❌ Ignorar un lock activo de otra sesión.
- ❌ No registrar una decisión de diseño que afecte a otros módulos.
- ❌ Dejar un archivo `in_progress` al cerrar sesión (si pasa, la siguiente sesión lo ve y pregunta).

---

**Última actualización**: 2026-06-19 — añadida sección 13 (Session Semaphore).
