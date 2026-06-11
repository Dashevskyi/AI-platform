import { useEffect, useState } from 'react';
import {
  Stack,
  Title,
  Text,
  Card,
  Grid,
  Group,
  Button,
  Table,
  TextInput,
  Textarea,
  ActionIcon,
  Alert,
  Badge,
  Loader,
  Center,
  Code,
  Select,
} from '@mantine/core';
import {
  IconVolume,
  IconPlus,
  IconTrash,
  IconDeviceFloppy,
  IconAlertCircle,
  IconAbc,
  IconLetterE,
  IconMusic,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import apiClient from '../shared/api/client';

type Rules = { abbr: Record<string, string>; pron: Record<string, string>; stress: Record<string, string> };
type Row = { k: string; v: string };

const toRows = (d: Record<string, string>): Row[] => Object.entries(d).map(([k, v]) => ({ k, v }));
const toDict = (rows: Row[]): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const { k, v } of rows) if (k.trim() && v.trim()) out[k.trim()] = v.trim();
  return out;
};

function RuleTable({
  title, icon, hint, keyLabel, valLabel, placeholderK, placeholderV, rows, setRows,
}: {
  title: string;
  icon: React.ReactNode;
  hint: string;
  keyLabel: string;
  valLabel: string;
  placeholderK: string;
  placeholderV: string;
  rows: Row[];
  setRows: (r: Row[]) => void;
}) {
  return (
    <Card withBorder padding="md">
      <Group justify="space-between" mb={4}>
        <Group gap={8}>
          {icon}
          <Text fw={600}>{title}</Text>
          <Badge variant="light" size="sm">{rows.filter((r) => r.k.trim()).length}</Badge>
        </Group>
        <Button size="compact-xs" variant="light" leftSection={<IconPlus size={12} />}
                onClick={() => setRows([...rows, { k: '', v: '' }])}>
          Добавить
        </Button>
      </Group>
      <Text size="xs" c="dimmed" mb="xs">{hint}</Text>
      {rows.length === 0 ? (
        <Text size="xs" c="dimmed">Правил нет — «Добавить».</Text>
      ) : (
        <Table verticalSpacing={4} fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{keyLabel}</Table.Th>
              <Table.Th>{valLabel}</Table.Th>
              <Table.Th style={{ width: 40 }} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row, i) => (
              <Table.Tr key={i}>
                <Table.Td>
                  <TextInput size="xs" value={row.k} placeholder={placeholderK}
                    onChange={(e) => setRows(rows.map((r, j) => j === i ? { ...r, k: e.currentTarget.value } : r))} />
                </Table.Td>
                <Table.Td>
                  <TextInput size="xs" value={row.v} placeholder={placeholderV}
                    onChange={(e) => setRows(rows.map((r, j) => j === i ? { ...r, v: e.currentTarget.value } : r))} />
                </Table.Td>
                <Table.Td>
                  <ActionIcon variant="subtle" color="red" size="sm"
                              onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                    <IconTrash size={14} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}

export function TtsLocalPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['tts-local', 'rules'],
    queryFn: async () => (await apiClient.get('/api/admin/tts-local/rules')).data as
      { custom: Rules; builtin_pron: Record<string, string> },
  });

  const [abbr, setAbbr] = useState<Row[]>([]);
  const [pron, setPron] = useState<Row[]>([]);
  const [stress, setStress] = useState<Row[]>([]);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (data) {
      setAbbr(toRows(data.custom.abbr));
      setPron(toRows(data.custom.pron));
      setStress(toRows(data.custom.stress));
      setDirty(false);
    }
  }, [data]);

  const wrap = <T,>(setter: (v: T) => void) => (v: T) => { setter(v); setDirty(true); };

  const saveMutation = useMutation({
    mutationFn: async () => (await apiClient.put('/api/admin/tts-local/rules', {
      abbr: toDict(abbr), pron: toDict(pron), stress: toDict(stress),
    })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tts-local', 'rules'] });
      setDirty(false);
      notifications.show({ title: 'Сохранено', message: 'Правила применены к локальному TTS', color: 'green' });
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e);
      notifications.show({ title: 'Ошибка', message: msg, color: 'red' });
    },
  });

  // ── Test box ──────────────────────────────────────────────────────────────
  const [testText, setTestText] = useState('Коротко: менеджер проверит претензию по заявке от 11.06.2026.');
  const [testLang, setTestLang] = useState<'ru' | 'ua'>('ru');
  const [testLoading, setTestLoading] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  async function runTest() {
    if (!testText.trim()) return;
    setTestLoading(true);
    try {
      const resp = await apiClient.post('/api/admin/tts-local/test',
        { text: testText.trim(), lang: testLang },
        { responseType: 'blob' });
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      const url = URL.createObjectURL(resp.data as Blob);
      setAudioUrl(url);
      new Audio(url).play().catch(() => { /* manual play available */ });
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e);
      notifications.show({ title: 'TTS', message: msg, color: 'red' });
    } finally {
      setTestLoading(false);
    }
  }

  if (isLoading) return <Center py="xl"><Loader /></Center>;
  if (error) {
    return (
      <Alert color="red" icon={<IconAlertCircle size={16} />}>
        Локальный TTS-сервис недоступен: {String((error as Error).message)}
      </Alert>
    );
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="center">
        <Group gap="sm">
          <IconVolume size={26} />
          <div>
            <Title order={2}>Локальный TTS — правила произношения</Title>
            <Text size="sm" c="dimmed">
              Системные правила движка Silero v5 (НЕ пер-тенант): применяются ко всем тенантам,
              использующим локальный голос. Сохраняются на сервисе и переживают перезапуск.
            </Text>
          </div>
        </Group>
        <Button leftSection={<IconDeviceFloppy size={16} />} loading={saveMutation.isPending}
                disabled={!dirty} onClick={() => saveMutation.mutate()}>
          Сохранить правила
        </Button>
      </Group>

      <Grid>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <RuleTable
            title="Сокращения" icon={<IconAbc size={18} />}
            hint="Разворачиваются перед всем остальным. Пример: «тех.» → «технический». Базовые адресные (ул., кв., буд., тел.) уже встроены в платформу."
            keyLabel="Сокращение" valLabel="Полная форма"
            placeholderK="тех." placeholderV="технический"
            rows={abbr} setRows={wrap(setAbbr)}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <RuleTable
            title="Твёрдое Э (заимствования)" icon={<IconLetterE size={18} />}
            hint="Замена основы слова до ударений: «претензи» → «претэнзи» (окончания сохраняются). Работает как префикс — короткие основы опасны (темп → сломает «температуру»)."
            keyLabel="Основа" valLabel="Как читать"
            placeholderK="детектор" placeholderV="дэтэктор"
            rows={pron} setRows={wrap(setPron)}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 4 }}>
          <RuleTable
            title="Ударения (исключения)" icon={<IconMusic size={18} />}
            hint="Перебивают автоматическую расстановку. «+» ставится ПЕРЕД ударной гласной: «к+оротко», «зам+ок». Для слов, где акцентуатор ошибается (улицы, фамилии, термины)."
            keyLabel="Слово" valLabel="С ударением (+)"
            placeholderK="коротко" placeholderV="к+оротко"
            rows={stress} setRows={wrap(setStress)}
          />
        </Grid.Col>
      </Grid>

      {data?.builtin_pron && Object.keys(data.builtin_pron).length > 0 && (
        <Text size="xs" c="dimmed">
          Встроенные Э-правила (зашиты в сервис):{' '}
          {Object.entries(data.builtin_pron).map(([k, v]) => `${k}→${v}`).join(', ')}
        </Text>
      )}

      <Card withBorder padding="md">
        <Text fw={600} mb={4}>🔊 Проверить произношение</Text>
        <Text size="xs" c="dimmed" mb="xs">
          Синтез напрямую на локальном движке (настройки тенантов не участвуют).
          Несохранённые правила в тесте НЕ действуют — сначала «Сохранить правила».
        </Text>
        <Group align="flex-end" gap="xs" wrap="nowrap">
          <Textarea style={{ flex: 1 }} autosize minRows={1} maxRows={4}
                    value={testText} onChange={(e) => setTestText(e.currentTarget.value)} />
          <Select w={110} data={[{ value: 'ru', label: 'ru' }, { value: 'ua', label: 'ua' }]}
                  value={testLang} onChange={(v) => setTestLang((v as 'ru' | 'ua') || 'ru')} allowDeselect={false} />
          <Button leftSection={<IconVolume size={16} />} loading={testLoading}
                  disabled={!testText.trim()} onClick={runTest}>
            Прослушать
          </Button>
        </Group>
        {audioUrl && (
          // eslint-disable-next-line jsx-a11y/media-has-caption
          <audio controls src={audioUrl} style={{ width: '100%', height: 36, marginTop: 8 }} />
        )}
        <Text size="xs" c="dimmed" mt={6}>
          Подсказка: разметку <Code>+</Code> можно писать прямо в тексте теста — она имеет приоритет над всеми правилами.
        </Text>
      </Card>
    </Stack>
  );
}
