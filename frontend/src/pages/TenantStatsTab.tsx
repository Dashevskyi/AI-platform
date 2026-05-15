import { useState, useMemo } from 'react';
import {
  Stack,
  Group,
  SimpleGrid,
  Card,
  Text,
  Title,
  Loader,
  Center,
  SegmentedControl,
  Alert,
} from '@mantine/core';
import { DatePickerInput } from '@mantine/dates';
import { IconAlertCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { BarChart } from '@mantine/charts';
import { statsApi } from '../shared/api/endpoints';
import type { DailyModelStats } from '../shared/api/types';

function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function defaultFrom(): Date {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return d;
}

export function StatsTab({ tenantId }: { tenantId: string }) {
  const [dateFrom, setDateFrom] = useState<string | null>(formatDate(defaultFrom()));
  const [dateTo, setDateTo] = useState<string | null>(formatDate(new Date()));
  const [metric, setMetric] = useState<'tokens' | 'cost'>('tokens');

  const { data, isLoading, error } = useQuery({
    queryKey: [
      'tenants',
      tenantId,
      'stats',
      dateFrom || '',
      dateTo || '',
    ],
    queryFn: () =>
      statsApi.get(
        tenantId,
        dateFrom || undefined,
        dateTo || undefined,
      ),
  });

  const { chartData, series } = useMemo(() => {
    if (!data?.daily.length) return { chartData: [], series: [] };

    // Collect unique models
    const models = [...new Set(data.daily.map((d: DailyModelStats) => d.model_name))];

    // Group by date
    const byDate: Record<string, Record<string, number>> = {};
    for (const row of data.daily) {
      if (!byDate[row.date]) byDate[row.date] = {};
      byDate[row.date][row.model_name] =
        metric === 'tokens' ? row.total_tokens : row.estimated_cost;
    }

    // Fill all dates in range
    const dates = Object.keys(byDate).sort();
    const chartData = dates.map((date) => {
      const entry: Record<string, unknown> = { date };
      for (const model of models) {
        entry[model] = byDate[date]?.[model] ?? 0;
      }
      return entry;
    });

    const colors = [
      'blue',
      'teal',
      'orange',
      'grape',
      'cyan',
      'pink',
      'lime',
      'indigo',
      'yellow',
      'red',
    ];

    const series = models.map((model, i) => ({
      name: model,
      color: `${colors[i % colors.length]}.6`,
    }));

    return { chartData, series };
  }, [data, metric]);

  if (isLoading) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    );
  }

  if (error) {
    return (
      <Alert icon={<IconAlertCircle size={16} />} title="Ошибка" color="red">
        Не удалось загрузить статистику.
      </Alert>
    );
  }

  const summary = data?.summary;

  return (
    <Stack gap="lg">
      <Group>
        <DatePickerInput
          label="С"
          value={dateFrom}
          onChange={setDateFrom}
          clearable
          maxDate={dateTo || undefined}
          style={{ width: 180 }}
        />
        <DatePickerInput
          label="По"
          value={dateTo}
          onChange={setDateTo}
          clearable
          minDate={dateFrom || undefined}
          style={{ width: 180 }}
        />
      </Group>

      {summary && (
        <SimpleGrid cols={{ base: 2, sm: 4 }}>
          <Card withBorder p="md">
            <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
              Всего токенов
            </Text>
            <Title order={3}>{summary.total_tokens.toLocaleString('ru-RU')}</Title>
            <Text size="xs" c="dimmed">
              Ввод: {summary.prompt_tokens.toLocaleString('ru-RU')} / Вывод:{' '}
              {summary.completion_tokens.toLocaleString('ru-RU')}
            </Text>
          </Card>
          <Card withBorder p="md">
            <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
              Стоимость
            </Text>
            <Title order={3}>${summary.estimated_cost.toFixed(4)}</Title>
          </Card>
          <Card withBorder p="md">
            <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
              Запросов
            </Text>
            <Title order={3}>{summary.request_count.toLocaleString('ru-RU')}</Title>
          </Card>
          <Card withBorder p="md">
            <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
              Средняя стоимость запроса
            </Text>
            <Title order={3}>
              ${summary.request_count > 0 ? (summary.estimated_cost / summary.request_count).toFixed(6) : '0'}
            </Title>
          </Card>
        </SimpleGrid>
      )}

      <Card withBorder p="md">
        <Group justify="space-between" mb="md">
          <Title order={4}>
            {metric === 'tokens' ? 'Использование токенов' : 'Стоимость'} по дням
          </Title>
          <SegmentedControl
            value={metric}
            onChange={(v) => setMetric(v as 'tokens' | 'cost')}
            data={[
              { label: 'Токены', value: 'tokens' },
              { label: 'Деньги', value: 'cost' },
            ]}
          />
        </Group>

        {chartData.length > 0 ? (
          <BarChart
            h={350}
            data={chartData}
            dataKey="date"
            type="stacked"
            series={series}
            withLegend
            withTooltip
            valueFormatter={(value) =>
              metric === 'tokens'
                ? Number(value).toLocaleString('ru-RU')
                : `$${Number(value).toFixed(4)}`
            }
          />
        ) : (
          <Center py="xl">
            <Text c="dimmed">Нет данных за выбранный период.</Text>
          </Center>
        )}
      </Card>
    </Stack>
  );
}
