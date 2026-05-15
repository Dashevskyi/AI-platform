import React, { useEffect, useMemo, useState } from 'react';
import {
  Accordion,
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Code,
  Drawer,
  Group,
  Loader,
  Modal,
  Pagination,
  Progress,
  ScrollArea,
  SegmentedControl,
  Select,
  Spoiler,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  ThemeIcon,
  Timeline,
  Tooltip,
} from '@mantine/core';
import {
  IconArrowBack,
  IconArrowRight,
  IconCheck,
  IconCopy,
  IconMaximize,
  IconRefresh,
  IconTool,
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { chatsApi, keysApi, logsApi } from '../../shared/api/endpoints';
import type { LLMLogDetail } from '../../shared/api/types';

type LogsTabProps = {
  tenantId: string;
};

type LogFilters = {
  chat_id?: string;
  api_key_id?: string;
  date_from?: string;
  date_to?: string;
};

type ToolExecutionEntry = Record<string, unknown>;
type JsonObject = Record<string, unknown>;
type PromptLayoutSection = {
  kind?: unknown;
  title?: unknown;
  role?: unknown;
  content?: unknown;
  chars?: unknown;
  est_tokens?: unknown;
};

function tryParseJson(value: unknown): unknown {
  if (typeof value !== 'string') {
    return value;
  }
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function isPlainObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isTabularData(value: unknown): value is JsonObject[] {
    if (!Array.isArray(value) || value.length === 0) {
        return false;
    }
    return value.every(isPlainObject);
}

function isTabularPayload(value: unknown): value is {
  count?: unknown;
  shown_limit?: unknown;
  truncated?: unknown;
  log_truncated?: unknown;
  log_shown_rows?: unknown;
  column_descriptions?: unknown;
  items: JsonObject[];
} {
  return isPlainObject(value) && isTabularData(value.items);
}

function asPromptLayoutSection(value: unknown): PromptLayoutSection | null {
  return isPlainObject(value) ? value as PromptLayoutSection : null;
}

function formatCellValue(value: unknown): string {
  if (value == null) {
    return '';
  }
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value);
}

function buildTsv(columns: string[], rows: JsonObject[]): string {
  const escape = (s: string) => s.replace(/\t/g, ' ').replace(/\r?\n/g, ' ');
  const header = columns.map(escape).join('\t');
  const body = rows
    .map((row) => columns.map((c) => escape(formatCellValue(row[c]))).join('\t'))
    .join('\n');
  return `${header}\n${body}`;
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to legacy path
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function TableView({
  columns,
  rows,
  compact,
}: {
  columns: string[];
  rows: JsonObject[];
  compact?: boolean;
}) {
  return (
    <Table
      striped
      highlightOnHover
      withTableBorder
      withColumnBorders
      stickyHeader={!compact}
      layout="auto"
      style={{ width: 'max-content', minWidth: '100%' }}
    >
      <Table.Thead>
        <Table.Tr>
          {columns.map((column) => (
            <Table.Th key={column} style={{ whiteSpace: 'nowrap' }}>
              <Text size="xs" ff="monospace">{column}</Text>
            </Table.Th>
          ))}
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map((row, idx) => (
          <Table.Tr key={idx}>
            {columns.map((column) => (
              <Table.Td key={column} style={{ maxWidth: 500 }}>
                <Text
                  size={compact ? 'xs' : 'sm'}
                  ff="monospace"
                  style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                >
                  {formatCellValue(row[column])}
                </Text>
              </Table.Td>
            ))}
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function ToolResultContent({ value }: { value: unknown }) {
  const parsed = tryParseJson(value);
  const tableData = isTabularPayload(parsed) ? parsed.items : isTabularData(parsed) ? parsed : null;
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const columns = useMemo(() => {
    if (!tableData) return [] as string[];
    const set = new Set<string>();
    for (const row of tableData) {
      for (const key of Object.keys(row)) set.add(key);
    }
    return Array.from(set);
  }, [tableData]);

  if (tableData) {
    const count = isTabularPayload(parsed) && typeof parsed.count === 'number' ? parsed.count : tableData.length;
    const truncated = isTabularPayload(parsed) && parsed.truncated === true;
    const shownLimit = isTabularPayload(parsed) && typeof parsed.shown_limit === 'number' ? parsed.shown_limit : null;
    const logTruncated = isTabularPayload(parsed) && parsed.log_truncated === true;
    const logShownRows = isTabularPayload(parsed) && typeof parsed.log_shown_rows === 'number'
      ? parsed.log_shown_rows
      : null;
    const columnDescriptions = isTabularPayload(parsed) && isPlainObject(parsed.column_descriptions)
      ? parsed.column_descriptions
      : null;

    const logNote = logTruncated && logShownRows
      ? ` (в логе сохранены первые ${logShownRows})`
      : '';

    const handleCopy = async () => {
      const tsv = buildTsv(columns, tableData);
      const ok = await copyToClipboard(tsv);
      if (ok) {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    };

    return (
      <Stack gap="xs" mt={4}>
        <Group justify="space-between" gap="xs" wrap="nowrap">
          <Text size="xs" c="dimmed">
            Найдено записей: {count}
            {truncated && shownLimit ? `, показаны первые ${shownLimit}` : ''}
            {logNote}
          </Text>
          <Group gap={4} wrap="nowrap">
            <Tooltip label={copied ? 'Скопировано' : 'Скопировать как TSV'}>
              <ActionIcon
                variant="subtle"
                size="sm"
                color={copied ? 'teal' : undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  handleCopy();
                }}
              >
                {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Развернуть на весь экран">
              <ActionIcon
                variant="subtle"
                size="sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded(true);
                }}
              >
                <IconMaximize size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        {columnDescriptions && (
          <Stack gap={2}>
            {columns.map((column) => {
              const description = columnDescriptions[column];
              if (typeof description !== 'string' || !description.trim()) {
                return null;
              }
              return (
                <Text key={column} size="xs" c="dimmed">
                  <Text span ff="monospace">{column}</Text>: {description}
                </Text>
              );
            })}
          </Stack>
        )}
        <ScrollArea>
          <TableView columns={columns} rows={tableData} compact />
        </ScrollArea>

        <Modal
          opened={expanded}
          onClose={() => setExpanded(false)}
          size="90%"
          padding="md"
          title={
            <Group gap="xs">
              <Text fw={600} size="sm">Результат tool</Text>
              <Text size="xs" c="dimmed">
                Найдено: {count}
                {truncated && shownLimit ? `, показаны первые ${shownLimit}` : ''}
                {logNote}
              </Text>
            </Group>
          }
          styles={{ content: { height: '90vh' }, body: { height: 'calc(90vh - 60px)' } }}
        >
          <Stack gap="sm" h="100%">
            <Group justify="flex-end" gap="xs">
              <Button
                variant="light"
                size="xs"
                leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
                color={copied ? 'teal' : undefined}
                onClick={handleCopy}
              >
                {copied ? 'Скопировано' : 'Копировать TSV'}
              </Button>
            </Group>
            <ScrollArea style={{ flex: 1 }}>
              <TableView columns={columns} rows={tableData} />
            </ScrollArea>
          </Stack>
        </Modal>
      </Stack>
    );
  }

  return (
    <Code block style={{ fontSize: '12px' }}>
      {typeof parsed === 'string' ? parsed : JSON.stringify(parsed, null, 2)}
    </Code>
  );
}


/**
 * Human-readable rendering of the LLM raw request that we logged.
 * Replaces giant JSON dump with named sections: Settings / Tools / Messages,
 * and a "show raw JSON" button that opens the dump in a modal.
 */
/**
 * Compact collapsible JSON tree viewer.
 * No deps. Objects/arrays show as collapsible nodes; primitives are syntax-highlighted.
 * Auto-expanded up to `defaultExpandLevel` depth.
 */
function JsonTreeNode({
  data,
  name,
  depth,
  defaultExpandLevel,
}: {
  data: unknown;
  name?: string | number;
  depth: number;
  defaultExpandLevel: number;
}) {
  const isObj = isPlainObject(data);
  const isArr = Array.isArray(data);
  const isContainer = isObj || isArr;
  const [open, setOpen] = useState(depth < defaultExpandLevel);

  const keyLabel = name != null
    ? (typeof name === 'number'
        ? <span style={{ color: '#999' }}>{name}: </span>
        : <span style={{ color: '#a626a4' }}>&quot;{String(name)}&quot;: </span>)
    : null;

  if (!isContainer) {
    let valueEl: React.ReactNode;
    if (typeof data === 'string') {
      valueEl = <span style={{ color: '#50a14f' }}>&quot;{data}&quot;</span>;
    } else if (typeof data === 'number') {
      valueEl = <span style={{ color: '#986801' }}>{data}</span>;
    } else if (typeof data === 'boolean') {
      valueEl = <span style={{ color: '#0184bc' }}>{String(data)}</span>;
    } else if (data === null) {
      valueEl = <span style={{ color: '#a0a1a7' }}>null</span>;
    } else {
      valueEl = <span>{String(data)}</span>;
    }
    return (
      <div style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}>
        {keyLabel}{valueEl}
      </div>
    );
  }

  const entries: Array<[string | number, unknown]> = isArr
    ? (data as unknown[]).map((v, i) => [i, v])
    : Object.entries(data as Record<string, unknown>);
  const openCh = isArr ? '[' : '{';
  const closeCh = isArr ? ']' : '}';

  return (
    <div style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12, lineHeight: 1.5 }}>
      <span
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: 'pointer', userSelect: 'none' }}
        title={open ? 'свернуть' : 'развернуть'}
      >
        <span style={{ color: '#888', display: 'inline-block', width: 12 }}>
          {open ? '▾' : '▸'}
        </span>
        {keyLabel}
        <span style={{ color: '#383a42' }}>{openCh}</span>
        {!open && (
          <>
            <span style={{ color: '#a0a1a7', margin: '0 4px' }}>
              {isArr ? `${entries.length} элементов` : `${entries.length} ключей`}
            </span>
            <span style={{ color: '#383a42' }}>{closeCh}</span>
          </>
        )}
      </span>
      {open && (
        <div style={{ paddingLeft: 14, borderLeft: '1px solid #e0e0e0', marginLeft: 5 }}>
          {entries.map(([k, v]) => (
            <JsonTreeNode
              key={String(k)}
              data={v}
              name={k}
              depth={depth + 1}
              defaultExpandLevel={defaultExpandLevel}
            />
          ))}
        </div>
      )}
      {open && <div style={{ color: '#383a42' }}>{closeCh}</div>}
    </div>
  );
}


