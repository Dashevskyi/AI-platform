# AI Platform — API Reference

## Base URL

```
https://<your-host>/api/tenants/{tenant_id}
```

`{tenant_id}` — UUID тенанта, выдаётся при создании в админке.

---

## Аутентификация

Все запросы к Tenant API требуют API-ключ. Ключ передаётся одним из двух способов:

```
X-API-Key: sk_live_abc123...
```

или

```
Authorization: Bearer sk_live_abc123...
```

API-ключи создаются в админке на вкладке "API Ключи" тенанта.

### Ошибки аутентификации

| Код | Описание |
|-----|----------|
| 401 | Ключ не передан или невалиден |
| 403 | Ключ деактивирован, истёк, или тенант неактивен |

---

## Чаты

### Создать чат

```
POST /api/tenants/{tenant_id}/chats
```

**Тело запроса:**

```json
{
  "title": "Обращение клиента #42",
  "description": "Вопрос о тарифах"
}
```

Оба поля опциональны.

**Ответ (201):**

```json
{
  "id": "a1b2c3d4-...",
  "tenant_id": "8ed857fe-...",
  "title": "Обращение клиента #42",
  "description": "Вопрос о тарифах",
  "status": "active",
  "created_by": null,
  "created_at": "2026-04-25T10:00:00",
  "updated_at": "2026-04-25T10:00:00"
}
```

### Список чатов

```
GET /api/tenants/{tenant_id}/chats?page=1&page_size=20
```

**Ответ:**

```json
{
  "items": [ /* ChatResponse[] */ ],
  "total_count": 42,
  "page": 1,
  "page_size": 20
}
```

### Получить чат

```
GET /api/tenants/{tenant_id}/chats/{chat_id}
```

---

## Сообщения

### Отправить сообщение

Отправляет сообщение пользователя и возвращает ответ ассистента. Запрос синхронный — ответ приходит после завершения генерации.

```
POST /api/tenants/{tenant_id}/chats/{chat_id}/messages
```

**Тело запроса:**

```json
{
  "content": "Как подключить ваш API?",
  "idempotency_key": "req_abc123"
}
```

- `content` (string, обязательно) — текст сообщения
- `idempotency_key` (string, опционально) — ключ идемпотентности для защиты от дублей

**Ответ (201):**

```json
{
  "id": "msg-uuid-...",
  "tenant_id": "8ed857fe-...",
  "chat_id": "a1b2c3d4-...",
  "role": "assistant",
  "content": "Для подключения API вам нужно...",
  "prompt_tokens": 150,
  "completion_tokens": 89,
  "total_tokens": 239,
  "latency_ms": 1230.5,
  "status": "sent",
  "created_at": "2026-04-25T10:00:05"
}
```

### Отправить сообщение с файлами

```
POST /api/tenants/{tenant_id}/chats/{chat_id}/messages/upload
Content-Type: multipart/form-data
```

**Поля формы:**

- `content` (string, обязательно) — текст сообщения
- `idempotency_key` (string, опционально) — ключ идемпотентности
- `files` (файлы, можно несколько) — прикладываемые файлы

**Ответ (201):** `MessageResponse` (аналогично обычному сообщению). Файлы обрабатываются асинхронно.

### История сообщений

```
GET /api/tenants/{tenant_id}/chats/{chat_id}/messages?page=1&page_size=50
```

`page_size` — максимум 200.

### Список вложений чата

```
GET /api/tenants/{tenant_id}/chats/{chat_id}/attachments
```

**Ответ:**

```json
[
  {
    "id": "att-uuid-...",
    "filename": "document.pdf",
    "file_type": "pdf",
    "file_size_bytes": 102400,
    "processing_status": "completed",
    "summary": "Документ содержит..."
  }
]
```

---

## Кастомные модели

Тенант может добавлять собственные LLM-модели.

### Список моделей

```
GET /api/tenants/{tenant_id}/custom-models?page=1&page_size=20
```

### Создать модель

```
POST /api/tenants/{tenant_id}/custom-models
```

```json
{
  "name": "GPT-4o",
  "provider_type": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "model_id": "gpt-4o",
  "tier": "heavy",
  "supports_tools": true,
  "supports_vision": true,
  "max_context_tokens": 128000
}
```

- `provider_type`: `"openai"`, `"anthropic"`, `"ollama"` и др.
- `tier`: `"light"`, `"medium"`, `"heavy"`
- `api_key` хранится в зашифрованном виде, в ответах возвращается замаскированным

### Обновить модель

```
PATCH /api/tenants/{tenant_id}/custom-models/{custom_model_id}
```

Все поля опциональны.

### Удалить модель

```
DELETE /api/tenants/{tenant_id}/custom-models/{custom_model_id}
```

Ответ: `204 No Content`.

---

## Пагинация

Все списковые эндпоинты поддерживают пагинацию:

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `page` | int | 1 | Номер страницы (от 1) |
| `page_size` | int | 20 | Размер страницы |

Формат ответа:

```json
{
  "items": [],
  "total_count": 100,
  "page": 1,
  "page_size": 20
}
```

---

## Примеры интеграции

### Python

