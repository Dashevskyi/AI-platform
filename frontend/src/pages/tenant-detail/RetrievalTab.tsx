import { useState, useEffect } from 'react';
import {
  Stack, Card, Text, Group, TextInput, Button, Checkbox, NumberInput,
  Badge, Code, Alert, Loader, Box, Select,
} from '@mantine/core';
import { IconSearch, IconDatabase, IconBrain, IconMessage, IconPaperclip } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { retrievalApi, chatsApi, type RetrievalTestResponse, type RetrievalSourceResult } from '../../shared/api/endpoints';

const SOURCES = [
  { key: 'kb', label: 'База знаний', icon: IconDatabase, tool: 'search_kb' },
  { key: 'memory', label: 'Память', icon: IconBrain, tool: 'recall_memory' },
  { key: 'chat', label: 'История чата', icon: IconMessage, tool: 'recall_chat' },
  { key: 'artifacts', label: 'Артефакты', icon: IconPaperclip, tool: 'find_artifacts' },
];

const SOURCE_LABEL: Record<string, string> = Object.fromEntries(
  SOURCES.map((s) => [s.key, s.label]),
);

function SourceCard({ r }: { r: RetrievalSourceResult }) {
  const empty = r.success && (!r.output.trim() || /ничего не найдено|не найдено|\(пуст/i.test(r.output));
  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" mb={6} wrap="nowrap">
        <Group gap="xs">
          <Text fw={600} size="sm">{SOURCE_LABEL[r.source] || r.source}</Text>
          <Code>{r.tool}</Code>
          {r.scope !== '—' && <Badge size="xs" variant="light">scope: {r.scope}</Badge>}
        </Group>
        <Group gap="xs">
          {!r.success && <Badge size="xs" color="red">ошибка</Badge>}
          {empty && <Badge size="xs" color="gray">пусто</Badge>}
          {r.success && !empty && <Badge size="xs" color="green">найдено</Badge>}
          <Text size="xs" c="dimmed">{r.latency_ms} мс</Text>
        </Group>
      </Group>
      {r.error ? (
        <Alert color="red" variant="light" p="xs">
          <Text size="xs">{r.error}</Text>
        </Alert>
      ) : (
        <Box
          component="pre"
          style={{
            margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            fontSize: 12, fontFamily: 'monospace', maxHeight: 360, overflow: 'auto',
            color: empty ? 'var(--mantine-color-dimmed)' : undefined,
          }}
        >
          {r.output.trim() || '(пустой ответ инструмента)'}
        </Box>
      )}
    </Card>
  );
}

export function RetrievalTab({ tenantId }: { tenantId: string }) {
  const [query, setQuery] = useState('');
  const [chatId, setChatId] = useState<string | null>(null);
  const [limit, setLimit] = useState(5);
  const [selected, setSelected] = useState<string[]>(SOURCES.map((s) => s.key));
  const [loading, setLoading] = useState(false);
  const [resp, setResp] = useState<RetrievalTestResponse | null>(null);
  const [chatOptions, setChatOptions] = useState<{ value: string; label: string }[]>([]);

  useEffect(() => {
    let cancelled = false;
    chatsApi.list(tenantId, 1, 100)
      .then((page) => {
        if (cancelled) return;
        setChatOptions(
          (page.items || []).map((c) => ({
            value: c.id,
            label: `${c.title || '(без названия)'} · ${new Date(c.created_at).toLocaleDateString()}`,
          })),
        );
      })
      .catch(() => { /* selector stays empty — query still works tenant-wide */ });
    return () => { cancelled = true; };
  }, [tenantId]);

  const toggle = (key: string) =>
    setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));

  async function run() {
    if (!query.trim() || selected.length === 0) return;
    setLoading(true);
    try {
      setResp(await retrievalApi.test(tenantId, query.trim(), selected, chatId || null, limit));
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      notifications.show({
        title: 'Поиск',
        message: typeof detail === 'string' ? detail : (e as Error).message || 'Ошибка',
        color: 'red',
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <Stack gap="md">
      <Card withBorder padding="md">
        <Text fw={600} mb={4}>Диагностика семантического поиска</Text>
        <Text size="sm" c="dimmed" mb="md">
          Прогон запроса через те же встроенные инструменты, которыми пользуется модель
          (<Code>search_kb</Code>, <Code>recall_memory</Code>, <Code>recall_chat</Code>,
          {' '}<Code>find_artifacts</Code>). Показывает дословно то, что инструмент вернул бы LLM —
          удобно проверять, находит ли индекс нужное по конкретной формулировке.
        </Text>

        <TextInput
          label="Запрос"
          placeholder="например: какой роутер у клиента на Косарева"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void run(); }}
          mb="sm"
        />

        <Group align="flex-end" gap="md" wrap="wrap" mb="sm">
          <Select
            label={<Text size="sm">Чат (опц.)</Text>}
            description="scope=chat для памяти/истории/артефактов; пусто → весь тенант"
            placeholder="Весь тенант"
            data={chatOptions}
            value={chatId}
            onChange={setChatId}
            clearable
            searchable
            w={360}
            nothingFoundMessage="Чаты не найдены"
          />
          <NumberInput label="Лимит" value={limit} onChange={(v) => setLimit(Number(v) || 5)} min={1} max={20} w={100} />
        </Group>

        <Group gap="lg" mb="md">
          {SOURCES.map((s) => (
            <Checkbox
              key={s.key}
              label={<Group gap={4}><s.icon size={14} />{s.label}</Group>}
              checked={selected.includes(s.key)}
              onChange={() => toggle(s.key)}
            />
          ))}
        </Group>

        <Button
          leftSection={loading ? <Loader size={14} color="white" /> : <IconSearch size={16} />}
          onClick={() => void run()}
          disabled={loading || !query.trim() || selected.length === 0}
        >
          Искать
        </Button>
      </Card>

      {resp && (
        <Stack gap="sm">
          <Group gap="xs">
            <Text size="sm" c="dimmed">Модель эмбеддингов:</Text>
            <Code>{resp.embedding_model || '— не настроена —'}</Code>
            {resp.recall_cross_chat_enabled
              ? <Badge size="xs" variant="light" color="blue">cross-chat вкл.</Badge>
              : <Badge size="xs" variant="light" color="gray">cross-chat выкл. (scope=tenant → chat)</Badge>}
          </Group>
          {!resp.embedding_model && (
            <Alert color="orange" variant="light" p="xs">
              <Text size="xs">У тенанта не задана модель эмбеддингов — семантический поиск работать не будет.</Text>
            </Alert>
          )}
          {resp.results.map((r) => <SourceCard key={r.source} r={r} />)}
        </Stack>
      )}
    </Stack>
  );
}
