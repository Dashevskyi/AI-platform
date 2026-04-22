import { useState, useEffect } from 'react';
import {
  Table,
  Button,
  Group,
  TextInput,
  Title,
  Badge,
  Stack,
  Modal,
  Textarea,
  Loader,
  Center,
  Text,
  Pagination,
  ActionIcon,
  Tooltip,
} from '@mantine/core';
import { IconPlus, IconSearch, IconTrash } from '@tabler/icons-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { notifications } from '@mantine/notifications';
import { tenantsApi } from '../shared/api/endpoints';

export function TenantListPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [newSlug, setNewSlug] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const pageSize = 20;

  useEffect(() => {
    if (searchParams.get('create') === '1') {
      setCreateOpen(true);
    }
  }, [searchParams]);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [search]);

  const { data, isLoading, error } = useQuery({
    queryKey: ['tenants', 'list', page, debouncedSearch],
    queryFn: () => tenantsApi.list(page, pageSize, debouncedSearch || undefined),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      tenantsApi.create({
        name: newName,
        slug: newSlug,
        description: newDescription || undefined,
      }),
    onSuccess: (tenant) => {
      queryClient.invalidateQueries({ queryKey: ['tenants'] });
      notifications.show({
        title: 'Тенант создан',
        message: `Тенант "${tenant.name}" успешно создан`,
        color: 'green',
      });
      setCreateOpen(false);
      setNewName('');
      setNewSlug('');
      setNewDescription('');
      navigate(`/tenants/${tenant.id}`);
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Не удалось создать тенант';
      notifications.show({ title: 'Ошибка', message, color: 'red' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => tenantsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants'] });
      notifications.show({
        title: 'Тенант удалён',
        message: 'Тенант был мягко удалён',
        color: 'yellow',
      });
    },
    onError: () => {
      notifications.show({
        title: 'Ошибка',
        message: 'Не удалось удалить тенант',
        color: 'red',
      });
    },
  });

  const totalPages = data ? Math.ceil(data.total_count / pageSize) : 0;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="center">
        <Title order={2}>Тенанты</Title>
        <Button
          leftSection={<IconPlus size={16} />}
          onClick={() => setCreateOpen(true)}
        >
          Создать тенант
        </Button>
      </Group>

      <TextInput
        placeholder="Поиск тенантов..."
        leftSection={<IconSearch size={16} />}
        value={search}
        onChange={(e) => setSearch(e.currentTarget.value)}
      />

      {isLoading ? (
        <Center py="xl">
          <Loader />
        </Center>
      ) : error ? (
        <Text c="red">Не удалось загрузить тенантов.</Text>
      ) : !data?.items.length ? (
        <Center py="xl">
          <Stack align="center" gap="sm">
            <Text c="dimmed">Тенанты не найдены.</Text>
            <Button variant="light" onClick={() => setCreateOpen(true)}>
              Создать первый тенант
            </Button>
          </Stack>
        </Center>
      ) : (
        <>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Название</Table.Th>
                <Table.Th>Slug</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Создан</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((tenant) => (
                <Table.Tr
                  key={tenant.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/tenants/${tenant.id}`)}
                >
                  <Table.Td fw={500}>{tenant.name}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed" ff="monospace">
                      {tenant.slug}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={tenant.is_active ? 'green' : 'gray'}>
                      {tenant.is_active ? 'Активный' : 'Неактивный'}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {new Date(tenant.created_at).toLocaleDateString()}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Tooltip label="Удалить тенант">
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (
                            window.confirm(
                              `Удалить тенант "${tenant.name}"? Это действие выполнит мягкое удаление.`
                            )
                          ) {
                            deleteMutation.mutate(tenant.id);
                          }
                        }}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          {totalPages > 1 && (
            <Center>
              <Pagination
                total={totalPages}
                value={page}
                onChange={setPage}
              />
            </Center>
          )}
        </>
      )}

      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Создать нового тенанта"
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
        >
          <Stack gap="md">
            <TextInput
              label="Название"
              placeholder="Моя компания"
              value={newName}
              onChange={(e) => setNewName(e.currentTarget.value)}
              required
            />
            <TextInput
              label="Slug"
              placeholder="my-company"
              value={newSlug}
              onChange={(e) => setNewSlug(e.currentTarget.value)}
              required
              description="URL-безопасный идентификатор"
            />
            <Textarea
              label="Описание"
              placeholder="Необязательное описание"
              value={newDescription}
              onChange={(e) => setNewDescription(e.currentTarget.value)}
            />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setCreateOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" loading={createMutation.isPending}>
                Создать
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
