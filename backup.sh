#!/bin/bash
# AI Platform — Backup Script
# Usage: ./backup.sh [backup_name]

set -e

BACKUP_ROOT="/home/ai-platform/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
NAME="${1:-$TIMESTAMP}"
BACKUP_DIR="${BACKUP_ROOT}/${NAME}"

mkdir -p "${BACKUP_DIR}"

echo "=== AI Platform Backup: ${NAME} ==="

# 1. Code
echo -n "Код проекта... "
tar czf "${BACKUP_DIR}/code.tar.gz" \
  --exclude='*/venv/*' \
  --exclude='*/node_modules/*' \
  --exclude='*/dist/*' \
  --exclude='*/__pycache__/*' \
  --exclude='*/.pytest_cache/*' \
  --exclude='*/backups/*' \
  -C /home ai-platform/.env ai-platform/.env.example ai-platform/README.md \
  ai-platform/backend ai-platform/frontend 2>/dev/null
echo "$(du -sh ${BACKUP_DIR}/code.tar.gz | cut -f1)"

# 2. Database
echo -n "База данных... "
sudo -u postgres pg_dump ai_platform | gzip > "${BACKUP_DIR}/database.sql.gz"
echo "$(du -sh ${BACKUP_DIR}/database.sql.gz | cut -f1)"

# 3. Configs
echo -n "Конфигурация... "
tar czf "${BACKUP_DIR}/configs.tar.gz" \
  /etc/nginx/conf.d/ai-platform.conf \
  /etc/systemd/system/ai-platform-backend.service \
  2>/dev/null
echo "$(du -sh ${BACKUP_DIR}/configs.tar.gz | cut -f1)"

# 4. Keep only last 10 backups
cd "${BACKUP_ROOT}"
ls -dt */ 2>/dev/null | tail -n +11 | xargs rm -rf 2>/dev/null || true

echo ""
echo "Готово: ${BACKUP_DIR}"
echo "Размер: $(du -sh ${BACKUP_DIR} | cut -f1)"
ls -la "${BACKUP_DIR}/"
