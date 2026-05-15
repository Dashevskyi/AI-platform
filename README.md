# Multi-tenant AI Platform

Backend API на Python/FastAPI + Frontend на React/Vite/TypeScript.
Каждый tenant работает в изолированной AI-оболочке с собственными настройками LLM, ключами, памятью, базой знаний, инструментами, чатами и логами.

## Стек

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy 2.x (async), PostgreSQL, Alembic
- **Frontend**: React 19, Vite, TypeScript, Mantine UI, TanStack Query
- **LLM**: Ollama (локально), OpenAI-compatible, Deepseek-compatible

## Быстрый старт (без Docker)

### Требования
- Python 3.12+
- PostgreSQL 16+
- Node.js 20+
- Ollama (опционально, для локальных моделей)

### 1. База данных

```bash
sudo -u postgres psql -c "CREATE ROLE ai_platform WITH LOGIN PASSWORD 'ai_platform_secret';"
sudo -u postgres psql -c "CREATE DATABASE ai_platform OWNER ai_platform;"
```

### 2. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Скопировать .env
cp ../.env.example ../.env
# Отредактировать ../.env при необходимости

# Миграции
PYTHONPATH=. alembic upgrade head

# Запуск
# Для замеров latency и стабильной работы с локальным Ollama лучше без --reload
PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Backend доступен: http://localhost:8000
API docs: http://localhost:8000/docs

### 3. Frontend

```bash
cd frontend
npm install
VITE_BACKEND_TARGET=http://127.0.0.1:8000 npm run dev
```

Frontend доступен: http://localhost:5173

Если порт `8000` в вашей системе даёт аномальную задержку на `POST /messages`,
можно поднять backend на другом порту, например `8001`, и направить proxy туда:

```bash
cd backend
PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8001

cd ../frontend
VITE_BACKEND_TARGET=http://127.0.0.1:8001 npm run dev
```

### 4. Вход в систему

- Логин: `admin`
- Пароль: `admin`

## Структура проекта

```
backend/
  app/
    api/          # FastAPI роутеры (admin + tenant)
    core/         # Конфигурация, БД, безопасность
    models/       # SQLAlchemy модели (12 таблиц)
    schemas/      # Pydantic схемы
    services/     # Бизнес-логика (LLM pipeline, audit)
    providers/    # LLM провайдеры (Ollama, OpenAI, Deepseek)
  alembic/        # Миграции
  tests/          # Тесты

frontend/
  src/
    pages/        # Страницы (Login, Dashboard, Tenants, Chat, Logs)
    shared/       # API клиент, хуки, UI компоненты, тема
```

## API Endpoints

| Группа | Путь | Описание |
|--------|------|----------|
| Auth | POST /api/admin/auth/login | Вход |
| Tenants | /api/admin/tenants | CRUD tenants |
| Keys | /api/admin/tenants/{id}/keys | API ключи |
| Shell | /api/admin/tenants/{id}/shell | Настройки LLM |
| Tools | /api/admin/tenants/{id}/tools | Инструменты |
| KB | /api/admin/tenants/{id}/kb | База знаний |
| Memory | /api/admin/tenants/{id}/memory | Память |
| Chats | /api/tenants/{id}/chats | Чаты и сообщения |
| Logs | /api/admin/tenants/{id}/logs | Логи LLM запросов |
| Audit | /api/admin/audit | Аудит действий |
| Health | /health, /ready | Мониторинг |

## Провайдеры LLM

- **Ollama** — локальные модели через Ollama
- **OpenAI-compatible** — любой OpenAI API-совместимый сервис, включая `vLLM`
- **Deepseek-compatible** — Deepseek API

Для `Qwen 2.5 32B` на отдельном GPU-host рекомендованный shell config:

```json
{
  "provider_type": "openai_compatible",
  "provider_base_url": "http://172.10.100.9:8000/v1",
  "model_name": "qwen2.5-32b"
}
```
# AI-platform
