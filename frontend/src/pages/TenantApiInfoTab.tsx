import { useState, useMemo, useCallback } from 'react';
import {
  Stack,
  Card,
  Text,
  Title,
  Code,
  ActionIcon,
  Group,
  Tooltip,
  Table,
  Loader,
  Center,
  Alert,
  Badge,
  Divider,
} from '@mantine/core';
import { IconCopy, IconCheck, IconAlertCircle, IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { keysApi } from '../shared/api/endpoints';
import { copyToClipboard } from '../shared/utils/clipboard';

function CopyBtn({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    copyToClipboard(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [value]);

  return (
    <Tooltip label={copied ? 'Скопировано' : 'Копировать'}>
      <ActionIcon variant="subtle" color={copied ? 'teal' : 'gray'} onClick={handleCopy}>
        {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
      </ActionIcon>
    </Tooltip>
  );
}

function CopyField({ label, value }: { label: string; value: string }) {
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="sm" fw={500} w={200} style={{ flexShrink: 0 }}>
        {label}
      </Text>
      <Code block style={{ flex: 1, userSelect: 'all' }}>
        {value}
      </Code>
      <CopyBtn value={value} />
    </Group>
  );
}

export function ApiInfoTab({ tenantId }: { tenantId: string }) {
  const baseUrl = useMemo(() => {
    const origin = window.location.origin;
    return `${origin}/api/tenants/${tenantId}`;
  }, [tenantId]);

  const { data: keysData, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'keys', 1],
    queryFn: () => keysApi.list(tenantId, 1, 100),
  });

  const activeKeys = keysData?.items.filter((k) => k.is_active) ?? [];

  const curlCreate = `curl -s -X POST "${baseUrl}/chats" \\
  -H "X-API-Key: \${API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{"title": "Новый чат"}' | jq .`;

  const curlMessage = `curl -s -X POST "${baseUrl}/chats/\${CHAT_ID}/messages" \\
  -H "X-API-Key: \${API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{"content": "Привет!"}' | jq .`;

  const curlStream = `curl -sN -X POST "${baseUrl}/chats/\${CHAT_ID}/messages/stream" \\
  -H "X-API-Key: \${API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{"content": "Привет!"}'`;

  const pythonExample = `import requests

BASE = "${baseUrl}"
KEY = "ваш_api_ключ"
headers = {"X-API-Key": KEY, "Content-Type": "application/json"}

# Создать чат
chat = requests.post(f"{BASE}/chats", headers=headers,
                     json={"title": "Тест"}).json()

# Отправить сообщение
reply = requests.post(f"{BASE}/chats/{chat['id']}/messages",
                      headers=headers,
                      json={"content": "Привет!"}).json()
print(reply["content"])`;

  const pythonStream = `import requests

with requests.post(
    f"{BASE}/chats/{chat['id']}/messages/stream",
    headers={"X-API-Key": KEY, "Content-Type": "application/json"},
    json={"content": "Привет!"},
    stream=True,
) as r:
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            print(event, line[6:])`;

  const jsExample = `const BASE = "${baseUrl}";
const KEY = "ваш_api_ключ";
const headers = { "X-API-Key": KEY, "Content-Type": "application/json" };

// Создать чат
const chat = await fetch(\`\${BASE}/chats\`, {
  method: "POST", headers,
  body: JSON.stringify({ title: "Тест" }),
}).then(r => r.json());

// Отправить сообщение
const reply = await fetch(\`\${BASE}/chats/\${chat.id}/messages\`, {
  method: "POST", headers,
  body: JSON.stringify({ content: "Привет!" }),
}).then(r => r.json());
console.log(reply.content);`;

  const jsStream = `// Получать ответ потоком (SSE)
const res = await fetch(\`\${BASE}/chats/\${chat.id}/messages/stream\`, {
  method: "POST", headers,
  body: JSON.stringify({ content: "Привет!" }),
});
const reader = res.body.getReader();
const decoder = new TextDecoder("utf-8");
let buf = "", content = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += decoder.decode(value, { stream: true });
  let sep;
  while ((sep = buf.indexOf("\\n\\n")) !== -1) {
    const block = buf.slice(0, sep); buf = buf.slice(sep + 2);
    let event = "message", data = "";
    for (const line of block.split("\\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data = line.slice(6);
    }
    const payload = JSON.parse(data || "{}");
    if (event === "content_chunk") content += payload.text;
    if (event === "done") console.log("Final:", payload.content);
  }
}`;

  const reactExample = `import { AiChat } from "@it-invest/ai-chat"; // путь зависит от пакета
import { MantineProvider } from "@mantine/core";

export function MyEmbeddedChat({ chatId }) {
  return (
    <MantineProvider>
      <AiChat
        tenantId="${tenantId}"
        chatId={chatId}
        mode="end-user"
        apiKey={process.env.AI_CHAT_KEY}
      />
    </MantineProvider>
  );
}`;

  return (
    <Stack gap="lg">
      <Card withBorder p="md">
        <Title order={4} mb="md">Параметры подключения</Title>
        <Stack gap="sm">
          <CopyField label="Tenant ID" value={tenantId} />
          <CopyField label="Base URL" value={baseUrl} />
          <CopyField label="Чаты" value={`${baseUrl}/chats`} />
          <CopyField label="Сообщения" value={`${baseUrl}/chats/{chat_id}/messages`} />
          <CopyField label="Поток (SSE)" value={`${baseUrl}/chats/{chat_id}/messages/stream`} />
          <CopyField label="Загрузка файлов" value={`${baseUrl}/chats/{chat_id}/messages/upload`} />
          <CopyField label="Кастомные модели" value={`${baseUrl}/custom-models`} />
        </Stack>
      </Card>

      <Card withBorder p="md">
        <Title order={4} mb="md">Аутентификация</Title>
        <Text size="sm" mb="sm">
          Передайте API-ключ в заголовке <Code>X-API-Key</Code> или <Code>Authorization: Bearer &lt;key&gt;</Code>.
        </Text>
        <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light" mb="sm">
          Tenant API возвращает <b>урезанный</b> набор полей сообщения: только <Code>id</Code>, <Code>chat_id</Code>, <Code>role</Code>, <Code>content</Code>, <Code>status</Code>, <Code>created_at</Code>.
          Внутренние данные (model, токены, размышления модели, цепочка tool-вызовов) не передаются клиенту — они доступны только в админ-панели.
        </Alert>

        {isLoading ? (
          <Center><Loader size="sm" /></Center>
        ) : activeKeys.length > 0 ? (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Имя ключа</Table.Th>
                <Table.Th>Префикс</Table.Th>
                <Table.Th>Истекает</Table.Th>
                <Table.Th>Последнее использование</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {activeKeys.map((key) => (
                <Table.Tr key={key.id}>
                  <Table.Td>{key.name}</Table.Td>
                  <Table.Td><Code>{key.key_prefix}...</Code></Table.Td>
                  <Table.Td>{key.expires_at ? new Date(key.expires_at).toLocaleDateString('ru-RU') : 'Бессрочный'}</Table.Td>
                  <Table.Td>{key.last_used_at ? new Date(key.last_used_at).toLocaleString('ru-RU') : '—'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Alert icon={<IconAlertCircle size={16} />} color="yellow">
            Нет активных API-ключей. Создайте ключ на вкладке "API Ключи".
          </Alert>
        )}
      </Card>

      <Card withBorder p="md">
        <Title order={4} mb="md">Эндпоинты</Title>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Метод</Table.Th>
              <Table.Th>Путь</Table.Th>
              <Table.Th>Описание</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            <Table.Tr><Table.Td><Code>POST</Code></Table.Td><Table.Td>/chats</Table.Td><Table.Td>Создать чат</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>GET</Code></Table.Td><Table.Td>/chats</Table.Td><Table.Td>Список чатов</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>GET</Code></Table.Td><Table.Td>/chats/&#123;chat_id&#125;</Table.Td><Table.Td>Получить чат</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>POST</Code></Table.Td><Table.Td>/chats/&#123;chat_id&#125;/messages</Table.Td><Table.Td>Отправить сообщение (синхронно)</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>POST</Code></Table.Td><Table.Td>/chats/&#123;chat_id&#125;/messages/stream</Table.Td><Table.Td>Отправить с потоковым ответом (SSE)</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>POST</Code></Table.Td><Table.Td>/chats/&#123;chat_id&#125;/messages/upload</Table.Td><Table.Td>Сообщение с файлами</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>GET</Code></Table.Td><Table.Td>/chats/&#123;chat_id&#125;/messages</Table.Td><Table.Td>История сообщений</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>GET</Code></Table.Td><Table.Td>/chats/&#123;chat_id&#125;/attachments</Table.Td><Table.Td>Список вложений</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>POST</Code></Table.Td><Table.Td>/custom-models</Table.Td><Table.Td>Создать модель</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>GET</Code></Table.Td><Table.Td>/custom-models</Table.Td><Table.Td>Список моделей</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>PATCH</Code></Table.Td><Table.Td>/custom-models/&#123;id&#125;</Table.Td><Table.Td>Обновить модель</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>DELETE</Code></Table.Td><Table.Td>/custom-models/&#123;id&#125;</Table.Td><Table.Td>Удалить модель</Table.Td></Table.Tr>
          </Table.Tbody>
        </Table>
        <Text size="xs" c="dimmed" mt="xs">Все пути относительно Base URL.</Text>
      </Card>

      <Card withBorder p="md">
        <Title order={4} mb="md">SSE события (стрим)</Title>
        <Text size="sm" mb="sm">
          Стрим отдаёт <Code>text/event-stream</Code>. Каждое событие — пара <Code>event: тип</Code> + <Code>data: JSON</Code>, разделены пустой строкой.
          Tenant API отправляет <b>только публичные</b> события — без раскрытия имён инструментов, KB-чанков, внутренних рассуждений.
        </Text>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Событие</Table.Th>
              <Table.Th>Видимость</Table.Th>
              <Table.Th>Payload</Table.Th>
              <Table.Th>Описание</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td><Code>stream_open</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{chat_id}`}</Code></Table.Td>
              <Table.Td>Соединение открыто</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>content_chunk</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{text, round}`}</Code></Table.Td>
              <Table.Td>Кусок текста ответа (token-streaming)</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>merge_pending</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{window_ms, buffered_count}`}</Code></Table.Td>
              <Table.Td>Сообщение ожидает объединения с другими (если включено в настройках тенанта)</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>merge_start</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{merged_count}`}</Code></Table.Td>
              <Table.Td>Объединение случилось — несколько сообщений → один LLM-запрос</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>throttle_rejected</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{message, retry_after}`}</Code></Table.Td>
              <Table.Td>Превышен лимит параллельных запросов</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>error</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{message}`}</Code></Table.Td>
              <Table.Td>Ошибка обработки запроса</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>done</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{content, ...}`}</Code></Table.Td>
              <Table.Td>Полный текст ответа собран (sanity-check для клиента)</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>final</Code></Table.Td>
              <Table.Td><Badge color="green" size="sm">public</Badge></Table.Td>
              <Table.Td><Code>{`{assistant_message_id}`}</Code></Table.Td>
              <Table.Td>Сообщение сохранено в БД, можно подгрузить через GET</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>kb_search_*</Code></Table.Td>
              <Table.Td><Badge color="orange" size="sm">admin only</Badge></Table.Td>
              <Table.Td>—</Table.Td>
              <Table.Td>Поиск в базе знаний (не отдаётся клиенту)</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>tool_call_*</Code></Table.Td>
              <Table.Td><Badge color="orange" size="sm">admin only</Badge></Table.Td>
              <Table.Td>—</Table.Td>
              <Table.Td>Имя/задержка инструмента (раскрывает внутреннее API)</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>provider_call_*</Code></Table.Td>
              <Table.Td><Badge color="orange" size="sm">admin only</Badge></Table.Td>
              <Table.Td>—</Table.Td>
              <Table.Td>Имя модели, латентность, токены</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Code>reasoning, reasoning_chunk</Code></Table.Td>
              <Table.Td><Badge color="orange" size="sm">admin only</Badge></Table.Td>
              <Table.Td>—</Table.Td>
              <Table.Td>Размышления модели (часто содержат system prompt)</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </Card>

      <Card withBorder p="md">
        <Group justify="space-between" mb="md">
          <Title order={4}>Примеры: cURL</Title>
        </Group>
        <Text size="sm" fw={500} mb={4}>Создать чат:</Text>
        <Group gap="xs" align="flex-start" wrap="nowrap" mb="sm">
          <Code block style={{ flex: 1 }}>{curlCreate}</Code>
          <CopyBtn value={curlCreate} />
        </Group>
        <Text size="sm" fw={500} mb={4}>Отправить сообщение:</Text>
        <Group gap="xs" align="flex-start" wrap="nowrap" mb="sm">
          <Code block style={{ flex: 1 }}>{curlMessage}</Code>
          <CopyBtn value={curlMessage} />
        </Group>
        <Text size="sm" fw={500} mb={4}>Стрим (SSE):</Text>
        <Group gap="xs" align="flex-start" wrap="nowrap">
          <Code block style={{ flex: 1 }}>{curlStream}</Code>
          <CopyBtn value={curlStream} />
        </Group>
      </Card>

      <Card withBorder p="md">
        <Group justify="space-between" mb="md">
          <Title order={4}>Пример: Python</Title>
          <CopyBtn value={pythonExample} />
        </Group>
        <Code block>{pythonExample}</Code>
        <Divider my="md" />
        <Group justify="space-between" mb="sm">
          <Text size="sm" fw={600}>Стрим в Python:</Text>
          <CopyBtn value={pythonStream} />
        </Group>
        <Code block>{pythonStream}</Code>
      </Card>

      <Card withBorder p="md">
        <Group justify="space-between" mb="md">
          <Title order={4}>Пример: JavaScript</Title>
          <CopyBtn value={jsExample} />
        </Group>
        <Code block>{jsExample}</Code>
        <Divider my="md" />
        <Group justify="space-between" mb="sm">
          <Text size="sm" fw={600}>Стрим в JS (fetch + ReadableStream):</Text>
          <CopyBtn value={jsStream} />
        </Group>
        <Code block>{jsStream}</Code>
      </Card>

      <Card withBorder p="md">
        <Title order={4} mb="md">React-компонент &lt;AiChat /&gt;</Title>
        <Text size="sm" mb="sm">
          Если у клиента React-приложение, можно встроить готовый чат-компонент вместо собственного UI.
          Компонент включает: token-streaming ответа, превью загруженных файлов, drag-and-drop, авто-скролл, обработку ошибок и лимитов.
        </Text>
        <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light" mb="sm">
          В режиме <Code>mode="end-user"</Code> компонент скрывает технические детали (имена tools, размышления модели, статистику токенов) — соответствует политике санитизации API.
        </Alert>
        <Title order={5} mt="sm" mb={4}>Props</Title>
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Prop</Table.Th>
              <Table.Th>Тип</Table.Th>
              <Table.Th>Описание</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            <Table.Tr><Table.Td><Code>tenantId</Code></Table.Td><Table.Td><Code>string</Code></Table.Td><Table.Td>UUID тенанта (обязательно)</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>chatId</Code></Table.Td><Table.Td><Code>string | null</Code></Table.Td><Table.Td>Активный чат; <Code>null</Code> = пустой экран</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>mode</Code></Table.Td><Table.Td><Code>"admin" | "end-user"</Code></Table.Td><Table.Td>По умолчанию <Code>"admin"</Code>. <Code>"end-user"</Code> для embed</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>apiKey</Code></Table.Td><Table.Td><Code>string</Code></Table.Td><Table.Td>Tenant API key (нужен только при <Code>mode="end-user"</Code>)</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>apiBase</Code></Table.Td><Table.Td><Code>string</Code></Table.Td><Table.Td>Кастомный base URL (по умолчанию same origin)</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>features</Code></Table.Td><Table.Td><Code>{`{ showTrail, showReasoning, showStats, showFiles, showToolResults, showChatControls }`}</Code></Table.Td><Table.Td>Точечные оверрайды флагов</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>onMessageSent</Code></Table.Td><Table.Td><Code>() =&gt; void</Code></Table.Td><Table.Td>Колбэк после успешной отправки</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>onChatCreated</Code></Table.Td><Table.Td><Code>(chatId: string) =&gt; void</Code></Table.Td><Table.Td>Колбэк при создании нового чата</Table.Td></Table.Tr>
          </Table.Tbody>
        </Table>
        <Title order={5} mt="md" mb={4}>Минимальный пример</Title>
        <Group gap="xs" align="flex-start" wrap="nowrap">
          <Code block style={{ flex: 1 }}>{reactExample}</Code>
          <CopyBtn value={reactExample} />
        </Group>
        <Text size="xs" c="dimmed" mt="xs">
          Зависимости: React 18+, <Code>@mantine/core</Code>, <Code>@tanstack/react-query</Code>. Компонент должен находиться внутри <Code>&lt;MantineProvider&gt;</Code> и <Code>&lt;QueryClientProvider&gt;</Code>.
        </Text>

        <Title order={5} mt="md" mb={4}>Цветовая гамма и темизация</Title>
        <Text size="sm" mb="sm">
          Компонент рисует все цвета через CSS-переменные Mantine — <Code>var(--mantine-color-blue-filled)</Code>, <Code>var(--mantine-color-default-hover)</Code>, <Code>var(--mantine-color-dimmed)</Code>, <Code>var(--mantine-color-red-6)</Code>. Достаточно настроить тему в <Code>MantineProvider</Code>, и все элементы (бабблы пользователя, фон ответа, бейджи, ссылки) подстроятся.
        </Text>
        <Code block>{`import { MantineProvider, createTheme } from "@mantine/core";

const theme = createTheme({
  primaryColor: "violet",        // основной цвет — синие баблы юзера, кнопка "отправить"
  primaryShade: { light: 6, dark: 5 },
  colors: {
    // переопределение конкретного оттенка (10 шкал)
    brand: ["#f0f4ff", "#dbe4ff", "#bac8ff", "#91a7ff", "#748ffc",
            "#5c7cfa", "#4c6ef5", "#4263eb", "#3b5bdb", "#364fc7"],
  },
  fontFamily: "Inter, sans-serif",
  defaultRadius: "md",
});

<MantineProvider theme={theme} defaultColorScheme="auto">
  <AiChat tenantId="..." chatId={id} mode="end-user" apiKey={key} />
</MantineProvider>`}</Code>

        <Text size="sm" mt="md" mb="sm">
          Светлая/тёмная тема — переключателем <Code>defaultColorScheme="light" | "dark" | "auto"</Code>. Компонент использует <Code>light-dark()</Code> CSS-функцию, чтобы корректно работать в обеих темах автоматически.
        </Text>

        <Title order={5} mt="md" mb={4}>Точечная настройка через CSS</Title>
        <Text size="sm" mb="sm">
          Если нужны нестандартные цвета только для чата (без вмешательства в общую тему приложения), оберни компонент в контейнер с переопределёнными переменными:
        </Text>
        <Code block>{`<div style={{
  "--mantine-color-blue-filled": "#10b981",  // цвет баблов пользователя
  "--mantine-color-default-hover": "#f3f4f6", // фон ответа ассистента
  "--mantine-color-dimmed": "#6b7280",        // подписи (время, токены)
}}>
  <AiChat ... />
</div>`}</Code>

        <Text size="sm" mt="md" mb="sm">
          Полный список используемых переменных:
        </Text>
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Переменная</Table.Th>
              <Table.Th>Где используется</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            <Table.Tr><Table.Td><Code>--mantine-color-blue-filled</Code></Table.Td><Table.Td>Бабл сообщения пользователя</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>--mantine-color-default-hover</Code></Table.Td><Table.Td>Фон бабла ассистента и индикатора обработки</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>--mantine-color-default-border</Code></Table.Td><Table.Td>Линия-разделитель шапки и поля ввода</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>--mantine-color-dimmed</Code></Table.Td><Table.Td>Подписи времени, иконки trail</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>--mantine-color-yellow-light</Code></Table.Td><Table.Td>Системные сообщения</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>--mantine-color-red-6</Code></Table.Td><Table.Td>Ошибки в trail</Table.Td></Table.Tr>
            <Table.Tr><Table.Td><Code>--mantine-color-body</Code></Table.Td><Table.Td>Фон страницы (наследуется)</Table.Td></Table.Tr>
          </Table.Tbody>
        </Table>

        <Title order={5} mt="md" mb={4}>Размер и layout</Title>
        <Text size="sm" mb="sm">
          Компонент занимает 100% высоты родителя (<Code>height: calc(100vh - 120px)</Code> в режиме страницы). Для embed в маленький виджет — оберни во flex-контейнер фиксированной высоты:
        </Text>
        <Code block>{`<div style={{ height: 600, display: "flex", flexDirection: "column" }}>
  <AiChat ... />
</div>`}</Code>
      </Card>
    </Stack>
  );
}
