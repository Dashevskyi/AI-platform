import { useEffect, useState, type ReactNode } from 'react';
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Code,
  Divider,
  Fieldset,
  Group,
  Loader,
  Modal,
  NumberInput,
  Pagination,
  PasswordInput,
  ScrollArea,
  Select,
  SimpleGrid,
  Slider,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  TextInput,
  Textarea,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertCircle,
  IconArrowBackUp,
  IconDeviceFloppy,
  IconHelpCircle,
  IconHistory,
  IconMicrophone,
  IconPlugConnected,
  IconRefresh,
  IconRobot,
  IconVolume,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { shellApi } from '../../shared/api/endpoints';
import type { ShellConfigUpdate, ShellVersionDetail } from '../../shared/api/types';

type ShellSettingsTabProps = {
  tenantId: string;
};

function Hint({ children, hint }: { children: ReactNode; hint: ReactNode }) {
  return (
    <Group gap={4} wrap="nowrap" align="center">
      <Text component="span" size="sm" fw={500}>{children}</Text>
      <Tooltip label={hint} multiline w={360} withArrow position="right" openDelay={150}>
        <ActionIcon size="xs" variant="subtle" color="gray" tabIndex={-1} aria-label="Подсказка">
          <IconHelpCircle size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

// ── STT vocab source sub-component ──────────────────────────────────────────
type VocabResult = { terms_count: number; sample: string[]; cached_at: number };

function STTVocabSection({
  tenantId,
  form,
  config,
  updateField,
}: {
  tenantId: string;
  form: ShellConfigUpdate;
  config: import('../../shared/api/types').ShellConfig | undefined;
  updateField: <K extends keyof ShellConfigUpdate>(key: K, value: ShellConfigUpdate[K]) => void;
}) {
  const [vocabDsn, setVocabDsn] = useState('');
  const [vocabResult, setVocabResult] = useState<VocabResult | null>(null);

  const srcType = (form.stt_vocab_source as Record<string, unknown> | undefined)?.type as string | undefined;
  const srcQuery = (form.stt_vocab_source as Record<string, unknown> | undefined)?.query as string | undefined;

  const rebuildMutation = useMutation({
    mutationFn: () => shellApi.rebuildSttVocab(tenantId),
    onSuccess: (data) => {
      setVocabResult(data);
      notifications.show({
        title: 'Словарь загружен',
        message: `${data.terms_count} терминов`,
        color: 'green',
      });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Ошибка загрузки';
      notifications.show({ title: 'Ошибка', message: msg, color: 'red' });
    },
  });

  const updateSource = (patch: Record<string, unknown>) => {
    const current = (form.stt_vocab_source as Record<string, unknown>) ?? {};
    updateField('stt_vocab_source', { ...current, ...patch });
  };

  return (
    <Stack gap="md">
      <Fieldset
        legend={<Group gap={6}><Text fw={500}>Параметры Whisper</Text><Text size="xs" c="dimmed">— декодер + beam search</Text></Group>}
        variant="filled"
      >
        <Stack gap="sm">
          <Textarea
            label={
              <Hint hint="Затравочный текст для Whisper-декодера (~200 токенов). Модель «видит» его как начало транскрипта — вписывай ключевые ISP-термины: GPON, VLAN, свич. Не надо улиц — их лучше настроить через источник словаря ниже.">
                Initial prompt (затравка декодера)
              </Hint>
            }
            placeholder="свич, свиче, свичі, VLAN, GPON, ONT, OLT, сплиттер, абонент"
            value={form.stt_initial_prompt ?? ''}
            onChange={(e) => updateField('stt_initial_prompt', e.currentTarget.value || undefined)}
            autosize minRows={2} maxRows={4}
          />
          <TextInput
            label={
              <Hint hint="Пробел-разделённые слова, вероятность которых усиливается при beam search (параметр hotwords faster-whisper). Обычно дублирует ключевые термины из initial_prompt.">
                Hotwords (beam search boost)
              </Hint>
            }
            placeholder="свич свиче VLAN GPON ONT"
            value={form.stt_hotwords ?? ''}
            onChange={(e) => updateField('stt_hotwords', e.currentTarget.value || undefined)}
          />
        </Stack>
      </Fieldset>

      <Fieldset
        legend={<Group gap={6}><Text fw={500}>Источник словаря</Text><Text size="xs" c="dimmed">— пост-обработка транскрипта</Text></Group>}
        variant="filled"
      >
        <Stack gap="sm">
          {/* Vocab source type */}
          <Select
            label={
              <Hint hint="Откуда брать список терминов для fuzzy-коррекции транскрипта после Whisper. sql — прямой запрос к MySQL/Postgres. http — GET JSON endpoint. Пусто — пост-обработка отключена.">
                Тип источника
              </Hint>
            }
            data={[
              { value: '', label: 'Отключено' },
              { value: 'sql', label: 'SQL (MySQL / Postgres)' },
              { value: 'http', label: 'HTTP JSON endpoint' },
            ]}
            value={srcType ?? ''}
            onChange={(val) => {
              if (!val) {
                updateField('stt_vocab_source', undefined);
              } else {
                updateSource({ type: val });
              }
            }}
            allowDeselect={false}
            w={260}
          />

          {srcType === 'sql' && (
            <Stack gap="xs">
              <PasswordInput
                label={
                  <Hint hint="DSN подключения к БД. Примеры: mysql://root:pass@172.10.100.13/billing  или  postgresql://user:pass@host/db. Шифруется при сохранении. Текущее значение: показывается замаскированным.">
                    Connection string (DSN)
                  </Hint>
                }
                placeholder="mysql://user:pass@host/dbname"
                description={config?.stt_vocab_source_dsn_masked
                  ? `Сохранено: ${config.stt_vocab_source_dsn_masked}`
                  : 'Не задано'}
                value={vocabDsn}
                onChange={(e) => {
                  setVocabDsn(e.currentTarget.value);
                  if (e.currentTarget.value) {
                    updateField('stt_vocab_source_dsn', e.currentTarget.value);
                  }
                }}
              />
              <Textarea
                label={
                  <Hint hint="SQL-запрос, возвращающий один столбец строк. Каждая строка — один термин словаря. Примеры: улицы, фамилии, названия тарифов.">
                    SQL-запрос
                  </Hint>
                }
                placeholder={'SELECT DISTINCT address_street\nFROM subscribers\nWHERE address_street != \'\'\nORDER BY address_street'}
                value={srcQuery ?? ''}
                onChange={(e) => updateSource({ query: e.currentTarget.value })}
                autosize minRows={3} maxRows={8}
                styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
              />
            </Stack>
          )}

          {srcType === 'http' && (
            <Stack gap="xs">
              <TextInput
                label={<Hint hint="URL, возвращающий JSON. Запрос GET без авторизации.">URL</Hint>}
                placeholder="https://api.example.com/streets"
                value={((form.stt_vocab_source as Record<string, unknown>)?.url as string) ?? ''}
                onChange={(e) => updateSource({ url: e.currentTarget.value })}
              />
              <TextInput
                label={<Hint hint='Dot-path для извлечения массива из JSON. Пример: ".data.streets" или ".items". Оставьте пустым если ответ — массив строк напрямую.'>JSON path</Hint>}
                placeholder=".streets"
                value={((form.stt_vocab_source as Record<string, unknown>)?.jq as string) ?? ''}
                onChange={(e) => updateSource({ jq: e.currentTarget.value })}
              />
            </Stack>
          )}

          {srcType && (
            <Group align="flex-end" gap="sm">
              <NumberInput
                label={
                  <Hint hint="Минимальный процент схожести (0-100) для замены слова. 88 — консервативно (избегает ложных замен). Снизь до 80 если пропускает очевидные опечатки, подними до 92 если заменяет лишнее.">
                    Fuzzy threshold
                  </Hint>
                }
                min={60} max={100} step={1}
                value={form.stt_fuzzy_threshold ?? 88}
                onChange={(v) => updateField('stt_fuzzy_threshold', typeof v === 'number' ? v : 88)}
                w={160}
              />
              <Button
                variant="light"
                leftSection={<IconRefresh size={14} />}
                loading={rebuildMutation.isPending}
                onClick={() => rebuildMutation.mutate()}
              >
                Загрузить / обновить словарь
              </Button>
            </Group>
          )}

          {/* Vocab test result */}
          {vocabResult && (
            <Alert color="green" variant="light" py="xs">
              <Group gap="xs" mb={4}>
                <Badge color="green" variant="filled" size="sm">{vocabResult.terms_count} терминов</Badge>
                <Text size="xs" c="dimmed">
                  Кэш: {new Date(vocabResult.cached_at * 1000).toLocaleTimeString()}
                </Text>
              </Group>
              <Text size="xs" c="dimmed" mb={4}>Первые 20:</Text>
              <ScrollArea h={60}>
                <Code block style={{ fontSize: 11, lineHeight: 1.4 }}>
                  {vocabResult.sample.join(', ')}
                </Code>
              </ScrollArea>
            </Alert>
          )}
        </Stack>
      </Fieldset>
    </Stack>
  );
}

// ── TTS configuration sub-component ─────────────────────────────────────────
function TTSSection({
  form,
  config,
  updateField,
  tenantId,
}: {
  form: ShellConfigUpdate;
  config: import('../../shared/api/types').ShellConfig | undefined;
  updateField: <K extends keyof ShellConfigUpdate>(key: K, value: ShellConfigUpdate[K]) => void;
  tenantId: string;
}) {
  const [ttsApiKey, setTtsApiKey] = useState('');
  const provider = form.tts_provider ?? 'system';

  return (
    <Stack gap="md">
      <Alert icon={<IconAlertCircle size={14} />} color="blue" variant="light" py={6}>
        Выбери провайдера TTS. <strong>Системный</strong> — использует настройки платформы из <code>.env</code>.
        <strong> ElevenLabs</strong> — облачный, высокое качество (~300 ms).
      </Alert>

      <Fieldset legend="Провайдер голоса" variant="filled">
        <Stack gap="sm">
          <Select
            label={
              <Hint hint="system — берёт настройки из глобального .env сервера (ElevenLabs или Fish Speech). elevenlabs — используй свой API-ключ ElevenLabs. fish_speech — локальный Fish Speech сервер.">
                Провайдер TTS
              </Hint>
            }
            data={[
              { value: 'system', label: '🖥 Системный (по умолчанию платформы)' },
              { value: 'silero', label: '⚡ Silero v5 (локальный, MIT, быстрый)' },
              { value: 'elevenlabs', label: '☁️ ElevenLabs (свой ключ)' },
            ]}
            value={provider}
            onChange={(val) => updateField('tts_provider', val || 'system')}
            allowDeselect={false}
            w={340}
          />

          {provider === 'system' && (
            <Alert color="gray" variant="light" py={6}>
              Используются системные настройки платформы. Если в <code>.env</code> установлен <code>ELEVENLABS_API_KEY</code> — работает ElevenLabs, иначе — Silero v5 (локальный, MIT, быстрый).
            </Alert>
          )}
        </Stack>
      </Fieldset>

      {provider === 'elevenlabs' && (
        <Fieldset legend="ElevenLabs" variant="filled">
          <Stack gap="sm">
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
              <PasswordInput
                label={
                  <Hint hint="API-ключ ElevenLabs. Шифруется при сохранении. Найти: elevenlabs.io → My Account → API Key.">
                    API Key
                  </Hint>
                }
                placeholder="sk_..."
                description={config?.tts_api_key_masked
                  ? `Сохранено: ${config.tts_api_key_masked}`
                  : 'Не задано'}
                value={ttsApiKey}
                onChange={(e) => {
                  setTtsApiKey(e.currentTarget.value);
                  if (e.currentTarget.value) {
                    updateField('tts_api_key', e.currentTarget.value);
                  }
                }}
              />
              <TextInput
                label={
                  <Hint hint="ID голоса ElevenLabs. Найти: elevenlabs.io → Voice Library. Пример: 2JdEiiOR5pv532Ssmi90.">
                    Voice ID
                  </Hint>
                }
                placeholder="2JdEiiOR5pv532Ssmi90"
                value={form.tts_voice_id ?? ''}
                onChange={(e) => updateField('tts_voice_id', e.currentTarget.value || undefined)}
              />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
              <TextInput
                label={
                  <Hint hint="Модель ElevenLabs. Рекомендуется eleven_turbo_v2_5 (низкая задержка, хорошее качество ru/uk). Альтернативы: eleven_multilingual_v2, eleven_flash_v2_5.">
                    Модель
                  </Hint>
                }
                placeholder="eleven_turbo_v2_5"
                value={form.tts_model ?? ''}
                onChange={(e) => updateField('tts_model', e.currentTarget.value || undefined)}
              />
              <NumberInput
                label={
                  <Hint hint="Скорость речи. 1.0 = нормальная. Диапазон 0.5–2.0. ElevenLabs поддерживает через voice_settings.speed (где доступно).">
                    Скорость речи
                  </Hint>
                }
                placeholder="1.0"
                min={0.5} max={2.0} step={0.1} decimalScale={1}
                value={form.tts_speed ?? undefined}
                onChange={(v) => updateField('tts_speed', typeof v === 'number' ? v : undefined)}
              />
            </SimpleGrid>
          </Stack>
        </Fieldset>
      )}

      {provider === 'silero' && (
        <Fieldset legend="Silero TTS v5 (MIT)" variant="filled">
          <Stack gap="sm">
            <Alert color="green" variant="light" py={6}>
              Silero v5 cis_base — локальный GPU-синтез, лицензия MIT (коммерческое использование разрешено).
              Очень быстро (~30–130 мс), 48 кГц. 60 голосов: <strong>ru</strong> (ru_saida, ru_alfia, ru_ekaterina, ru_dmitriy…) и{' '}
              <strong>ua</strong> (ukr_roman, ukr_igor). Язык определяется автоматически по тексту.
            </Alert>
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
              <TextInput
                label={
                  <Hint hint="Базовый URL Silero сервера. Пусто — используется системный адрес из .env (SILERO_TTS_URL). Пример: http://172.10.100.9:8006">
                    URL сервера (необязательно)
                  </Hint>
                }
                placeholder="http://172.10.100.9:8006"
                value={form.tts_fish_url ?? ''}
                onChange={(e) => updateField('tts_fish_url', e.currentTarget.value || undefined)}
              />
              <TextInput
                label={
                  <Hint hint="Имя диктора. Для ru: ru_saida, ru_alfia, ru_ekaterina, ru_dmitriy и др. Для ua: ukr_roman, ukr_igor. Пусто = системный default (ru_saida/ukr_roman). Полный список: GET /speakers на сервере TTS.">
                    Голос (speaker)
                  </Hint>
                }
                placeholder="ru_saida"
                value={form.tts_voice_id ?? ''}
                onChange={(e) => updateField('tts_voice_id', e.currentTarget.value || undefined)}
              />
              <NumberInput
                label={
                  <Hint hint="Скорость речи через SSML prosody. 1.0 = нормальная, 1.15–1.3 — бодрее (рекомендуется для длинных ответов), 0.9 — медленнее. Диапазон 0.5–2.0.">
                    Скорость речи
                  </Hint>
                }
                placeholder="1.0"
                min={0.5} max={2.0} step={0.05} decimalScale={2}
                value={form.tts_speed ?? undefined}
                onChange={(v) => updateField('tts_speed', typeof v === 'number' ? v : undefined)}
              />
              <Select
                label={
                  <Hint hint="Тон голоса через SSML prosody pitch. medium — как в модели, high/x-high — выше (звонче), low/x-low — ниже (солиднее).">
                    Тон голоса
                  </Hint>
                }
                data={[
                  { value: '', label: 'По умолчанию (medium)' },
                  { value: 'x-low', label: 'Очень низкий' },
                  { value: 'low', label: 'Низкий' },
                  { value: 'high', label: 'Высокий' },
                  { value: 'x-high', label: 'Очень высокий' },
                ]}
                value={form.tts_pitch ?? ''}
                onChange={(v) => updateField('tts_pitch', v || undefined)}
              />
            </SimpleGrid>
          </Stack>
        </Fieldset>
      )}


      <TTSTestPlayer tenantId={tenantId} />
    </Stack>
  );
}

// ── TTS test player: synthesize via the tenant's SAVED settings ─────────────
function TTSTestPlayer({ tenantId }: { tenantId: string }) {
  const [text, setText] = useState(
    'Здравствуйте! Ваш баланс сто двадцать гривен. Чем могу помочь сегодня?'
  );
  const [loading, setLoading] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function synth() {
    if (!text.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      const token = localStorage.getItem('auth_token') || '';
      const resp = await fetch(`/api/admin/tenants/${tenantId}/voice/tts`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ text: text.trim() }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      if (blob.size < 100) throw new Error('Пустой ответ TTS — проверьте настройки провайдера');
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      const url = URL.createObjectURL(blob);
      setAudioUrl(url);
      // autoplay
      new Audio(url).play().catch(() => {/* user can press play manually */});
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Fieldset legend={<Group gap={6}><Text fw={500}>🔊 Тест голоса</Text><Text size="xs" c="dimmed">— использует сохранённые настройки</Text></Group>} variant="filled">
      <Stack gap="xs">
        <Alert color="blue" variant="light" py={4}>
          <Text size="xs">Синтез идёт по <b>сохранённым</b> настройкам тенанта — измените параметры, нажмите «Сохранить изменения» внизу, затем тестируйте.</Text>
        </Alert>
        <Group align="flex-end" gap="xs" wrap="nowrap">
          <Textarea
            label="Текст для озвучки (ru или ua — язык определяется автоматически)"
            autosize minRows={1} maxRows={5}
            style={{ flex: 1 }}
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
          />
          <Button
            leftSection={<IconVolume size={16} />}
            loading={loading}
            disabled={!text.trim()}
            onClick={synth}
          >
            Прослушать
          </Button>
        </Group>
        {err && <Text size="xs" c="red">{err}</Text>}
        {audioUrl && (
          // eslint-disable-next-line jsx-a11y/media-has-caption
          <audio controls src={audioUrl} style={{ width: '100%', height: 36 }} />
        )}
      </Stack>
    </Fieldset>
  );
}

// ── Version history sub-component ───────────────────────────────────────────
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return '∅';
  if (typeof v === 'boolean') return v ? 'вкл' : 'выкл';
  if (typeof v === 'object') return JSON.stringify(v);
  const s = String(v);
  return s.length > 200 ? s.slice(0, 200) + '…' : s;
}

function VersionDiff({ detail }: { detail: ShellVersionDetail }) {
  const prev = (detail.previous_payload ?? {}) as Record<string, unknown>;
  const next = detail.new_payload as Record<string, unknown>;
  const readonly = new Set(['id', 'tenant_id', 'created_at', 'updated_at']);
  const fields = [...new Set([...Object.keys(prev), ...Object.keys(next)])]
    .filter((k) => !readonly.has(k) && JSON.stringify(prev[k]) !== JSON.stringify(next[k]))
    .sort();

  if (fields.length === 0) {
    return <Text size="sm" c="dimmed">Без изменений полей конфигурации (служебная запись).</Text>;
  }
  return (
    <Table withTableBorder withColumnBorders verticalSpacing={6} fz="xs">
      <Table.Thead>
        <Table.Tr>
          <Table.Th style={{ width: 200 }}>Поле</Table.Th>
          <Table.Th>Было</Table.Th>
          <Table.Th>Стало</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {fields.map((f) => (
          <Table.Tr key={f}>
            <Table.Td><Code fz="xs">{f}</Code></Table.Td>
            <Table.Td><Text size="xs" c="red.7" style={{ wordBreak: 'break-word' }}>{fmtVal(prev[f])}</Text></Table.Td>
            <Table.Td><Text size="xs" c="green.7" style={{ wordBreak: 'break-word' }}>{fmtVal(next[f])}</Text></Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function VersionsSection({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<string | null>(null);
  const pageSize = 20;

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'shell', 'versions', page],
    queryFn: () => shellApi.listVersions(tenantId, page, pageSize),
  });

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'shell', 'version', openId],
    queryFn: () => shellApi.getVersion(tenantId, openId as string),
    enabled: !!openId,
  });

  const restoreMutation = useMutation({
    mutationFn: (versionId: string) => shellApi.restoreVersion(tenantId, versionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'shell'] });
      setOpenId(null);
      notifications.show({ title: 'Восстановлено', message: 'Конфигурация откатана к выбранной версии', color: 'green' });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Не удалось восстановить';
      notifications.show({ title: 'Ошибка', message: msg, color: 'red' });
    },
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total_count / pageSize)) : 1;

  return (
    <Stack gap="md">
      <Alert icon={<IconHistory size={14} />} color="blue" variant="light" py={6}>
        Каждое сохранение настроек оболочки фиксируется как версия. Открой запись, чтобы увидеть diff,
        и при необходимости откати конфигурацию — откат сам записывается новой версией (обратимо).
      </Alert>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data || data.items.length === 0 ? (
        <Text size="sm" c="dimmed">История пуста.</Text>
      ) : (
        <>
          <Table highlightOnHover verticalSpacing="xs" fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th style={{ width: 170 }}>Дата</Table.Th>
                <Table.Th style={{ width: 140 }}>Автор</Table.Th>
                <Table.Th>Изменённые поля</Table.Th>
                <Table.Th style={{ width: 90 }} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((v) => (
                <Table.Tr
                  key={v.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setOpenId(v.id)}
                >
                  <Table.Td>{new Date(v.changed_at).toLocaleString()}</Table.Td>
                  <Table.Td>{v.changed_by ?? <Text span c="dimmed" size="xs">система</Text>}</Table.Td>
                  <Table.Td>
                    {v.comment ? (
                      <Text size="xs" c="dimmed">{v.comment}</Text>
                    ) : v.changed_fields.length === 0 ? (
                      <Text size="xs" c="dimmed">—</Text>
                    ) : (
                      <Group gap={4}>
                        {v.changed_fields.slice(0, 6).map((f) => (
                          <Badge key={f} variant="light" size="xs" color="gray">{f}</Badge>
                        ))}
                        {v.changed_fields.length > 6 && (
                          <Badge variant="light" size="xs" color="gray">+{v.changed_fields.length - 6}</Badge>
                        )}
                      </Group>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Button size="compact-xs" variant="subtle">Diff</Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {totalPages > 1 && (
            <Group justify="center">
              <Pagination value={page} onChange={setPage} total={totalPages} size="sm" />
            </Group>
          )}
        </>
      )}

      <Modal
        opened={!!openId}
        onClose={() => setOpenId(null)}
        title="Версия конфигурации"
        size="xl"
      >
        {detailLoading || !detail ? (
          <Center py="md"><Loader /></Center>
        ) : (
          <Stack gap="md">
            <Group gap="lg">
              <Text size="sm"><Text span fw={500}>Дата:</Text> {new Date(detail.changed_at).toLocaleString()}</Text>
              <Text size="sm"><Text span fw={500}>Автор:</Text> {detail.changed_by ?? 'система'}</Text>
            </Group>
            {detail.comment && <Text size="sm" c="dimmed">{detail.comment}</Text>}
            <ScrollArea.Autosize mah={420}>
              <VersionDiff detail={detail} />
            </ScrollArea.Autosize>
            <Divider />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setOpenId(null)}>Закрыть</Button>
              <Button
                color="orange"
                leftSection={<IconArrowBackUp size={16} />}
                loading={restoreMutation.isPending}
                onClick={() => restoreMutation.mutate(detail.id)}
              >
                Восстановить эту версию
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

export function ShellSettingsTab({ tenantId }: ShellSettingsTabProps) {
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'shell'],
    queryFn: () => shellApi.get(tenantId),
  });

  const [form, setForm] = useState<ShellConfigUpdate>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (config) {
      setForm({
        provider_type: config.provider_type,
        provider_base_url: config.provider_base_url ?? undefined,
        model_name: config.model_name,
        system_prompt: config.system_prompt ?? undefined,
        ontology_prompt: config.ontology_prompt ?? undefined,
        rules_text: config.rules_text ?? undefined,
        temperature: config.temperature,
        max_context_messages: config.max_context_messages,
        max_tokens: config.max_tokens,
        context_mode: config.context_mode,
        memory_enabled: config.memory_enabled,
        knowledge_base_enabled: config.knowledge_base_enabled,
        kb_inject_auto: config.kb_inject_auto ?? true,
        embedding_model_name: config.embedding_model_name ?? undefined,
        vision_model_name: config.vision_model_name ?? undefined,
        kb_max_chunks: config.kb_max_chunks,
        enable_thinking: config.enable_thinking || 'on',
        response_language: config.response_language || 'ru',
        debug_enabled: config.debug_enabled,
        timezone: config.timezone ?? undefined,
        tool_semantic_floor: config.tool_semantic_floor,
        tool_routing_temperature: config.tool_routing_temperature,
        lazy_tool_catalog_topk: config.lazy_tool_catalog_topk,
        max_tool_rounds: config.max_tool_rounds,
        tier0_enabled: config.tier0_enabled,
        tier0_min_tool_score: config.tier0_min_tool_score,
        tier0_max_score_gap: config.tier0_max_score_gap,
        pii_routing_enabled: config.pii_routing_enabled,
        stt_initial_prompt: config.stt_initial_prompt ?? undefined,
        stt_hotwords: config.stt_hotwords ?? undefined,
        stt_vocab_source: config.stt_vocab_source ?? undefined,
        stt_fuzzy_threshold: config.stt_fuzzy_threshold ?? 88,
        tts_provider: config.tts_provider ?? 'system',
        tts_voice_id: config.tts_voice_id ?? undefined,
        tts_model: config.tts_model ?? undefined,
        tts_speed: config.tts_speed ?? undefined,
        tts_pitch: config.tts_pitch ?? undefined,
        tts_fish_url: config.tts_fish_url ?? undefined,
      });
      setDirty(false);
    }
  }, [config]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const updateField = <K extends keyof ShellConfigUpdate>(key: K, value: ShellConfigUpdate[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => shellApi.update(tenantId, form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'shell'] });
      setDirty(false);
      notifications.show({ title: 'Сохранено', message: 'Настройки оболочки обновлены', color: 'green' });
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Не удалось сохранить';
      notifications.show({ title: 'Ошибка', message, color: 'red' });
    },
  });

  const testMutation = useMutation({
    mutationFn: () => shellApi.testConnection(tenantId),
    onSuccess: (result) => {
      notifications.show({
        title: result.success ? 'Соединение установлено' : 'Ошибка соединения',
        message: result.message,
        color: result.success ? 'green' : 'red',
      });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Тест соединения не удался', color: 'red' });
    },
  });

  if (isLoading) {
    return <Center py="md"><Loader /></Center>;
  }

  return (
    <Card withBorder padding="lg" maw={1100}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          saveMutation.mutate();
        }}
      >
        <Stack gap="md">
          {dirty && (
            <Alert icon={<IconAlertCircle size={16} />} color="yellow" variant="light" py="xs">
              У вас есть несохранённые изменения.
            </Alert>
          )}

          <Tabs defaultValue="llm">
            <Tabs.List mb="md">
              <Tabs.Tab value="llm" leftSection={<IconRobot size={16} />}>LLM</Tabs.Tab>
              <Tabs.Tab value="stt" leftSection={<IconMicrophone size={16} />}>STT</Tabs.Tab>
              <Tabs.Tab value="tts" leftSection={<IconVolume size={16} />}>TTS</Tabs.Tab>
              <Tabs.Tab value="history" leftSection={<IconHistory size={16} />}>История</Tabs.Tab>
            </Tabs.List>

            {/* ── LLM tab ──────────────────────────────────────────────────── */}
            <Tabs.Panel value="llm">
              <Stack gap="md">

                {/* ── Provider ─────────────────────────────────────────── */}
                <Fieldset legend="Провайдер" variant="filled">
                  <Stack gap="sm">
                    <SimpleGrid cols={{ base: 1, md: 3 }} spacing="sm">
                      <Select
                        label={
                          <Hint hint="Ollama — локальные модели. OpenAI Compatible — любой OpenAI-совместимый API. DeepSeek — официальное API DeepSeek.">
                            Тип провайдера
                          </Hint>
                        }
                        data={[
                          { value: 'ollama', label: 'Ollama (локальный)' },
                          { value: 'openai_compatible', label: 'OpenAI Compatible' },
                          { value: 'deepseek_compatible', label: 'DeepSeek Compatible' },
                        ]}
                        value={form.provider_type || ''}
                        onChange={(val) => updateField('provider_type', val || '')}
                      />
                      <TextInput
                        label={
                          <Hint hint="Ollama: http://localhost:11434. DeepSeek: https://api.deepseek.com.">
                            Базовый URL
                          </Hint>
                        }
                        placeholder="http://localhost:11434"
                        value={form.provider_base_url || ''}
                        onChange={(e) => updateField('provider_base_url', e.currentTarget.value)}
                      />
                      <PasswordInput
                        label={
                          <Hint hint="Ключ аутентификации у провайдера. Для локального Ollama не требуется.">
                            API ключ
                          </Hint>
                        }
                        placeholder="sk-..."
                        value={form.provider_api_key || ''}
                        onChange={(e) => updateField('provider_api_key', e.currentTarget.value)}
                      />
                    </SimpleGrid>
                    <Alert icon={<IconAlertCircle size={14} />} color="blue" variant="light" py={6}>
                      Основная модель чата выбирается во вкладке «Модель». Здесь — провайдер, промпты, лимиты, поведение оболочки.
                    </Alert>
                  </Stack>
                </Fieldset>

                {/* ── Prompts ─────────────────────────────────────────── */}
                <Fieldset legend="Промпты" variant="filled">
                  <Stack gap="sm">
                    <Textarea
                      label={
                        <Hint hint="Идентичность ассистента и общий стиль. Короткое, 1-3 предложения. Уходит в LLM первым блоком.">
                          Системный промпт — кто ассистент
                        </Hint>
                      }
                      placeholder="Ты — AI техспециалист компании X. Отвечай на языке запроса."
                      value={form.system_prompt || ''}
                      onChange={(e) => updateField('system_prompt', e.currentTarget.value)}
                      autosize
                      minRows={2}
                      maxRows={6}
                    />
                    <Textarea
                      label={
                        <Hint hint="Структура данных, термины, mapping тема→tool. Только то, что не вынести в KB/память. Второй блок промпта.">
                          Онтология / Domain knowledge
                        </Hint>
                      }
                      placeholder={'OLT — головное PON-устройство. Splitter — пассивный делитель.\n\nТема ↔ tool:\n• клиенты — search_clients'}
                      value={form.ontology_prompt || ''}
                      onChange={(e) => updateField('ontology_prompt', e.currentTarget.value)}
                      autosize
                      minRows={3}
                      maxRows={10}
                    />
                    <Textarea
                      label={
                        <Hint hint="Длина, формат, стилевые исключения. Третий блок промпта, с префиксом «Rules:».">
                          Правила формата ответов
                        </Hint>
                      }
                      placeholder="Отвечай короткими фразами по 4-5 предложений. Исключения — код, таблицы."
                      value={form.rules_text || ''}
                      onChange={(e) => updateField('rules_text', e.currentTarget.value)}
                      autosize
                      minRows={2}
                      maxRows={5}
                    />
                  </Stack>
                </Fieldset>

                {/* ── Generation ─────────────────────────────────────── */}
                <Fieldset legend="Генерация" variant="filled">
                  <Stack gap="sm">
                    <Group align="flex-end" gap="md" wrap="wrap">
                      <div style={{ flex: '1 1 320px', minWidth: 280 }}>
                        <Hint hint="Для support-ассистента температура ограничена сверху 0.7 — снижает галлюцинации и шум.">
                          Температура: {(form.temperature ?? 0.3).toFixed(2)}
                        </Hint>
                        <Slider
                          min={0}
                          max={0.7}
                          step={0.01}
                          value={Math.min(form.temperature ?? 0.3, 0.7)}
                          onChange={(val) => updateField('temperature', val)}
                          marks={[
                            { value: 0, label: '0' },
                            { value: 0.3, label: '0.3' },
                            { value: 0.7, label: '0.7' },
                          ]}
                          mt={6}
                        />
                      </div>
                      <NumberInput
                        label={<Hint hint="Сколько последних сообщений чата отправлять в LLM.">Макс. сообщений контекста</Hint>}
                        value={form.max_context_messages ?? 20}
                        onChange={(val) => updateField('max_context_messages', Number(val))}
                        min={1}
                        max={200}
                        w={200}
                      />
                      <NumberInput
                        label={<Hint hint="Максимальная длина ответа LLM в токенах.">Макс. токенов ответа</Hint>}
                        value={form.max_tokens ?? 4096}
                        onChange={(val) => updateField('max_tokens', Number(val))}
                        min={1}
                        max={128000}
                        w={200}
                      />
                    </Group>

                    <SimpleGrid cols={{ base: 1, md: 3 }} spacing="sm">
                      <Select
                        label={
                          <Hint hint="Что подмешивать из истории. summary_plus_recent — резюме + последние; recent_only — только хвост; summary_only — только резюме.">
                            Режим контекста
                          </Hint>
                        }
                        data={[
                          { value: 'recent_only', label: 'Только последние сообщения' },
                          { value: 'summary_plus_recent', label: 'Резюме + последние' },
                          { value: 'summary_only', label: 'Только резюме' },
                        ]}
                        value={form.context_mode || 'summary_plus_recent'}
                        onChange={(val) => updateField('context_mode', val || 'summary_plus_recent')}
                        allowDeselect={false}
                      />
                      <Select
                        label={
                          <Hint hint="on — всегда «думает» (точнее, медленнее). off — сразу отвечает (быстро). auto — думает только на сложных. Влияет на Qwen3 и аналоги.">
                            Reasoning (thinking)
                          </Hint>
                        }
                        data={[
                          { value: 'on', label: 'On — всегда' },
                          { value: 'off', label: 'Off — никогда' },
                          { value: 'auto', label: 'Auto — по эвристике' },
                        ]}
                        value={form.enable_thinking || 'on'}
                        onChange={(val) => updateField('enable_thinking', val || 'on')}
                        allowDeselect={false}
                      />
                      <Select
                        label={
                          <Hint hint="Жёсткая привязка языка для всех LLM-вызовов: чат, резюме, описания attachment. Без неё multilingual-модели срываются.">
                            Язык ответов
                          </Hint>
                        }
                        data={[
                          { value: 'ru', label: 'Русский' },
                          { value: 'uk', label: 'Українська' },
                          { value: 'en', label: 'English' },
                          { value: 'pl', label: 'Polski' },
                          { value: 'de', label: 'Deutsch' },
                          { value: 'es', label: 'Español' },
                          { value: 'fr', label: 'Français' },
                        ]}
                        value={form.response_language || 'ru'}
                        onChange={(val) => updateField('response_language', val || 'ru')}
                        allowDeselect={false}
                      />
                    </SimpleGrid>

                    <TextInput
                      label={
                        <Hint hint="IANA timezone для блока «текущая дата» в системном промпте. Например: Europe/Kyiv, UTC, Asia/Tokyo. Пусто — серверный TZ.">
                          Timezone (IANA)
                        </Hint>
                      }
                      placeholder="Europe/Kyiv"
                      value={form.timezone ?? ''}
                      onChange={(e) => updateField('timezone', e.currentTarget.value || undefined)}
                      w={260}
                    />
                  </Stack>
                </Fieldset>

                {/* ── Tools / Routing ────────────────────────────────── */}
                <Fieldset legend="Tools / роутинг" variant="filled">
                  <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
                    <NumberInput
                      label={
                        <Hint hint="Минимальный cosine similarity (0.0-1.0) для tool, выбранного семантикой. Ниже = выбрасывается. Default 0.5.">
                          Semantic floor
                        </Hint>
                      }
                      min={0}
                      max={1}
                      step={0.05}
                      decimalScale={2}
                      value={form.tool_semantic_floor ?? 0.5}
                      onChange={(v) => updateField('tool_semantic_floor', typeof v === 'number' ? v : 0.5)}
                    />
                    <NumberInput
                      label={
                        <Hint hint="Temperature на раундах LLM с tools в payload (выбор tool / аргументов). Ниже = детерминированнее. Default 0.3.">
                          Routing temperature
                        </Hint>
                      }
                      min={0}
                      max={2}
                      step={0.1}
                      decimalScale={2}
                      value={form.tool_routing_temperature ?? 0.3}
                      onChange={(v) => updateField('tool_routing_temperature', typeof v === 'number' ? v : 0.3)}
                    />
                    <NumberInput
                      label={
                        <Hint hint="Сколько top-K tools идёт с полной schema. Остальные — компактно (имя + 1 строка), деталь через describe_tool. Default 3. 100 чтобы выключить.">
                          Lazy catalog top-K
                        </Hint>
                      }
                      min={1}
                      max={100}
                      step={1}
                      value={form.lazy_tool_catalog_topk ?? 3}
                      onChange={(v) => updateField('lazy_tool_catalog_topk', typeof v === 'number' ? v : 3)}
                    />
                    <NumberInput
                      label={
                        <Hint hint="Максимум tool-раундов в одном запросе (защита от бесконечных циклов). Default 6. Multi-stage пайплайны могут поднять до 10-12.">
                          Max tool rounds
                        </Hint>
                      }
                      min={1}
                      max={20}
                      step={1}
                      value={form.max_tool_rounds ?? 6}
                      onChange={(v) => updateField('max_tool_rounds', typeof v === 'number' ? v : 6)}
                    />
                  </SimpleGrid>
                </Fieldset>

                {/* ── Tier 0 routing ────────────────────────────────────── */}
                <Fieldset legend={<Group gap={6}><Text fw={500}>⚡ Tier 0 routing</Text><Text size="xs" c="dimmed">— деterминистический шорткат без LLM</Text></Group>} variant="filled">
                  <Stack gap="sm">
                    <Group gap="lg">
                      <Tooltip
                        label="Когда включено: если запрос matches с уверенным tool (см. пороги ниже), pipeline вызывает tool напрямую и рендерит результат через template, минуя LLM. ~100-700ms вместо 1-3s."
                        multiline w={380} withArrow
                      >
                        <Switch
                          label="Включить Tier 0"
                          checked={form.tier0_enabled ?? false}
                          onChange={(e) => updateField('tier0_enabled', e.currentTarget.checked)}
                        />
                      </Tooltip>
                    </Group>
                    <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
                      <NumberInput
                        label={
                          <Hint hint="Минимальный boosted-score топового tool чтобы Tier 0 считал «уверенным». Ниже = больше hit rate, но и больше false positive. Рекомендую 0.70-0.80.">
                            Min tool score
                          </Hint>
                        }
                        min={0.50} max={1.00} step={0.05} decimalScale={2}
                        value={form.tier0_min_tool_score ?? 0.80}
                        onChange={(v) => updateField('tier0_min_tool_score', typeof v === 'number' ? v : 0.80)}
                      />
                      <NumberInput
                        label={
                          <Hint hint="Минимальный gap между топом и 2-м кандидатом — гарантирует что нет close competitor. Чем выше, тем строже. Default 0.15.">
                            Min gap к 2-му
                          </Hint>
                        }
                        min={0.05} max={0.50} step={0.05} decimalScale={2}
                        value={form.tier0_max_score_gap ?? 0.15}
                        onChange={(v) => updateField('tier0_max_score_gap', typeof v === 'number' ? v : 0.15)}
                      />
                    </SimpleGrid>
                  </Stack>
                </Fieldset>

                {/* ── PII routing ────────────────────────────────────── */}
                <Fieldset legend={<Group gap={6}><Text fw={500}>🛡 PII routing</Text><Text size="xs" c="dimmed">— безопасность данных</Text></Group>} variant="filled">
                  <Tooltip
                    label="При обнаружении PII в запросе (телефон / MAC / IP) auto-router НЕ будет escalate в cloud модель — остаётся на local навсегда для этого чата. Защищает персональные данные клиентов от выхода за пределы локальной сети."
                    multiline w={420} withArrow
                  >
                    <Switch
                      label="Запретить cloud при PII в запросе"
                      description="Phone / MAC / IP детектятся regex'ом. Локально всё равно ответит — просто без esc к DeepSeek/Claude."
                      checked={form.pii_routing_enabled ?? false}
                      onChange={(e) => updateField('pii_routing_enabled', e.currentTarget.checked)}
                    />
                  </Tooltip>
                </Fieldset>

                {/* ── Memory & KB ────────────────────────────────────── */}
                <Fieldset legend="Память и KB" variant="filled">
                  <Stack gap="sm">
                    <Group gap="lg">
                      <Switch
                        label="Память включена"
                        checked={form.memory_enabled ?? false}
                        onChange={(e) => updateField('memory_enabled', e.currentTarget.checked)}
                      />
                      <Switch
                        label="База знаний"
                        checked={form.knowledge_base_enabled ?? false}
                        onChange={(e) => updateField('knowledge_base_enabled', e.currentTarget.checked)}
                      />
                      <Tooltip
                        label="Сохранять полный JSON debug (grounding, tool calls, rounds) в llm_request_logs.debug. Выключи, когда не ведёшь расследование."
                        multiline
                        w={340}
                        withArrow
                      >
                        <Switch
                          label="Debug-трейс"
                          checked={form.debug_enabled ?? true}
                          onChange={(e) => updateField('debug_enabled', e.currentTarget.checked)}
                        />
                      </Tooltip>
                    </Group>

                    {form.knowledge_base_enabled && (
                      <Stack gap="sm">
                        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
                          <TextInput
                            label={<Hint hint="Модель для эмбеддингов (например, bge-m3, nomic-embed-text).">Модель эмбеддингов</Hint>}
                            placeholder="bge-m3"
                            value={form.embedding_model_name ?? ''}
                            onChange={(e) => updateField('embedding_model_name', e.currentTarget.value || undefined)}
                          />
                          <NumberInput
                            label={<Hint hint="Сколько релевантных чанков KB подмешивать в контекст (только в eager-режиме).">Макс. чанков KB</Hint>}
                            min={1}
                            max={50}
                            value={form.kb_max_chunks ?? 10}
                            onChange={(val) => updateField('kb_max_chunks', typeof val === 'number' ? val : 10)}
                            disabled={!(form.kb_inject_auto ?? true)}
                          />
                        </SimpleGrid>
                        <Tooltip
                          label="Eager (по умолчанию): топ-K чанков KB автоматически добавляются в каждый промпт — экономит +1 раунд для KB-запросов, но тратит ~1800 токенов даже там, где KB не нужен. On-demand: KB не в промпте, модель вызывает search_kb() только когда нужно — меньший промпт, быстрее для операционных запросов."
                          multiline
                          w={380}
                          withArrow
                        >
                          <Switch
                            label="KB: eager-инъекция (выключить = on-demand через tool)"
                            checked={form.kb_inject_auto ?? true}
                            onChange={(e) => updateField('kb_inject_auto', e.currentTarget.checked)}
                          />
                        </Tooltip>
                      </Stack>
                    )}

                    <TextInput
                      label={<Hint hint="Ollama-модель для описания изображений вложений. Пусто = авто: qwen2-vl > llava > moondream.">Vision-модель</Hint>}
                      placeholder="llava:13b"
                      value={form.vision_model_name ?? ''}
                      onChange={(e) => updateField('vision_model_name', e.currentTarget.value || undefined)}
                    />
                  </Stack>
                </Fieldset>

              </Stack>
            </Tabs.Panel>

            {/* ── STT tab ──────────────────────────────────────────────────── */}
            <Tabs.Panel value="stt">
              <STTVocabSection
                tenantId={tenantId}
                form={form}
                config={config}
                updateField={updateField}
              />
            </Tabs.Panel>

            {/* ── TTS tab ──────────────────────────────────────────────────── */}
            <Tabs.Panel value="tts">
              <TTSSection
                form={form}
                config={config}
                updateField={updateField}
                tenantId={tenantId}
              />
            </Tabs.Panel>

            {/* ── History tab ──────────────────────────────────────────────── */}
            <Tabs.Panel value="history">
              <VersionsSection tenantId={tenantId} />
            </Tabs.Panel>

          </Tabs>

          <Divider />

          <Group justify="space-between">
            <Button
              variant="outline"
              leftSection={<IconPlugConnected size={16} />}
              onClick={() => testMutation.mutate()}
              loading={testMutation.isPending}
            >
              Тест соединения
            </Button>
            <Button
              type="submit"
              leftSection={<IconDeviceFloppy size={16} />}
              loading={saveMutation.isPending}
              disabled={!dirty}
            >
              Сохранить изменения
            </Button>
          </Group>
        </Stack>
      </form>
    </Card>
  );
}
