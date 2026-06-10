import { useState } from 'react';
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Center,
  Collapse,
  Code,
  Divider,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconBolt, IconClock, IconPercentage, IconHash, IconRefresh,
  IconChevronDown, IconChevronRight, IconSearch, IconCheck, IconAlertTriangle,
  IconEye,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { tier0Api, type Tier0AuditCandidate } from '../../shared/api/endpoints';

type Tier0TabProps = {
  tenantId: string;
};

function StatCard({
  label,
  value,
  hint,
  icon,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: React.ReactNode;
  color?: string;
}) {
  return (
    <Card withBorder padding="sm" radius="md" bg={color ? `var(--mantine-color-${color}-light)` : undefined}>
      <Group gap="xs" wrap="nowrap">
        <Center
          w={36}
          h={36}
          style={{
            borderRadius: 8,
            background: color ? `var(--mantine-color-${color}-filled)` : 'var(--mantine-color-gray-2)',
            color: color ? 'white' : 'var(--mantine-color-gray-7)',
          }}
        >
          {icon}
        </Center>
        <Stack gap={0}>
          <Text size="xs" c="dimmed" fw={500}>
            {label}
          </Text>
          <Text fw={700} size="lg" lh={1.2}>
            {value}
          </Text>
          {hint && (
            <Text size="xs" c="dimmed">
              {hint}
            </Text>
          )}
        </Stack>
      </Group>
    </Card>
  );
}

