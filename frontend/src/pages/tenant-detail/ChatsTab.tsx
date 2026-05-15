import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Center, Group, Loader, Pagination, Stack, Table, Text, Badge } from '@mantine/core';
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

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'admin', page],
    queryFn: () => chatsApi.listAdmin(tenantId, page),
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
      <Table.Td fw={500}>{chat.title}</Table.Td>
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
      <Table.Td>{new Date(chat.created_at).toLocaleString()}</Table.Td>
    </Table.Tr>
  ));

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>Чаты</Text>
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
