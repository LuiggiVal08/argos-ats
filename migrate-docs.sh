#!/usr/bin/env bash
# =============================================================================
# migrate-docs.sh — Reorganización documental argos-ats
# =============================================================================
# Separa ACTIVE de ARCHIVE, crea research/, models/production, registry/
# Genera /tmp/migration.manifest para rollback con migrate-rollback.sh
# =============================================================================
set -euo pipefail

ROOT="/home/egraterol/projects/argos-bot"
MANIFEST="/tmp/migration.manifest"
ROLLBACK_LOG="/tmp/migration.rollback.log"

cd "$ROOT"

# Init manifest
: > "$MANIFEST"
: > "$ROLLBACK_LOG"

log()  { echo "[MIGRATE] $*"; }
mv_m() {
  # mv + manifest: mueve src a dst y lo registra
  local src="$1" dst="$2"
  if [ -e "$src" ] || [ -L "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    mv "$src" "$dst" && echo "mv||$src||$dst" >> "$MANIFEST" && log "✔ mv $src → $dst"
  else
    log "⚠ SKIP $src (no existe)"
  fi
}
cp_m() {
  # cp + manifest: copia src a dst y registra para rollback (rm del destino)
  local src="$1" dst="$2"
  if [ -f "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst" && echo "cp||$src||$dst" >> "$MANIFEST" && log "✔ cp $src → $dst"
  else
    log "⚠ SKIP $src (no existe)"
  fi
}
rm_m() {
  # rm + manifest: borra y registra para rollback (no se puede restaurar — warning)
  local target="$1" reason="$2"
  if [ -e "$target" ] || [ -L "$target" ]; then
    echo "rm||$target||$reason" >> "$MANIFEST"
    rm -rf "$target" && log "✔ rm $target ($reason)"
  fi
}
ln_m() {
  # ln -s + manifest: crea symlink
  local target="$1" link="$2"
  if [ ! -e "$link" ]; then
    ln -s "$target" "$link" && echo "ln||$target||$link" >> "$MANIFEST" && log "✔ ln -s $target → $link"
  else
    log "⚠ SKIP $link (ya existe)"
  fi
}
sed_m() {
  # sed para actualizar paths: registra patrón para revertir
  # Usa printf para evitar problemas con comillas anidadas en bash
  local pattern="$1" replacement="$2" file="$3"
  if [ -f "$file" ]; then
    echo "sed||$pattern||$replacement||$file" >> "$MANIFEST"
    printf 's|%s|%s|g\n' "$pattern" "$replacement" > /tmp/_sed_script.$$
    sed -i -f /tmp/_sed_script.$$ "$file"
    rm -f /tmp/_sed_script.$$
    log "✔ sed s|$pattern|$replacement| $file"
  else
    log "⚠ SKIP sed $file (no existe)"
  fi
}

# ============================================================================
# FASE 1: Directorios
# ============================================================================
log "=== FASE 1: Crear directorios ==="

mkdir -p archive/{audits,incidents,sessions,roadmaps,specs,business,qv2}
mkdir -p archive/reports/{audits,experiments,legacy,bootstrap_fix}
mkdir -p research/qv2/{baseline,feature_selection,economic_validation,production_validation,shared}
mkdir -p models/{production,shadow,candidates,archive/{legacy,invalid,superseded}}
mkdir -p reports/active/{operational,paper_trading}
mkdir -p reports/archive/{qv2,audits,experiments,legacy,bootstrap_fix,distribution_comparison}
mkdir -p registry/decisions

# ============================================================================
# FASE 2: Raíz → archive/
# ============================================================================
log "=== FASE 2: Raíz → archive/ ==="

# Audits
for f in \
  AUDIT_FINAL.md AUDIT_REPORT.md AUDIT_REPORT_RUNTIME.md \
  AUDIT_VERDICT.md EXECUTION_SAFETY_VERDICT.md SYSTEM_VALIDATION_REPORT.md
do
  mv_m "$f" "archive/audits/$f"
done

# Incidents
mv_m "REDUCE_ONLY_FORENSIC_REPORT.md" "archive/incidents/REDUCE_ONLY_FORENSIC_REPORT.md"

# Sessions
mv_m "FINAL_SESSION_REPORT.md" "archive/sessions/FINAL_SESSION_REPORT.md"
mv_m "FIX_REPORT.md" "archive/sessions/FIX_REPORT.md"

# Roadmaps
mv_m "ROADMAP-v2.md" "archive/roadmaps/ROADMAP-v2.md"
mv_m "ROADMAP_PRODUCTION_AND_RESEARCH.md" "archive/roadmaps/ROADMAP_PRODUCTION_AND_RESEARCH.md"
if [ -d "roadmaps" ]; then
  mv_m "roadmaps" "archive/roadmaps/roadmaps"
fi

# Specs
mv_m "TARGET_SPEC.md" "archive/specs/TARGET_SPEC.md"
mv_m "TARGET_ANALYSIS_REPORT.md" "archive/specs/TARGET_ANALYSIS_REPORT.md"
if [ -d "specs" ]; then
  mv_m "specs" "archive/specs/specs"
fi

# Business
mv_m "BUSINESS-PLAN.md" "archive/business/BUSINESS-PLAN.md"

# QV2 output dirs
for d in qv2_baseline_output qv2_phase35_output qv2_phase375_output qv2_phase38_output; do
  if [ -d "$d" ]; then
    mv_m "$d" "archive/qv2/$d"
  fi
done

# QV2 logs
for f in qv2_*.log; do
  if [ -f "$f" ]; then
    mv_m "$f" "archive/qv2/$f"
  fi
done

# ============================================================================
# FASE 3: scripts/ QV2 → research/qv2/
# ============================================================================
log "=== FASE 3: Scripts de investigación → research/qv2/ ==="

mv_m "scripts/qv2_baseline.py" "research/qv2/baseline/qv2_baseline.py"
mv_m "scripts/qv2_phase35.py" "research/qv2/production_validation/qv2_phase35.py"
mv_m "scripts/qv2_phase375.py" "research/qv2/production_validation/qv2_phase375.py"
mv_m "scripts/qv2_phase375b_stability.py" "research/qv2/production_validation/qv2_phase375b_stability.py"
mv_m "scripts/qv2_phase38.py" "research/qv2/production_validation/qv2_phase38.py"
mv_m "scripts/qv2_phase38_fast.py" "research/qv2/production_validation/qv2_phase38_fast.py"
mv_m "scripts/qv2_phase39.py" "research/qv2/production_validation/qv2_phase39.py"
mv_m "scripts/qv2_phase4.py" "research/qv2/production_validation/qv2_phase4.py"
mv_m "scripts/qv2_phase4_report.py" "research/qv2/production_validation/qv2_phase4_report.py"
mv_m "scripts/target_analysis.py" "research/qv2/feature_selection/target_analysis.py"
mv_m "scripts/strategy_vs_market_separation_test.py" \
   "research/qv2/economic_validation/strategy_vs_market_separation_test.py"

# target_analysis_output/
if [ -d "scripts/target_analysis_output" ]; then
  mv_m "scripts/target_analysis_output" "research/qv2/feature_selection/target_analysis_output"
fi

# ============================================================================
# FASE 4: models/ → models/production/ + shadow/ + archive/
# ============================================================================
log "=== FASE 4: models/ → estructura viva ==="

# Mover modelos de producción
mv_m "models/btc" "models/production/btc"
mv_m "models/btc_usdt" "models/.btc_usdt_duplicate_removed"
mv_m "models/eth" "models/production/eth"
mv_m "models/sol" "models/production/sol"

# Symlink backward-compat
ln_m "production/btc" "models/btc"

log "\n⚠ NOTA: models/btc_usdt era IDÉNTICO a models/btc (diff confirmado)."
log "  Movido a models/.btc_usdt_duplicate_removed/ por si se necesita restaurar."

# ============================================================================
# FASE 5: reports/ → active/ + archive/
# ============================================================================
log "=== FASE 5: reports/ → active/ + archive/ ==="

# --- ACTIVE: documentos operativos ---
mv_m "reports/PAPER_TRADING_OBSERVABILITY_AUDIT.md" \
     "reports/active/paper_trading/PAPER_TRADING_OBSERVABILITY_AUDIT.md"
mv_m "reports/STARTUP_OBSERVABILITY_VALIDATION.md" \
     "reports/active/operational/STARTUP_OBSERVABILITY_VALIDATION.md"
mv_m "reports/PHASE4_PATCH_REPORT.md" \
     "reports/active/operational/PHASE4_PATCH_REPORT.md"

# Copiar PHASE4_PRODUCTION_REPORT.md desde qv2_phase4_output/ a active/
if [ -f "reports/qv2_phase4_output/PHASE4_PRODUCTION_REPORT.md" ]; then
  cp_m "reports/qv2_phase4_output/PHASE4_PRODUCTION_REPORT.md" \
       "reports/active/operational/PHASE4_PRODUCTION_REPORT.md"
fi

# --- ARCHIVE: reports históricos ---
# reports/agente/ → reports/archive/audits/
if [ -d "reports/agente" ]; then
  mv_m "reports/agente" "reports/archive/audits/agente"
fi

# reports/quant_validation_v1/ → reports/archive/experiments/
if [ -d "reports/quant_validation_v1" ]; then
  mv_m "reports/quant_validation_v1" "reports/archive/experiments/quant_validation_v1"
fi

# reports/quant_validation_v2*/ → reports/archive/qv2/
for d in reports/quant_validation_v2*; do
  if [ -d "$d" ]; then
    mv_m "$d" "reports/archive/qv2/$(basename "$d")"
  fi
done

# reports/qv2_phase39_output/  (si no se movió ya con el glob de arriba)
for d in reports/qv2_phase39_output reports/qv2_phase4_output; do
  if [ -d "$d" ]; then
    mv_m "$d" "reports/archive/qv2/$(basename "$d")"
  fi
done

# Subdirectorios de reports/
for d in reports/bootstrap_fix reports/distribution_comparison reports/smoke_test; do
  if [ -d "$d" ]; then
    mv_m "$d" "reports/archive/$(basename "$d")"
  fi
done

# Reports .md y .json sueltos → reports/archive/legacy/
# (excluir lo que ya está en active/ o archive/)
for f in reports/*.md reports/*.json; do
  if [ -f "$f" ]; then
    mv_m "$f" "reports/archive/legacy/$(basename "$f")"
  fi
done

# ============================================================================
# FASE 6: experiments/ → research/qv2/experiments/
# ============================================================================
log "=== FASE 6: experiments/ → research/qv2/experiments/ ==="

if [ -d "experiments" ]; then
  mv_m "experiments" "research/qv2/experiments"
fi

# ============================================================================
# FASE 7: Actualizar paths hardcodeados
# ============================================================================
log "=== FASE 7: Actualizar paths hardcodeados ==="

# chaos_test.py — paths absolutos
sed_m \
  "/home/egraterol/projects/argos-bot/models/btc/" \
  "/home/egraterol/projects/argos-bot/models/production/btc/" \
  "scripts/chaos_test.py"

# test_pipeline_consistency.py — path relativo
sed_m \
  "models/btc/" \
  "models/production/btc/" \
  "apps/analytics-engine/tests/unit/test_pipeline_consistency.py"

# test_boot.py — path relativo
sed_m \
  "Path(\"models/btc\")" \
  "Path(\"models/production/btc\")" \
  "tests/validation/test_boot.py"

# ============================================================================
# FASE 8: registry/ — trazabilidad
# ============================================================================
log "=== FASE 8: Crear registry/ ==="

cat > registry/models.yaml << 'REGEOF'
# registry/models.yaml — Catálogo de modelos
# Mantené este archivo actualizado al deployar/quitar modelos.

- model_id: btc_lr_v4
  status: production
  type: LogisticRegression
  feature_set: reduced_33
  target_spec: TARGET_SPEC_V1
  lookahead: 3
  threshold_buy: 0.50
  threshold_sell: 0.50
  training_dataset: btc_1h_2020_2026_v2
  experiment_source: qv2_phase39
  mcc: 0.324
  sharpe: 5.03
  deployment_date: 2026-06-26
  path: models/production/btc/
REGEOF

cat > registry/experiments.yaml << 'REGEOF'
# registry/experiments.yaml — Catálogo de experimentos ejecutados
- experiment_id: qv2_baseline
  phase: baseline
  status: completed
  conclusion: Established baseline metrics for quant validation framework
  date: 2026-05-01
- experiment_id: qv2_phase35
  phase: 3.5
  status: completed
  conclusion: Walk-forward validation passed
  date: 2026-05-15
- experiment_id: qv2_phase375
  phase: 3.75
  status: completed
  conclusion: Robustness tests passed
  date: 2026-05-22
- experiment_id: qv2_phase38
  phase: 3.8
  status: completed
  conclusion: Production validation — MCC 0.324, Sharpe 5.03
  date: 2026-06-01
- experiment_id: qv2_phase39
  phase: 3.9
  status: completed
  conclusion: Economic validation passed — alpha survives frictions
  date: 2026-06-15
- experiment_id: qv2_phase4
  phase: 4
  status: completed
  conclusion: Production deployment validated — all checks passed
  date: 2026-06-20
REGEOF

cat > registry/datasets.yaml << 'REGEOF'
# registry/datasets.yaml — Catálogo de datasets
- dataset_id: btc_1h_2020_2026_v2
  symbol: BTC/USDT
  timeframe: 1h
  range_start: 2020-01-01
  range_end: 2026-06-26
  n_samples: 56647
  features_used: 30
REGEOF

cat > registry/deployments.yaml << 'REGEOF'
# registry/deployments.yaml — Historial de despliegues
- deploy_id: d20260626_001
  model_id: btc_lr_v4
  status: active
  deployment_date: 2026-06-26
  environment: PAPER_TRADING
REGEOF

# ============================================================================
# FASE 9: Actualizar .gitignore
# ============================================================================
log "=== FASE 9: Actualizar .gitignore ==="

if ! grep -q "^migrate-docs.sh" .gitignore 2>/dev/null; then
  cat >> .gitignore << 'GITEOF'

# Migration scripts (temporales)
migrate-docs.sh
migrate-rollback.sh

# Trazabilidad local
registry/*.yaml
GITEOF
  log "✔ .gitignore actualizado"
fi

# ============================================================================
# RESUMEN
# ============================================================================
echo ""
log "╔══════════════════════════════════════════════════════════════════╗"
log "║               MIGRACIÓN COMPLETA                                ║"
log "╠══════════════════════════════════════════════════════════════════╣"
log "║  Manifest:         /tmp/migration.manifest                       ║"
log "║  Rollback:         bash migrate-rollback.sh                      ║"
log "║                                                                    ║"
log "║  Próximos pasos:                                                  ║"
log "║  1. bash migrate-rollback.sh  (si algo falló)                     ║"
log "║  2. quality_test_data_engine                                      ║"
log "║  3. quality_test_analytics_engine                                 ║"
log "║  4. Verificar carga de modelos                                   ║"
log "║  5. Monitorear logs 24h                                          ║"
log "║  6. Eliminar symlink models/btc cuando legacy paths mueran       ║"
log "╚══════════════════════════════════════════════════════════════════╝"