function RawRequestView({ raw }: { raw: Record<string, unknown> | null }) {
  const [view, setView] = useState<'parsed' | 'raw'>('parsed');
  const [copied, setCopied] = useState(false);

  if (!raw || typeof raw !== 'object') {
    return <Code block>{JSON.stringify(raw, null, 2)}</Code>;
  }

  const model = (raw.model as string) || '—';
  const temperature = raw.temperature as number | undefined;
  const maxTokens = raw.max_tokens as number | undefined;
  const tools = Array.isArray(raw.tools) ? (raw.tools as JsonObject[]) : [];
  const messages = Array.isArray(raw.messages) ? (raw.messages as JsonObject[]) : [];
  const chatTemplate = (raw.chat_template_kwargs as JsonObject | undefined) || undefined;

  const handleCopyJson = async () => {
    const ok = await copyToClipboard(JSON.stringify(raw, null, 2));
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const roleColor = (role: string) => {
    switch (role) {
      case 'system': return 'indigo';
      case 'user': return 'green';
      case 'assistant': return 'blue';
      case 'tool': return 'orange';
      default: return 'gray';
    }
  };

  // Try to split the (often single, very large) system message into logical blocks
  // that pipeline assembles: rules, anti-lazy, memory, KB, attachments, etc.
  // Pipeline joins with "\n\n", so splitting on double-newline is a good heuristic.
  const splitSystemBlocks = (text: string): { title: string; body: string }[] => {
    const chunks = text.split(/\n\n+/).filter((s) => s.trim());
    return chunks.map((chunk) => {
      const firstLine = chunk.split('\n')[0]?.trim() || '';
      const isHeader =
        firstLine.length > 0 &&
        firstLine.length < 90 &&
        (/^[A-ZА-ЯЁ\s\-—:]+:?$/.test(firstLine) ||
          /^(Rules|Memory|Knowledge Base|Приложенные файлы|ОНТОЛОГИЯ|АНТИ|КРАТКОСТЬ|ЯЗЫК|ВАЖНО|КРИТИЧЕСКИ|ЭКОНОМИЯ)/i.test(firstLine));
      if (isHeader) {
        return { title: firstLine.replace(/:+$/, ''), body: chunk.slice(firstLine.length).trim() };
      }
      return { title: firstLine.slice(0, 80), body: chunk };
    });
  };

  // Pipeline often concatenates pieces without explicit \n inside one block,
  // so visually it reads as one wall of text. Insert soft breaks before list
  // markers (- • 1.) and section keywords to improve readability — display-only.
  const beautifyForDisplay = (text: string): string => {
    return text
      // Insert \n before bullets that follow inline text
      .replace(/([^\n])\s+(?=[-•]\s)/g, '$1\n')
      // Insert \n before numbered list items like "1. "
      .replace(/([^\n])\s+(?=\d+[.)]\s+[A-ZА-ЯЁ])/g, '$1\n')
      // Insert \n before well-known section markers
      .replace(/([^\n])\s+(?=(Rules|Memory|Knowledge Base|Приложенные файлы|Tags|Параметры|Когда вызывать|Категория):\s)/g, '$1\n\n')
      // Collapse runs of 3+ newlines
      .replace(/\n{3,}/g, '\n\n');
  };

  const renderMessageContent = (m: JsonObject) => {
    const content = m.content;
    if (typeof content === 'string') {
      // For system role — split into logical blocks
      if (m.role === 'system' && content.length > 600) {
        const blocks = splitSystemBlocks(content);
        return (
          <Stack gap={6}>
            {blocks.map((b, i) => (
              <Card key={i} withBorder padding="xs" radius="sm">
                <Text size="xs" fw={600} c="indigo" mb={4}>{b.title || `Блок ${i + 1}`}</Text>
                <Spoiler maxHeight={120} showLabel="развернуть" hideLabel="свернуть">
                  <Text size="xs" style={{ whiteSpace: 'pre-wrap', wordBreak: 'normal', overflowWrap: 'anywhere', lineHeight: 1.55 }}>
                    {beautifyForDisplay(b.body)}
                  </Text>
                </Spoiler>
              </Card>
            ))}
          </Stack>
        );
      }
      return (
        <Spoiler maxHeight={100} showLabel="развернуть" hideLabel="свернуть">
          <Text size="sm" style={{ whiteSpace: 'pre-wrap', wordBreak: 'normal', overflowWrap: 'anywhere', lineHeight: 1.5 }}>
            {beautifyForDisplay(content)}
          </Text>
        </Spoiler>
      );
    }
    if (Array.isArray(content)) {
      // Multimodal content (text + image)
      return (
        <Stack gap={4}>
          {content.map((part: unknown, i: number) => {
            if (isPlainObject(part) && part.type === 'text') {
              return (
                <Text key={i} size="sm" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {String(part.text || '')}
                </Text>
              );
            }
            return (
              <Badge key={i} size="xs" variant="light" color="grape">
                {isPlainObject(part) ? String(part.type || 'unknown') : 'part'}
              </Badge>
            );
          })}
        </Stack>
      );
    }
    return <Code block>{JSON.stringify(content)}</Code>;
  };

  return (
    <Stack gap="md">
      {/* View toggle */}
      <Group justify="space-between" wrap="nowrap">
        <SegmentedControl
          size="xs"
          value={view}
          onChange={(v: string) => setView(v as 'parsed' | 'raw')}
          data={[
            { label: 'Разобранный', value: 'parsed' },
            { label: 'Сырой JSON', value: 'raw' },
          ]}
        />
        <Tooltip label={copied ? 'Скопировано' : 'Скопировать JSON'}>
          <ActionIcon variant="subtle" size="sm" color={copied ? 'teal' : undefined} onClick={handleCopyJson}>
            {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
          </ActionIcon>
        </Tooltip>
      </Group>

      {view === 'raw' ? (
        <Card withBorder padding="xs" style={{ maxHeight: '70vh', overflow: 'auto' }}>
          <JsonTreeNode data={raw} depth={0} defaultExpandLevel={2} />
        </Card>
      ) : null}

      {view === 'parsed' ? <>
      {/* Settings */}
      <Card withBorder padding="xs">
        <Text size="sm" fw={600}>⚙️ Параметры</Text>
        <Group gap="md" mt={6} wrap="wrap">
          <Badge variant="light" color="blue">model: {model}</Badge>
          {temperature != null && <Badge variant="light" color="grape">temp: {temperature}</Badge>}
          {maxTokens != null && (
            <Badge variant="light" color="teal">
              max_tokens: {maxTokens.toLocaleString('ru-RU')}
            </Badge>
          )}
          {chatTemplate && Object.entries(chatTemplate).map(([k, v]) => (
            <Badge key={k} variant="light" color="orange">{k}: {String(v)}</Badge>
          ))}
        </Group>
      </Card>

      {/* Tools */}
      {tools.length > 0 && (
        <Card withBorder padding="xs">
          <Text size="sm" fw={600} mb={6}>🔧 Tools, отправленные модели ({tools.length})</Text>
          <Stack gap={4}>
            {tools.map((t, i) => {
              const fn = isPlainObject(t.function) ? t.function : {};
              const name = String(fn.name ?? '?');
              const desc = String(fn.description ?? '');
              const params = isPlainObject(fn.parameters)
                ? Object.keys((fn.parameters as JsonObject).properties as JsonObject || {})
                : [];
              return (
                <Group key={i} gap="xs" wrap="nowrap" align="flex-start">
                  <Badge size="sm" variant="filled" color="blue" style={{ flexShrink: 0 }}>{name}</Badge>
                  <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                    {desc && (
                      <Text size="xs" c="dimmed" lineClamp={2} style={{ wordBreak: 'break-word' }}>
                        {desc}
                      </Text>
                    )}
                    {params.length > 0 && (
                      <Text size="xs" c="gray.6" ff="monospace">
                        ({params.join(', ')})
                      </Text>
                    )}
                  </Stack>
                </Group>
              );
            })}
          </Stack>
        </Card>
      )}

      {/* Messages — split into: system / history / current user query */}
      {messages.length > 0 && (() => {
        // Find the last user message — that's the actual current query.
        // Everything between system and that is "history/context".
        let lastUserIdx = -1;
        for (let i = messages.length - 1; i >= 0; i--) {
          if ((messages[i].role as string) === 'user') {
            lastUserIdx = i;
            break;
          }
        }
        const systemMsgs = messages.filter((m, i) => m.role === 'system' && i < (lastUserIdx === -1 ? messages.length : lastUserIdx));
        const historyMsgs = messages.filter((m, i) => m.role !== 'system' && i < lastUserIdx);
        const currentUser = lastUserIdx >= 0 ? messages[lastUserIdx] : null;

        const renderMsgCard = (m: JsonObject, idx: number) => {
          const role = String(m.role ?? 'unknown');
          const hasToolCalls = Array.isArray(m.tool_calls) && (m.tool_calls as unknown[]).length > 0;
          return (
            <Card key={idx} withBorder padding="xs" radius="sm">
              <Group justify="space-between" gap="xs" mb={4} wrap="nowrap">
                <Group gap={6}>
                  <Badge size="sm" variant="filled" color={roleColor(role)}>{role}</Badge>
                  {hasToolCalls && (
                    <Badge size="xs" variant="light" color="orange">
                      tool_calls: {(m.tool_calls as unknown[]).length}
                    </Badge>
                  )}
                  {typeof m.tool_call_id === 'string' && m.tool_call_id && (
                    <Badge size="xs" variant="light" color="gray">
                      for: {m.tool_call_id.slice(0, 14)}…
                    </Badge>
                  )}
                </Group>
                <Text size="xs" c="dimmed">#{idx + 1}</Text>
              </Group>
              {renderMessageContent(m)}
              {hasToolCalls && (
                <Stack gap={2} mt={4}>
                  {(m.tool_calls as JsonObject[]).map((tc, j) => {
                    const fn = isPlainObject(tc.function) ? tc.function : {};
                    return (
                      <Text key={j} size="xs" ff="monospace" c="orange.7">
                        → {String(fn.name ?? '?')}({String(fn.arguments ?? '')})
                      </Text>
                    );
                  })}
                </Stack>
              )}
            </Card>
          );
        };

        return (
          <Stack gap="md">
            {/* SYSTEM (instructions) */}
            {systemMsgs.length > 0 && (
              <Card withBorder padding="xs" style={{ borderColor: 'var(--mantine-color-indigo-5)' }}>
                <Text size="sm" fw={600} c="indigo" mb={6}>
                  ⚙️ Инструкции системы
                  <Text component="span" size="xs" c="dimmed" ml={6}>({systemMsgs.length})</Text>
                </Text>
                <Stack gap={6}>
                  {systemMsgs.map((m) => renderMsgCard(m, messages.indexOf(m)))}
                </Stack>
              </Card>
            )}

            {/* HISTORY (context) */}
            {historyMsgs.length > 0 && (
              <Card withBorder padding="xs" style={{ borderColor: 'var(--mantine-color-gray-5)' }}>
                <Text size="sm" fw={600} c="gray.7" mb={6}>
                  📜 История диалога — справочный контекст
                  <Text component="span" size="xs" c="dimmed" ml={6}>({historyMsgs.length})</Text>
                </Text>
                <Stack gap={6}>
                  {historyMsgs.map((m) => renderMsgCard(m, messages.indexOf(m)))}
                </Stack>
              </Card>
            )}

            {/* CURRENT USER QUERY */}
            {currentUser && (
              <Card
                withBorder
                padding="sm"
                style={{
                  borderColor: 'var(--mantine-color-green-5)',
                  borderWidth: 2,
                }}
              >
                <Text size="sm" fw={700} c="green.8" mb={6}>
                  🎯 Текущий запрос пользователя
                </Text>
                {renderMessageContent(currentUser)}
              </Card>
            )}
          </Stack>
        );
      })()}

      </> : null}
    </Stack>
  );
}


export function LogsTab({ tenantId }: LogsTabProps) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [chatFilter, setChatFilter] = useState<string | null>(null);
  const [apiKeyFilter, setApiKeyFilter] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(false);

  const filters: LogFilters = {
    chat_id: chatFilter || undefined,
    api_key_id: apiKeyFilter || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  };

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['tenants', tenantId, 'logs', page, chatFilter, apiKeyFilter, dateFrom, dateTo],
    queryFn: () => logsApi.list(tenantId, page, 20, filters),
    refetchInterval: autoRefresh ? 5000 : false,
  });

  const { data: logDetail, isLoading: detailLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'logs', selectedLogId, 'detail'],
    queryFn: () => logsApi.getDetail(tenantId, selectedLogId!),
    enabled: !!selectedLogId,
  });

  const { data: chatsData } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'admin', 1, apiKeyFilter],
    queryFn: () => chatsApi.listAdmin(
      tenantId,
      1,
      100,
      apiKeyFilter ? { api_key_id: apiKeyFilter } : undefined,
    ),
  });
  // Full chat list (unfiltered) — used to compute chat counts per API key.
  // Backend caps page_size at 100; for tenants with >100 chats counts may be
  // approximate (we only see the latest 100), but it's enough to identify
  // active vs. dead keys.
  const { data: allChatsData } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'admin', 'all', 1],
    queryFn: () => chatsApi.listAdmin(tenantId, 1, 100),
  });
  const keyChatCounts = new Map<string, number>();
  for (const chat of allChatsData?.items || []) {
    if (chat.api_key_id) {
      keyChatCounts.set(chat.api_key_id, (keyChatCounts.get(chat.api_key_id) || 0) + 1);
    }
  }
  const countsLoaded = !!allChatsData;
  const { data: keysData } = useQuery({
    queryKey: ['tenants', tenantId, 'keys', 'admin', 1],
    queryFn: () => keysApi.list(tenantId, 1, 100),
  });

  useEffect(() => {
    if (!chatFilter) {
      return;
    }
    const validChatIds = new Set((chatsData?.items || []).map((chat) => chat.id));
    if (!validChatIds.has(chatFilter)) {
      setChatFilter(null);
      setPage(1);
    }
  }, [apiKeyFilter, chatFilter, chatsData?.items]);

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;
  const chatMap = new Map(
    (chatsData?.items || []).map((chat) => [
      chat.id,
      chat.title || chat.description || chat.id.slice(0, 8),
    ]),
  );
  const keyMap = new Map(
    (keysData?.items || []).map((key) => [
      key.id,
      key.name || key.key_prefix || key.id.slice(0, 8),
    ]),
  );
  const visibleLogs = apiKeyFilter
    ? (data?.items || []).filter((log) => {
        if (log.api_key_id) {
          return log.api_key_id === apiKeyFilter;
        }
        return !!log.chat_id && (chatsData?.items || []).some((chat) => chat.id === log.chat_id);
      })
    : (data?.items || []);

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="xs">
          <Text fw={500}>LLM Логи</Text>
          <Tooltip label="Обновить список">
            <ActionIcon
              variant="subtle"
              size="md"
              onClick={() => {
                refetch();
                queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', 'admin'] });
                queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys', 'admin'] });
              }}
              loading={isFetching}
            >
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
          <Switch
            size="xs"
            label="Авто (5 сек)"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.currentTarget.checked)}
          />
        </Group>
        <Group gap="xs">
          <Select
            placeholder={apiKeyFilter ? 'Чаты ключа' : 'Все чаты'}
            clearable
            size="xs"
            w={200}
            value={chatFilter}
            onChange={(value) => {
              setChatFilter(value);
              setPage(1);
            }}
            data={(chatsData?.items || []).map((chat) => ({
              value: chat.id,
              label: chat.title || chat.description || chat.id.slice(0, 8),
            }))}
          />
          <Select
            placeholder="Все ключи"
            clearable
            size="xs"
            w={260}
            value={apiKeyFilter}
            onChange={(value) => {
              setApiKeyFilter(value);
              setPage(1);
            }}
            data={(() => {
              const items = (keysData?.items || []).map((key) => ({
                key,
                count: keyChatCounts.get(key.id) || 0,
              }));
              // Hide keys with 0 chats only when counts have actually loaded;
              // otherwise show all keys (counts pending).
              const visible = countsLoaded ? items.filter((x) => x.count > 0) : items;
              return visible
                .sort((a, b) => b.count - a.count)
                .map(({ key, count }) => ({
                  value: key.id,
                  label: countsLoaded
                    ? `${key.name} (${key.key_prefix}…) · ${count} чат(ов)`
                    : `${key.name} (${key.key_prefix}…)`,
                }));
            })()}
          />
          <TextInput
            type="date"
            size="xs"
            w={140}
            placeholder="Дата от"
            value={dateFrom}
            onChange={(e) => {
              setDateFrom(e.currentTarget.value);
              setPage(1);
            }}
          />
          <TextInput
            type="date"
            size="xs"
            w={140}
            placeholder="Дата до"
            value={dateTo}
            onChange={(e) => {
              setDateTo(e.currentTarget.value);
              setPage(1);
            }}
          />
          {(chatFilter || dateFrom || dateTo) && (
            <Button
              variant="subtle"
              size="xs"
              onClick={() => {
                setChatFilter(null);
                setDateFrom('');
                setDateTo('');
                setPage(1);
              }}
            >
              Сбросить
            </Button>
          )}
          {apiKeyFilter && (
            <Button
              variant="subtle"
              size="xs"
              onClick={() => {
                setApiKeyFilter(null);
                setPage(1);
              }}
            >
              Сбросить ключ
            </Button>
          )}
        </Group>
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !visibleLogs.length ? (
        <Text c="dimmed" ta="center" py="md">Логов пока нет.</Text>
      ) : (
        <>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Время</Table.Th>
                <Table.Th>Чат</Table.Th>
                <Table.Th>API ключ</Table.Th>
                <Table.Th>Модель</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Токены</Table.Th>
                <Table.Th>Задержка</Table.Th>
                <Table.Th>Tools</Table.Th>
                <Table.Th>Стоимость</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {visibleLogs.map((log) => (
                <Table.Tr
                  key={log.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => {
                    setSelectedLogId(log.id);
                    setDetailOpen(true);
                  }}
                >
                  <Table.Td>
                    <Text size="sm">{new Date(log.created_at).toLocaleString()}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" lineClamp={1}>
                      {log.chat_id ? (chatMap.get(log.chat_id) || log.chat_id.slice(0, 8)) : '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" lineClamp={1}>
                      {log.api_key_id ? (keyMap.get(log.api_key_id) || log.api_key_id.slice(0, 8)) : '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td><Text size="sm" ff="monospace">{log.model_name}</Text></Table.Td>
                  <Table.Td>
                    <Badge color={log.status === 'success' ? 'green' : 'red'}>
                      {log.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {log.prompt_tokens?.toLocaleString('ru-RU') ?? '-'} / {log.completion_tokens?.toLocaleString('ru-RU') ?? '-'} / {log.total_tokens?.toLocaleString('ru-RU') ?? '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {log.latency_ms != null ? `${Math.round(log.latency_ms).toLocaleString('ru-RU')} мс` : '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{log.tool_calls_count || '-'}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {log.estimated_cost != null ? `$${log.estimated_cost.toFixed(6)}` : '-'}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {totalPages > 1 && (
            <Center><Pagination total={totalPages} value={page} onChange={setPage} /></Center>
          )}
        </>
      )}

      <Drawer
        opened={detailOpen}
        onClose={() => {
          setDetailOpen(false);
          setSelectedLogId(null);
        }}
        title="Детали лога"
        position="right"
        size="xl"
      >
        {detailLoading ? (
          <Center py="md"><Loader /></Center>
        ) : logDetail ? (
          <LogDetailView logDetail={logDetail} />
        ) : (
          <Text c="dimmed">Нет данных.</Text>
        )}
      </Drawer>
    </Stack>
  );
}

function ToolExecutionView({ toolExecution }: { toolExecution: ToolExecutionEntry[] }) {
  const items: Array<{ type: 'call' | 'result'; name?: string; arguments?: unknown; content?: unknown }> = [];

  for (const entry of toolExecution) {
    if (entry.role === 'assistant_tool_calls' && Array.isArray(entry.calls)) {
      for (const call of entry.calls as Array<{ name?: string; arguments?: unknown }>) {
        items.push({ type: 'call', name: call.name, arguments: call.arguments });
      }
    } else if (entry.role === 'tool') {
      items.push({ type: 'result', content: entry.content });
    }
  }

  if (!items.length) {
    return null;
  }

  return (
    <Timeline active={items.length - 1} bulletSize={28} lineWidth={2}>
      {items.map((item, idx) => (
        <Timeline.Item
          key={idx}
          bullet={
            <ThemeIcon
              size={28}
              variant="filled"
              color={item.type === 'call' ? 'blue' : 'teal'}
              radius="xl"
            >
              {item.type === 'call' ? <IconArrowRight size={14} /> : <IconArrowBack size={14} />}
            </ThemeIcon>
          }
          title={
            item.type === 'call' ? (
              <Group gap="xs">
                <Badge variant="filled" color="blue" size="sm">CALL</Badge>
                <Text size="sm" fw={600} ff="monospace">{item.name || 'unknown'}</Text>
              </Group>
            ) : (
              <Badge variant="filled" color="teal" size="sm">RESULT</Badge>
            )
          }
        >
          {item.type === 'call' && item.arguments != null && (
            <Spoiler maxHeight={120} showLabel="Показать полностью" hideLabel="Свернуть" mt={4}>
              <Code block style={{ fontSize: '12px' }}>
                {typeof item.arguments === 'string'
                  ? (() => {
                      try {
                        return JSON.stringify(JSON.parse(item.arguments), null, 2);
                      } catch {
                        return item.arguments;
                      }
                    })()
                  : JSON.stringify(item.arguments, null, 2)}
              </Code>
            </Spoiler>
          )}
          {item.type === 'result' && item.content != null && item.content !== '' && (
            <Spoiler maxHeight={120} showLabel="Показать полностью" hideLabel="Свернуть" mt={4}>
              <ToolResultContent value={item.content} />
            </Spoiler>
          )}
        </Timeline.Item>
      ))}
    </Timeline>
  );
}

function PromptStructureView({ promptLayout }: { promptLayout: Record<string, unknown> }) {
  const rawSections = Array.isArray(promptLayout.sections) ? promptLayout.sections : [];
  const sections = rawSections.map(asPromptLayoutSection).filter((x): x is PromptLayoutSection => !!x);
  const tools = isPlainObject(promptLayout.tools) ? promptLayout.tools : null;
  const toolNames = Array.isArray(tools?.names)
    ? tools.names.filter((x): x is string => typeof x === 'string')
    : [];
  const mode = typeof promptLayout.mode === 'string' ? promptLayout.mode : null;

  if (!sections.length) {
    return null;
  }

  const colorByKind: Record<string, string> = {
    system_instructions: 'gray',
    history_reference: 'orange',
    current_request: 'green',
    history_message: 'blue',
  };

  const labelByKind: Record<string, string> = {
    system_instructions: 'SYSTEM',
    history_reference: 'HISTORY',
    current_request: 'CURRENT',
    history_message: 'CHAT',
  };

  return (
    <Card withBorder>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <div>
            <Text size="sm" fw={600}>Структура prompt</Text>
            <Text size="xs" c="dimmed">
              {mode === 'tool_partitioned'
                ? 'История передана как справка, текущий запрос отделён явно.'
                : 'Обычный режим диалога без специального разделения истории.'}
            </Text>
          </div>
          {toolNames.length > 0 && (
            <Badge variant="light" color="blue">
              Tools: {toolNames.length}
            </Badge>
          )}
        </Group>

        {toolNames.length > 0 && (
          <Group gap={6}>
            {toolNames.map((name) => (
              <Badge key={name} variant="light" color="blue" ff="monospace">
                {name}
              </Badge>
            ))}
          </Group>
        )}

        <Stack gap="xs">
          {sections.map((section, index) => {
            const kind = typeof section.kind === 'string' ? section.kind : 'history_message';
            const title = typeof section.title === 'string' ? section.title : 'Секция';
            const content = typeof section.content === 'string' ? section.content : '';
            const chars = typeof section.chars === 'number' ? section.chars : null;
            const estTokens = typeof section.est_tokens === 'number' ? section.est_tokens : null;

            return (
              <Card key={`${kind}-${index}`} withBorder padding="sm" bg="var(--mantine-color-body)">
                <Stack gap={6}>
                  <Group justify="space-between" align="center">
                    <Group gap="xs">
                      <Badge variant="filled" color={colorByKind[kind] || 'gray'}>
                        {labelByKind[kind] || kind.toUpperCase()}
                      </Badge>
                      <Text size="sm" fw={500}>{title}</Text>
                    </Group>
                    <Text size="xs" c="dimmed">
                      {chars != null ? `${chars} симв.` : '-'}
                      {estTokens != null ? ` · ~${estTokens} ток.` : ''}
                    </Text>
                  </Group>
                  <Code block style={{ fontSize: '12px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {content || '—'}
                  </Code>
                </Stack>
              </Card>
            );
          })}
        </Stack>
      </Stack>
    </Card>
  );
}

function LogDetailView({ logDetail }: { logDetail: LLMLogDetail }) {
  const toolExecution = (logDetail.normalized_response as Record<string, unknown> | null)?.tool_execution as
    ToolExecutionEntry[] | undefined;
  const promptLayout = (logDetail.normalized_request as Record<string, unknown> | null)?.prompt_layout;

  return (
    <ScrollArea h="calc(100vh - 100px)">
      <Stack gap="md">
        <Card withBorder>
          <Stack gap="xs">
            <Group>
              <Text size="sm" fw={500}>Модель:</Text>
              <Text size="sm" ff="monospace">{logDetail.model_name}</Text>
            </Group>
            <Group>
              <Text size="sm" fw={500}>Статус:</Text>
              <Badge color={logDetail.status === 'success' ? 'green' : 'red'}>
                {logDetail.status}
              </Badge>
            </Group>
            <Group>
              <Text size="sm" fw={500}>Токены:</Text>
              <Text size="sm">
                Промпт: {logDetail.prompt_tokens?.toLocaleString('ru-RU') ?? '-'} | Ответ: {logDetail.completion_tokens?.toLocaleString('ru-RU') ?? '-'} | Всего: {logDetail.total_tokens?.toLocaleString('ru-RU') ?? '-'}
              </Text>
            </Group>
            {(() => {
              const parts = [
                { name: 'system', value: logDetail.tokens_system, color: 'indigo' },
                { name: 'tools', value: logDetail.tokens_tools, color: 'blue' },
                { name: 'memory', value: logDetail.tokens_memory, color: 'grape' },
                { name: 'kb', value: logDetail.tokens_kb, color: 'teal' },
                { name: 'history', value: logDetail.tokens_history, color: 'orange' },
                { name: 'user', value: logDetail.tokens_user, color: 'green' },
              ];
              const has = parts.some((p) => p.value != null && p.value > 0);
              if (!has) return null;
              const total = parts.reduce((s, p) => s + (p.value || 0), 0);
              const partLabels: Record<string, string> = {
                system: 'System',
                tools: 'Tools',
                memory: 'Memory',
                kb: 'KB',
                history: 'История',
                user: 'Запрос',
              };
              const visible = parts.filter((p) => p.value && p.value > 0)
                .sort((a, b) => (b.value || 0) - (a.value || 0));
              return (
                <Card withBorder padding="sm" mt="xs">
                  <Text size="sm" fw={600} mb={8}>
                    Структура prompt начального раунда <Text component="span" size="xs" c="dimmed">(≈, tiktoken)</Text>
                  </Text>

                  {/* Stacked bar — all sections in one row, proportionally */}
                  <div
                    style={{
                      display: 'flex',
                      width: '100%',
                      height: 18,
                      borderRadius: 4,
                      overflow: 'hidden',
                      marginBottom: 10,
                      border: '1px solid var(--mantine-color-gray-3)',
                    }}
                  >
                    {visible.map((p) => {
                      const pct = total > 0 ? (p.value! / total) * 100 : 0;
                      return (
                        <Tooltip
                          key={p.name}
                          label={`${partLabels[p.name] || p.name}: ${p.value!.toLocaleString('ru-RU')} (${pct.toFixed(1)}%)`}
                        >
                          <div
                            style={{
                              width: `${pct}%`,
                              backgroundColor: `var(--mantine-color-${p.color}-6)`,
                              cursor: 'help',
                            }}
                          />
                        </Tooltip>
                      );
                    })}
                  </div>

                  {/* Aligned grid: label · count · % · sparkline */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto auto', columnGap: 12, rowGap: 6, alignItems: 'center' }}>
                    {visible.map((p) => {
                      const pct = total > 0 ? (p.value! / total) * 100 : 0;
                      return (
                        <React.Fragment key={p.name}>
                          <Badge size="sm" variant="dot" color={p.color} style={{ justifySelf: 'start' }}>
                            {partLabels[p.name] || p.name}
                          </Badge>
                          <Progress value={pct} color={p.color} size="md" radius="sm" />
                          <Text size="sm" ff="monospace" ta="right" style={{ minWidth: 64 }}>
                            {p.value!.toLocaleString('ru-RU')}
                          </Text>
                          <Text size="xs" c="dimmed" ta="right" style={{ minWidth: 48 }}>
                            {pct.toFixed(1)}%
                          </Text>
                        </React.Fragment>
                      );
                    })}
                  </div>

                  <Text size="xs" c="dimmed" mt={10}>
                    Σ начальный раунд: <b>{total.toLocaleString('ru-RU')}</b> токенов
                    {logDetail.tool_calls_count && logDetail.tool_calls_count > 0 ? (
                      <>
                        {' · '}провайдер посчитал{' '}
                        <b>{logDetail.prompt_tokens?.toLocaleString('ru-RU') ?? '?'}</b>{' '}
                        суммарно по {logDetail.tool_calls_count + 1} раундам (tool-результаты
                        наращивают prompt с каждым)
                      </>
                    ) : (
                      <>
                        {' ≈ '}{logDetail.prompt_tokens?.toLocaleString('ru-RU') ?? '?'} (точное от провайдера)
                      </>
                    )}
                  </Text>
                </Card>
              );
            })()}
            <Group>
              <Text size="sm" fw={500}>Задержка:</Text>
              <Text size="sm">
                {logDetail.latency_ms != null
                  ? `${Math.round(logDetail.latency_ms).toLocaleString('ru-RU')} мс`
                  : '-'}
              </Text>
            </Group>
            {logDetail.estimated_cost != null && (
              <Group>
                <Text size="sm" fw={500}>Стоимость:</Text>
                <Text size="sm">${logDetail.estimated_cost.toFixed(6)}</Text>
              </Group>
            )}
            {logDetail.tool_calls_count != null && logDetail.tool_calls_count > 0 && (
              <Group>
                <Text size="sm" fw={500}>Вызовов инструментов:</Text>
                <Badge variant="light" color="blue" leftSection={<IconTool size={12} />}>
                  {logDetail.tool_calls_count}
                </Badge>
              </Group>
            )}
            {logDetail.error_text && (
              <Alert color="red" variant="light">
                {logDetail.error_text}
              </Alert>
            )}
          </Stack>
        </Card>

        {isPlainObject(promptLayout) && <PromptStructureView promptLayout={promptLayout} />}

        {toolExecution && toolExecution.length > 0 && (
          <Card withBorder>
            <Text size="sm" fw={600} mb="md">
              <Group gap="xs">
                <IconTool size={16} />
                Выполнение инструментов
              </Group>
            </Text>
            <ToolExecutionView toolExecution={toolExecution} />
          </Card>
        )}

        <Accordion variant="separated">
          {logDetail.raw_request && (
            <Accordion.Item value="raw_request">
              <Accordion.Control>
                <Text size="sm" fw={500}>Исходный запрос</Text>
              </Accordion.Control>
              <Accordion.Panel>
                <RawRequestView raw={logDetail.raw_request} />
              </Accordion.Panel>
            </Accordion.Item>
          )}

          {logDetail.raw_response && (
            <Accordion.Item value="raw_response">
              <Accordion.Control>
                <Text size="sm" fw={500}>Исходный ответ</Text>
              </Accordion.Control>
              <Accordion.Panel>
                <Code block style={{ maxHeight: 400, overflow: 'auto' }}>
                  {JSON.stringify(logDetail.raw_response, null, 2)}
                </Code>
              </Accordion.Panel>
            </Accordion.Item>
          )}

          {logDetail.normalized_request && (
            <Accordion.Item value="norm_request">
              <Accordion.Control>
                <Text size="sm" fw={500}>Нормализованный запрос</Text>
              </Accordion.Control>
              <Accordion.Panel>
                <Code block style={{ maxHeight: 400, overflow: 'auto' }}>
                  {JSON.stringify(logDetail.normalized_request, null, 2)}
                </Code>
              </Accordion.Panel>
            </Accordion.Item>
          )}

          {logDetail.normalized_response && (
            <Accordion.Item value="norm_response">
              <Accordion.Control>
                <Text size="sm" fw={500}>Нормализованный ответ</Text>
              </Accordion.Control>
              <Accordion.Panel>
                <Code block style={{ maxHeight: 400, overflow: 'auto' }}>
                  {JSON.stringify(logDetail.normalized_response, null, 2)}
                </Code>
              </Accordion.Panel>
            </Accordion.Item>
          )}
        </Accordion>
      </Stack>
    </ScrollArea>
  );
}
