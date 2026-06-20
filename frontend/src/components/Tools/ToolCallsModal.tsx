import { useState } from 'react';
import {
  Modal,
  Stack,
  Group,
  Text,
  Badge,
  SegmentedControl,
  ScrollArea,
  Paper,
  Code,
  Loader,
  Center,
  ActionIcon,
  Tooltip,
  Button,
  Textarea,
  Collapse,
  Table,
} from '@mantine/core';
import { useQuery, useMutation } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import {
  IconRefresh,
  IconPlayerPlay,
  IconChevronDown,
  IconChevronRight,
  IconExternalLink,
  IconChartBar,
} from '@tabler/icons-react';
import { isAxiosError } from 'axios';
import { toolsApi, type ToolCallRecord } from '../../shared/api/endpoints';
import type { ToolTestResponse } from '../../shared/api/types';

function errMessage(e: unknown): string {
  if (isAxiosError(e)) {
    const d = e.response?.data as { detail?: string } | undefined;
    return d?.detail || e.message;
  }
  return e instanceof Error ? e.message : 'Неизвестная ошибка';
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/** Render a tool's JSON output as a table when it's tabular (array of records,
 *  or an object wrapping such an array), else a key/value table for a flat
 *  object, else fall back to raw/pretty JSON. */
function ToolResultView({ output }: { output: string }) {
  let parsed: unknown;
  try {
    parsed = JSON.parse(output);
  } catch {
    return <Code block style={{ fontSize: 11 }}>{output}</Code>;
  }

  let rows: Record<string, unknown>[] | null = null;
  let countNote: number | null = null;
  if (Array.isArray(parsed)) {
    rows = parsed as Record<string, unknown>[];
  } else if (parsed && typeof parsed === 'object') {
    const obj = parsed as Record<string, unknown>;
    if (typeof obj.count === 'number') countNote = obj.count;
    for (const k of ['items', 'results', 'data', 'rows', 'records', 'list', 'matches']) {
      if (Array.isArray(obj[k])) {
        rows = obj[k] as Record<string, unknown>[];
        break;
      }
    }
    if (!rows) {
      // Flat object → key/value table.
      const entries = Object.entries(obj);
      return (
        <Table striped withTableBorder withColumnBorders style={{ fontSize: 11 }}>
          <Table.Tbody>
            {entries.map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td fw={600} style={{ whiteSpace: 'nowrap' }}>{k}</Table.Td>
                <Table.Td>{fmtCell(v)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      );
    }
  }

  if (rows && rows.length > 0 && typeof rows[0] === 'object' && rows[0] !== null) {
    const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r || {}))));
    return (
      <Stack gap={4}>
        {countNote !== null && (
          <Text size="xs" c="dimmed">записей: {countNote}</Text>
        )}
        <Table striped withTableBorder withColumnBorders style={{ fontSize: 11 }} stickyHeader>
          <Table.Thead>
            <Table.Tr>{cols.map((c) => <Table.Th key={c} style={{ whiteSpace: 'nowrap' }}>{c}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r, i) => (
              <Table.Tr key={i}>
                {cols.map((c) => <Table.Td key={c}>{fmtCell(r[c])}</Table.Td>)}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Stack>
    );
  }

  if (rows && rows.length === 0) {
    return <Text size="xs" c="dimmed">Пусто (0 записей){countNote !== null ? `, count=${countNote}` : ''}.</Text>;
  }

  // Non-tabular JSON → pretty-printed.
  return <Code block style={{ fontSize: 11 }}>{JSON.stringify(parsed, null, 2)}</Code>;
}

function CallRow({
  tenantId,
  configJson,
  call,
}: {
  tenantId: string;
  configJson: Record<string, unknown>;
  call: ToolCallRecord;
}) {
  const [open, setOpen] = useState(false);
  const [argsText, setArgsText] = useState(call.args_preview || '{}');
  const [result, setResult] = useState<ToolTestResponse | null>(null);

  const replayMut = useMutation({
    mutationFn: () => {
      let args: Record<string, unknown> = {};
      try {
        args = JSON.parse(argsText || '{}');
      } catch {
        throw new Error('Параметры — невалидный JSON (возможно, в логе они обрезаны; поправьте вручную).');
      }
      return toolsApi.test(tenantId, { config_json: configJson, arguments: args });
    },
    onSuccess: setResult,
    onError: (e) => notifications.show({ title: 'Ошибка повтора', message: errMessage(e), color: 'red' }),
  });

  return (
    <Paper withBorder radius="sm" p={8}>
      <Group gap={8} wrap="nowrap" justify="space-between">
        <Group gap={8} wrap="nowrap" style={{ minWidth: 0, flex: 1, cursor: 'pointer' }} onClick={() => setOpen((o) => !o)}>
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Badge size="xs" color={call.ok ? 'green' : 'red'} variant="light" style={{ flexShrink: 0 }}>
            {call.ok ? 'успех' : 'ошибка'}
          </Badge>
          <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>{fmtTime(call.created_at)}</Text>
          <Code style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {call.args_preview || '—'}
          </Code>
        </Group>
        <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
          {call.latency_ms != null && <Text size="xs" c="dimmed">{call.latency_ms}мс</Text>}
          {call.output_chars != null && <Text size="xs" c="dimmed">{call.output_chars}↩</Text>}
          {call.chat_id && (
            <Tooltip label="Открыть чат">
              <ActionIcon variant="subtle" size="sm" component="a" href={`/tenants/${tenantId}/chat/${call.chat_id}`} target="_blank">
                <IconExternalLink size={14} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Group>

      <Collapse expanded={open}>
        <Stack gap={6} mt={8}>
          <Text size="xs" fw={600}>Параметры вызова (можно поправить и повторить):</Text>
          <Textarea
            size="xs"
            autosize
            minRows={2}
            maxRows={8}
            value={argsText}
            onChange={(e) => setArgsText(e.currentTarget.value)}
            styles={{ input: { fontFamily: 'monospace', fontSize: 11 } }}
          />
          <Group>
            <Button
              size="xs"
              leftSection={<IconPlayerPlay size={14} />}
              loading={replayMut.isPending}
              onClick={() => replayMut.mutate()}
            >
              Повторить вызов
            </Button>
            <Text size="xs" c="dimmed">тот же инструмент с этими параметрами → живой результат</Text>
          </Group>
          {result && (
            <Paper withBorder radius="sm" p={6} bg={result.success ? undefined : 'var(--mantine-color-red-light)'}>
              <Group gap={6} mb={4}>
                <Badge size="xs" color={result.success ? 'green' : 'red'} variant="light">
                  {result.success ? 'успех' : 'ошибка'}
                </Badge>
                <Text size="xs" c="dimmed">результат повторного вызова</Text>
              </Group>
              <ScrollArea.Autosize mah={320}>
                {result.success
                  ? <ToolResultView output={result.output} />
                  : <Code block style={{ fontSize: 11 }}>{result.error || 'нет данных'}</Code>}
              </ScrollArea.Autosize>
            </Paper>
          )}
        </Stack>
      </Collapse>
    </Paper>
  );
}

export function ToolCallsModal({
  tenantId,
  tool,
  onClose,
}: {
  tenantId: string;
  tool: { name: string; config_json: Record<string, unknown> } | null;
  onClose: () => void;
}) {
  const [status, setStatus] = useState<'all' | 'success' | 'error'>('all');
  const opened = tool !== null;

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['tenants', tenantId, 'tools', 'calls', tool?.name, status],
    queryFn: () =>
      toolsApi.calls(tenantId, tool!.name, {
        status: status === 'all' ? undefined : status,
        limit: 100,
      }),
    enabled: opened,
  });

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      size="xl"
      title={
        <Group gap={8}>
          <IconChartBar size={20} />
          <Text fw={600}>Вызовы инструмента</Text>
          {tool && <Code>{tool.name}</Code>}
        </Group>
      }
      styles={{ body: { display: 'flex', flexDirection: 'column', height: '72vh' } }}
    >
      <Stack gap="sm" style={{ flex: 1, minHeight: 0 }}>
        <Group justify="space-between">
          <SegmentedControl
            size="xs"
            value={status}
            onChange={(v) => setStatus(v as typeof status)}
            data={[
              { label: 'Все', value: 'all' },
              { label: 'Успешные', value: 'success' },
              { label: 'Ошибки', value: 'error' },
            ]}
          />
          <Group gap="xs">
            <Text size="xs" c="dimmed">
              {data ? `${data.length} вызов(ов)` : ''}
            </Text>
            <Tooltip label="Обновить">
              <ActionIcon variant="subtle" onClick={() => refetch()} loading={isFetching}>
                <IconRefresh size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>

        <Text size="xs" c="dimmed">
          Результаты вызовов не сохраняются. Разверните вызов и нажмите «Повторить» — инструмент
          выполнится заново с теми же параметрами и покажет актуальные данные.
        </Text>

        {isLoading ? (
          <Center style={{ flex: 1 }}><Loader /></Center>
        ) : !data?.length ? (
          <Center style={{ flex: 1 }}>
            <Text c="dimmed" size="sm">Вызовов не найдено за выбранный период/фильтр.</Text>
          </Center>
        ) : (
          <ScrollArea style={{ flex: 1 }}>
            <Stack gap={6} pr="sm">
              {data.map((call, i) => (
                <CallRow key={i} tenantId={tenantId} configJson={tool!.config_json} call={call} />
              ))}
            </Stack>
          </ScrollArea>
        )}
      </Stack>
    </Modal>
  );
}
