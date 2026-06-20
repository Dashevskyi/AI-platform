import { useEffect, useRef, useState } from 'react';
import {
  Modal,
  Stack,
  Group,
  Text,
  Textarea,
  Button,
  ScrollArea,
  Paper,
  Badge,
  Code,
  Loader,
  Alert,
  ActionIcon,
  Tooltip,
  Collapse,
  TextInput,
  Switch,
  Box,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import {
  IconSend,
  IconRobot,
  IconUser,
  IconTool,
  IconChevronDown,
  IconChevronRight,
  IconWand,
  IconAlertCircle,
  IconRefresh,
} from '@tabler/icons-react';
import { isAxiosError } from 'axios';
import {
  toolBuilderApi,
  type ToolBuilderMessage,
  type ToolBuilderTraceStep,
  type ToolBuilderProposal,
} from '../../shared/api/endpoints';
import { MarkdownContent } from '../../shared/ui/MarkdownContent';

interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  trace?: ToolBuilderTraceStep[];
  proposed?: ToolBuilderProposal | null;
}

const TOOL_LABELS: Record<string, string> = {
  list_data_sources: 'источники данных',
  list_tables: 'таблицы',
  list_columns: 'колонки',
  dry_run_sql: 'пробный SQL',
  propose_tool: 'предложение инструмента',
};

function errMessage(e: unknown): string {
  if (isAxiosError(e)) {
    const d = e.response?.data as { detail?: string } | undefined;
    return d?.detail || e.message;
  }
  return e instanceof Error ? e.message : 'Неизвестная ошибка';
}