```python
import requests

BASE_URL = "https://your-host.com/api/tenants"
TENANT_ID = "8ed857fe-1487-4c78-9a8b-b3316dd2e1af"
API_KEY = "sk_live_your_key_here"

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
}

# 1. Создать чат
chat = requests.post(
    f"{BASE_URL}/{TENANT_ID}/chats",
    headers=headers,
    json={"title": "Новый диалог"},
).json()

chat_id = chat["id"]
print(f"Чат создан: {chat_id}")

# 2. Отправить сообщение и получить ответ
response = requests.post(
    f"{BASE_URL}/{TENANT_ID}/chats/{chat_id}/messages",
    headers=headers,
    json={"content": "Привет! Расскажи о себе."},
).json()

print(f"Ассистент: {response['content']}")
print(f"Токены: {response['total_tokens']}, Задержка: {response['latency_ms']}ms")

# 3. Отправить сообщение с файлом
with open("report.pdf", "rb") as f:
    resp = requests.post(
        f"{BASE_URL}/{TENANT_ID}/chats/{chat_id}/messages/upload",
        headers={"X-API-Key": API_KEY},
        data={"content": "Проанализируй этот документ"},
        files={"files": ("report.pdf", f, "application/pdf")},
    ).json()

print(f"Ответ по файлу: {resp['content']}")

# 4. Получить историю
history = requests.get(
    f"{BASE_URL}/{TENANT_ID}/chats/{chat_id}/messages",
    headers=headers,
    params={"page": 1, "page_size": 50},
).json()

for msg in history["items"]:
    print(f"[{msg['role']}] {msg['content'][:80]}")
```

### JavaScript / TypeScript

```typescript
const BASE_URL = "https://your-host.com/api/tenants";
const TENANT_ID = "8ed857fe-1487-4c78-9a8b-b3316dd2e1af";
const API_KEY = "sk_live_your_key_here";

const headers = {
  "X-API-Key": API_KEY,
  "Content-Type": "application/json",
};

// 1. Создать чат
const chat = await fetch(`${BASE_URL}/${TENANT_ID}/chats`, {
  method: "POST",
  headers,
  body: JSON.stringify({ title: "Новый диалог" }),
}).then((r) => r.json());

const chatId = chat.id;

// 2. Отправить сообщение
const reply = await fetch(
  `${BASE_URL}/${TENANT_ID}/chats/${chatId}/messages`,
  {
    method: "POST",
    headers,
    body: JSON.stringify({
      content: "Привет! Расскажи о себе.",
      idempotency_key: crypto.randomUUID(),
    }),
  }
).then((r) => r.json());

console.log(`Ассистент: ${reply.content}`);
console.log(`Токены: ${reply.total_tokens}`);

// 3. Загрузить файл
const formData = new FormData();
formData.append("content", "Проанализируй документ");
formData.append("files", fileInput.files[0]);

const fileReply = await fetch(
  `${BASE_URL}/${TENANT_ID}/chats/${chatId}/messages/upload`,
  {
    method: "POST",
    headers: { "X-API-Key": API_KEY },
    body: formData,
  }
).then((r) => r.json());

// 4. История сообщений
const history = await fetch(
  `${BASE_URL}/${TENANT_ID}/chats/${chatId}/messages?page=1&page_size=50`,
  { headers }
).then((r) => r.json());

history.items.forEach((msg) =>
  console.log(`[${msg.role}] ${msg.content}`)
);
```

### cURL

```bash
TENANT_ID="8ed857fe-1487-4c78-9a8b-b3316dd2e1af"
API_KEY="sk_live_your_key_here"
BASE="https://your-host.com/api/tenants/$TENANT_ID"

# Создать чат
curl -s -X POST "$BASE/chats" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Тест"}' | jq .

# Отправить сообщение (подставьте chat_id)
CHAT_ID="..."
curl -s -X POST "$BASE/chats/$CHAT_ID/messages" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content": "Привет!"}' | jq .

# Отправить с файлом
curl -s -X POST "$BASE/chats/$CHAT_ID/messages/upload" \
  -H "X-API-Key: $API_KEY" \
  -F "content=Проанализируй" \
  -F "files=@report.pdf" | jq .

# История
curl -s "$BASE/chats/$CHAT_ID/messages?page=1&page_size=50" \
  -H "X-API-Key: $API_KEY" | jq .
```

---

## Коды ошибок

| Код | Описание |
|-----|----------|
| 200 | Успешный запрос |
| 201 | Ресурс создан |
| 204 | Удалено (без тела ответа) |
| 400 | Невалидные данные запроса |
| 401 | Не передан или невалидный API-ключ |
| 403 | Ключ деактивирован / истёк / тенант неактивен |
| 404 | Ресурс не найден |
| 409 | Дубликат (idempotency_key) |
| 422 | Ошибка валидации (Pydantic) |
| 500 | Внутренняя ошибка сервера |

Тело ошибки:

```json
{
  "detail": "Описание ошибки"
}
```

---

## Полная таблица эндпоинтов

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/chats` | Создать чат |
| GET | `/chats` | Список чатов |
| GET | `/chats/{chat_id}` | Получить чат |
| POST | `/chats/{chat_id}/messages` | Отправить сообщение |
| POST | `/chats/{chat_id}/messages/upload` | Сообщение с файлами |
| GET | `/chats/{chat_id}/messages` | История сообщений |
| GET | `/chats/{chat_id}/attachments` | Список вложений |
| POST | `/custom-models` | Создать кастомную модель |
| GET | `/custom-models` | Список кастомных моделей |
| GET | `/custom-models/{id}` | Получить модель |
| PATCH | `/custom-models/{id}` | Обновить модель |
| DELETE | `/custom-models/{id}` | Удалить модель |

Все пути относительно `/api/tenants/{tenant_id}`.
