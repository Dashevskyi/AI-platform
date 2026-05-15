import { useMemo, useState } from 'react';
import {
  Box, Stack, Group, Text, Badge, Loader, Center, ActionIcon,
  Tooltip, Paper, Collapse, ScrollArea,
} from '@mantine/core';
import {
  IconCode, IconCopy, IconChevronRight, IconRefresh,
  IconFileText, IconBraces, IconDatabase, IconTerminal2,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useQuery } from '@tanstack/react-query';
import { useAiChatArtifacts, getAiChatApi } from '../../packages/ai-chat-core';
import type { ArtifactDetail, AuthMode } from '../../packages/ai-chat-core';

type Props = {
  tenantId: string;
  chatId: string;
  mode: 'admin' | 'end-user';
  apiBase: string;
  apiKey?: string;
  authBearer?: string;
};

const KIND_ICON: Record<string, React.ComponentType<{ size?: number; color?: string }>> = {
  'bash-script': IconTerminal2,
  'python-script': IconTerminal2,
  'sql-query': IconDatabase,
  'yaml-config': IconBraces,
  'json-config': IconBraces,
  'nginx-config': IconBraces,
  'dockerfile': IconBraces,
  'instruction': IconFileText,
  'document': IconFileText,
  'code': IconCode,
};

const KIND_COLOR: Record<string, string> = {
  'bash-script': 'green',
  'python-script': 'blue',
  'sql-query': 'orange',
  'yaml-config': 'pink',
  'json-config': 'cyan',
  'nginx-config': 'indigo',
  'dockerfile': 'grape',
  'instruction': 'gray',
  'document': 'gray',
  'code': 'gray',
};