function TraceBlock({ trace }: { trace: ToolBuilderTraceStep[] }) {
  const [open, setOpen] = useState(false);
  if (!trace.length) return null;
  return (
    <Box mt={6}>
      <Group gap={4} style={{ cursor: 'pointer' }} onClick={() => setOpen((o) => !o)}>
        {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        <IconTool size={13} />
        <Text size="xs" c="dimmed">
          Действия агента: {trace.map((t) => TOOL_LABELS[t.tool] || t.tool).join(' → ')}
        </Text>
      </Group>
      <Collapse expanded={open}>
        <Stack gap={4} mt={4} pl={18}>
          {trace.map((t, i) => (
            <Paper key={i} withBorder p={6} bg="dark.8" radius="sm">
              <Group gap={6} mb={2}>
                <Badge size="xs" variant="light" color="grape">
                  {t.tool}
                </Badge>
                {Object.keys(t.args || {}).length > 0 && (
                  <Code style={{ fontSize: 10 }}>{JSON.stringify(t.args)}</Code>
                )}
              </Group>
              <Text size="xs" c="dimmed" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {t.result_preview}
              </Text>
            </Paper>
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
}

function ProposalCard({
  tenantId,
  proposal,
  onCreated,
}: {
  tenantId: string;
  proposal: ToolBuilderProposal;
  onCreated: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(proposal.name || '');
  const [description, setDescription] = useState(proposal.description || '');
  const [active, setActive] = useState(false);
  const [showJson, setShowJson] = useState(false);
  const [created, setCreated] = useState(false);

  const createMut = useMutation({
    mutationFn: () =>
      toolBuilderApi.create(tenantId, {
        name: name.trim(),
        description: description.trim() || null,
        config_json: proposal.config_json,
        is_active: active,
      }),
    onSuccess: (res) => {
      setCreated(true);
      notifications.show({
        title: 'Инструмент создан',
        message: `«${res.name}» ${res.is_active ? 'включён' : 'создан выключенным — включите после проверки'}`,
        color: 'green',
      });
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'tools'] });
      onCreated();
    },
    onError: (e) =>
      notifications.show({ title: 'Ошибка', message: errMessage(e), color: 'red' }),
  });

  return (
    <Paper withBorder p="sm" radius="md" mt={6} bg="dark.6">
      <Group gap={6} mb="xs">
        <IconWand size={16} color="var(--mantine-color-teal-4)" />
        <Text fw={600} size="sm">
          Предложенный инструмент
        </Text>
      </Group>
      <Stack gap="xs">
        <TextInput
          label="Machine-имя"
          size="xs"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          disabled={created}
        />
        <Textarea
          label="Описание (для модели)"
          size="xs"
          autosize
          minRows={2}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
          disabled={created}
        />
        <Group gap={4} style={{ cursor: 'pointer' }} onClick={() => setShowJson((s) => !s)}>
          {showJson ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
          <Text size="xs" c="dimmed">
            config_json ({Object.keys(proposal.config_json || {}).length} полей)
          </Text>
        </Group>
        <Collapse expanded={showJson}>
          <ScrollArea.Autosize mah={260}>
            <Code block style={{ fontSize: 11 }}>
              {JSON.stringify(proposal.config_json, null, 2)}
            </Code>
          </ScrollArea.Autosize>
        </Collapse>
        <Group justify="space-between" mt={4}>
          <Switch
            size="xs"
            label="Включить сразу"
            checked={active}
            onChange={(e) => setActive(e.currentTarget.checked)}
            disabled={created}
          />
          <Button
            size="xs"
            color="teal"
            leftSection={<IconWand size={14} />}
            loading={createMut.isPending}
            disabled={created || !name.trim()}
            onClick={() => createMut.mutate()}
          >
            {created ? 'Создан' : 'Создать инструмент'}
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

export function ToolBuilderModal({
  tenantId,
  opened,
  onClose,
}: {
  tenantId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);

  const chatMut = useMutation({
    mutationFn: (history: ToolBuilderMessage[]) => toolBuilderApi.chat(tenantId, history),
    onSuccess: (res) => {
      setTurns((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: res.reply,
          trace: res.trace,
          proposed: res.proposed,
        },
      ]);
    },
    onError: (e) => {
      setError(errMessage(e));
      // Roll back the optimistic user turn so the history stays sendable.
      setTurns((prev) => (prev.length && prev[prev.length - 1].role === 'user' ? prev.slice(0, -1) : prev));
    },
  });

  // Auto-scroll to the latest message.
  useEffect(() => {
    const el = viewportRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [turns, chatMut.isPending]);

  const reset = () => {
    setTurns([]);
    setInput('');
    setError(null);
  };

  const send = () => {
    const text = input.trim();
    if (!text || chatMut.isPending) return;
    setError(null);
    const next: ChatTurn[] = [...turns, { role: 'user', content: text }];
    setTurns(next);
    setInput('');
    chatMut.mutate(next.map((t) => ({ role: t.role, content: t.content })));
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      size="xl"
      title={
        <Group gap={8}>
          <IconRobot size={20} />
          <Text fw={600}>Конструктор инструментов (агент)</Text>
        </Group>
      }
      styles={{ body: { display: 'flex', flexDirection: 'column', height: '70vh' } }}
    >
      <Stack gap="xs" style={{ flex: 1, minHeight: 0 }}>
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            Опишите словами, какой инструмент нужен. Агент сам посмотрит реальную схему БД,
            проверит SQL и предложит готовый конфиг.
          </Text>
          {turns.length > 0 && (
            <Tooltip label="Начать заново">
              <ActionIcon variant="subtle" color="gray" onClick={reset}>
                <IconRefresh size={16} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>

        <ScrollArea style={{ flex: 1 }} viewportRef={viewportRef}>
          <Stack gap="sm" p={4}>
            {turns.length === 0 && (
              <Paper withBorder p="md" radius="md" bg="dark.7">
                <Text size="sm" c="dimmed">
                  Например:{' '}
                  <Text span fs="italic">
                    «Создай поиск запитки на свиче по его имени — из БД мониторинга (250)»
                  </Text>{' '}
                  или{' '}
                  <Text span fs="italic">
                    «Нужен инструмент: найти абонента по номеру договора»
                  </Text>
                  .
                </Text>
              </Paper>
            )}
            {turns.map((t, i) => (
              <Group key={i} align="flex-start" gap={8} wrap="nowrap">
                <Box pt={2}>
                  {t.role === 'user' ? (
                    <IconUser size={18} color="var(--mantine-color-blue-4)" />
                  ) : (
                    <IconRobot size={18} color="var(--mantine-color-teal-4)" />
                  )}
                </Box>
                <Box style={{ flex: 1, minWidth: 0 }}>
                  {t.role === 'user' ? (
                    <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                      {t.content}
                    </Text>
                  ) : (
                    <>
                      {t.content && <MarkdownContent content={t.content} />}
                      {t.trace && t.trace.length > 0 && <TraceBlock trace={t.trace} />}
                      {t.proposed && (
                        <ProposalCard tenantId={tenantId} proposal={t.proposed} onCreated={() => {}} />
                      )}
                    </>
                  )}
                </Box>
              </Group>
            ))}
            {chatMut.isPending && (
              <Group gap={8}>
                <IconRobot size={18} color="var(--mantine-color-teal-4)" />
                <Loader size="xs" />
                <Text size="xs" c="dimmed">
                  Агент изучает схему и проверяет запрос…
                </Text>
              </Group>
            )}
          </Stack>
        </ScrollArea>

        {error && (
          <Alert color="red" icon={<IconAlertCircle size={16} />} py={6} onClose={() => setError(null)} withCloseButton>
            {error}
          </Alert>
        )}

        <Group align="flex-end" gap="xs">
          <Textarea
            style={{ flex: 1 }}
            placeholder="Опишите нужный инструмент… (Ctrl+Enter — отправить)"
            autosize
            minRows={1}
            maxRows={4}
            value={input}
            onChange={(e) => setInput(e.currentTarget.value)}
            onKeyDown={onKeyDown}
            disabled={chatMut.isPending}
          />
          <Button
            leftSection={<IconSend size={16} />}
            onClick={send}
            loading={chatMut.isPending}
            disabled={!input.trim()}
          >
            Отправить
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
