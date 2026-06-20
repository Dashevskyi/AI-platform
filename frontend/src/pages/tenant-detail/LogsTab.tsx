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
  Tabs,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import {
  IconCheck,
  IconCopy,
  IconExternalLink,
  IconMaximize,
  IconRefresh,
  IconTool,
} from '@tabler/icons-react';
import { useNavigate } from 'react-router-dom';
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
  status?: string;
  served_by?: string;
};

type ToolExecutionEntry = Record<string, unknown>;
type JsonObject = Record<string, unknown>;

function LogStat({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div>
      <Text size="9px" c="dimmed" tt="uppercase" fw={700}>{label}</Text>
      <Text size="sm" fw={600} c={color}>{value}</Text>
    </div>
  );
}
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
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [chatFilter, setChatFilter] = useState<string | null>(null);
  const [apiKeyFilter, setApiKeyFilter] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [servedByFilter, setServedByFilter] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const filters: LogFilters = {
    chat_id: chatFilter || undefined,
    api_key_id: apiKeyFilter || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    status: statusFilter || undefined,
    served_by: servedByFilter || undefined,
  };

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['tenants', tenantId, 'logs', page, chatFilter, apiKeyFilter, dateFrom, dateTo, statusFilter, servedByFilter],
    queryFn: () => logsApi.list(tenantId, page, 20, filters),
    refetchInterval: autoRefresh ? 5000 : false,
  });

  // Aggregates over the same filter set — drives the stats bar above the table.
  const { data: summary } = useQuery({
    queryKey: ['tenants', tenantId, 'logs', 'summary', chatFilter, apiKeyFilter, dateFrom, dateTo, statusFilter, servedByFilter],
    queryFn: () => logsApi.summary(tenantId, filters),
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
          <Select
            placeholder="Статус"
            clearable
            size="xs"
            w={120}
            value={statusFilter}
            onChange={(value) => { setStatusFilter(value); setPage(1); }}
            data={[
              { value: 'success', label: 'Успех' },
              { value: 'error', label: 'Ошибки' },
            ]}
          />
          <Select
            placeholder="Tier"
            clearable
            size="xs"
            w={130}
            value={servedByFilter}
            onChange={(value) => { setServedByFilter(value); setPage(1); }}
            data={[
              { value: 'tier0_template', label: 'Tier 0' },
              { value: 'llm', label: 'LLM' },
            ]}
          />
          {(chatFilter || dateFrom || dateTo || statusFilter || servedByFilter) && (
            <Button
              variant="subtle"
              size="xs"
              onClick={() => {
                setChatFilter(null);
                setDateFrom('');
                setDateTo('');
                setStatusFilter(null);
                setServedByFilter(null);
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

      {summary && summary.total > 0 && (
        <Card withBorder padding="xs">
          <Group gap="xl" wrap="wrap">
            <LogStat label="Запросов" value={summary.total.toLocaleString('ru-RU')} />
            <LogStat
              label="Ошибки"
              value={`${summary.errors} · ${(summary.error_rate * 100).toFixed(1)}%`}
              color={summary.errors ? 'red' : undefined}
            />
            <LogStat
              label="Avg задержка"
              value={summary.avg_latency_ms != null ? `${(summary.avg_latency_ms / 1000).toFixed(1)} с` : '—'}
            />
            <LogStat
              label="Avg токенов"
              value={summary.avg_total_tokens != null ? Math.round(summary.avg_total_tokens).toLocaleString('ru-RU') : '—'}
            />
            <LogStat label="Всего токенов" value={summary.total_tokens.toLocaleString('ru-RU')} />
            <LogStat label="Стоимость" value={`$${summary.estimated_cost.toFixed(4)}`} />
            <LogStat label="Tier 0" value={`${(summary.tier0_share * 100).toFixed(0)}%`} />
            <LogStat label="С tools" value={summary.with_tool_calls.toLocaleString('ru-RU')} />
          </Group>
        </Card>
      )}

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
                  <Table.Td style={{ maxWidth: 260 }}>
                    <Text size="sm" lineClamp={1}>
                      {log.chat_id ? (chatMap.get(log.chat_id) || log.chat_id.slice(0, 8)) : '-'}
                    </Text>
                    {log.request_preview && (
                      <Text size="xs" c="dimmed" lineClamp={1} title={log.request_preview}>
                        {log.request_preview}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" lineClamp={1}>
                      {log.api_key_id ? (keyMap.get(log.api_key_id) || log.api_key_id.slice(0, 8)) : '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <Text size="sm" ff="monospace">{log.model_name}</Text>
                      {log.served_by === 'tier0_template' && (
                        <Badge size="xs" color="grape" variant="light">T0</Badge>
                      )}
                    </Group>
                  </Table.Td>
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
                    {log.tool_calls_count ? (
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm">{log.tool_calls_count}</Text>
                        {!!log.tool_errors_count && (
                          <Tooltip label={`${log.tool_errors_count} вызов(ов) с ошибкой`}>
                            <Badge size="xs" color="red" variant="light">{log.tool_errors_count} ✕</Badge>
                          </Tooltip>
                        )}
                      </Group>
                    ) : (
                      <Text size="sm">-</Text>
                    )}
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
        // Закрытие Drawer'а только по крестику. Нужно потому что внутри
        // открываются Modal'ы (tool-call detail, tool-payload detail) —
        // клик по их подложке или Escape иначе закрывают и сам Drawer.
        closeOnClickOutside={false}
        closeOnEscape={false}
      >
        {detailLoading ? (
          <Center py="md"><Loader /></Center>
        ) : logDetail ? (
          <Stack gap="sm">
            {logDetail.chat_id && (
              <Button
                variant="light"
                size="xs"
                leftSection={<IconExternalLink size={14} />}
                onClick={() => navigate(`/tenants/${tenantId}/chat/${logDetail.chat_id}`)}
                style={{ alignSelf: 'flex-start' }}
              >
                Открыть чат
              </Button>
            )}
            <LogDetailView logDetail={logDetail} />
          </Stack>
        ) : (
          <Text c="dimmed">Нет данных.</Text>
        )}
      </Drawer>
    </Stack>
  );
}

// =====================================================================
// Debug-trace views (rendered when llm_request_logs.debug is populated).
// =====================================================================

type DebugToolCall = {
  round: number;
  name: string;
  args_preview: string;
  ok: boolean;
  latency_ms: number;
  output_chars: number;
};

type DebugRound = {
  round: number;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  has_tool_calls: boolean;
  tool_calls_count: number;
  messages_snapshot?: Array<{
    role: string;
    chars: number;
    est_tokens: number;
    brief: string;
    tool_calls?: string[];
    tool_call_id?: string;
  }>;
  response_content_chars?: number;
  response_reasoning_chars?: number;
  artifacts_captured?: Array<{ tool_name: string; artifact_id: string | null }>;
};

type DebugToolPayloadEntry = {
  name: string;
  source: string;
  similarity: number | null;
  description_chars: number;
  parameters_chars: number;
  description: string;
  parameters: unknown;
};

type ToolExecutionPair = {
  name: string;
  args: unknown;
  result: unknown;
  resultIsError?: boolean;
};

function _pairExecutionByName(exec: ToolExecutionEntry[]): ToolExecutionPair[] {
  // tool_execution interleaves: assistant_tool_calls (with N calls) then N tool results.
  const pairs: ToolExecutionPair[] = [];
  const pendingCalls: Array<{ name: string; args: unknown }> = [];
  for (const e of exec || []) {
    if (e.role === 'assistant_tool_calls' && Array.isArray(e.calls)) {
      for (const c of e.calls as Array<{ name?: string; arguments?: unknown }>) {
        pendingCalls.push({ name: c.name || 'unknown', args: c.arguments });
      }
    } else if (e.role === 'tool') {
      const call = pendingCalls.shift();
      if (call) {
        const resultStr = typeof e.content === 'string' ? e.content : JSON.stringify(e.content);
        const isErr = /^Ошибка:|"error"|HTTP 4\d\d|HTTP 5\d\d/i.test(resultStr || '');
        pairs.push({ name: call.name, args: call.args, result: e.content, resultIsError: isErr });
      }
    }
  }
  return pairs;
}

function _toolCallColor(call: DebugToolCall): { color: string; label: string } {
  if (!call.ok) return { color: 'red', label: 'ERROR' };
  if (call.output_chars === 0) return { color: 'gray', label: 'EMPTY' };
  return { color: 'teal', label: 'OK' };
}

const _fmtN = (n: number | null | undefined): string =>
  n == null ? '—' : Number(n).toLocaleString('ru-RU');

function RoundsView(props: {
  rounds: DebugRound[];
  toolCalls: DebugToolCall[];
  execution: ToolExecutionPair[];
  onOpenCall: (entry: { call: DebugToolCall; execution?: ToolExecutionPair }) => void;
  selectedTab: string;
  onTabChange: (val: string) => void;
}) {
  const { rounds, toolCalls, execution, onOpenCall, selectedTab, onTabChange } = props;
  if (!rounds.length) return null;

  // Group tool calls by round (round=0 has no tool calls — it's the initial call;
  // round N tool calls were emitted by round N-1's LLM response, executed before round N).
  const callsByRound = new Map<number, DebugToolCall[]>();
  for (const c of toolCalls) {
    const arr = callsByRound.get(c.round) || [];
    arr.push(c);
    callsByRound.set(c.round, arr);
  }

  // Assign execution pairs in order (best-effort: pairs[i] aligns with the i-th call
  // across all rounds, since both arrays are time-ordered).
  let execIdx = 0;
  const callExec = new Map<DebugToolCall, ToolExecutionPair | undefined>();
  for (const c of toolCalls) {
    callExec.set(c, execution[execIdx]);
    execIdx += 1;
  }

  const totalPrompt = rounds.reduce((s, r) => s + (r.prompt_tokens || 0), 0);
  const totalCompletion = rounds.reduce((s, r) => s + (r.completion_tokens || 0), 0);
  const totalLatency = rounds.reduce((s, r) => s + (r.latency_ms || 0), 0);
  // Peak context occupancy: the largest prompt (input) token count across the
  // rounds — i.e. how full the context window got at its worst this request.
  const peakRound = rounds.reduce(
    (best, r) => ((r.prompt_tokens || 0) > (best?.prompt_tokens || 0) ? r : best),
    rounds[0],
  );
  const maxPromptTokens = peakRound?.prompt_tokens || 0;

  const renderCallList = (calls: DebugToolCall[]) => {
    if (!calls.length) {
      return <Text size="xs" c="dimmed" fs="italic">— нет tool-вызовов —</Text>;
    }
    return (
      <Stack gap={4}>
        {calls.map((c, i) => {
          const { color, label } = _toolCallColor(c);
          return (
            <Group
              key={`${c.round}-${i}`}
              gap="xs"
              wrap="nowrap"
              style={{ cursor: 'pointer', padding: '4px 6px', borderRadius: 4 }}
              onClick={() => onOpenCall({ call: c, execution: callExec.get(c) })}
            >
              <Badge variant="filled" color={color} size="xs" w={56}>{label}</Badge>
              <Text size="xs" c="dimmed" w={28}>R{c.round}</Text>
              <Text size="xs" ff="monospace" fw={500} style={{ flex: 1, minWidth: 0 }}>{c.name}</Text>
              <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                {_fmtN(c.latency_ms)}ms · {_fmtN(c.output_chars)}ch
              </Text>
            </Group>
          );
        })}
      </Stack>
    );
  };

  return (
    <Card withBorder>
      <Stack gap="sm">
        <Group justify="space-between">
          <Text size="sm" fw={600}>Раунды LLM ({rounds.length})</Text>
        </Group>

        <Tabs value={selectedTab} onChange={(v) => v && onTabChange(v)} variant="outline">
          <Tabs.List>
            <Tabs.Tab value="summary">
              <Group gap={4}>
                <Text size="xs" fw={600}>Summary</Text>
                <Badge size="xs" variant="light" color="gray">{toolCalls.length}</Badge>
              </Group>
            </Tabs.Tab>
            {rounds.map((r) => {
              const calls = callsByRound.get(r.round) || [];
              const hasErr = calls.some((c) => !c.ok);
              const hasEmpty = calls.some((c) => c.ok && c.output_chars === 0);
              const dot = hasErr ? 'red' : (hasEmpty ? 'gray' : (calls.length ? 'teal' : 'indigo'));
              return (
                <Tabs.Tab
                  key={r.round}
                  value={`r${r.round}`}
                  rightSection={
                    <Badge variant="dot" color={dot} size="xs">{calls.length}</Badge>
                  }
                >
                  R{r.round}
                </Tabs.Tab>
              );
            })}
          </Tabs.List>

          <Tabs.Panel value="summary" pt="sm">
            <Stack gap="xs">
              <Group gap="md">
                <Text size="xs" c="dimmed">
                  Σ prompt: <Text component="span" fw={600} c="bright">{_fmtN(totalPrompt)}</Text>
                </Text>
                <Text size="xs" c="dimmed">
                  Σ completion: <Text component="span" fw={600} c="bright">{_fmtN(totalCompletion)}</Text>
                </Text>
                <Tooltip label="Максимум prompt-токенов среди раундов — пик занятости контекстного окна за этот запрос">
                  <Text size="xs" c="dimmed" style={{ cursor: 'help' }}>
                    Пик контекста: <Text component="span" fw={600} c="bright">{_fmtN(maxPromptTokens)}</Text>
                    {rounds.length > 1 && peakRound ? <Text component="span" c="dimmed"> (R{peakRound.round})</Text> : null}
                  </Text>
                </Tooltip>
                <Text size="xs" c="dimmed">
                  Σ latency: <Text component="span" fw={600} c="bright">{_fmtN(totalLatency)}ms</Text>
                </Text>
                <Text size="xs" c="dimmed">
                  Раундов: <Text component="span" fw={600} c="bright">{rounds.length}</Text>
                </Text>
                <Text size="xs" c="dimmed">
                  Tool calls: <Text component="span" fw={600} c="bright">{toolCalls.length}</Text>
                </Text>
              </Group>
              <Text size="xs" c="dimmed">
                Все tool-вызовы (в порядке выполнения, клик — детали):
              </Text>
              {renderCallList(toolCalls)}
            </Stack>
          </Tabs.Panel>

          {rounds.map((r) => {
            const calls = callsByRound.get(r.round) || [];
            const snap = r.messages_snapshot || [];
            const totalCtxChars = snap.reduce((s, m) => s + (m.chars || 0), 0);
            const totalCtxTokens = snap.reduce((s, m) => s + (m.est_tokens || 0), 0);
            const arts = r.artifacts_captured || [];
            return (
              <Tabs.Panel key={r.round} value={`r${r.round}`} pt="sm">
                <Stack gap="sm">
                  <Group gap="md">
                    <Text size="xs" c="dimmed">
                      prompt: <Text component="span" fw={600} c="bright">{_fmtN(r.prompt_tokens)}</Text>
                    </Text>
                    <Text size="xs" c="dimmed">
                      completion: <Text component="span" fw={600} c="bright">{_fmtN(r.completion_tokens)}</Text>
                    </Text>
                    <Text size="xs" c="dimmed">
                      latency: <Text component="span" fw={600} c="bright">{_fmtN(r.latency_ms)}ms</Text>
                    </Text>
                    {r.response_content_chars != null && (
                      <Text size="xs" c="dimmed">
                        ответ: <Text component="span" fw={600} c="bright">{_fmtN(r.response_content_chars)} симв</Text>
                        {r.response_reasoning_chars ? <Text component="span" c="dimmed"> (reasoning {_fmtN(r.response_reasoning_chars)})</Text> : null}
                      </Text>
                    )}
                    {r.has_tool_calls && (
                      <Badge variant="light" color="blue" size="xs">
                        → {r.tool_calls_count} tool call(s)
                      </Badge>
                    )}
                  </Group>

                  {/* Tool calls of this round (those triggered after this round's response) */}
                  <div>
                    <Text size="xs" fw={600} c="dimmed" mb={4}>Tool calls раунда</Text>
                    {renderCallList(calls)}
                  </div>

                  {/* Context that went into the LLM for this round */}
                  {snap.length > 0 && (
                    <div>
                      <Group justify="space-between" mb={4}>
                        <Text size="xs" fw={600} c="dimmed">
                          Контекст (что отправили в LLM)
                        </Text>
                        <Text size="xs" c="dimmed">
                          {snap.length} сообщ. · Σ {_fmtN(totalCtxChars)} симв · ~{_fmtN(totalCtxTokens)} ток.
                        </Text>
                      </Group>
                      <Stack gap={4}>
                        {snap.map((m, i) => {
                          const roleColor =
                            m.role === 'system' ? 'gray' :
                            m.role === 'user' ? 'green' :
                            m.role === 'assistant' ? 'blue' :
                            m.role === 'tool' ? 'teal' : 'orange';
                          return (
                            <Card key={i} withBorder padding="xs" bg="var(--mantine-color-body)">
                              <Group justify="space-between" wrap="nowrap" mb={2}>
                                <Group gap="xs">
                                  <Badge color={roleColor} variant="filled" size="xs">{m.role}</Badge>
                                  {m.tool_calls && m.tool_calls.length > 0 && (
                                    <Text size="xs" c="dimmed">→ tool_calls: {m.tool_calls.join(', ')}</Text>
                                  )}
                                  {m.tool_call_id && (
                                    <Text size="xs" c="dimmed">tool_call_id: {m.tool_call_id}</Text>
                                  )}
                                </Group>
                                <Text size="xs" c="dimmed">{_fmtN(m.chars)} симв · ~{_fmtN(m.est_tokens)} ток</Text>
                              </Group>
                              <Text size="xs" ff="monospace" c="dimmed" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                {m.brief || '—'}
                              </Text>
                            </Card>
                          );
                        })}
                      </Stack>
                    </div>
                  )}

                  {/* Artifacts captured from THIS round's tool results */}
                  {arts.length > 0 && (
                    <div>
                      <Text size="xs" fw={600} c="dimmed" mb={4}>
                        Артефакты, созданные раундом ({arts.length})
                      </Text>
                      <Stack gap={3}>
                        {arts.map((a, i) => (
                          <Group key={i} gap="xs" wrap="nowrap">
                            <Badge variant="light" color={a.artifact_id ? 'violet' : 'gray'} size="xs">
                              {a.artifact_id ? 'saved' : 'skipped'}
                            </Badge>
                            <Text size="xs" ff="monospace">{a.tool_name}</Text>
                            {a.artifact_id && (
                              <Text size="xs" c="dimmed" ff="monospace" style={{ flex: 1, minWidth: 0 }}>
                                id={a.artifact_id}
                              </Text>
                            )}
                          </Group>
                        ))}
                      </Stack>
                    </div>
                  )}
                </Stack>
              </Tabs.Panel>
            );
          })}
        </Tabs>
      </Stack>
    </Card>
  );
}

function _payloadSourceBadge(source: string, similarity: number | null) {
  if (source === 'pinned') return { color: 'grape', label: 'pinned', tip: 'Закреплён админом — всегда в payload' };
  if (source === 'builtin') return { color: 'cyan', label: 'builtin', tip: 'Системный tool из builtin_registry — всегда выше budget' };
  if (source === 'semantic') {
    const sim = similarity != null ? ` ${similarity.toFixed(2)}` : '';
    return { color: 'green', label: `semantic${sim}`, tip: `Выбран по semantic similarity (cosine ${similarity ?? '?'})` };
  }
  if (source && source.startsWith('route:')) {
    return { color: 'orange', label: source, tip: 'Выбран по доменному tool-route' };
  }
  if (source === 'keyword') return { color: 'yellow', label: 'keyword', tip: 'Выбран keyword-matcher (fallback)' };
  if (source === 'llm-pick') return { color: 'lime', label: 'llm-pick', tip: 'Выбран отдельным LLM-выбором (последний fallback)' };
  if (source === 'non-embedded-fallback') return { color: 'gray', label: 'no-embed', tip: 'Без embedding — добавлен сверху semantic-выборки' };
  if (source === 'attachment') return { color: 'violet', label: 'attachment', tip: 'Поиск внутри вложений чата' };
  return { color: 'gray', label: source || 'unknown', tip: source };
}


type SystemBlock = { label: string; content: string; chars: number; est_tokens: number };

function asSystemBlock(value: unknown): SystemBlock | null {
  if (!isPlainObject(value)) return null;
  return {
    label: typeof value.label === 'string' ? value.label : 'System block',
    content: typeof value.content === 'string' ? value.content : '',
    chars: typeof value.chars === 'number' ? value.chars : 0,
    est_tokens: typeof value.est_tokens === 'number' ? value.est_tokens : 0,
  };
}

function systemBlockColor(label: string): string {
  const u = label.toLowerCase();
  if (u.startsWith('hardcoded')) return 'indigo';
  if (u.startsWith('block-memory')) return 'grape';
  if (u.startsWith('block-kb')) return 'teal';
  if (u.startsWith('block-attachments')) return 'pink';
  if (u.startsWith('history-resumes')) return 'orange';
  if (u.startsWith('tenant')) return 'cyan';
  if (u.startsWith('language')) return 'lime';
  return 'gray';
}

function PromptStructureView(props: {
  promptLayout: Record<string, unknown>;
  toolsPayload?: DebugToolPayloadEntry[];
  onOpenTool?: (entry: DebugToolPayloadEntry) => void;
}) {
  const { promptLayout, toolsPayload, onOpenTool } = props;
  const rawSections = Array.isArray(promptLayout.sections) ? promptLayout.sections : [];
  const sections = rawSections.map(asPromptLayoutSection).filter((x): x is PromptLayoutSection => !!x);
  const rawBlocks = Array.isArray(promptLayout.system_blocks) ? promptLayout.system_blocks : [];
  const systemBlocks = rawBlocks.map(asSystemBlock).filter((x): x is SystemBlock => !!x);
  const tools = isPlainObject(promptLayout.tools) ? promptLayout.tools : null;
  const toolNames = Array.isArray(tools?.names)
    ? tools.names.filter((x): x is string => typeof x === 'string')
    : [];
  const mode = typeof promptLayout.mode === 'string' ? promptLayout.mode : null;
  const enrichedTools = (toolsPayload && toolsPayload.length > 0) ? toolsPayload : null;

  if (!sections.length && systemBlocks.length === 0) {
    return null;
  }

  // Pull the user's current question out of sections — it's THE thing
  // admins look for and we want it rendered prominently at the top, not
  // buried below 15 system blocks.
  const currentRequest = sections.find((s) => s.kind === 'current_request') || null;
  const nonRequestSections = sections.filter((s) => s.kind !== 'current_request');
  const systemInstrIdx = nonRequestSections.findIndex((s) => s.kind === 'system_instructions');

  const colorByKind: Record<string, string> = {
    system_instructions: 'gray',
    history_reference: 'orange',
    history_message: 'blue',
  };

  const labelByKind: Record<string, string> = {
    system_instructions: 'SYSTEM',
    history_reference: 'HISTORY',
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
          {(enrichedTools?.length ?? toolNames.length) > 0 && (
            <Badge variant="light" color="blue">
              Tools: {enrichedTools?.length ?? toolNames.length}
            </Badge>
          )}
        </Group>

        {currentRequest && (() => {
          const reqContent = typeof currentRequest.content === 'string' ? currentRequest.content : '';
          const reqChars = typeof currentRequest.chars === 'number' ? currentRequest.chars : 0;
          const reqTokens = typeof currentRequest.est_tokens === 'number' ? currentRequest.est_tokens : 0;
          return (
            <Card
              withBorder
              padding="md"
              radius="md"
              style={{
                borderColor: 'var(--mantine-color-green-6)',
                borderWidth: 2,
                background: 'var(--mantine-color-green-light)',
              }}
            >
              <Stack gap={4}>
                <Group justify="space-between" align="center">
                  <Group gap="xs">
                    <Badge variant="filled" color="green" size="lg">📍 Запрос пользователя</Badge>
                    <Text size="xs" c="dimmed">— то что прислал юзер в этом обмене</Text>
                  </Group>
                  <Text size="xs" c="dimmed">
                    {reqChars} симв · ~{reqTokens} ток
                  </Text>
                </Group>
                <Code block style={{ fontSize: '13px', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontWeight: 500 }}>
                  {reqContent || '—'}
                </Code>
              </Stack>
            </Card>
          );
        })()}

        {enrichedTools ? (
          <>
            <Text size="xs" c="dimmed">
              Tools в порядке payload (клик — детали; цвет = источник).
            </Text>
            <Group gap={6}>
              {enrichedTools.map((t) => {
                const b = _payloadSourceBadge(t.source, t.similarity);
                return (
                  <Tooltip key={t.name} label={`${b.tip} · ${t.description_chars}+${t.parameters_chars} симв`} multiline w={280}>
                    <Badge
                      variant="light"
                      color={b.color}
                      ff="monospace"
                      style={{ cursor: onOpenTool ? 'pointer' : 'default' }}
                      onClick={() => onOpenTool?.(t)}
                      rightSection={
                        <Text size="xs" c="dimmed" component="span" ml={4}>· {b.label}</Text>
                      }
                    >
                      {t.name}
                    </Badge>
                  </Tooltip>
                );
              })}
            </Group>
          </>
        ) : (
          toolNames.length > 0 && (
            <Group gap={6}>
              {toolNames.map((name) => (
                <Badge key={name} variant="light" color="blue" ff="monospace">
                  {name}
                </Badge>
              ))}
            </Group>
          )
        )}

        <Stack gap="xs">
          {nonRequestSections.map((section, index) => {
            const kind = typeof section.kind === 'string' ? section.kind : 'history_message';
            const title = typeof section.title === 'string' ? section.title : 'Секция';
            const content = typeof section.content === 'string' ? section.content : '';
            const chars = typeof section.chars === 'number' ? section.chars : null;
            const estTokens = typeof section.est_tokens === 'number' ? section.est_tokens : null;
            const isSystemInstructions = kind === 'system_instructions' && index === systemInstrIdx;

            return (
              <Card key={`${kind}-${index}`} withBorder padding="sm" bg="var(--mantine-color-body)">
                <Stack gap={6}>
                  <Group justify="space-between" align="center">
                    <Group gap="xs">
                      <Badge variant="filled" color={colorByKind[kind] || 'gray'}>
                        {labelByKind[kind] || kind.toUpperCase()}
                      </Badge>
                      <Text size="sm" fw={500}>{title}</Text>
                      {isSystemInstructions && systemBlocks.length > 0 && (
                        <Badge variant="light" color="indigo" size="sm">
                          {systemBlocks.length} блоков
                        </Badge>
                      )}
                    </Group>
                    <Text size="xs" c="dimmed">
                      {chars != null ? `${chars} симв.` : '-'}
                      {estTokens != null ? ` · ~${estTokens} ток.` : ''}
                    </Text>
                  </Group>

                  {isSystemInstructions && systemBlocks.length > 0 ? (
                    <Stack gap={6}>
                      <Text size="xs" c="dimmed">
                        System-сообщение собирается из этих именованных блоков (порядок = порядок в промпте).
                        Раскройте, чтобы увидеть содержимое каждого.
                      </Text>
                      {systemBlocks.map((blk, i) => {
                        const color = systemBlockColor(blk.label);
                        return (
                          <details key={i}>
                            <summary
                              style={{
                                cursor: 'pointer',
                                listStyle: 'none',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 8,
                                padding: '4px 6px',
                                borderRadius: 4,
                                userSelect: 'none',
                              }}
                              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'var(--mantine-color-default-hover)'; }}
                              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                            >
                              <Text size="xs" c="dimmed" style={{ cursor: 'pointer' }}>▸</Text>
                              <Badge
                                variant="light"
                                color={color}
                                ff="monospace"
                                size="sm"
                                style={{ cursor: 'pointer' }}
                              >
                                {blk.label}
                              </Badge>
                              <Text size="xs" c="dimmed" style={{ cursor: 'pointer' }}>
                                {blk.chars} симв · ~{blk.est_tokens} ток
                              </Text>
                            </summary>
                            <Code block mt={4} style={{ fontSize: '11.5px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                              {blk.content || '—'}
                            </Code>
                          </details>
                        );
                      })}
                    </Stack>
                  ) : (
                    <Code block style={{ fontSize: '12px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {content || '—'}
                    </Code>
                  )}
                </Stack>
              </Card>
            );
          })}
        </Stack>
      </Stack>
    </Card>
  );
}

function TokenBreakdownView(props: {
  selectedTab: string;
  rounds: DebugRound[];
  logDetail: LLMLogDetail;
}) {
  const { selectedTab, rounds, logDetail } = props;

  if (selectedTab === 'summary') {
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
      system: 'System', tools: 'Tools', memory: 'Memory', kb: 'KB', history: 'История', user: 'Запрос',
    };
    const visible = parts.filter((p) => p.value && p.value > 0).sort((a, b) => (b.value || 0) - (a.value || 0));
    return (
      <Card withBorder padding="sm">
        <Text size="sm" fw={600} mb={8}>
          Структура prompt начального раунда <Text component="span" size="xs" c="dimmed">(≈, tiktoken; за весь запрос)</Text>
        </Text>
        <div style={{ display: 'flex', width: '100%', height: 18, borderRadius: 4, overflow: 'hidden', marginBottom: 10, border: '1px solid var(--mantine-color-gray-3)' }}>
          {visible.map((p) => {
            const pct = total > 0 ? (p.value! / total) * 100 : 0;
            return (
              <Tooltip key={p.name} label={`${partLabels[p.name] || p.name}: ${_fmtN(p.value!)} (${pct.toFixed(1)}%)`}>
                <div style={{ width: `${pct}%`, backgroundColor: `var(--mantine-color-${p.color}-6)`, cursor: 'help' }} />
              </Tooltip>
            );
          })}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto auto', columnGap: 12, rowGap: 6, alignItems: 'center' }}>
          {visible.map((p) => {
            const pct = total > 0 ? (p.value! / total) * 100 : 0;
            return (
              <React.Fragment key={p.name}>
                <Badge size="sm" variant="dot" color={p.color} style={{ justifySelf: 'start' }}>{partLabels[p.name] || p.name}</Badge>
                <Progress value={pct} color={p.color} size="md" radius="sm" />
                <Text size="sm" ff="monospace" ta="right" style={{ minWidth: 64 }}>{_fmtN(p.value!)}</Text>
                <Text size="xs" c="dimmed" ta="right" style={{ minWidth: 48 }}>{pct.toFixed(1)}%</Text>
              </React.Fragment>
            );
          })}
        </div>
        <Text size="xs" c="dimmed" mt={10}>
          Σ начальный раунд: <b>{_fmtN(total)}</b> токенов
          {logDetail.tool_calls_count && logDetail.tool_calls_count > 0
            ? <> · провайдер посчитал <b>{_fmtN(logDetail.prompt_tokens ?? null)}</b> суммарно по {logDetail.tool_calls_count + 1} раундам</>
            : <> ≈ {_fmtN(logDetail.prompt_tokens ?? null)} (точное от провайдера)</>}
        </Text>
      </Card>
    );
  }

  // Per-round view: bucket the round's messages_snapshot by role.
  const m = /^r(\d+)$/.exec(selectedTab);
  if (!m) return null;
  const rNum = Number(m[1]);
  const round = rounds.find((x) => x.round === rNum);
  if (!round || !round.messages_snapshot) return null;

  const roleColors: Record<string, string> = {
    system: 'indigo', user: 'green', assistant: 'blue', tool: 'teal',
  };
  const buckets = new Map<string, { count: number; chars: number; tokens: number }>();
  for (const msg of round.messages_snapshot) {
    const b = buckets.get(msg.role) || { count: 0, chars: 0, tokens: 0 };
    b.count += 1;
    b.chars += msg.chars || 0;
    b.tokens += msg.est_tokens || 0;
    buckets.set(msg.role, b);
  }
  const rows = Array.from(buckets.entries()).map(([role, b]) => ({ role, ...b }))
    .sort((a, b) => b.tokens - a.tokens);
  const totalTokens = rows.reduce((s, r) => s + r.tokens, 0);
  const totalChars = rows.reduce((s, r) => s + r.chars, 0);

  return (
    <Card withBorder padding="sm">
      <Text size="sm" fw={600} mb={8}>
        Структура prompt раунда R{rNum} <Text component="span" size="xs" c="dimmed">(по ролям, ≈ tiktoken)</Text>
      </Text>
      <div style={{ display: 'flex', width: '100%', height: 18, borderRadius: 4, overflow: 'hidden', marginBottom: 10, border: '1px solid var(--mantine-color-gray-3)' }}>
        {rows.map((r) => {
          const pct = totalTokens > 0 ? (r.tokens / totalTokens) * 100 : 0;
          const color = roleColors[r.role] || 'gray';
          return (
            <Tooltip key={r.role} label={`${r.role}: ${_fmtN(r.tokens)} ток (${pct.toFixed(1)}%)`}>
              <div style={{ width: `${pct}%`, backgroundColor: `var(--mantine-color-${color}-6)`, cursor: 'help' }} />
            </Tooltip>
          );
        })}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto auto auto', columnGap: 12, rowGap: 6, alignItems: 'center' }}>
        {rows.map((r) => {
          const pct = totalTokens > 0 ? (r.tokens / totalTokens) * 100 : 0;
          const color = roleColors[r.role] || 'gray';
          return (
            <React.Fragment key={r.role}>
              <Badge size="sm" variant="dot" color={color}>{r.role} ×{r.count}</Badge>
              <Progress value={pct} color={color} size="md" radius="sm" />
              <Text size="sm" ff="monospace" ta="right">{_fmtN(r.tokens)} ток</Text>
              <Text size="xs" c="dimmed" ta="right">{_fmtN(r.chars)} симв</Text>
              <Text size="xs" c="dimmed" ta="right" style={{ minWidth: 48 }}>{pct.toFixed(1)}%</Text>
            </React.Fragment>
          );
        })}
      </div>
      <Text size="xs" c="dimmed" mt={10}>
        Σ R{rNum}: <b>{_fmtN(totalTokens)}</b> ≈ ток · {_fmtN(totalChars)} симв · провайдер посчитал <b>{_fmtN(round.prompt_tokens)}</b> на этом раунде
      </Text>
    </Card>
  );
}

export function LogDetailView({ logDetail }: { logDetail: LLMLogDetail }) {
  const toolExecution = (logDetail.normalized_response as Record<string, unknown> | null)?.tool_execution as
    ToolExecutionEntry[] | undefined;
  const promptLayout = (logDetail.normalized_request as Record<string, unknown> | null)?.prompt_layout;
  const debug = (logDetail.debug as Record<string, unknown> | null) || null;
  const debugRounds = (debug?.rounds as DebugRound[] | undefined) || [];
  const debugToolCalls = (debug?.tool_calls as DebugToolCall[] | undefined) || [];
  const debugToolsPayload = (debug?.tools_payload as DebugToolPayloadEntry[] | undefined) || [];
  const debugUserQuery = (debug?.user_query as string | undefined) || '';
  const executionPairs = useMemo(
    () => _pairExecutionByName(toolExecution || []),
    [toolExecution],
  );
  const [openCall, setOpenCall] = useState<{ call: DebugToolCall; execution?: ToolExecutionPair } | null>(null);
  const [openPayloadTool, setOpenPayloadTool] = useState<DebugToolPayloadEntry | null>(null);
  // Round tab state lifted up so TokenBreakdownView reacts to it too.
  const [selectedRoundTab, setSelectedRoundTab] = useState<string>('summary');

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

        {debugRounds.length > 0 && (
          <RoundsView
            rounds={debugRounds}
            toolCalls={debugToolCalls}
            execution={executionPairs}
            onOpenCall={setOpenCall}
            selectedTab={selectedRoundTab}
            onTabChange={setSelectedRoundTab}
          />
        )}

        {(debugRounds.length > 0 || logDetail.tokens_user) && (
          <TokenBreakdownView
            selectedTab={selectedRoundTab}
            rounds={debugRounds}
            logDetail={logDetail}
          />
        )}

        {isPlainObject(promptLayout) && (
          <PromptStructureView
            promptLayout={promptLayout}
            toolsPayload={debugToolsPayload}
            onOpenTool={setOpenPayloadTool}
          />
        )}

        <Accordion variant="separated">
          {logDetail.debug && (
            <Accordion.Item value="debug">
              <Accordion.Control>
                <Text size="sm" fw={500} c="teal">Debug-трейс (телеметрия)</Text>
              </Accordion.Control>
              <Accordion.Panel>
                <Code block style={{ maxHeight: 500, overflow: 'auto' }}>
                  {JSON.stringify(logDetail.debug, null, 2)}
                </Code>
              </Accordion.Panel>
            </Accordion.Item>
          )}

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

      {/* Tool-call detail modal (from RoundsView click) */}
      <Modal
        opened={!!openCall}
        onClose={() => setOpenCall(null)}
        title={openCall ? `Tool: ${openCall.call.name} (R${openCall.call.round})` : ''}
        size="xl"
        scrollAreaComponent={ScrollArea.Autosize}
      >
        {openCall && (() => {
          const { call, execution } = openCall;
          const { color, label } = _toolCallColor(call);
          const formatArgs = (raw: unknown): string => {
            if (typeof raw === 'string') {
              try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw; }
            }
            return JSON.stringify(raw, null, 2);
          };
          return (
            <Stack gap="sm">
              <Group>
                <Badge color={color} size="md">{label}</Badge>
                <Text size="xs" c="dimmed">
                  Round {call.round} · {_fmtN(call.latency_ms)}ms · output {_fmtN(call.output_chars)} симв
                </Text>
              </Group>
              <Text size="xs" fw={600} c="dimmed">Arguments</Text>
              <Code block style={{ fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 250, overflow: 'auto' }}>
                {execution ? formatArgs(execution.args) : (call.args_preview || '—') + ' (preview)'}
              </Code>
              <Text size="xs" fw={600} c="dimmed">Result</Text>
              {execution
                ? <ToolResultContent value={execution.result} />
                : <Code block style={{ fontSize: 12 }}>{'(полный текст недоступен; есть только output_chars=' + _fmtN(call.output_chars) + ')'}</Code>}
            </Stack>
          );
        })()}
      </Modal>

      {/* Tool-in-payload detail modal (from PromptStructureView click) */}
      <Modal
        opened={!!openPayloadTool}
        onClose={() => setOpenPayloadTool(null)}
        title={openPayloadTool ? `Tool в payload: ${openPayloadTool.name}` : ''}
        size="xl"
        scrollAreaComponent={ScrollArea.Autosize}
      >
        {openPayloadTool && (() => {
          const t = openPayloadTool;
          const b = _payloadSourceBadge(t.source, t.similarity);

          // Per-source explanation block — what "why this tool got here" means.
          let whyBlock: React.ReactNode = null;
          if (t.source === 'semantic') {
            whyBlock = (
              <Card withBorder padding="xs" bg="var(--mantine-color-green-light)">
                <Text size="xs" fw={600} mb={4}>Почему semantic выбрал именно его</Text>
                <Text size="xs" c="dimmed" mb={4}>
                  Запрос пользователя эмбеддится и сравнивается через cosine similarity с эмбеддингом
                  каждого tool (name + description + parameter descriptions + теги + примеры). Берутся
                  top-18 по близости (`TOOL_SEMANTIC_TOPK`), затем bucket режется до бюджета модели
                  (~8 для qwen). <b>Floor отсечения по similarity у tools нет</b> — попадание зависит
                  только от ранга в top-18 и места в бюджете.
                </Text>
                <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', columnGap: 8, rowGap: 4 }}>
                  <Text size="xs" c="dimmed">similarity:</Text>
                  <Text size="xs" ff="monospace" fw={600}>
                    {t.similarity != null ? t.similarity.toFixed(3) : '—'}
                    {t.similarity != null && (
                      <Text component="span" c="dimmed" ml={6}>
                        ({t.similarity >= 0.7 ? 'сильный матч'
                          : t.similarity >= 0.5 ? 'средний'
                          : t.similarity >= 0.4 ? 'слабый'
                          : 'очень слабый — но попал по top-K'})
                      </Text>
                    )}
                  </Text>
                  <Text size="xs" c="dimmed">user query:</Text>
                  <Text size="xs" ff="monospace" style={{ wordBreak: 'break-word' }}>
                    {debugUserQuery || '—'}
                  </Text>
                </div>
                <Text size="xs" c="dimmed" mt={4}>
                  Сопоставление шло по плотному embedding'у (см. description ниже) — keywords как
                  таковых нет.
                </Text>
              </Card>
            );
          } else if (t.source === 'pinned') {
            whyBlock = (
              <Card withBorder padding="xs" bg="var(--mantine-color-grape-light)">
                <Text size="xs" fw={600} mb={4}>Pinned</Text>
                <Text size="xs" c="dimmed">
                  Закреплён админом в Tools-табе (is_pinned=true) — всегда в payload, не конкурирует с semantic за бюджет.
                </Text>
              </Card>
            );
          } else if (t.source === 'builtin') {
            whyBlock = (
              <Card withBorder padding="xs" bg="var(--mantine-color-cyan-light)">
                <Text size="xs" fw={600} mb={4}>Builtin</Text>
                <Text size="xs" c="dimmed">
                  Системный tool из builtin_registry (memory/artifacts/RAG/time). Живёт в коде, всегда добавляется поверх бюджета.
                </Text>
              </Card>
            );
          } else if (t.source.startsWith('route:')) {
            whyBlock = (
              <Card withBorder padding="xs" bg="var(--mantine-color-orange-light)">
                <Text size="xs" fw={600} mb={4}>Route: {t.source.slice(6)}</Text>
                <Text size="xs" c="dimmed">
                  Запрос совпал с доменным tool-route — выбран фиксированный набор tools под этот сценарий, без semantic.
                </Text>
              </Card>
            );
          } else if (t.source === 'keyword') {
            whyBlock = (
              <Card withBorder padding="xs" bg="var(--mantine-color-yellow-light)">
                <Text size="xs" fw={600} mb={4}>Keyword fallback</Text>
                <Text size="xs" c="dimmed">
                  Embeddings tools не оказалось / semantic не сработал. Выбор по ключевым словам description ↔ user query.
                </Text>
                {debugUserQuery && (
                  <Text size="xs" ff="monospace" mt={4}>user query: {debugUserQuery}</Text>
                )}
              </Card>
            );
          } else if (t.source === 'llm-pick') {
            whyBlock = (
              <Card withBorder padding="xs" bg="var(--mantine-color-lime-light)">
                <Text size="xs" fw={600} mb={4}>LLM-pick (последний fallback)</Text>
                <Text size="xs" c="dimmed">
                  Ни semantic, ни keyword не дали результата. Отдельный LLM-запрос выбрал tools по списку name+description.
                </Text>
              </Card>
            );
          } else if (t.source === 'non-embedded-fallback') {
            whyBlock = (
              <Card withBorder padding="xs">
                <Text size="xs" fw={600} mb={4}>Без embedding</Text>
                <Text size="xs" c="dimmed">
                  У tool нет embedding (не успели проиндексировать?). Добавлен сверху semantic-выборки, чтобы не «терять» tools тихо.
                </Text>
              </Card>
            );
          } else if (t.source === 'attachment') {
            whyBlock = (
              <Card withBorder padding="xs" bg="var(--mantine-color-violet-light)">
                <Text size="xs" fw={600} mb={4}>Attachment search</Text>
                <Text size="xs" c="dimmed">
                  Tool для поиска по конкретному вложению чата. Авто-создан на основании файлов прикреплённых к чату.
                </Text>
              </Card>
            );
          }

          return (
            <Stack gap="sm">
              <Group>
                <Badge color={b.color} size="md">{b.label}</Badge>
                <Text size="xs" c="dimmed">{b.tip}</Text>
              </Group>
              <Group>
                <Text size="xs" c="dimmed">
                  description: {_fmtN(t.description_chars)} симв · parameters: {_fmtN(t.parameters_chars)} симв
                </Text>
              </Group>
              {whyBlock}
              <Text size="xs" fw={600} c="dimmed">Description (как видит модель)</Text>
              <Code block style={{ fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 250, overflow: 'auto' }}>
                {t.description || '—'}
              </Code>
              <Text size="xs" fw={600} c="dimmed">Parameters schema</Text>
              <Code block style={{ fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 350, overflow: 'auto' }}>
                {JSON.stringify(t.parameters, null, 2)}
              </Code>
            </Stack>
          );
        })()}
      </Modal>
    </ScrollArea>
  );
}
