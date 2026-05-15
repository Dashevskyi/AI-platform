import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import {
  Card,
  Container,
  Grid,
  Group,
  Loader,
  Progress,
  RingProgress,
  SegmentedControl,
  Stack,
  Text,
  Title,
  Badge,
  Alert,
} from '@mantine/core';
import { LineChart } from '@mantine/charts';
import { IconAlertTriangle } from '@tabler/icons-react';
import { gpuApi, type GpuLive, type GpuHistoryPoint } from '../shared/api/endpoints';

const RANGE_OPTIONS = [
  { label: '15м', value: '15m' },
  { label: '1ч', value: '1h' },
  { label: '6ч', value: '6h' },
  { label: '24ч', value: '24h' },
  { label: '7д', value: '7d' },
] as const;

type RangeValue = (typeof RANGE_OPTIONS)[number]['value'];

function gbStr(bytes: number | null | undefined): string {
  if (!bytes) return '—';
  return (bytes / 1024 / 1024 / 1024).toFixed(1);
}

function GpuCard({ gpu }: { gpu: GpuLive['gpus'][number] }) {
  const memPct = gpu.memory_total_bytes
    ? (gpu.memory_used_bytes / gpu.memory_total_bytes) * 100
    : 0;
  const isHotMem = memPct > 92;
  return (
    <Card withBorder padding="md">
      <Group justify="space-between" mb="xs">
        <Stack gap={0}>
          <Text fw={600}>GPU {gpu.idx}: {gpu.name}</Text>
          <Text size="xs" c="dimmed">{gpu.uuid.slice(0, 8)}…</Text>
        </Stack>
        <Group gap="xs">
          <Badge variant="light">{gpu.temperature_c?.toFixed(0)}°C</Badge>
          <Badge variant="light">{gpu.power_w?.toFixed(0)} W</Badge>
        </Group>
      </Group>
      <Grid>
        <Grid.Col span={6}>
          <Group justify="center">
            <RingProgress
              size={120}
              thickness={12}
              roundCaps
              sections={[{ value: gpu.util_pct || 0, color: 'blue' }]}
              label={
                <Stack gap={0} align="center">
                  <Text fw={700} size="lg">{(gpu.util_pct || 0).toFixed(0)}%</Text>
                  <Text size="xs" c="dimmed">GPU util</Text>
                </Stack>
              }
            />
          </Group>
        </Grid.Col>
        <Grid.Col span={6}>
          <Stack gap="xs" mt="xs">
            <Text size="sm">
              VRAM: <b>{gbStr(gpu.memory_used_bytes)}</b> / {gbStr(gpu.memory_total_bytes)} GB
            </Text>
            <Progress value={memPct} color={isHotMem ? 'red' : 'teal'} striped={isHotMem} animated={isHotMem} />
            <Text size="xs" c={isHotMem ? 'red' : 'dimmed'}>
              {memPct.toFixed(1)}% занято{isHotMem ? ' — близко к пределу' : ''}
            </Text>
          </Stack>
        </Grid.Col>
      </Grid>
    </Card>
  );
}

function VllmCard({ vllm }: { vllm: GpuLive['vllm'] }) {
  if (!vllm) {
    return (
      <Card withBorder padding="md">
        <Title order={5} mb="xs">vLLM</Title>
        <Text c="dimmed">метрики недоступны</Text>
      </Card>
    );
  }
  const kv = vllm.kv_cache_usage != null ? vllm.kv_cache_usage * 100 : null;
  const prefix = vllm.prefix_cache_hit_rate != null ? vllm.prefix_cache_hit_rate * 100 : null;
  return (
    <Card withBorder padding="md">
      <Title order={5} mb="xs">vLLM</Title>
      <Grid>
        <Grid.Col span={4}>
          <Stack gap={2} align="center">
            <Text fw={700} size="xl">{vllm.running}</Text>
            <Text size="xs" c="dimmed">в работе</Text>
          </Stack>
        </Grid.Col>
        <Grid.Col span={4}>
          <Stack gap={2} align="center">
            <Text fw={700} size="xl">{vllm.waiting}</Text>
            <Text size="xs" c="dimmed">в очереди</Text>
          </Stack>
        </Grid.Col>
        <Grid.Col span={4}>
          <Stack gap={2} align="center">
            <Text fw={700} size="xl">{vllm.generation_tokens_total.toLocaleString()}</Text>
            <Text size="xs" c="dimmed">токенов всего</Text>
          </Stack>
        </Grid.Col>
        <Grid.Col span={6}>
          <Text size="sm">KV-cache</Text>
          {kv != null ? (
            <>
              <Progress value={kv} color={kv > 85 ? 'red' : 'blue'} />
              <Text size="xs" c="dimmed" mt={2}>{kv.toFixed(1)}% занято</Text>
            </>
          ) : (
            <Text c="dimmed" size="xs">—</Text>
          )}
        </Grid.Col>
        <Grid.Col span={6}>
          <Text size="sm">Prefix-cache hit</Text>
          {prefix != null ? (
            <>
              <Progress value={prefix} color="green" />
              <Text size="xs" c="dimmed" mt={2}>{prefix.toFixed(1)}%</Text>
            </>
          ) : (
            <Text c="dimmed" size="xs">—</Text>
          )}
        </Grid.Col>
      </Grid>
    </Card>
  );
}

