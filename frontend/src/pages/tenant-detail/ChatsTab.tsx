import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Button, Center, Group, Loader, Pagination, Select, Stack, Table, Text, TextInput, Tooltip } from '@mantine/core';
import { IconPlus } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { chatsApi, keysApi } from '../../shared/api/endpoints';
import type { Chat } from '../../shared/api/types';

type ChatsTabProps = {
  tenantId: string;
};

export function ChatsTab({ tenantId }: ChatsTabProps) {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'admin', page, search, statusFilter],
    queryFn: () => chatsApi.listAdmin(tenantId, page, 20, {
      search: search || undefined,
      status: statusFilter || undefined,
    }),
  });
  const { data: keysData } = useQuery({
    queryKey: ['tenants', tenantId, 'keys', 'all-for-chat-groups'],
    queryFn: () => keysApi.list(tenantId, 1, 100),
  });

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;
  const hasApiKeys = (keysData?.items?.length || 0) > 0;
  const keyNameById = new Map((keysData?.items || []).map((key) => [key.id, key.name]));
  const groupedChats = hasApiKeys
    ? (data?.items || []).reduce<Record<string, Chat[]>>((acc, chat) => {
        const groupKey = chat.api_key_id || '__no_key__';
        if (!acc[groupKey]) {
          acc[groupKey] = [];
        }
        acc[groupKey].push(chat);
        return acc;
      }, {})
    : null;
  const groupedEntries = groupedChats ? Object.entries(groupedChats) : [];
  const renderChatRows = (items: Chat[]) => items.map((chat) => (
    <Table.Tr
      key={chat.id}
      style={{ cursor: 'pointer' }}
      onClick={() => navigate(`/tenants/${tenantId}/chat/${chat.id}`)}
    >
      <Table.Td fw={500}>
        <Group gap={6} wrap="nowrap">
          <Text size="sm" fw={500} lineClamp={1}>{chat.title || '(без названия)'}</Text>
          {chat.flagged_issue && (
            <Tooltip label={chat.flagged_issue} multiline w={280}>
              <Badge size="xs" color="orange" variant="light">⚑</Badge>
            </Tooltip>
          )}
        </Group>
      </Table.Td>
      <Table.Td>
        <Text size="sm" c="dimmed" lineClamp={1}>
          {chat.description || '-'}
        </Text>
      </Table.Td>
      <Table.Td>
        <Badge
          color={
            chat.status === 'active'
              ? 'green'
              : chat.status === 'closed'
                ? 'gray'
                : 'blue'
          }
        >
          {chat.status}
        </Badge>
      </Table.Td>
      <Table.Td><Text size="sm">{chat.message_count ?? '-'}</Text></Table.Td>
      <Table.Td>{new Date(chat.created_at).toLocaleString()}</Table.Td>
    </Table.Tr>
  ));

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group gap="xs">
          <Text fw={500}>Чаты</Text>
          <TextInput
            placeholder="Поиск по названию…"
            size="xs"
            w={220}
            value={searchInput}
            onChange={(e) => setSearchInput(e.currentTarget.value)}
          />
          <Select
            placeholder="Статус"
            clearable
            size="xs"
            w={140}
            value={statusFilter}
            onChange={(v) => { setStatusFilter(v); setPage(1); }}
            data={[
              { value: 'active', label: 'Активные' },
              { value: 'closed', label: 'Закрытые' },
              { value: 'archived', label: 'Архив' },
            ]}
          />
        </Group>
        <Button
          leftSection={<IconPlus size={16} />}
          size="sm"
          onClick={() => navigate(`/tenants/${tenantId}/chat`)}
        >
          Открыть интерфейс чата
        </Button>
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">Чатов пока нет.</Text>
      ) : (
        <>
          {hasApiKeys ? (
            <Stack gap="lg">
              {groupedEntries.map(([groupKey, chats]) => (
                <Stack key={groupKey} gap="xs">
                  <Group gap="xs">
                    <Text fw={600}>
                      {groupKey === '__no_key__'
                        ? 'Без API ключа'
                        : keyNameById.get(groupKey) || `Ключ ${groupKey.slice(0, 8)}`}
                    </Text>
                    <Badge variant="light">{chats.length}</Badge>
                  </Group>
                  <Table striped highlightOnHover>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Заголовок</Table.Th>
                        <Table.Th>Описание</Table.Th>
                        <Table.Th>Статус</Table.Th>
                        <Table.Th>Сообщений</Table.Th>
                        <Table.Th>Создан</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>{renderChatRows(chats)}</Table.Tbody>
                  </Table>
                </Stack>
              ))}
            </Stack>
          ) : (
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Заголовок</Table.Th>
                  <Table.Th>Описание</Table.Th>
                  <Table.Th>Статус</Table.Th>
                  <Table.Th>Сообщений</Table.Th>
                  <Table.Th>Создан</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>{renderChatRows(data.items)}</Table.Tbody>
            </Table>
          )}
          {totalPages > 1 && (
            <Center><Pagination total={totalPages} value={page} onChange={setPage} /></Center>
          )}
        </>
      )}
    </Stack>
  );
}
