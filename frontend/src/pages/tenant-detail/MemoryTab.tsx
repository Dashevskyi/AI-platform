import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Center,
  Group,
  Loader,
  Modal,
  NumberInput,
  Pagination,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { IconPlus, IconSearch, IconTrash, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { chatsApi, memoryApi } from '../../shared/api/endpoints';
import type { MemoryEntry, MemoryEntryCreate, MemoryEntryUpdate } from '../../shared/api/types';

type MemoryTabProps = {
  tenantId: string;
};

export function MemoryTab({ tenantId }: MemoryTabProps) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  // Debounce search input by 300ms
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [memType, setMemType] = useState('');
  const [memContent, setMemContent] = useState('');
  const [memChatId, setMemChatId] = useState<string | null>(null);
  const [memPriority, setMemPriority] = useState(0);
  const [memPinned, setMemPinned] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'memory', page, typeFilter, search],
    queryFn: () => memoryApi.list(tenantId, page, 20, typeFilter || undefined, search || undefined),
  });

  const { data: chatsData } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'admin', 1, 100],
    queryFn: () => chatsApi.listAdmin(tenantId, 1, 100),
  });

  const chatMap = new Map(
    (chatsData?.items || []).map((chat) => [
      chat.id,
      chat.title || chat.description || `Чат ${chat.id.slice(0, 8)}`,
    ]),
  );

  const chatSelectData = [
    { value: '', label: 'Глобальная (все чаты)' },
    ...(chatsData?.items || []).map((chat) => ({
      value: chat.id,
      label: chat.title || chat.description || `Чат ${chat.id.slice(0, 8)}`,
    })),
  ];

  const openCreate = () => {
    setEditId(null);
    setMemType('');
    setMemContent('');
    setMemChatId(null);
    setMemPriority(0);
    setMemPinned(false);
    setModalOpen(true);
  };

  const openEdit = (entry: MemoryEntry) => {
    setEditId(entry.id);
    setMemType(entry.memory_type);
    setMemContent(entry.content);
    setMemChatId(entry.chat_id);
    setMemPriority(entry.priority);
    setMemPinned(entry.is_pinned);
    setModalOpen(true);
  };

  const createMutation = useMutation({
    mutationFn: (data: MemoryEntryCreate) => memoryApi.create(tenantId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'memory'] });
      setModalOpen(false);
      notifications.show({ title: 'Создано', message: 'Запись памяти создана', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать запись', color: 'red' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ entryId, data }: { entryId: string; data: MemoryEntryUpdate }) =>
      memoryApi.update(tenantId, entryId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'memory'] });
      setModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Запись памяти обновлена', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось обновить запись', color: 'red' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (entryId: string) => memoryApi.delete(tenantId, entryId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'memory'] });
      notifications.show({ title: 'Удалено', message: 'Запись удалена', color: 'green' });
    },
  });

  const handleSave = () => {
    if (editId) {
      updateMutation.mutate({
        entryId: editId,
        data: {
          memory_type: memType,
          content: memContent,
          priority: memPriority,
          is_pinned: memPinned,
        },
      });
    } else {
      createMutation.mutate({
        memory_type: memType,
        content: memContent,
        chat_id: memChatId || undefined,
        priority: memPriority,
        is_pinned: memPinned,
      });
    }
  };

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group>
          <Text fw={500}>Память</Text>
          <Select
            placeholder="Фильтр по типу"
            clearable
            data={['short_term', 'long_term', 'episodic']}
            value={typeFilter}
            onChange={(value) => {
              setTypeFilter(value);
              setPage(1);
            }}
            size="sm"
            w={180}
          />
          <TextInput
            placeholder="Поиск по содержанию"
            leftSection={<IconSearch size={14} />}
            rightSection={searchInput ? (
              <ActionIcon variant="subtle" size="sm" onClick={() => setSearchInput('')}>
                <IconX size={12} />
              </ActionIcon>
            ) : null}
            value={searchInput}
            onChange={(e) => setSearchInput(e.currentTarget.value)}
            size="sm"
            w={260}
          />
          {data && (
            <Text size="xs" c="dimmed">
              Найдено: {data.total_count}
            </Text>
          )}
        </Group>
        <Button leftSection={<IconPlus size={16} />} size="sm" onClick={openCreate}>
          Добавить запись
        </Button>
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">Записей памяти нет.</Text>
      ) : (
        <>
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Тип</Table.Th>
                <Table.Th>Область</Table.Th>
                <Table.Th>Содержание</Table.Th>
                <Table.Th>Приоритет</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((entry) => (
                <Table.Tr key={entry.id} style={{ cursor: 'pointer' }} onClick={() => openEdit(entry)}>
                  <Table.Td><Badge variant="light">{entry.memory_type}</Badge></Table.Td>
                  <Table.Td>
                    {entry.chat_id ? (
                      <Badge variant="dot" size="sm" color="blue">
                        {chatMap.get(entry.chat_id) || entry.chat_id.slice(0, 8)}
                      </Badge>
                    ) : (
                      <Badge variant="dot" size="sm" color="orange">Глобальная</Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" lineClamp={2}>{entry.content}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={entry.is_pinned ? 'blue' : 'gray'}>
                      {entry.is_pinned ? 'Закреплено' : `П:${entry.priority}`}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (window.confirm('Удалить эту запись?')) {
                          deleteMutation.mutate(entry.id);
                        }
                      }}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
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

      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editId ? 'Редактировать запись памяти' : 'Создать запись памяти'}
      >
        <Text size="sm" c="dimmed" mb="md">
          Память позволяет LLM помнить факты о тенанте или чате.
          Глобальная память попадает во все чаты, привязанная к чату — только в конкретный.
        </Text>
        <Stack gap="md">
          <Select
            label="Тип памяти"
            description="short_term — текущий контекст, long_term — постоянные факты, episodic — выжимки из прошлых сессий"
            data={['short_term', 'long_term', 'episodic']}
            value={memType}
            onChange={(value) => setMemType(value || '')}
            required
            allowDeselect={false}
          />
          {!editId && (
            <Select
              label="Область действия"
              description="Глобальная — попадает во все чаты тенанта. Конкретный чат — только в выбранный."
              data={chatSelectData}
              value={memChatId || ''}
              onChange={(value) => setMemChatId(value || null)}
              searchable
              clearable
            />
          )}
          {editId && (
            <TextInput
              label="Область действия"
              value={memChatId ? (chatMap.get(memChatId) || memChatId.slice(0, 8)) : 'Глобальная (все чаты)'}
              disabled
            />
          )}
          <Textarea
            label="Содержание"
            description="Текст записи, например: 'Клиент предпочитает общение на русском языке'"
            placeholder="Клиент использует тариф «Бизнес 100»"
            value={memContent}
            onChange={(e) => setMemContent(e.currentTarget.value)}
            autosize
            minRows={3}
            maxRows={12}
            styles={{ input: { resize: 'vertical', whiteSpace: 'pre-wrap' } }}
            required
          />
          <NumberInput
            label="Приоритет"
            description="Чем выше число, тем раньше запись попадёт в контекст (0 — обычный)"
            value={memPriority}
            onChange={(value) => setMemPriority(Number(value))}
          />
          <Switch
            label="Закреплено"
            description="Закреплённые записи всегда включаются в контекст"
            checked={memPinned}
            onChange={(e) => setMemPinned(e.currentTarget.checked)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setModalOpen(false)}>Отмена</Button>
            <Button onClick={handleSave} loading={createMutation.isPending || updateMutation.isPending}>
              {editId ? 'Обновить' : 'Создать'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
