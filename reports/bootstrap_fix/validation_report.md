# Bootstrap Fix Report — 2026-06-19

## Status: 🟢 ALL 7 PHASES COMPLETE (code changes applied on disk)

## Phase 1 — ML Runtime Dependencies
- **File**: `apps/analytics-engine/pyproject.toml`
- **Changes**: Promoted `tensorflow>=2.15`, `xgboost>=2.0`, `scikit-learn>=1.3` from optional to main dependencies
- **Action needed**: Rebuild Docker image (`docker compose build analytics-engine`) or `pip install -e '.[ml,validation,dev]'`

## Phase 2 — Force LIVE_SIMULATION Mode
- **Files**: `composition.py`, `preflight.py`, `environment_mode_writer.py`
- **Changes**:
  - Added `LIVE_SIMULATION` to `EnvironmentMode` enum
  - Added `FORCE_LIVE_SIMULATION=true` env var override in `build_composition()`
  - Made preflight non-fatal for non-LIVE modes (warns instead of `sys.exit(1)`)
  - Added `[BOOT] MODE = LIVE_SIMULATION (ENFORCED)` log
- **Action needed**: Set `FORCE_LIVE_SIMULATION=true` in container environment

## Phase 3 — Circuit Breaker Fix (await bug)
- **Files**: `main.py`, `composition.py`
- **Root cause**: `comp.check_drawdown.is_halted()` called without `await` at 4 locations
- **Fix**: Added `await` to all 4 calls
- **Impact**: `drawdown_halted_skipping_position_check` will no longer appear every 5s

## Phase 4 — Boot Orchestration
- **Verdict**: No changes needed — boot sequence is correct
- Current order: preflight → build_composition → stream validation → recovery → streaming loops
- All loops gated on `comp.mode != "BACKTESTING"` (correct for LIVE_SIMULATION)

## Phase 5 — Contract Pipeline Validation
- **File**: `main.py` (lifespan)
- **Changes**: Added Redis stream existence check at boot
- Expected streams: `ticks:BTCUSDT`, `signals:trading`, `orders:execution`, `fills:execution`
- Missing streams are logged as warnings, never block boot

## Phase 6 — Health Gate Delayed Activation
- **File**: `main.py` (health endpoint + boot timestamp)
- **Changes**: Health endpoint returns `starting_up` for first 30s, then `ok`
- Added `uptime_s` field to health response

## Phase 7 — LIVE_SIMULATION Validation
- **File**: `main.py` (lifespan, after recovery)
- **Changes**: Validates mode == LIVE_SIMULATION and recovery not blocked
- Logs `[BOOT] LIVE_SIMULATION VALIDATION PASSED` or `FAILED` with details

## Manual Deployment Steps
1. `docker compose build analytics-engine` (ML deps)
2. Add `FORCE_LIVE_SIMULATION=true` to `docker-compose.yml` env or `.env`
3. `docker compose up -d analytics-engine`
4. Check `/health` returns `status: ok` after 30s warmup
5. Check logs: `[BOOT] MODE = LIVE_SIMULATION (ENFORCED)` then `[BOOT] LIVE_SIMULATION VALIDATION PASSED`

## Files Modified
| File | Changes |
|------|---------|
| `apps/analytics-engine/pyproject.toml` | Added tensorflow, xgboost, scikit-learn to main deps |
| `apps/analytics-engine/app/application/ports/environment_mode_writer.py` | Added LIVE_SIMULATION to enum |
| `apps/analytics-engine/app/preflight.py` | Non-fatal preflight for simulation modes |
| `apps/analytics-engine/app/composition.py` | Force override + 2 await fixes |
| `apps/analytics-engine/app/main.py` | 2 await fixes + stream validation + health gate + boot validation |

## Known Issues Not Addressed
1. **Binance testnet deprecation**: `LiveDriftWatchdog` error every 5min (`fetch_positions_failed`) — caught and logged, non-fatal
2. **New contract pipeline not deployed**: DE only publishes ticks, AE builds own candles — streams `signals:trading`, `orders:execution`, `fills:execution` will be missing (warned at boot)
3. **No Telegram env vars**: Preflight warns but doesn't block for LIVE_SIMULATION
