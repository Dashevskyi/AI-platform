import { useMemo, useState } from 'react';
import { Card, Group, Text, Table, SegmentedControl, Loader, Center, Badge } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { voiceApi, type VoiceUsageRow } from '../../shared/api/endpoints';

const PERIODS = [
  { label: '7 дней', value: '7' },
  { label: '30 дней', value: '30' },
  { label: 'Всё время', value: 'all' },
];

function fmtUnits(units: number, unitType: string): string {
  if (unitType === 'seconds') {
    if (units >= 3600) return `${(units / 3600).toFixed(1)} ч`;
    if (units >= 60) return `${(units / 60).toFixed(1)} мин`;
    return `${units} сек`;
  }
  // chars
  return `${units.toLocaleString('ru-RU')} симв`;
}

const KIND_LABEL: Record<string, string> = { stt: 'Распознавание (STT)', tts: 'Синтез (TTS)' };

export function VoiceUsagePanel({ tenantId }: { tenantId: string }) {
  const [period, setPeriod] = useState('30');

  const dateFrom = useMemo(() => {
    if (period === 'all') return undefined;
    const d = new Date();
    d.setDate(d.getDate() - parseInt(period, 10));
    return d.toISOString();
  }, [period]);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'voice-usage', period],
    queryFn: () => voiceApi.usage(tenantId, { date_from: dateFrom }),
  });

  const rows: VoiceUsageRow[] = data?.items || [];
  const totalCost = rows.reduce((s, r) => s + (r.cost_usd || 0), 0);
  const totalCalls = rows.reduce((s, r) => s + r.calls, 0);

  return (
    <Card withBorder padding="sm" radius="md">
      <Group justify="space-between" mb="xs">
        <Text size="sm" fw={600}>Использование голоса</Text>
        <SegmentedControl size="xs" data={PERIODS} value={period} onChange={setPeriod} />
      </Group>

      {isLoading ? (
        <Center py="md"><Loader size="sm" /></Center>
      ) : rows.length === 0 ? (
        <Text size="xs" c="dimmed" py="xs">За выбранный период голос не использовался.</Text>
      ) : (
        <>
          <Table striped withTableBorder withColumnBorders style={{ fontSize: 12 }}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Сервис</Table.Th>
                <Table.Th>Провайдер</Table.Th>
                <Table.Th ta="right">Вызовов</Table.Th>
                <Table.Th ta="right">Объём</Table.Th>
                <Table.Th ta="right">Стоимость</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((r, i) => (
                <Table.Tr key={i}>
                  <Table.Td>
                    <Badge size="xs" variant="light" color={r.kind === 'tts' ? 'grape' : 'teal'}>
                      {KIND_LABEL[r.kind] || r.kind}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{r.provider || '—'}</Table.Td>
                  <Table.Td ta="right">{r.calls.toLocaleString('ru-RU')}</Table.Td>
                  <Table.Td ta="right">{fmtUnits(r.units, r.unit_type)}</Table.Td>
                  <Table.Td ta="right">{r.cost_usd ? `$${r.cost_usd.toFixed(4)}` : '—'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Group justify="flex-end" gap="lg" mt={6}>
            <Text size="xs" c="dimmed">Всего вызовов: <Text span fw={600}>{totalCalls.toLocaleString('ru-RU')}</Text></Text>
            <Text size="xs" c="dimmed">
              Сумма: <Text span fw={600}>{totalCost ? `$${totalCost.toFixed(4)}` : '— (тариф не задан)'}</Text>
            </Text>
          </Group>
        </>
      )}
    </Card>
  );
}