function HistoryCharts({ points }: { points: GpuHistoryPoint[] }) {
  // Massage data for each chart series
  const utilData = points.map((p) => {
    const row: Record<string, number | string> = { ts: new Date(p.ts).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' }) };
    p.gpus.forEach((g) => (row[`GPU ${g.idx}`] = g.util_pct || 0));
    return row;
  });
  const memData = points.map((p) => {
    const row: Record<string, number | string> = { ts: new Date(p.ts).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' }) };
    p.gpus.forEach((g) => (row[`GPU ${g.idx}`] = +(g.memory_used_bytes / 1024 / 1024 / 1024).toFixed(2)));
    return row;
  });
  const vllmData = points.map((p) => ({
    ts: new Date(p.ts).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' }),
    'tokens/s': p.vllm?.generation_tps != null ? +p.vllm.generation_tps.toFixed(1) : 0,
    'KV %': p.vllm?.kv_cache_usage != null ? +(p.vllm.kv_cache_usage * 100).toFixed(1) : 0,
  }));
  const gpuKeys = points[0]?.gpus.map((g) => `GPU ${g.idx}`) ?? [];
  const colorOf = (i: number) => ['blue.6', 'orange.6', 'teal.6', 'grape.6'][i % 4];

  return (
    <Stack gap="md">
      <Card withBorder padding="md">
        <Title order={5} mb="xs">GPU util %</Title>
        <LineChart
          h={220}
          data={utilData}
          dataKey="ts"
          series={gpuKeys.map((k, i) => ({ name: k, color: colorOf(i) }))}
          yAxisProps={{ domain: [0, 100] }}
          withDots={false}
          curveType="linear"
        />
      </Card>
      <Card withBorder padding="md">
        <Title order={5} mb="xs">VRAM использование, GB</Title>
        <LineChart
          h={220}
          data={memData}
          dataKey="ts"
          series={gpuKeys.map((k, i) => ({ name: k, color: colorOf(i) }))}
          yAxisProps={{ domain: [0, 24] }}
          withDots={false}
          curveType="linear"
        />
      </Card>
      <Card withBorder padding="md">
        <Title order={5} mb="xs">vLLM throughput и KV-cache</Title>
        <LineChart
          h={220}
          data={vllmData}
          dataKey="ts"
          series={[
            { name: 'tokens/s', color: 'green.6' },
            { name: 'KV %', color: 'red.6' },
          ]}
          withDots={false}
          curveType="linear"
        />
      </Card>
    </Stack>
  );
}

export function InfrastructurePage() {
  const [range, setRange] = useState<RangeValue>('1h');

  const live = useQuery({
    queryKey: ['gpu', 'live'],
    queryFn: gpuApi.live,
    refetchInterval: 3000,
  });

  const history = useQuery({
    queryKey: ['gpu', 'history', range],
    queryFn: () => gpuApi.history(range),
    refetchInterval: 15000,
  });

  const anyMemPressure = live.data?.gpus.some(
    (g) => g.memory_total_bytes && g.memory_used_bytes / g.memory_total_bytes > 0.92
  );

  return (
    <Container size="xl" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Инфраструктура</Title>
        <Group>
          <Text size="sm" c="dimmed">История за</Text>
          <SegmentedControl
            size="sm"
            value={range}
            onChange={(v) => setRange(v as RangeValue)}
            data={RANGE_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
          />
        </Group>
      </Group>

      {anyMemPressure && (
        <Alert color="red" icon={<IconAlertTriangle />} mb="md">
          На одной из карт занято &gt;92% VRAM — возможен OOM при пиковой нагрузке.
        </Alert>
      )}

      {live.isLoading ? (
        <Loader />
      ) : live.error ? (
        <Alert color="red">Ошибка загрузки метрик: {(live.error as Error).message}</Alert>
      ) : live.data ? (
        <Stack gap="md">
          <Grid>
            {live.data.gpus.map((g) => (
              <Grid.Col key={g.uuid} span={{ base: 12, md: 6 }}>
                <GpuCard gpu={g} />
              </Grid.Col>
            ))}
            <Grid.Col span={12}>
              <VllmCard vllm={live.data.vllm} />
            </Grid.Col>
          </Grid>

          {history.data && history.data.points.length > 0 ? (
            <HistoryCharts points={history.data.points} />
          ) : (
            <Card withBorder>
              <Text c="dimmed">История пока пустая, накапливается…</Text>
            </Card>
          )}
        </Stack>
      ) : null}
    </Container>
  );
}