function formatLatency(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)} мс`;
  return `${(ms / 1000).toFixed(2)} с`;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('ru-RU', {
      year: '2-digit',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return iso;
  }
}

// ─── Audit section ────────────────────────────────────────────────────────────

const PRIORITY_META = {
  high:       { color: 'red',    icon: '🔴', label: 'Высокий приоритет' },
  medium:     { color: 'orange', icon: '🟡', label: 'Средний приоритет' },
  low:        { color: 'gray',   icon: '⚪', label: 'Низкий приоритет' },
  configured: { color: 'green',  icon: '✅', label: 'Tier 0 настроен' },
};

function CandidateRow({ c }: { c: Tier0AuditCandidate }) {
  const [open, setOpen] = useState(false);
  const meta = PRIORITY_META[c.priority] ?? PRIORITY_META.low;

  return (
    <Box
      p="sm"
      style={{
        borderRadius: 8,
        border: `1px solid var(--mantine-color-${meta.color}-${c.priority === 'configured' ? '3' : '4'})`,
        background: c.priority === 'configured'
          ? 'var(--mantine-color-green-light)'
          : c.priority === 'high'
            ? 'var(--mantine-color-red-light)'
            : c.priority === 'medium'
              ? 'var(--mantine-color-orange-light)'
              : undefined,
      }}
    >
      <Group justify="space-between" wrap="nowrap">
        <Group gap="xs" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ fontSize: 16, flexShrink: 0 }}>{meta.icon}</Text>
          <div style={{ minWidth: 0 }}>
            <Group gap={6} wrap="nowrap">
              <Code ff="monospace" style={{ fontSize: 12 }}>{c.tool_name}</Code>
              <Badge size="xs" color="blue" variant="light">
                {c.call_count} вызовов
              </Badge>
              {c.unique_query_count > 1 && (
                <Badge size="xs" color="gray" variant="outline">
                  {c.unique_query_count} уник. запросов
                </Badge>
              )}
            </Group>
            {!c.has_tier0 && (
              <Text size="xs" c={`${meta.color}.7`} fw={500} mt={2}>
                {meta.label} — настройте Tier 0, чтобы сэкономить ~2–5с на каждом запросе
              </Text>
            )}
            {c.has_tier0 && (
              <Text size="xs" c="green.7" mt={2}>
                Tier 0 уже настроен — всё хорошо 👍
              </Text>
            )}
          </div>
        </Group>

        {c.sample_queries.length > 0 && (
          <Button
            size="xs"
            variant="subtle"
            color="gray"
            rightSection={open ? <IconChevronDown size={11} /> : <IconChevronRight size={11} />}
            onClick={() => setOpen(v => !v)}
            style={{ flexShrink: 0 }}
          >
            Примеры
          </Button>
        )}
      </Group>

      <Collapse expanded={open}>
        <Stack gap={4} mt="xs" pl={26}>
          <Text size="xs" c="dimmed" fw={500}>Примеры реальных запросов пользователей:</Text>
          {c.sample_queries.map((q, i) => (
            <Box
              key={i}
              p="xs"
              style={{
                background: 'var(--mantine-color-body)',
                border: '1px solid var(--mantine-color-default-border)',
                borderRadius: 4,
              }}
            >
              <Text size="sm">«{q}»</Text>
            </Box>
          ))}
          {c.sample_args.length > 0 && (
            <>
              <Text size="xs" c="dimmed" fw={500} mt={4}>Аргументы tool (args_preview):</Text>
              {c.sample_args.map((a, i) => (
                <Code key={i} block style={{ fontSize: 11 }}>{a}</Code>
              ))}
            </>
          )}
          {!c.has_tier0 && (
            <Alert color="blue" variant="light" p="xs" mt={4}>
              <Text size="xs">
                Перейди на вкладку <b>«Tools»</b>, найди{' '}
                <Code style={{ fontSize: 11 }}>{c.tool_name}</Code> и открой секцию
                {' '}<b>«⚡ Tier 0 template»</b>.
                Используй конструктор 🪄 чтобы настроить keyword_regex.
              </Text>
            </Alert>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}

function AuditSection({ tenantId }: { tenantId: string }) {
  const [auditDays, setAuditDays] = useState('30');
  const [enabled, setEnabled] = useState(false);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['tenants', tenantId, 'tier0', 'audit', auditDays],
    queryFn: () => tier0Api.getAudit(tenantId, parseInt(auditDays, 10), 3),
    enabled,
    staleTime: 5 * 60_000,
  });

  const unconfigured = data?.candidates.filter(c => !c.has_tier0) ?? [];
  const configured   = data?.candidates.filter(c => c.has_tier0) ?? [];

  return (
    <Card withBorder padding="md">
      <Group justify="space-between" mb="sm">
        <div>
          <Group gap="xs">
            <IconSearch size={18} />
            <Title order={5}>Аудит кандидатов для Tier 0</Title>
          </Group>
          <Text size="xs" c="dimmed" mt={2}>
            Анализирует LLM-логи: какие tools часто вызываются через LLM, но без Tier 0
          </Text>
        </div>
        <Group gap="xs">
          <Select
            size="xs"
            value={auditDays}
            onChange={v => { if (v) { setAuditDays(v); if (enabled) refetch(); } }}
            data={[
              { value: '7', label: '7 дней' },
              { value: '30', label: '30 дней' },
              { value: '60', label: '60 дней' },
              { value: '90', label: '90 дней' },
            ]}
            w={110}
          />
          <Button
            size="xs"
            variant={enabled ? 'light' : 'filled'}
            color="blue"
            loading={isLoading || isFetching}
            leftSection={enabled ? <IconRefresh size={12} /> : <IconSearch size={12} />}
            onClick={() => { setEnabled(true); if (enabled) refetch(); }}
          >
            {enabled ? 'Обновить' : 'Запустить аудит'}
          </Button>
        </Group>
      </Group>

      {!enabled && (
        <Alert color="gray" variant="light" p="sm">
          <Text size="sm" c="dimmed">
            Нажми «Запустить аудит» — анализ LLM-логов покажет какие tools можно переключить на Tier 0.
          </Text>
        </Alert>
      )}

      {enabled && (isLoading || isFetching) && !data && (
        <Center py={24}><Loader size="sm" /></Center>
      )}

      {data && (
        <Stack gap="sm">
          <Text size="xs" c="dimmed">
            Проанализировано <b>{data.total_rows_analyzed.toLocaleString('ru-RU')}</b> LLM-вызовов
            за {data.period_days} дней · мин. {data.min_calls} вызовов для включения в список
          </Text>

          {data.candidates.length === 0 && (
            <Alert color="green" variant="light">
              <Group gap="xs">
                <IconCheck size={16} />
                <Text size="sm">Все часто-используемые tools уже настроены или вызываются слишком редко.</Text>
              </Group>
            </Alert>
          )}

          {unconfigured.length > 0 && (
            <>
              <Group gap="xs">
                <IconAlertTriangle size={16} color="var(--mantine-color-orange-5)" />
                <Text size="sm" fw={600}>Не настроены ({unconfigured.length})</Text>
              </Group>
              <Stack gap={6}>
                {unconfigured.map(c => <CandidateRow key={c.tool_name} c={c} />)}
              </Stack>
            </>
          )}

          {configured.length > 0 && (
            <>
              <Divider label={`✅ Уже с Tier 0 (${configured.length})`} labelPosition="left" mt="xs" />
              <Stack gap={6}>
                {configured.map(c => <CandidateRow key={c.tool_name} c={c} />)}
              </Stack>
            </>
          )}
        </Stack>
      )}
    </Card>
  );
}

export function Tier0Tab({ tenantId }: Tier0TabProps) {
  const [days, setDays] = useState('7');
  const [viewOutput, setViewOutput] = useState<{ query: string; output: string; tool: string | null } | null>(null);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['tenants', tenantId, 'tier0', 'stats', days],
    queryFn: () => tier0Api.getStats(tenantId, parseInt(days, 10), 30),
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <Center py={48}>
        <Loader />
      </Center>
    );
  }

  if (!data) {
    return (
      <Alert color="red">Не удалось загрузить статистику Tier 0.</Alert>
    );
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="sm">
          <IconBolt size={24} color="var(--mantine-color-yellow-6)" />
          <Title order={3}>Tier 0 — детерминистический шорткат</Title>
          {data.enabled ? (
            <Badge color="green" variant="filled">включён</Badge>
          ) : (
            <Badge color="gray" variant="outline">выключен</Badge>
          )}
        </Group>
        <Group gap="xs">
          <Select
            size="xs"
            value={days}
            onChange={(v) => v && setDays(v)}
            data={[
              { value: '1', label: 'За сутки' },
              { value: '7', label: 'За неделю' },
              { value: '30', label: 'За 30 дней' },
              { value: '90', label: 'За 90 дней' },
            ]}
            w={150}
          />
          <Tooltip label="Обновить (auto каждые 30с)">
            <IconRefresh
              size={18}
              style={{ cursor: 'pointer', opacity: isFetching ? 0.4 : 1 }}
              onClick={() => refetch()}
            />
          </Tooltip>
        </Group>
      </Group>

      {!data.enabled && (
        <Alert color="yellow" title="Tier 0 выключен для тенанта">
          Включи в <b>«Настройки оболочки»</b> → секция «⚡ Tier 0 routing».
          После этого нужно сконфигурировать хотя бы один tool с `tier0_template` в его config_json.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
        <StatCard
          label="Hit rate"
          value={`${data.hit_rate_pct.toFixed(1)}%`}
          hint={`${data.tier0_hits} из ${data.total_assistant_messages} ответов`}
          icon={<IconPercentage size={18} />}
          color={data.hit_rate_pct >= 30 ? 'green' : data.hit_rate_pct >= 10 ? 'yellow' : 'gray'}
        />
        <StatCard
          label="Tier 0 hits"
          value={data.tier0_hits.toLocaleString('ru-RU')}
          hint="ответов без LLM"
          icon={<IconHash size={18} />}
          color="yellow"
        />
        <StatCard
          label="Avg latency"
          value={formatLatency(data.avg_latency_ms)}
          hint="среднее по hits"
          icon={<IconClock size={18} />}
          color="blue"
        />
        <StatCard
          label="Пороги"
          value={`≥${data.min_tool_score.toFixed(2)} / Δ${data.max_score_gap.toFixed(2)}`}
          hint="min score / min gap"
          icon={<IconBolt size={18} />}
        />
      </SimpleGrid>

      <Card withBorder padding="md">
        <Title order={5} mb="xs">
          По tools
        </Title>
        {data.by_tool.length === 0 ? (
          <Text size="sm" c="dimmed">Пока нет hits за выбранный период.</Text>
        ) : (
          <Table verticalSpacing="xs" highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Tool</Table.Th>
                <Table.Th style={{ textAlign: 'right' }}>Hits</Table.Th>
                <Table.Th style={{ textAlign: 'right' }}>Avg latency</Table.Th>
                <Table.Th style={{ textAlign: 'right' }}>% от всех hits</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.by_tool.map((row) => (
                <Table.Tr key={row.tool}>
                  <Table.Td>
                    <Badge color="yellow" variant="light" leftSection="⚡">
                      {row.tool}
                    </Badge>
                  </Table.Td>
                  <Table.Td style={{ textAlign: 'right' }}>{row.count}</Table.Td>
                  <Table.Td style={{ textAlign: 'right' }}>{formatLatency(row.avg_ms)}</Table.Td>
                  <Table.Td style={{ textAlign: 'right' }}>
                    {data.tier0_hits ? ((100 * row.count) / data.tier0_hits).toFixed(1) : '0'}%
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <AuditSection tenantId={tenantId} />

      {/* Modal: rendered output viewer */}
      <Modal
        opened={!!viewOutput}
        onClose={() => setViewOutput(null)}
        title={
          <Group gap="xs">
            <Badge color="yellow" variant="light">{viewOutput?.tool ?? '—'}</Badge>
            <Text size="sm" c="dimmed" style={{ wordBreak: 'break-all' }}>
              «{viewOutput?.query}»
            </Text>
          </Group>
        }
        size="lg"
      >
        <Box
          p="sm"
          style={{
            background: 'var(--mantine-color-default-hover)',
            borderRadius: 6,
            whiteSpace: 'pre-wrap',
            fontFamily: 'inherit',
            fontSize: 14,
            lineHeight: 1.6,
            maxHeight: 500,
            overflowY: 'auto',
          }}
        >
          {viewOutput?.output || <Text c="dimmed" fs="italic">пустой ответ</Text>}
        </Box>
      </Modal>

      <Card withBorder padding="md">
        <Title order={5} mb="xs">
          Последние hits
        </Title>
        {data.recent_hits.length === 0 ? (
          <Text size="sm" c="dimmed">Пока нет hits за выбранный период.</Text>
        ) : (
          <ScrollArea.Autosize mah={520}>
            <Table verticalSpacing="xs" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Время</Table.Th>
                  <Table.Th>Tool</Table.Th>
                  <Table.Th>Запрос пользователя</Table.Th>
                  <Table.Th>Entities</Table.Th>
                  <Table.Th style={{ textAlign: 'right' }}>Conf</Table.Th>
                  <Table.Th style={{ textAlign: 'right' }}>Latency</Table.Th>
                  <Table.Th w={36} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.recent_hits.map((hit) => (
                  <Table.Tr key={hit.message_id}>
                    <Table.Td>
                      <Text size="xs" c="dimmed" ff="monospace">
                        {formatTimestamp(hit.ts)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge size="sm" color="yellow" variant="light">
                        {hit.tool || '—'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{hit.user_query || '—'}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        {hit.entities
                          ? Object.entries(hit.entities)
                              .filter(([, v]) => Array.isArray(v) && v.length > 0)
                              .map(([k, v]) => (
                                <Badge key={k} size="xs" color="blue" variant="light">
                                  {k}: {(v as string[]).join(', ')}
                                </Badge>
                              ))
                          : '—'}
                      </Group>
                    </Table.Td>
                    <Table.Td style={{ textAlign: 'right' }}>
                      {hit.confidence != null ? hit.confidence.toFixed(3) : '—'}
                    </Table.Td>
                    <Table.Td style={{ textAlign: 'right' }}>{formatLatency(hit.latency_ms)}</Table.Td>
                    <Table.Td>
                      <Tooltip label="Посмотреть ответ Tier 0" withArrow>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          color="blue"
                          disabled={!hit.rendered_output}
                          onClick={() => setViewOutput({
                            query: hit.user_query,
                            output: hit.rendered_output,
                            tool: hit.tool,
                          })}
                        >
                          <IconEye size={14} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea.Autosize>
        )}
      </Card>
    </Stack>
  );
}
