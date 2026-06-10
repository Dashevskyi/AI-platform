#!/usr/bin/env bash
# sync-crm-aichat.sh — синхронизирует ai-chat-core и компоненты AiChat
# из AI Platform во фронтенд CRM, затем пересобирает CRM.
#
# Запускается автоматически после деплоя AI Platform frontend.
# Вручную: bash /home/ai-platform/scripts/sync-crm-aichat.sh

set -euo pipefail

AP_CORE="/home/ai-platform/frontend/src/packages/ai-chat-core"
AP_COMP="/home/ai-platform/frontend/src/components/AiChat"
CRM_HOST="root@172.10.100.13"
CRM_CORE="/home/it-invest-crm/src/packages/ai-chat-core"
CRM_SAAS="/home/it-invest-crm/src/Components/AIChat/saas"
CRM_DIR="/home/it-invest-crm"

echo "[sync-crm] Starting AI Platform → CRM sync $(date '+%Y-%m-%d %H:%M:%S')"

# ── 1. packages/ai-chat-core ──────────────────────────────────────────────────
echo "[sync-crm] Syncing ai-chat-core package..."
for f in api.ts types.ts index.ts \
          useAiChatList.ts useAiChatMessages.ts useAiChatSend.ts \
          useAiChatAttachments.ts useAiChatArtifacts.ts \
          useMediaRecorder.ts useVAD.ts useWhisperLiveSTT.ts; do
  [ -f "$AP_CORE/$f" ] && scp -q "$AP_CORE/$f" "$CRM_HOST:$CRM_CORE/$f" && echo "  ✓ core/$f"
done

# ── 2. AiChat компоненты (с фиксом пути к пакету) ────────────────────────────
echo "[sync-crm] Syncing AiChat components..."
for f in AiChat.tsx ArtifactsPanel.tsx MicButton.tsx SpeakButton.tsx \
          VoiceModeOverlay.tsx index.ts; do
  if [ -f "$AP_COMP/$f" ]; then
    # ../../packages/ai-chat-core → ../../../packages/ai-chat-core (CRM глубже на 1 уровень)
    sed "s|'../../packages/ai-chat-core|'../../../packages/ai-chat-core|g; \
         s|\"../../packages/ai-chat-core|\"../../../packages/ai-chat-core|g" \
      "$AP_COMP/$f" > "/tmp/crm_sync_$f"
    scp -q "/tmp/crm_sync_$f" "$CRM_HOST:$CRM_SAAS/$f"
    echo "  ✓ saas/$f"
  fi
done

# ── 3. Сборка CRM ─────────────────────────────────────────────────────────────
echo "[sync-crm] Building CRM..."
ssh "$CRM_HOST" "cd $CRM_DIR && npm run build 2>&1 | tail -5"

echo "[sync-crm] Done ✓"
