#!/usr/bin/env bash
# =============================================================================
# ARGOS — Security CI Audit
# =============================================================================
# Verifica que no haya secretos hardcodeados, que .env esté ignorado,
# que .env.example exista, y que preflight esté integrado.
#
# USO:
#   bash scripts/security_audit.sh
#
# EXIT CODES:
#   0 → audit passed
#   1 → audit failed
# =============================================================================
set -euo pipefail

AUDIT_NAME="ARGOS Security Audit"
AUDIT_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
FAIL=0

# Busca texto en archivos excluyendo dirs no relevantes
# Compatible con entornos sin ripgrep (usa grep como fallback)
SEARCH() {
    local pattern="$1"
    local dir="${2:-.}"
    if command -v rg &>/dev/null; then
        rg -rn "$pattern" "$dir" \
            --glob '!.venv' --glob '!node_modules' --glob '!dist' \
            --glob '!__pycache__' --glob '!.git' --glob '!checkpoints' \
            --glob '!reports' --glob '!experiments' --glob '!test' \
            --glob '!tests' --glob '!mocks' --glob '!fixtures' \
            --glob '!*.example' --glob '!scripts/security_audit.sh' \
            2>/dev/null || true
    else
        grep -rn "$pattern" "$dir" \
            --include="*.ts" --include="*.py" --include="*.js" \
            --include="*.json" --include="*.yml" --include="*.yaml" \
            --include="*.env" --include="*.sh" \
            --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=dist \
            --exclude-dir=__pycache__ --exclude-dir=.git \
            --exclude-dir=checkpoints --exclude-dir=reports \
            --exclude-dir=experiments --exclude-dir=test --exclude-dir=tests \
            --exclude-dir=mocks --exclude-dir=fixtures \
            --exclude="*.example" \
            --exclude="security_audit.sh" 2>/dev/null || true
    fi
}

echo "═══════════════════════════════════════════════════════════════"
echo "  $AUDIT_NAME"
echo "  $AUDIT_DATE"
echo "═══════════════════════════════════════════════════════════════"

# ── Check 1: No hardcoded secrets in code ────────────────────────────────────
echo ""
echo "▸ Check 1: No hardcoded API keys/secrets in code"

# Check for the known leaked values
for pattern in "8618962250:AAEF" "jC7ouOdQuLs" "fBK7nk3BneGe"; do
    if SEARCH "$pattern" | grep -q .; then
        echo "  ❌ Found known leaked secret: $pattern"
        SEARCH "$pattern"
        FAIL=1
    fi
done

# Check for secrets pattern in source code (non-example files)
SECRET_PATTERN='(BINANCE_TESTNET_API_KEY|BINANCE_TESTNET_SECRET|EXCHANGE_API_KEY|EXCHANGE_API_SECRET|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|DISCORD_WEBHOOK_URL)\s*=\s*[A-Za-z0-9_\-:]{10,}'
if SEARCH "$SECRET_PATTERN" | grep -v 'your_token\|placeholder\|YOUR_TOKEN\|CHANGE_ME\|your_key\|<your_' | grep -q .; then
    echo "  ❌ Found potential hardcoded secrets in code:"
    SEARCH "$SECRET_PATTERN" | grep -v 'your_token\|placeholder\|YOUR_TOKEN\|CHANGE_ME\|your_key\|<your_'
    FAIL=1
else
    echo "  ✅ No hardcoded secrets in code"
fi

# ── Check 2: .env is gitignored ─────────────────────────────────────────────
echo ""
echo "▸ Check 2: .env files are gitignored"
for f in ".env" "apps/analytics-engine/.env" "apps/data-engine/.env"; do
    if [ -f "$f" ]; then
        if git check-ignore "$f" > /dev/null 2>&1; then
            echo "  ✅ $f is gitignored"
        else
            echo "  ❌ $f is NOT gitignored"
            FAIL=1
        fi
    else
        echo "  ⚠️  $f does not exist (not a problem)"
    fi
done

# ── Check 3: .env.example exists ────────────────────────────────────────────
echo ""
echo "▸ Check 3: .env.example files exist"
for f in ".env.example" "apps/data-engine/.env.example" "apps/analytics-engine/.env.example"; do
    if [ -f "$f" ]; then
        echo "  ✅ $f exists"
    else
        echo "  ❌ $f missing"
        FAIL=1
    fi
done

# ── Check 4: No .env files tracked in git ────────────────────────────────────
echo ""
echo "▸ Check 4: No .env files tracked in git"
TRACKED=$(git ls-files "*.env" 2>/dev/null | grep -v "\.env\.example" | grep -v "env-check" || true)
if [ -n "$TRACKED" ]; then
    echo "  ❌ Found tracked .env files: $TRACKED"
    FAIL=1
else
    echo "  ✅ No .env files tracked in git"
fi

# ── Check 5: Preflight integrated in data-engine ─────────────────────────────
echo ""
echo "▸ Check 5: Preflight integrated in data-engine bootstrap"
if grep -q "preflightCheck" apps/data-engine/src/main.ts 2>/dev/null; then
    echo "  ✅ data-engine preflight integrated (main.ts)"
else
    echo "  ❌ data-engine preflight NOT integrated"
    FAIL=1
fi

# ── Check 6: Preflight integrated in analytics-engine ────────────────────────
echo ""
echo "▸ Check 6: Preflight integrated in analytics-engine bootstrap"
if grep -q "abort_if_missing" apps/analytics-engine/app/composition.py 2>/dev/null; then
    echo "  ✅ analytics-engine preflight integrated (composition.py)"
elif grep -q "abort_if_missing" apps/analytics-engine/app/preflight.py 2>/dev/null; then
    echo "  ✅ analytics-engine preflight module exists (preflight.py)"
else
    echo "  ❌ analytics-engine preflight NOT integrated"
    FAIL=1
fi

# ── Check 7: Security infrastructure exists ──────────────────────────────────
echo ""
echo "▸ Check 7: Security infrastructure exists"
for f in \
    "security/required_secrets.json" \
    "security/preflight.py" \
    "apps/data-engine/src/infrastructure/security/preflight.ts" \
    "scripts/start-data-engine.sh" \
    "scripts/start-analytics-engine.sh" \
    "scripts/security_audit.sh"; do
    if [ -f "$f" ]; then
        echo "  ✅ $f"
    else
        echo "  ❌ $f missing"
        FAIL=1
    fi
done

# ── Check 8: No env var logging in production code ───────────────────────────
echo ""
echo "▸ Check 8: No full env var logging in production code"
if grep -rn "console\.\(log\|warn\|error\).*process\.env" apps/data-engine/src/ --include="*.ts" 2>/dev/null | grep -q .; then
    echo "  ❌ Found console.log(process.env) in data-engine"
    FAIL=1
elif grep -rn "print\(os\.environ\)" apps/analytics-engine/app/ --include="*.py" 2>/dev/null | grep -q .; then
    echo "  ❌ Found print(os.environ) in analytics-engine"
    FAIL=1
else
    echo "  ✅ No full env var logging detected"
fi

# ── Results ──────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
if [ $FAIL -eq 0 ]; then
    echo "  ✅ $AUDIT_NAME: PASSED"
    exit 0
else
    echo "  ❌ $AUDIT_NAME: FAILED — review issues above"
    exit 1
fi