function formatTokens(n: number): string {
  if (n < 1000) return `${n}`;
  return `${(n / 1000).toFixed(1)}k`;
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const m = Math.floor(diffMs / 60000);
  if (m < 1) return 'только что';
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} ч назад`;
  return d.toLocaleDateString();
}

export function ArtifactsPanel({
  tenantId, chatId, mode, apiBase, apiKey, authBearer,
}: Props) {
  const connection = useMemo(() => {
    if (mode === 'admin') {
      const auth: AuthMode | undefined = authBearer
        ? { type: 'bearer', token: authBearer }
        : undefined;
      return { mode: 'admin' as const, apiBase, auth };
    }
    return { mode: 'end-user' as const, apiBase, apiKey };
  }, [mode, apiBase, apiKey, authBearer]);

  const { artifacts, isLoading, refetch } = useAiChatArtifacts(tenantId, chatId, connection);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <Box style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Group justify="space-between" p="sm" style={{ borderBottom: '1px solid var(--mantine-color-default-border)' }}>
        <Group gap={6}>
          <IconCode size={16} />
          <Text size="sm" fw={500}>Артефакты чата</Text>
          {!isLoading && <Badge size="xs">{artifacts.length}</Badge>}
        </Group>
        <Tooltip label="Обновить">
          <ActionIcon variant="subtle" size="sm" onClick={() => refetch()}>
            <IconRefresh size={14} />
          </ActionIcon>
        </Tooltip>
      </Group>

      <ScrollArea style={{ flex: 1 }} p="xs">
        {isLoading ? (
          <Center py="xl"><Loader size="sm" /></Center>
        ) : artifacts.length === 0 ? (
          <Center py="xl">
            <Stack align="center" gap={6}>
              <IconCode size={32} color="var(--mantine-color-dimmed)" />
              <Text size="xs" c="dimmed" ta="center">
                Артефактов пока нет.<br/>Они появятся когда модель пришлёт скрипт, SQL или конфиг.
              </Text>
            </Stack>
          </Center>
        ) : (
          <Stack gap={6}>
            {artifacts.map((a) => (
              <ArtifactRow
                key={a.id}
                artifact={a}
                tenantId={tenantId}
                chatId={chatId}
                mode={mode}
                apiBase={apiBase}
                apiKey={apiKey}
                authBearer={authBearer}
                expanded={expandedId === a.id}
                onToggle={() => setExpandedId(expandedId === a.id ? null : a.id)}
              />
            ))}
          </Stack>
        )}
      </ScrollArea>
    </Box>
  );
}

type RowProps = Props & {
  artifact: import('../../packages/ai-chat-core').ArtifactBrief;
  expanded: boolean;
  onToggle: () => void;
};

function ArtifactRow({
  artifact, tenantId, chatId, mode, apiBase, apiKey, authBearer, expanded, onToggle,
}: RowProps) {
  const api = useMemo(() => {
    const auth: AuthMode | undefined =
      mode === 'admin'
        ? (authBearer ? { type: 'bearer', token: authBearer } : undefined)
        : (apiKey ? { type: 'apiKey', apiKey } : undefined);
    return getAiChatApi({
      variant: mode === 'admin' ? 'admin' : 'tenant',
      apiBase,
      auth,
    });
  }, [mode, apiBase, apiKey, authBearer]);

  // Lazy-fetch content only when expanded.
  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['ai-chat-core', 'artifact', tenantId, chatId, artifact.id],
    queryFn: () => api.getArtifact(tenantId, chatId, artifact.id),
    enabled: expanded,
    staleTime: 60_000,
  });

  const Icon = KIND_ICON[artifact.kind] || IconCode;
  const color = KIND_COLOR[artifact.kind] || 'gray';

  const copyContent = async (c: string) => {
    try {
      await navigator.clipboard.writeText(c);
      notifications.show({ title: 'Скопировано', message: artifact.label, color: 'green' });
    } catch {
      notifications.show({ title: 'Не удалось скопировать', message: '', color: 'red' });
    }
  };

  return (
    <Paper withBorder radius="sm" p={6}>
      <Group gap={6} wrap="nowrap" style={{ cursor: 'pointer' }} onClick={onToggle}>
        <IconChevronRight
          size={12}
          style={{
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform 0.15s ease',
          }}
        />
        <Icon size={14} color={`var(--mantine-color-${color}-7)`} />
        <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
          <Text size="xs" fw={500} truncate="end">{artifact.label}</Text>
          <Group gap={4}>
            <Badge size="xs" color={color} variant="light">{artifact.kind}</Badge>
            {artifact.lang && (
              <Badge size="xs" color="gray" variant="outline">{artifact.lang}</Badge>
            )}
            {artifact.version > 1 && (
              <Badge size="xs" color="gray">v{artifact.version}</Badge>
            )}
            <Text size="xs" c="dimmed">~{formatTokens(artifact.tokens_estimate)} tok</Text>
            <Text size="xs" c="dimmed">·</Text>
            <Text size="xs" c="dimmed">{formatRelative(artifact.created_at)}</Text>
          </Group>
        </Stack>
      </Group>
      <Collapse expanded={expanded}>
        <Box mt={6}>
          {detailLoading ? (
            <Center py="sm"><Loader size="xs" /></Center>
          ) : detail ? (
            <ContentBlock detail={detail} onCopy={() => copyContent(detail.content)} />
          ) : (
            <Text size="xs" c="dimmed">Не удалось загрузить</Text>
          )}
        </Box>
      </Collapse>
    </Paper>
  );
}

function ContentBlock({ detail, onCopy }: { detail: ArtifactDetail; onCopy: () => void }) {
  return (
    <Box>
      <Group justify="space-between" mb={4}>
        <Text size="xs" c="dimmed" ff="monospace" truncate="end" style={{ flex: 1 }}>
          id: {detail.id}
        </Text>
        <Tooltip label="Копировать содержимое">
          <ActionIcon size="xs" variant="subtle" onClick={onCopy}>
            <IconCopy size={12} />
          </ActionIcon>
        </Tooltip>
      </Group>
      <Paper
        bg="var(--mantine-color-default-hover)"
        p="xs"
        style={{ maxHeight: 400, overflow: 'auto', border: 'none' }}
      >
        <Text size="xs" ff="monospace" style={{ whiteSpace: 'pre', wordBreak: 'break-all' }}>
          {detail.content}
        </Text>
      </Paper>
    </Box>
  );
}
