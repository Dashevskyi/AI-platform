import { Badge, Card, Center, Group, Loader, SimpleGrid, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { logsApi, toolsApi } from '../../shared/api/endpoints';

function startOfTodayISO(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

function fmtLatency(ms: number | null): string {
  if (ms == null) return '—';
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} с` : `${Math.round(ms)} мс`;
}

function relativeTime(iso?: string | null): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'только что';
  if (min < 60) return `${min} мин назад`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} ч назад`;
  return `${Math.floor(h / 24)} дн назад`;
}

function Stat({ label, value, color, sub }: { label: string; value: string | number; color?: string; sub?: string }) {
  return (
    <Card withBorder padding="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={700}>{label}</Text>
      <Text size="xl" fw={700} c={color}>{value}</Text>
      {sub && <Text size="xs" c="dimmed">{sub}</Text>}
    </Card>
  );
}

function healthColor(rate: number): string {
  if (rate < 0.02) return 'green';
  if (rate < 0.05) return 'yellow';
  return 'red';
}

export function OverviewTab({ tenantId }: { tenantId: string }) {
  const today = startOfTodayISO();
  const { data: todaySum, isLoading: l1 } = useQuery({
    queryKey: ['tenants', tenantId, 'overview', 'today', today],
    queryFn: () => logsApi.summary(tenantId, { date_from: today }),
  });
  const { data: allSum, isLoading: l2 } = useQuery({
    queryKey: ['tenants', tenantId, 'overview', 'all'],
    queryFn: () => logsApi.summary(tenantId),
  });
  const { data: metrics } = useQuery({
    queryKey: ['tenants', tenantId, 'tools', 'metrics'],
    queryFn: () => toolsApi.metrics(tenantId),
    staleTime: 60_000,
  });
  const { data: latest } = useQuery({
    queryKey: ['tenants', tenantId, 'overview', 'latest'],
    queryFn: () => logsApi.list(tenantId, 1, 1),
  });
  const lastActivity = latest?.items?.[0]?.created_at;
  const topTools = (metrics || []).slice(0, 6);

  if (l1 || l2) return <Center py="xl"><Loader /></Center>;

  return (
    <Stack gap="lg">
      <div>
        <Text fw={600} mb="xs">Сегодня</Text>
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }}>
          <Stat label="Запросов" value={(todaySum?.total ?? 0).toLocaleString('ru-RU')} />
          <Stat
            label="Ошибки"
            value={`${todaySum?.errors ?? 0} · ${((todaySum?.error_rate ?? 0) * 100).toFixed(1)}%`}
            color={todaySum?.errors ? healthColor(todaySum.error_rate) : undefined}
          />
          <Stat label="Avg задержка" value={fmtLatency(todaySum?.avg_latency_ms ?? null)} />
          <Stat label="Tier 0" value={`${((todaySum?.tier0_share ?? 0) * 100).toFixed(0)}%`} sub="без LLM" />
          <Stat label="Стоимость" value={`$${(todaySum?.estimated_cost ?? 0).toFixed(4)}`} />
        </SimpleGrid>
      </div>

      <div>
        <Group justify="space-between" mb="xs">
          <Text fw={600}>За всё время</Text>
          <Text size="sm" c="dimmed">Последняя активность: {relativeTime(lastActivity)}</Text>
        </Group>
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }}>
          <Stat label="Запросов" value={(allSum?.total ?? 0).toLocaleString('ru-RU')} />
          <Stat
            label="Доля ошибок"
            value={`${((allSum?.error_rate ?? 0) * 100).toFixed(1)}%`}
            color={allSum ? healthColor(allSum.error_rate) : undefined}
          />
          <Stat label="Avg задержка" value={fmtLatency(allSum?.avg_latency_ms ?? null)} />
          <Stat
            label="Avg токенов"
            value={allSum?.avg_total_tokens != null ? Math.round(allSum.avg_total_tokens).toLocaleString('ru-RU') : '—'}
          />
          <Stat label="Tier 0" value={`${((allSum?.tier0_share ?? 0) * 100).toFixed(0)}%`} />
        </SimpleGrid>
      </div>

      <Card withBorder padding="md">
        <Text fw={600} mb="sm">Топ инструментов по вызовам</Text>
        {topTools.length ? (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Инструмент</Table.Th>
                <Table.Th>Вызовов</Table.Th>
                <Table.Th>Успех</Table.Th>
                <Table.Th>Avg задержка</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {topTools.map((m) => (
                <Table.Tr key={m.name}>
                  <Table.Td><Text size="sm" ff="monospace">{m.name}</Text></Table.Td>
                  <Table.Td>{m.calls.toLocaleString('ru-RU')}</Table.Td>
                  <Table.Td>
                    <Badge size="sm" variant="light"
                      color={m.success_rate >= 0.9 ? 'green' : m.success_rate >= 0.7 ? 'yellow' : 'red'}>
                      {(m.success_rate * 100).toFixed(0)}%
                    </Badge>
                  </Table.Td>
                  <Table.Td><Text size="sm" c="dimmed">{fmtLatency(m.avg_latency_ms)}</Text></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text c="dimmed" size="sm">Пока нет данных о вызовах инструментов.</Text>
        )}
      </Card>
    </Stack>
  );
}
