import { useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Center,
  Checkbox,
  Group,
  Loader,
  Modal,
  PasswordInput,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconPencil, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { adminUsersApi } from '../../shared/api/endpoints';
import type { AdminUserListItem } from '../../shared/api/types';
import {
  ALL_PERMISSIONS,
  PERMISSION_LABELS,
  usePermissions,
} from '../../shared/hooks/usePermissions';
import type { Permission } from '../../shared/hooks/usePermissions';

type UsersTabProps = {
  tenantId: string;
};

export function UsersTab({ tenantId }: UsersTabProps) {
  const queryClient = useQueryClient();
  const { isSuperadmin, permissions: ownPerms } = usePermissions();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<AdminUserListItem | null>(null);
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [perms, setPerms] = useState<Permission[]>([]);
  const [active, setActive] = useState(true);

  const grantablePerms: Permission[] = isSuperadmin
    ? ALL_PERMISSIONS
    : ALL_PERMISSIONS.filter((p) => ownPerms.includes(p));

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'users'],
    queryFn: () => adminUsersApi.list(tenantId, 1, 100),
  });

  const createMut = useMutation({
    mutationFn: () =>
      adminUsersApi.create(tenantId, {
        login: login.trim(),
        password,
        role: 'tenant_admin',
        permissions: perms,
        is_active: active,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'users'] });
      setModalOpen(false);
      notifications.show({ title: 'Создано', message: 'Пользователь добавлен', color: 'green' });
    },
    onError: (e: Error & { response?: { data?: { detail?: string } } }) => {
      notifications.show({
        title: 'Ошибка',
        message: e.response?.data?.detail || e.message || 'Не удалось создать пользователя',
        color: 'red',
      });
    },
  });

  const updateMut = useMutation({
    mutationFn: () => {
      if (!editing) throw new Error('No user to update');
      return adminUsersApi.update(tenantId, editing.id, {
        password: password ? password : undefined,
        permissions: perms,
        is_active: active,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'users'] });
      setModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Пользователь обновлён', color: 'green' });
    },
    onError: (e: Error & { response?: { data?: { detail?: string } } }) => {
      notifications.show({
        title: 'Ошибка',
        message: e.response?.data?.detail || e.message || 'Не удалось обновить пользователя',
        color: 'red',
      });
    },
  });

  const deleteMut = useMutation({
    mutationFn: (userId: string) => adminUsersApi.delete(tenantId, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'users'] });
      notifications.show({ title: 'Удалено', message: 'Пользователь удалён', color: 'green' });
    },
    onError: (e: Error & { response?: { data?: { detail?: string } } }) => {
      notifications.show({
        title: 'Ошибка',
        message: e.response?.data?.detail || 'Не удалось удалить',
        color: 'red',
      });
    },
  });

  const openCreate = () => {
    setEditing(null);
    setLogin('');
    setPassword('');
    setPerms([]);
    setActive(true);
    setModalOpen(true);
  };

  const openEdit = (u: AdminUserListItem) => {
    setEditing(u);
    setLogin(u.login);
    setPassword('');
    setPerms((u.permissions || []).filter((p): p is Permission => ALL_PERMISSIONS.includes(p as Permission)));
    setActive(u.is_active);
    setModalOpen(true);
  };

  const togglePerm = (p: Permission) => {
    setPerms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));
  };

  const handleSave = () => {
    if (!editing) {
      if (!login.trim() || !password) {
        notifications.show({ title: 'Ошибка', message: 'Логин и пароль обязательны', color: 'red' });
        return;
      }
      createMut.mutate();
    } else {
      updateMut.mutate();
    }
  };

  if (isLoading) {
    return <Center py="md"><Loader /></Center>;
  }

  const items = data?.items || [];

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>Пользователи тенанта</Text>
        <Button leftSection={<IconPlus size={14} />} onClick={openCreate}>
          Добавить пользователя
        </Button>
      </Group>

      {items.length === 0 ? (
        <Text c="dimmed">Пользователей пока нет.</Text>
      ) : (
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Логин</Table.Th>
              <Table.Th>Роль</Table.Th>
              <Table.Th>Права</Table.Th>
              <Table.Th>Статус</Table.Th>
              <Table.Th>Создан</Table.Th>
              <Table.Th w={100}></Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((u) => (
              <Table.Tr key={u.id}>
                <Table.Td><Text ff="monospace">{u.login}</Text></Table.Td>
                <Table.Td><Badge color={u.role === 'superadmin' ? 'red' : 'blue'}>{u.role}</Badge></Table.Td>
                <Table.Td>
                  <Group gap={4}>
                    {(u.permissions || []).map((p) => (
                      <Badge key={p} variant="light" size="xs">{PERMISSION_LABELS[p as Permission] || p}</Badge>
                    ))}
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Badge color={u.is_active ? 'green' : 'gray'}>
                    {u.is_active ? 'Активен' : 'Отключён'}
                  </Badge>
                </Table.Td>
                <Table.Td><Text size="sm" c="dimmed">{new Date(u.created_at).toLocaleDateString()}</Text></Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Tooltip label="Редактировать">
                      <ActionIcon variant="subtle" onClick={() => openEdit(u)}>
                        <IconPencil size={14} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Удалить">
                      <ActionIcon variant="subtle" color="red" onClick={() => {
                        if (confirm(`Удалить пользователя ${u.login}?`)) deleteMut.mutate(u.id);
                      }}>
                        <IconTrash size={14} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editing ? `Редактирование пользователя «${editing.login}»` : 'Новый пользователь'}
        size="md"
      >
        <Stack gap="sm">
          <TextInput
            label="Логин"
            value={login}
            onChange={(e) => setLogin(e.currentTarget.value)}
            disabled={!!editing}
            required
          />
          <PasswordInput
            label={editing ? 'Новый пароль (оставь пустым, чтобы не менять)' : 'Пароль'}
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
            required={!editing}
          />
          <Switch
            label="Активен"
            checked={active}
            onChange={(e) => setActive(e.currentTarget.checked)}
          />
          <Text size="sm" fw={500}>Права:</Text>
          <Stack gap={4}>
            {grantablePerms.map((p) => (
              <Checkbox
                key={p}
                checked={perms.includes(p)}
                onChange={() => togglePerm(p)}
                label={`${PERMISSION_LABELS[p]} (${p})`}
              />
            ))}
            {grantablePerms.length === 0 && (
              <Text size="xs" c="dimmed">Нет доступных прав для назначения.</Text>
            )}
          </Stack>
          <Group justify="flex-end" gap="xs">
            <Button variant="default" onClick={() => setModalOpen(false)}>Отмена</Button>
            <Button
              onClick={handleSave}
              loading={createMut.isPending || updateMut.isPending}
            >
              {editing ? 'Сохранить' : 'Создать'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
