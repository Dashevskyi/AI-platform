#!/bin/bash
# AI Platform — Restore Script
# Usage: ./restore.sh <backup_name>
#   Example: ./restore.sh 20260407_194636

set -e

BACKUP_ROOT="/home/ai-platform/backups"

if [ -z "$1" ]; then
  echo "Использование: ./restore.sh <backup_name>"
  echo ""
  echo "Доступные бекапы:"
  ls -dt "${BACKUP_ROOT}"/*/ 2>/dev/null | while read d; do
    name=$(basename "$d")
    size=$(du -sh "$d" | cut -f1)
    echo "  ${name}  (${size})"
  done
  exit 1
fi

BACKUP_DIR="${BACKUP_ROOT}/${1}"

if [ ! -d "${BACKUP_DIR}" ]; then
  echo "Ошибка: бекап '${1}' не найден в ${BACKUP_ROOT}"
  exit 1
fi

echo "=== AI Platform Restore: ${1} ==="
echo "ВНИМАНИЕ: текущие данные будут перезаписаны!"
read -p "Продолжить? (y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
  echo "Отменено."
  exit 0
fi

# 1. Stop services
echo "Остановка сервисов..."
systemctl stop ai-platform-backend 2>/dev/null || true
systemctl stop nginx 2>/dev/null || true

# 2. Restore code
if [ -f "${BACKUP_DIR}/code.tar.gz" ]; then
  echo "Восстановление кода..."
  # Save venv and node_modules
  tar xzf "${BACKUP_DIR}/code.tar.gz" -C /home
fi

# 3. Restore database
if [ -f "${BACKUP_DIR}/database.sql.gz" ]; then
  echo "Восстановление базы данных..."
  sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='ai_platform' AND pid <> pg_backend_pid();" 2>/dev/null || true
  sudo -u postgres dropdb --if-exists ai_platform
  sudo -u postgres createdb -O ai_platform ai_platform
  gunzip -c "${BACKUP_DIR}/database.sql.gz" | sudo -u postgres psql ai_platform > /dev/null 2>&1
fi

# 4. Restore configs
if [ -f "${BACKUP_DIR}/configs.tar.gz" ]; then
  echo "Восстановление конфигурации..."
  tar xzf "${BACKUP_DIR}/configs.tar.gz" -C /
fi

# 5. Rebuild frontend
echo "Сборка фронтенда..."
cd /home/ai-platform/frontend
npm install --silent 2>/dev/null
npm run build 2>/dev/null
chcon -R -t httpd_sys_content_t dist/ 2>/dev/null || true

# 6. Restart services
echo "Запуск сервисов..."
systemctl daemon-reload
systemctl start ai-platform-backend
systemctl start nginx

sleep 3

# 7. Verify
echo ""
echo "Проверка..."
BACKEND=$(systemctl is-active ai-platform-backend)
NGINX=$(systemctl is-active nginx)
HEALTH=$(curl -s http://localhost/health 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "error")

echo "  Backend: ${BACKEND}"
echo "  Nginx:   ${NGINX}"
echo "  Health:  ${HEALTH}"
echo ""
echo "Восстановление завершено!"
