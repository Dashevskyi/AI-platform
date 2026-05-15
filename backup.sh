#!/bin/bash
# AI Platform — Backup Script
# Usage: ./backup.sh [backup_name]

set -euo pipefail

PROJECT_ROOT="/home/ai-platform"
BACKUP_ROOT="${PROJECT_ROOT}/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
NAME="${1:-$TIMESTAMP}"
BACKUP_DIR="${BACKUP_ROOT}/${NAME}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

mkdir -p "${BACKUP_DIR}"

echo "=== AI Platform Backup: ${NAME} ==="

echo -n "Backend... "
tar czf "${BACKUP_DIR}/backend.tar.gz" \
  --exclude='backend/venv' \
  --exclude='backend/__pycache__' \
  --exclude='backend/.pytest_cache' \
  --exclude='backend/.mypy_cache' \
  --exclude='backend/uploads' \
  -C "${PROJECT_ROOT}" backend
echo "$(du -sh "${BACKUP_DIR}/backend.tar.gz" | cut -f1)"

echo -n "Frontend... "
tar czf "${BACKUP_DIR}/frontend.tar.gz" \
  --exclude='frontend/node_modules' \
  --exclude='frontend/dist' \
  -C "${PROJECT_ROOT}" frontend
echo "$(du -sh "${BACKUP_DIR}/frontend.tar.gz" | cut -f1)"

echo -n "Root files... "
tar czf "${BACKUP_DIR}/root-files.tar.gz" \
  -C "${PROJECT_ROOT}" \
  .env .env.example README.md backup.sh restore.sh
echo "$(du -sh "${BACKUP_DIR}/root-files.tar.gz" | cut -f1)"

echo -n "Database... "
sudo -u postgres pg_dump ai_platform | gzip > "${BACKUP_DIR}/database.sql.gz"
echo "$(du -sh "${BACKUP_DIR}/database.sql.gz" | cut -f1)"

echo -n "Configs... "
tar czf "${BACKUP_DIR}/configs.tar.gz" \
  /etc/nginx/conf.d/ai-platform.conf \
  /etc/systemd/system/ai-platform-backend.service \
  /etc/systemd/system/ai-platform-backup.service \
  /etc/systemd/system/ai-platform-backup.timer \
  2>/dev/null || true
if [ -f "${BACKUP_DIR}/configs.tar.gz" ]; then
  echo "$(du -sh "${BACKUP_DIR}/configs.tar.gz" | cut -f1)"
else
  echo "пропущено"
fi

echo -n "Retention... "
find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -mtime +"$((RETENTION_DAYS - 1))" -print -exec rm -rf {} + 2>/dev/null || true
echo "older than ${RETENTION_DAYS} days removed"

echo ""
echo "Done: ${BACKUP_DIR}"
echo "Size: $(du -sh "${BACKUP_DIR}" | cut -f1)"
ls -la "${BACKUP_DIR}/"
