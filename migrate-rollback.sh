#!/usr/bin/env bash
# =============================================================================
# migrate-rollback.sh — Revierte migrate-docs.sh
# =============================================================================
# Lee /tmp/migration.manifest y deshace cada operación en orden inverso.
# Uso: bash migrate-rollback.sh
# =============================================================================
set -euo pipefail

ROOT="/home/egraterol/projects/argos-bot"
MANIFEST="/tmp/migration.manifest"

cd "$ROOT"

if [ ! -f "$MANIFEST" ]; then
  echo "[ROLLBACK] ERROR: No se encuentra /tmp/migration.manifest"
  echo "[ROLLBACK] No hay nada que revertir."
  exit 1
fi

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║           ROLLBACK — Revertir reorganización documental          ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Leyendo /tmp/migration.manifest...                             ║"
echo "╚══════════════════════════════════════════════════════════════════╝"

# Backup del manifest antes de consumirlo
cp "$MANIFEST" /tmp/migration.manifest.snap

# Contadores
total=0
ok=0
warn=0

rollback_mv() {
  local src="$1" dst="$2"
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    mkdir -p "$(dirname "$src")"
    mv "$dst" "$src" && echo "[ROLLBACK] ✔ restore $dst → $src"
  else
    echo "[ROLLBACK] ⚠ destino no existe: $dst (saltando)"
    warn=$((warn + 1))
  fi
}

rollback_cp() {
  local src="$1" dst="$2"
  if [ -f "$dst" ]; then
    rm "$dst" && echo "[ROLLBACK] ✔ rm copy $dst"
  else
    echo "[ROLLBACK] ⚠ copy no existe: $dst (saltando)"
    warn=$((warn + 1))
  fi
}

rollback_rm() {
  local target="$1" reason="$2"
  echo "[ROLLBACK] ⚠ No se puede restaurar: $target ($reason)"
  warn=$((warn + 1))
}

rollback_ln() {
  local target="$1" link="$2"
  if [ -L "$link" ]; then
    rm "$link" && echo "[ROLLBACK] ✔ rm symlink $link"
  else
    echo "[ROLLBACK] ⚠ symlink no existe: $link (saltando)"
    warn=$((warn + 1))
  fi
}

# Las operaciones sed requieren el patrón inverso
# Pero es difícil revertir sed exactamente (podría haber reemplazos múltiples)
# En su lugar, usamos git checkout para archivos modificados
rollback_sed() {
  local _pattern="$1" _replacement="$2" file="$3"
  if [ -f "$file" ]; then
    if git diff --quiet "$file" 2>/dev/null; then
      echo "[ROLLBACK] ✔ $file sin cambios (nada que revertir)"
    else
      git checkout -- "$file" && echo "[ROLLBACK] ✔ git checkout $file"
    fi
  else
    echo "[ROLLBACK] ⚠ archivo no existe: $file (saltando)"
    warn=$((warn + 1))
  fi
}

# Procesar manifest en orden inverso (por línea, reversed)
while IFS='||' read -r op src dst extra1 extra2; do
  total=$((total + 1))
  case "$op" in
    mv)
      rollback_mv "$src" "$dst"
      ok=$((ok + 1))
      ;;
    cp)
      rollback_cp "$src" "$dst"
      ok=$((ok + 1))
      ;;
    rm)
      rollback_rm "$src" "$dst"
      ;;
    ln)
      rollback_ln "$src" "$dst"
      ok=$((ok + 1))
      ;;
    sed)
      rollback_sed "$src" "$dst" "$extra1"
      ok=$((ok + 1))
      ;;
    *)
      echo "[ROLLBACK] ⚠ operación desconocida: $op"
      warn=$((warn + 1))
      ;;
  esac
done < <(tac "$MANIFEST")

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║               ROLLBACK COMPLETO                                 ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Total operaciones procesadas: $total"
echo "║  Restauradas:                  $ok"
echo "║  Advertencias:                 $warn"
echo "║                                                                ║"
echo "║  ⚠ Notas:                                                      ║"
echo "║  - Los directorios vacíos no se eliminan automáticamente.      ║"
echo "║  - Si se crearon nuevos archivos después de migrate,           ║"
echo "║    pueden quedar huérfanos. Verificar con git status.          ║"
echo "║  - Corré quality_test_* y health_health_check después.         ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
