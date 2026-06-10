#!/usr/bin/env bash
# deploy.sh — сборка и деплой AI Platform frontend + синхронизация CRM
#
# Использование:
#   bash deploy.sh          # полный деплой
#   bash deploy.sh --no-crm # только AI Platform, без синхронизации CRM

set -euo pipefail
SKIP_CRM=false
[[ "${1:-}" == "--no-crm" ]] && SKIP_CRM=true

FRONTEND="/home/ai-platform/frontend"
STATIC="/home/ai-platform/backend/static"
SCRIPTS="/home/ai-platform/scripts"

echo "═══════════════════════════════════════════"
echo " AI Platform Deploy  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════"

# ── 1. Сборка фронтенда ───────────────────────────────────────────────────────
echo "[deploy] Building frontend..."
cd "$FRONTEND"
npm run build

# ── 2. Копируем в static бэкенда ─────────────────────────────────────────────
echo "[deploy] Copying to backend static..."
\cp -rf "$FRONTEND/dist/." "$STATIC/"
echo "  ✓ $(cat $STATIC/index.html | grep 'index-' | grep -o 'index-[^.]*\.js')"

# ── 3. Синхронизация CRM ──────────────────────────────────────────────────────
if [ "$SKIP_CRM" = false ]; then
  echo "[deploy] Syncing CRM..."
  bash "$SCRIPTS/sync-crm-aichat.sh"
else
  echo "[deploy] Skipping CRM sync (--no-crm)"
fi

echo ""
echo "✅ Deploy complete!"
