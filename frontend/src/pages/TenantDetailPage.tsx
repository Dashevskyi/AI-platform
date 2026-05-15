import { useEffect, useState, Fragment } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Tabs,
  Title,
  Group,
  Button,
  TextInput,
  Textarea,
  Switch,
  Select,
  MultiSelect,
  TagsInput,
  NumberInput,
  Table,
  Badge,
  Modal,
  Stack,
  Text,
  Loader,
  Center,
  Card,
  Alert,
  ActionIcon,
  Tooltip,
  Pagination,
  Code,
  CopyButton,
  SimpleGrid,
  Autocomplete,
  Checkbox,
} from '@mantine/core';
import {
  IconPlus,
  IconTrash,
  IconRefresh,
  IconPlayerStop,
  IconArrowLeft,
  IconAlertCircle,
  IconCopy,
  IconCheck,
  IconEdit,
  IconGripVertical,
  IconTool,
  IconTerminal2,
  IconNetwork,
  IconDatabase,
  IconApi,
  IconWorldWww,
  IconRouter,
} from '@tabler/icons-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import {
  tenantsApi,
  keysApi,
  keyGroupsApi,
  toolsApi,
  dataSourcesApi,
} from '../shared/api/endpoints';
import type {
  TenantApiKey,
  TenantApiKeyGroup,
  Tool,
  ToolCreate,
  ToolUpdate,
} from '../shared/api/types';
import { GeneralTab } from './tenant-detail/GeneralTab';
import { ChatsTab } from './tenant-detail/ChatsTab';
import { DataSourcesTab } from './tenant-detail/DataSourcesTab';
import { KBTab } from './tenant-detail/KBTab';
import { LogsTab } from './tenant-detail/LogsTab';
import { MemoryTab } from './tenant-detail/MemoryTab';
import { ModelConfigTab } from './tenant-detail/ModelConfigTab';
import { ShellSettingsTab } from './tenant-detail/ShellSettingsTab';
import { StatsTab } from './TenantStatsTab';
import { ApiInfoTab } from './TenantApiInfoTab';
import { UsersTab } from './tenant-detail/UsersTab';
import { usePermissions } from '../shared/hooks/usePermissions';

type ToolPreset = {
  label: string;
  description: string;
  tags: string[];
  all?: boolean;
  none?: boolean;
};

const TOOL_PERMISSION_PRESETS: ToolPreset[] = [
  {
    label: 'Все tools',
    description: 'Полный доступ без ограничений.',
    tags: [],
    all: true,
  },
  {
    label: 'Без tools',
    description: 'Полный запрет использования tools.',
    tags: [],
    none: true,
  },
  {
    label: 'Сеть',
    description: 'Проверка доступности и диагностика сети.',
    tags: ['network', 'diagnostics'],
  },
  {
    label: 'Поиск данных',
    description: 'Поиск по БД и API-источникам tenant-а.',
    tags: ['data_search', 'db_search', 'api_search', 'records'],
  },
  {
    label: 'Биллинг',
    description: 'Платежи и начисления.',
    tags: ['billing', 'payments'],
  },
];

function readToolCapabilityTags(config: Record<string, unknown> | null | undefined): string[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  const tags = Array.isArray(runtime.capability_tags) ? runtime.capability_tags : [];
  return tags.map((tag) => String(tag).trim()).filter(Boolean);
}

function applyToolCapabilityTags(
  runtime: Record<string, unknown>,
  tags: string[],
): Record<string, unknown> {
  const next = { ...runtime };
  const normalized = Array.from(new Set(tags.map((tag) => tag.trim()).filter(Boolean)));
  if (normalized.length > 0) {
    next.capability_tags = normalized;
  } else if ('capability_tags' in next) {
    delete next.capability_tags;
  }
  return next;
}

export function TenantDetailPage() {
  const { id } = useParams<{ id: string }>();
  const tenantId = id!;
  const navigate = useNavigate();
  const { isSuperadmin, has } = usePermissions();

  const { data: tenant, isLoading } = useQuery({
    queryKey: ['tenants', tenantId],
    queryFn: () => tenantsApi.get(tenantId),
    enabled: !!tenantId,
  });

  if (isLoading) {
    return (
      <Center py="xl">
        <Loader />
      </Center>
    );
  }

  if (!tenant) {
    return (
      <Center py="xl">
        <Text c="red">Тенант не найден.</Text>
      </Center>
    );
  }

  return (
    <Stack gap="lg">
      <Group>
        {isSuperadmin && (
          <ActionIcon variant="subtle" onClick={() => navigate('/tenants')}>
            <IconArrowLeft size={20} />
          </ActionIcon>
        )}
        <Title order={2}>{tenant.name}</Title>
        <Badge color={tenant.is_active ? 'green' : 'gray'}>
          {tenant.is_active ? 'Активный' : 'Неактивный'}
        </Badge>
      </Group>

      <Tabs defaultValue="general" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="general">Общее</Tabs.Tab>
          {has('keys') && <Tabs.Tab value="keys">API Ключи</Tabs.Tab>}
          {has('model_config') && <Tabs.Tab value="model">Модель</Tabs.Tab>}
          {has('shell_config') && <Tabs.Tab value="shell">Настройки оболочки</Tabs.Tab>}
          {has('data_sources') && <Tabs.Tab value="data-sources">Источники данных</Tabs.Tab>}
          {has('tools') && <Tabs.Tab value="tools">Инструменты</Tabs.Tab>}
          {has('kb') && <Tabs.Tab value="kb">База знаний</Tabs.Tab>}
          {has('memory') && <Tabs.Tab value="memory">Память</Tabs.Tab>}
          {has('chats') && <Tabs.Tab value="chats">Чаты</Tabs.Tab>}
          {has('logs') && <Tabs.Tab value="logs">Логи</Tabs.Tab>}
          {isSuperadmin && <Tabs.Tab value="stats">Статистика</Tabs.Tab>}
          {has('users') && <Tabs.Tab value="users">Пользователи</Tabs.Tab>}
          {isSuperadmin && <Tabs.Tab value="api-info">API</Tabs.Tab>}
        </Tabs.List>

        <Tabs.Panel value="general" pt="md">
          <GeneralTab tenantId={tenantId} />
        </Tabs.Panel>
        {has('keys') && (
          <Tabs.Panel value="keys" pt="md">
            <ApiKeysTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('model_config') && (
          <Tabs.Panel value="model" pt="md">
            <ModelConfigTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('shell_config') && (
          <Tabs.Panel value="shell" pt="md">
            <ShellSettingsTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('data_sources') && (
          <Tabs.Panel value="data-sources" pt="md">
            <DataSourcesTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('tools') && (
          <Tabs.Panel value="tools" pt="md">
            <ToolsTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('kb') && (
          <Tabs.Panel value="kb" pt="md">
            <KBTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('memory') && (
          <Tabs.Panel value="memory" pt="md">
            <MemoryTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('chats') && (
          <Tabs.Panel value="chats" pt="md">
            <ChatsTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('logs') && (
          <Tabs.Panel value="logs" pt="md">
            <LogsTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {isSuperadmin && (
          <Tabs.Panel value="stats" pt="md">
            <StatsTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('users') && (
          <Tabs.Panel value="users" pt="md">
            <UsersTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {isSuperadmin && (
          <Tabs.Panel value="api-info" pt="md">
            <ApiInfoTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
      </Tabs>
    </Stack>
  );
}

// ===== API KEYS TAB =====

function ApiKeysTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [keyName, setKeyName] = useState('');
  const [keyGroupId, setKeyGroupId] = useState<string | null>(null);
  const [keyMemoryPrompt, setKeyMemoryPrompt] = useState('');
  const [keyAllowedToolsRestricted, setKeyAllowedToolsRestricted] = useState(false);
  const [keyAllowedToolIds, setKeyAllowedToolIds] = useState<string[]>([]);
  const [editKey, setEditKey] = useState<TenantApiKey | null>(null);
  const [rawKey, setRawKey] = useState('');
  const [rawKeyModalOpen, setRawKeyModalOpen] = useState(false);
  const [groupModalOpen, setGroupModalOpen] = useState(false);
  const [editGroup, setEditGroup] = useState<TenantApiKeyGroup | null>(null);
  const [groupName, setGroupName] = useState('');
  const [groupMemoryPrompt, setGroupMemoryPrompt] = useState('');
  const [groupAllowedToolsRestricted, setGroupAllowedToolsRestricted] = useState(false);
  const [groupAllowedToolIds, setGroupAllowedToolIds] = useState<string[]>([]);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'keys', page],
    queryFn: () => keysApi.list(tenantId, page),
  });

  const { data: groupsData } = useQuery({
    queryKey: ['tenants', tenantId, 'key-groups'],
    queryFn: () => keyGroupsApi.list(tenantId, 1, 100),
  });
  const { data: toolsData } = useQuery({
    queryKey: ['tenants', tenantId, 'tools', 'for-api-key-permissions'],
    queryFn: () => toolsApi.list(tenantId, 1, 100),
  });

  const groups = groupsData?.items || [];
  const tools = toolsData?.items || [];
  const groupNameById = new Map(groups.map((group) => [group.id, group.name]));
  const groupOptions = [
    { value: '', label: 'Без группы' },
    ...groups.map((group) => ({ value: group.id, label: group.name })),
  ];
  const toolOptions = tools.map((tool) => ({ value: tool.id, label: tool.name }));

  const normalizePermissionList = (value: string[] | null | undefined) => {
    if (value === undefined || value === null) {
      return { restricted: false, ids: [] as string[] };
    }
    return { restricted: true, ids: value };
  };

  const applyToolPreset = (
    preset: ToolPreset,
    setRestricted: (value: boolean) => void,
    setIds: (value: string[]) => void,
  ) => {
    if (preset.all) {
      setRestricted(false);
      setIds([]);
      return;
    }
    if (preset.none) {
      setRestricted(true);
      setIds([]);
      return;
    }
    const selectedIds = tools
      .filter((tool) => {
        const toolTags = readToolCapabilityTags(tool.config_json);
        return toolTags.some((tag) => preset.tags.includes(tag));
      })
      .map((tool) => tool.id);
    setRestricted(true);
    setIds(selectedIds);
  };

  const createMutation = useMutation({
    mutationFn: () => keysApi.create(tenantId, {
      name: keyName,
      group_id: keyGroupId || undefined,
      memory_prompt: keyMemoryPrompt || undefined,
      allowed_tool_ids: keyAllowedToolsRestricted ? keyAllowedToolIds : null,
    }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      setCreateOpen(false);
      setKeyName('');
      setKeyGroupId(null);
      setKeyMemoryPrompt('');
      setKeyAllowedToolsRestricted(false);
      setKeyAllowedToolIds([]);
      setRawKey(result.raw_key);
      setRawKeyModalOpen(true);
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать ключ', color: 'red' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ keyId, payload }: { keyId: string; payload: Record<string, unknown> }) =>
      keysApi.update(tenantId, keyId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      setEditKey(null);
      setKeyName('');
      setKeyGroupId(null);
      setKeyMemoryPrompt('');
      setKeyAllowedToolsRestricted(false);
      setKeyAllowedToolIds([]);
      notifications.show({ title: 'Обновлено', message: 'API ключ обновлён', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось обновить ключ', color: 'red' });
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: (keyId: string) => keysApi.deactivate(tenantId, keyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      notifications.show({ title: 'Ключ деактивирован', message: '', color: 'yellow' });
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (keyId: string) => keysApi.rotate(tenantId, keyId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      setRawKey(result.raw_key);
      setRawKeyModalOpen(true);
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось ротировать ключ', color: 'red' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (keyId: string) => keysApi.delete(tenantId, keyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      notifications.show({ title: 'Ключ удалён', message: '', color: 'green' });
    },
  });

  const createGroupMutation = useMutation({
    mutationFn: () => keyGroupsApi.create(tenantId, {
      name: groupName,
      memory_prompt: groupMemoryPrompt || undefined,
      allowed_tool_ids: groupAllowedToolsRestricted ? groupAllowedToolIds : null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'key-groups'] });
      setGroupModalOpen(false);
      setGroupName('');
      setGroupMemoryPrompt('');
      setGroupAllowedToolsRestricted(false);
      setGroupAllowedToolIds([]);
      notifications.show({ title: 'Создано', message: 'Группа ключей создана', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать группу', color: 'red' });
    },
  });

  const updateGroupMutation = useMutation({
    mutationFn: ({ groupId, payload }: { groupId: string; payload: Record<string, unknown> }) =>
      keyGroupsApi.update(tenantId, groupId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'key-groups'] });
      setGroupModalOpen(false);
      setEditGroup(null);
      setGroupName('');
      setGroupMemoryPrompt('');
      setGroupAllowedToolsRestricted(false);
      setGroupAllowedToolIds([]);
      notifications.show({ title: 'Обновлено', message: 'Группа ключей обновлена', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось обновить группу', color: 'red' });
    },
  });

  const deleteGroupMutation = useMutation({
    mutationFn: (groupId: string) => keyGroupsApi.delete(tenantId, groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'key-groups'] });
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      notifications.show({ title: 'Удалено', message: 'Группа удалена', color: 'green' });
    },
  });

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;

  const openCreateKey = () => {
    setEditKey(null);
    setKeyName('');
    setKeyGroupId(null);
    setKeyMemoryPrompt('');
    setKeyAllowedToolsRestricted(false);
    setKeyAllowedToolIds([]);
    setCreateOpen(true);
  };

  const openEditKey = (key: TenantApiKey) => {
    setEditKey(key);
    setKeyName(key.name);
    setKeyGroupId(key.group_id);
    setKeyMemoryPrompt(key.memory_prompt || '');
    const keyPermissions = normalizePermissionList(key.allowed_tool_ids);
    setKeyAllowedToolsRestricted(keyPermissions.restricted);
    setKeyAllowedToolIds(keyPermissions.ids);
    setCreateOpen(true);
  };

  const openCreateGroup = () => {
    setEditGroup(null);
    setGroupName('');
    setGroupMemoryPrompt('');
    setGroupAllowedToolsRestricted(false);
    setGroupAllowedToolIds([]);
    setGroupModalOpen(true);
  };

  const openEditGroup = (group: TenantApiKeyGroup) => {
    setEditGroup(group);
    setGroupName(group.name);
    setGroupMemoryPrompt(group.memory_prompt || '');
    const groupPermissions = normalizePermissionList(group.allowed_tool_ids);
    setGroupAllowedToolsRestricted(groupPermissions.restricted);
    setGroupAllowedToolIds(groupPermissions.ids);
    setGroupModalOpen(true);
  };

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>API Ключи</Text>
        <Button
          leftSection={<IconPlus size={16} />}
          size="sm"
          onClick={openCreateKey}
        >
          Создать ключ
        </Button>
      </Group>

      <Card withBorder>
        <Stack gap="sm">
          <Group justify="space-between">
            <Text fw={500}>Группы ключей</Text>
            <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={openCreateGroup}>
              Создать группу
            </Button>
          </Group>
          {!groups.length ? (
            <Text size="sm" c="dimmed">Групп пока нет.</Text>
          ) : (
            <Table striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Название</Table.Th>
                  <Table.Th>Память группы</Table.Th>
                  <Table.Th>Tools</Table.Th>
                  <Table.Th>Действия</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {groups.map((group) => (
                  <Table.Tr key={group.id}>
                    <Table.Td>{group.name}</Table.Td>
                    <Table.Td>
                      <Text size="sm" lineClamp={2}>{group.memory_prompt || '—'}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light">
                        {group.allowed_tool_ids === null
                          ? 'Все'
                          : `${group.allowed_tool_ids.length} tools`}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs">
                        <Tooltip label="Редактировать группу">
                          <ActionIcon variant="subtle" color="blue" onClick={() => openEditGroup(group)}>
                            <IconEdit size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Удалить группу">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            onClick={() => {
                              if (window.confirm(`Удалить группу "${group.name}"? Ключи останутся без группы.`)) {
                                deleteGroupMutation.mutate(group.id);
                              }
                            }}
                          >
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      </Card>

      {isLoading ? (
        <Center py="md">
          <Loader />
        </Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">
          API ключей пока нет.
        </Text>
      ) : (
        <>
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Название</Table.Th>
                <Table.Th>Группа</Table.Th>
                <Table.Th>Префикс</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Память ключа</Table.Th>
                <Table.Th>Tools</Table.Th>
                <Table.Th>Истекает</Table.Th>
                <Table.Th>Последнее использование</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((key) => (
                <Table.Tr key={key.id}>
                  <Table.Td>
                    <Button variant="subtle" size="xs" onClick={() => openEditKey(key)}>
                      {key.name}
                    </Button>
                  </Table.Td>
                  <Table.Td>{key.group_name || (key.group_id ? groupNameById.get(key.group_id) : null) || '—'}</Table.Td>
                  <Table.Td>
                    <Code>{key.key_prefix}...</Code>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={key.is_active ? 'green' : 'gray'}>
                      {key.is_active ? 'Активный' : 'Неактивный'}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" lineClamp={2}>{key.memory_prompt || '—'}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light">
                      {key.allowed_tool_ids === null
                        ? (key.group_id
                            ? `Наследует: ${groupNameById.get(key.group_id) || 'группу'}`
                            : 'Все')
                        : `${key.allowed_tool_ids.length} tools`}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {key.expires_at
                      ? new Date(key.expires_at).toLocaleDateString()
                      : 'Никогда'}
                  </Table.Td>
                  <Table.Td>
                    {key.last_used_at
                      ? new Date(key.last_used_at).toLocaleString()
                      : 'Никогда'}
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      {key.is_active && (
                        <>
                          <Tooltip label="Редактировать ключ">
                            <ActionIcon
                              variant="subtle"
                              color="blue"
                              onClick={() => openEditKey(key)}
                            >
                              <IconEdit size={16} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="Ротировать ключ">
                            <ActionIcon
                              variant="subtle"
                              color="blue"
                              onClick={() => rotateMutation.mutate(key.id)}
                              loading={rotateMutation.isPending}
                            >
                              <IconRefresh size={16} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="Деактивировать">
                            <ActionIcon
                              variant="subtle"
                              color="yellow"
                              onClick={() => deactivateMutation.mutate(key.id)}
                              loading={deactivateMutation.isPending}
                            >
                              <IconPlayerStop size={16} />
                            </ActionIcon>
                          </Tooltip>
                        </>
                      )}
                      <Tooltip label="Удалить ключ">
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          onClick={() => {
                            if (window.confirm('Удалить этот API ключ навсегда?')) {
                              deleteMutation.mutate(key.id);
                            }
                          }}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {totalPages > 1 && (
            <Center>
              <Pagination total={totalPages} value={page} onChange={setPage} />
            </Center>
          )}
        </>
      )}

      {/* Create Key Modal */}
      <Modal
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        title={editKey ? 'Редактировать API ключ' : 'Создать API ключ'}
      >
        <Text size="sm" c="dimmed" mb="md">
          API ключ используется для доступа к чату от имени тенанта через REST API.
          {editKey ? 'Здесь можно изменить группу, название и память ключа.' : 'Полный ключ будет показан только один раз — сохраните его!'}
        </Text>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (editKey) {
              updateMutation.mutate({
                keyId: editKey.id,
                payload: {
                  name: keyName,
                  group_id: keyGroupId || null,
                  memory_prompt: keyMemoryPrompt || null,
                  allowed_tool_ids: keyAllowedToolsRestricted ? keyAllowedToolIds : null,
                },
              });
            } else {
              createMutation.mutate();
            }
          }}
        >
          <Stack gap="md">
            <TextInput
              label="Название ключа"
              description="Понятное имя для идентификации, например: production-bot, test-key"
              placeholder="production-key"
              value={keyName}
              onChange={(e) => setKeyName(e.currentTarget.value)}
              required
            />
            <Select
              label="Группа"
              data={groupOptions}
              value={keyGroupId || ''}
              onChange={(value) => setKeyGroupId(value || null)}
            />
            <Textarea
              label="Память ключа"
              description="Этот текст будет подмешиваться в запросы, выполненные через данный API ключ."
              placeholder="Например: этот ключ используется только для биллинга и ответов по платежам."
              autosize
              minRows={4}
              maxRows={14}
              styles={{ input: { resize: 'vertical', whiteSpace: 'pre-wrap' } }}
              value={keyMemoryPrompt}
              onChange={(e) => setKeyMemoryPrompt(e.currentTarget.value)}
            />
            <Switch
              label="Ограничить доступ к tools"
              checked={keyAllowedToolsRestricted}
              onChange={(e) => setKeyAllowedToolsRestricted(e.currentTarget.checked)}
            />
            <MultiSelect
              label="Разрешённые tools"
              description="Пустой список означает запрет всех tools. Если ограничение выключено, ключ сможет использовать все доступные tools."
              data={toolOptions}
              value={keyAllowedToolIds}
              onChange={setKeyAllowedToolIds}
              searchable
              clearable
              disabled={!keyAllowedToolsRestricted}
              placeholder="Выберите tools"
              nothingFoundMessage="Tools не найдены"
            />
            <Stack gap={6}>
              <Text size="xs" c="dimmed">Пресеты по меткам возможностей инструмента</Text>
              <Text size="xs" c="dimmed">
                Работают по `capability_tags`, а не по имени или группе инструмента.
              </Text>
              <Group gap="xs" wrap="wrap">
                {TOOL_PERMISSION_PRESETS.map((preset) => (
                  <Button
                    key={preset.label}
                    variant="light"
                    size="xs"
                    onClick={() => applyToolPreset(preset, setKeyAllowedToolsRestricted, setKeyAllowedToolIds)}
                  >
                    {preset.label}
                  </Button>
                ))}
              </Group>
            </Stack>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setCreateOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" loading={createMutation.isPending || updateMutation.isPending}>
                {editKey ? 'Сохранить' : 'Создать'}
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>

      <Modal
        opened={groupModalOpen}
        onClose={() => setGroupModalOpen(false)}
        title={editGroup ? 'Редактировать группу ключей' : 'Создать группу ключей'}
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (editGroup) {
              updateGroupMutation.mutate({
                groupId: editGroup.id,
                payload: {
                  name: groupName,
                  memory_prompt: groupMemoryPrompt || null,
                  allowed_tool_ids: groupAllowedToolsRestricted ? groupAllowedToolIds : null,
                },
              });
            } else {
              createGroupMutation.mutate();
            }
          }}
        >
          <Stack gap="md">
            <TextInput
              label="Название группы"
              value={groupName}
              onChange={(e) => setGroupName(e.currentTarget.value)}
              required
            />
            <Textarea
              label="Память группы"
              description="Этот текст будет подмешиваться в запросы для всех API ключей этой группы."
              autosize
              minRows={4}
              maxRows={14}
              styles={{ input: { resize: 'vertical', whiteSpace: 'pre-wrap' } }}
              value={groupMemoryPrompt}
              onChange={(e) => setGroupMemoryPrompt(e.currentTarget.value)}
            />
            <Switch
              label="Ограничить tools для группы"
              checked={groupAllowedToolsRestricted}
              onChange={(e) => setGroupAllowedToolsRestricted(e.currentTarget.checked)}
            />
            <MultiSelect
              label="Разрешённые tools группы"
              description="Пустой список означает запрет всех tools для ключей этой группы. Если ограничение выключено, применяется доступ без ограничений."
              data={toolOptions}
              value={groupAllowedToolIds}
              onChange={setGroupAllowedToolIds}
              searchable
              clearable
              disabled={!groupAllowedToolsRestricted}
              placeholder="Выберите tools"
              nothingFoundMessage="Tools не найдены"
            />
            <Stack gap={6}>
              <Text size="xs" c="dimmed">Пресеты по меткам возможностей инструмента</Text>
              <Text size="xs" c="dimmed">
                Работают по `capability_tags`, а не по имени или группе инструмента.
              </Text>
              <Group gap="xs" wrap="wrap">
                {TOOL_PERMISSION_PRESETS.map((preset) => (
                  <Button
                    key={preset.label}
                    variant="light"
                    size="xs"
                    onClick={() => applyToolPreset(preset, setGroupAllowedToolsRestricted, setGroupAllowedToolIds)}
                  >
                    {preset.label}
                  </Button>
                ))}
              </Group>
            </Stack>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setGroupModalOpen(false)}>Отмена</Button>
              <Button type="submit" loading={createGroupMutation.isPending || updateGroupMutation.isPending}>
                {editGroup ? 'Сохранить' : 'Создать'}
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>

      {/* Raw Key Display Modal */}
      <Modal
        opened={rawKeyModalOpen}
        onClose={() => {
          setRawKeyModalOpen(false);
          setRawKey('');
        }}
        title="API ключ создан"
      >
        <Stack gap="md">
          <Alert color="yellow" variant="light" icon={<IconAlertCircle size={16} />}>
            Скопируйте этот ключ сейчас. Вы не сможете увидеть его снова.
          </Alert>
          <Group gap="xs">
            <Code block style={{ flex: 1, wordBreak: 'break-all' }}>
              {rawKey}
            </Code>
            <CopyButton value={rawKey}>
              {({ copied, copy }) => (
                <Tooltip label={copied ? 'Скопировано' : 'Копировать'}>
                  <ActionIcon color={copied ? 'green' : 'gray'} onClick={copy} variant="subtle">
                    {copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                  </ActionIcon>
                </Tooltip>
              )}
            </CopyButton>
          </Group>
          <Button onClick={() => { setRawKeyModalOpen(false); setRawKey(''); }}>
            Готово
          </Button>
        </Stack>
      </Modal>
    </Stack>
  );
}

// ===== TOOLS TAB =====

const DB_SEARCH_TOOL_TEMPLATE = {
  type: 'function',
  function: {
    name: 'search_records',
    description:
      'Выполняет безопасный read-only поиск записей в настроенной БД по whitelist-фильтрам и/или общему query.',
    parameters: {
      type: 'object',
      properties: {
        filters: {
          type: 'object',
          description: 'Набор фильтров по разрешённым alias, например {"client_id":"123","ip":"172.10.100.20"}',
        },
        query: { type: 'string', description: 'Свободный текстовый поиск по search_columns' },
        limit: { type: 'integer', description: 'Сколько записей вернуть', minimum: 1, maximum: 25 },
      },
      additionalProperties: false,
    },
  },
  x_backend_config: {
    handler: 'search_records',
    table: 'public.records_view',
    filter_fields: {
      record_id: { column: 'id', mode: 'exact' },
      ip: { column: 'ip_address', mode: 'exact' },
      name: { column: 'name', mode: 'contains' },
      address: { column: 'address', mode: 'contains' },
    },
    search_columns: ['name', 'address', 'notes'],
    result_columns: ['id', 'name', 'ip_address', 'address', 'status'],
    default_limit: 10,
    max_limit: 25,
    sort_by: 'id',
    static_filters: {
      is_active: true,
    },
  },
};

const API_FETCH_TOOL_TEMPLATE = {
  type: 'function',
  function: {
    name: 'fetch_api_data',
    description:
      'Получает read-only данные из внешнего HTTP API по заранее разрешённым path/query параметрам.',
    parameters: {
      type: 'object',
      properties: {
        path_values: {
          type: 'object',
          description: 'Значения для path-плейсхолдеров endpoint, например {"client_id":"123"}',
        },
        query_params: {
          type: 'object',
          description: 'Разрешённые query-параметры, например {"ip":"172.10.100.20"}',
        },
      },
      additionalProperties: false,
    },
  },
  x_backend_config: {
    handler: 'fetch_api_data',
    base_url: 'https://api.example.com',
    endpoint: '/records/{record_id}',
    method: 'GET',
    path_params: ['record_id'],
    query_params: {
      ip: 'ip',
      client_id: 'client_id',
    },
    headers: {
      Accept: 'application/json',
    },
    timeout_seconds: 15,
    result_path: 'data',
  },
};

const SSH_EXEC_TOOL_TEMPLATE = {
  type: 'function',
  function: {
    name: 'ssh_exec',
    description: 'Выполняет команду на удалённом сервере/устройстве по SSH из списка разрешённых команд.',
    parameters: {
      type: 'object',
      properties: {
        command_name: { type: 'string', description: 'Имя команды из списка разрешённых' },
        params: { type: 'object', description: 'Параметры для подстановки в шаблон команды' },
      },
      required: ['command_name'],
      additionalProperties: false,
    },
  },
  x_backend_config: {
    handler: 'ssh_exec',
    timeout_seconds: 15,
    strip_ansi: true,
    commands: {
      show_interfaces: {
        command: 'ip -br addr show',
        description: 'Список сетевых интерфейсов с IP',
      },
      ping_host: {
        command: 'ping -c 4 -W 2 {target}',
        description: 'Пинг указанного хоста',
        params: ['target'],
      },
      show_routes: {
        command: 'ip route show',
        description: 'Таблица маршрутизации',
      },
    },
  },
};

const TELNET_EXEC_TOOL_TEMPLATE = {
  type: 'function',
  function: {
    name: 'telnet_exec',
    description: 'Выполняет команду на сетевом оборудовании по Telnet из списка разрешённых команд.',
    parameters: {
      type: 'object',
      properties: {
        command_name: { type: 'string', description: 'Имя команды из списка разрешённых' },
        params: { type: 'object', description: 'Параметры для подстановки в шаблон команды' },
      },
      required: ['command_name'],
      additionalProperties: false,
    },
  },
  x_backend_config: {
    handler: 'telnet_exec',
    vendor: 'dlink',
    timeout_seconds: 15,
    strip_ansi: true,
    commands: {
      show_ports: {
        command: 'show ports',
        description: 'Статусы всех портов коммутатора',
      },
      show_fdb_port: {
        command: 'show fdb port {port}',
        description: 'MAC-адреса на порту',
        params: ['port'],
      },
      show_cable_diag: {
        command: 'cable_diag ports {port}',
        description: 'Диагностика кабеля на порту',
        params: ['port'],
      },
    },
  },
};

const SNMP_TOOL_TEMPLATE = {
  type: 'function',
  function: {
    name: 'snmp_query',
    description: 'Запрашивает данные с сетевого оборудования по SNMP из списка разрешённых OID.',
    parameters: {
      type: 'object',
      properties: {
        oid_name: { type: 'string', description: 'Имя OID из списка разрешённых' },
        params: { type: 'object', description: 'Параметры для подстановки в OID' },
      },
      required: ['oid_name'],
      additionalProperties: false,
    },
  },
  x_backend_config: {
    handler: 'snmp_get',
    timeout_seconds: 10,
    walk_max_rows: 256,
    oids: {
      port_status: {
        oid: '1.3.6.1.2.1.2.2.1.8.{port_index}',
        description: 'Статус порта (1=up, 2=down, 3=testing)',
        params: ['port_index'],
        value_map: { '1': 'up', '2': 'down', '3': 'testing' },
      },
      port_speed: {
        oid: '1.3.6.1.2.1.2.2.1.5.{port_index}',
        description: 'Скорость порта в bps',
        params: ['port_index'],
      },
      sys_uptime: {
        oid: '1.3.6.1.2.1.1.3.0',
        description: 'Аптайм устройства',
      },
      sys_name: {
        oid: '1.3.6.1.2.1.1.5.0',
        description: 'Имя устройства',
      },
    },
    walk_oids: {
      all_port_statuses: {
        oid: '1.3.6.1.2.1.2.2.1.8',
        description: 'Статусы всех портов',
      },
      all_port_descriptions: {
        oid: '1.3.6.1.2.1.2.2.1.2',
        description: 'Описания всех портов',
      },
    },
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

type SearchFilterFieldRow = {
  alias: string;
  column: string;
  mode: string;
  description: string;
};

type SearchResultFieldRow = {
  alias: string;
  column: string;
  description: string;
};

type SearchJoinRow = {
  type: 'left' | 'inner';
  table: string;
  alias: string;
  left_column: string;
  right_column: string;
};

type SearchStaticFilterRow = {
  key: string;
  value: string;
};

function isSearchRecordsConfig(config: Record<string, unknown> | null | undefined): boolean {
  if (!isRecord(config)) return false;
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return runtime.handler === 'search_records';
}

// ---- SSH/Telnet command builder types & helpers ----

type CmdEditorRow = {
  name: string;
  command: string;
  description: string;
  params: string; // comma-separated param names
};

const _CMD_HANDLERS = new Set(['ssh_exec', 'telnet_exec']);

const _HANDLER_DISPLAY: Record<string, { label: string; color: string; icon: typeof IconTool }> = {
  ssh_exec: { label: 'SSH', color: 'teal', icon: IconTerminal2 },
  telnet_exec: { label: 'Telnet', color: 'orange', icon: IconRouter },
  snmp_get: { label: 'SNMP', color: 'grape', icon: IconNetwork },
  search_records: { label: 'SQL', color: 'blue', icon: IconDatabase },
  fetch_api_data: { label: 'API', color: 'cyan', icon: IconApi },
  ping: { label: 'Ping', color: 'green', icon: IconWorldWww },
  dns_lookup: { label: 'DNS', color: 'indigo', icon: IconWorldWww },
  traceroute: { label: 'Traceroute', color: 'lime', icon: IconWorldWww },
};

function getToolHandlerInfo(config: Record<string, unknown> | null | undefined): { label: string; color: string; icon: typeof IconTool } {
  if (!isRecord(config)) return { label: 'function', color: 'gray', icon: IconTool };
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  const handler = typeof runtime.handler === 'string' ? runtime.handler : '';
  return _HANDLER_DISPLAY[handler] || { label: handler || 'function', color: 'gray', icon: IconTool };
}

function isCmdEditorConfig(config: Record<string, unknown> | null | undefined): boolean {
  if (!isRecord(config)) return false;
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return _CMD_HANDLERS.has(String(runtime.handler || ''));
}

function readCmdEditorRows(config: Record<string, unknown> | null | undefined): CmdEditorRow[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  const commands = isRecord(runtime.commands) ? runtime.commands : {};
  return Object.entries(commands).map(([name, raw]) => {
    const cfg = isRecord(raw) ? raw : {};
    const params = Array.isArray(cfg.params) ? (cfg.params as string[]).join(', ') : '';
    return {
      name,
      command: typeof cfg.command === 'string' ? cfg.command : '',
      description: typeof cfg.description === 'string' ? cfg.description : '',
      params,
    };
  });
}

function readCmdTimeout(config: Record<string, unknown> | null | undefined): number {
  if (!isRecord(config)) return 15;
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return typeof runtime.timeout_seconds === 'number' ? runtime.timeout_seconds : 15;
}

function readCmdVendor(config: Record<string, unknown> | null | undefined): string {
  if (!isRecord(config)) return '';
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return typeof runtime.vendor === 'string' ? runtime.vendor : '';
}

function applyCmdEditor(
  config: Record<string, unknown>,
  rows: CmdEditorRow[],
  timeout: number,
  vendor: string,
): Record<string, unknown> {
  const nextConfig = JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
  const runtime = isRecord(nextConfig.x_backend_config) ? { ...nextConfig.x_backend_config } : {};

  const commands: Record<string, unknown> = {};
  for (const row of rows) {
    const name = row.name.trim();
    const command = row.command.trim();
    if (!name || !command) continue;
    const paramsList = row.params.split(',').map(s => s.trim()).filter(Boolean);
    commands[name] = {
      command,
      description: row.description.trim() || undefined,
      ...(paramsList.length > 0 ? { params: paramsList } : {}),
    };
  }
  runtime.commands = commands;
  runtime.timeout_seconds = timeout;
  if (vendor.trim()) runtime.vendor = vendor.trim();
  else delete runtime.vendor;

  nextConfig.x_backend_config = runtime;
  return nextConfig;
}

function readSearchFilterRows(config: Record<string, unknown> | null | undefined): SearchFilterFieldRow[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  const functionCfg = isRecord(config.function) ? config.function : {};
  const params = isRecord(functionCfg.parameters) ? functionCfg.parameters : {};
  const props = isRecord(params.properties) ? params.properties : {};
  const filters = isRecord(props.filters) ? props.filters : {};
  const filterProps = isRecord(filters.properties) ? filters.properties : {};
  const filterFields = isRecord(runtime.filter_fields) ? runtime.filter_fields : {};

  return Object.entries(filterFields).map(([alias, rawField]) => {
    const fieldCfg = isRecord(rawField) ? rawField : {};
    const propCfg = isRecord(filterProps[alias]) ? filterProps[alias] : {};
    return {
      alias,
      column: typeof fieldCfg.column === 'string' ? fieldCfg.column : '',
      mode: typeof fieldCfg.mode === 'string' ? fieldCfg.mode : 'contains',
      description: typeof propCfg.description === 'string' ? propCfg.description : '',
    };
  });
}

function readSearchResultRows(config: Record<string, unknown> | null | undefined): SearchResultFieldRow[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  const resultColumns = Array.isArray(runtime.result_columns) ? runtime.result_columns : [];
  return resultColumns.map((item) => {
    if (typeof item === 'string') {
      return { alias: item, column: item, description: '' };
    }
    if (isRecord(item)) {
      return {
        alias: typeof item.alias === 'string' ? item.alias : '',
        column: typeof item.column === 'string' ? item.column : '',
        description: typeof item.description === 'string' ? item.description : '',
      };
    }
    return { alias: '', column: '', description: '' };
  });
}

function readSearchTable(config: Record<string, unknown> | null | undefined): string {
  if (!isRecord(config)) return '';
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return typeof runtime.table === 'string' ? runtime.table : (typeof runtime.view === 'string' ? runtime.view : '');
}

function readSearchTableAlias(config: Record<string, unknown> | null | undefined): string {
  if (!isRecord(config)) return '';
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return typeof runtime.table_alias === 'string' ? runtime.table_alias : '';
}

function readSearchColumns(config: Record<string, unknown> | null | undefined): string[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return Array.isArray(runtime.search_columns) ? runtime.search_columns.filter((s): s is string => typeof s === 'string') : [];
}

function readSearchJoinRows(config: Record<string, unknown> | null | undefined): SearchJoinRow[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  if (!Array.isArray(runtime.joins)) return [];
  return runtime.joins.filter(isRecord).map((j) => ({
    type: j.type === 'inner' ? 'inner' as const : 'left' as const,
    table: typeof j.table === 'string' ? j.table : '',
    alias: typeof j.alias === 'string' ? j.alias : '',
    left_column: typeof j.left_column === 'string' ? j.left_column : '',
    right_column: typeof j.right_column === 'string' ? j.right_column : '',
  }));
}

function readSearchStaticFilters(config: Record<string, unknown> | null | undefined): SearchStaticFilterRow[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  if (!isRecord(runtime.static_filters)) return [];
  return Object.entries(runtime.static_filters).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }));
}

function readSearchDateWindow(config: Record<string, unknown> | null | undefined): { column: string; days: number } | null {
  if (!isRecord(config)) return null;
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  if (!isRecord(runtime.date_window)) return null;
  const column = typeof runtime.date_window.column === 'string' ? runtime.date_window.column : '';
  const days = typeof runtime.date_window.days === 'number' ? runtime.date_window.days : 0;
  if (!column || !days) return null;
  return { column, days };
}

function readSearchLimits(config: Record<string, unknown> | null | undefined): { defaultLimit: number; maxLimit: number; unlimitedResults: boolean } {
  if (!isRecord(config)) return { defaultLimit: 10, maxLimit: 25, unlimitedResults: false };
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return {
    defaultLimit: typeof runtime.default_limit === 'number' ? runtime.default_limit : 10,
    maxLimit: typeof runtime.max_limit === 'number' ? runtime.max_limit : 25,
    unlimitedResults: !!runtime.unlimited_results,
  };
}

function readSearchSortBy(config: Record<string, unknown> | null | undefined): string {
  if (!isRecord(config)) return '';
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return typeof runtime.sort_by === 'string' ? runtime.sort_by : '';
}

function applySearchRecordsEditor(
  config: Record<string, unknown>,
  filterRows: SearchFilterFieldRow[],
  resultRows: SearchResultFieldRow[],
  table: string,
  tableAlias: string,
  searchCols: string[],
  joinRows: SearchJoinRow[],
  staticFilters: SearchStaticFilterRow[],
  dateWindow: { column: string; days: number } | null,
  defaultLimit: number,
  maxLimit: number,
  unlimitedResults: boolean,
  sortBy: string,
): Record<string, unknown> {
  const nextConfig = JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
  const functionCfg = isRecord(nextConfig.function) ? { ...nextConfig.function } : {};
  const params = isRecord(functionCfg.parameters) ? { ...functionCfg.parameters } : {};
  const props = isRecord(params.properties) ? { ...params.properties } : {};
  const filters = isRecord(props.filters) ? { ...props.filters } : {};
  const runtime = isRecord(nextConfig.x_backend_config) ? { ...nextConfig.x_backend_config } : {};

  // Filter fields
  const nextFilterFields: Record<string, unknown> = {};
  const nextFilterProps: Record<string, unknown> = {};
  for (const row of filterRows) {
    const alias = row.alias.trim();
    const column = row.column.trim();
    if (!alias || !column) continue;
    nextFilterFields[alias] = {
      column,
      mode: row.mode || 'contains',
    };
    nextFilterProps[alias] = {
      type: 'string',
      ...(row.description.trim() ? { description: row.description.trim() } : {}),
    };
  }
  runtime.filter_fields = nextFilterFields;
  runtime.result_columns = resultRows
    .map((row) => {
      const alias = row.alias.trim();
      const column = row.column.trim();
      const description = row.description.trim();
      const obj: Record<string, string> = { alias, column };
      if (description) obj.description = description;
      return obj;
    })
    .filter((row) => row.alias && row.column);

  // Table source
  if (table.trim()) runtime.table = table.trim();
  if (tableAlias.trim()) runtime.table_alias = tableAlias.trim();
  else delete runtime.table_alias;

  // Search columns
  const validSearchCols = searchCols.map(s => s.trim()).filter(Boolean);
  if (validSearchCols.length > 0) runtime.search_columns = validSearchCols;
  else delete runtime.search_columns;

  // Joins
  const validJoins = joinRows
    .filter(j => j.table.trim() && j.left_column.trim() && j.right_column.trim())
    .map(j => ({
      type: j.type,
      table: j.table.trim(),
      ...(j.alias.trim() ? { alias: j.alias.trim() } : {}),
      left_column: j.left_column.trim(),
      right_column: j.right_column.trim(),
    }));
  if (validJoins.length > 0) runtime.joins = validJoins;
  else delete runtime.joins;

  // Static filters
  const sf: Record<string, unknown> = {};
  for (const { key, value } of staticFilters) {
    const k = key.trim();
    if (!k) continue;
    try { sf[k] = JSON.parse(value); } catch { sf[k] = value; }
  }
  if (Object.keys(sf).length > 0) runtime.static_filters = sf;
  else delete runtime.static_filters;

  // Date window
  if (dateWindow && dateWindow.column.trim() && dateWindow.days > 0) {
    runtime.date_window = { column: dateWindow.column.trim(), days: dateWindow.days };
  } else {
    delete runtime.date_window;
  }

  // Limits
  runtime.default_limit = defaultLimit;
  runtime.max_limit = maxLimit;
  if (unlimitedResults) runtime.unlimited_results = true;
  else delete runtime.unlimited_results;

  // Sort
  if (sortBy.trim()) runtime.sort_by = sortBy.trim();
  else delete runtime.sort_by;

  // Update limit.maximum in function parameters to match maxLimit
  const limitProp = isRecord(props.limit) ? { ...props.limit } : {};
  limitProp.maximum = maxLimit;
  props.limit = limitProp;

  filters.properties = nextFilterProps;
  props.filters = filters;
  params.properties = props;
  functionCfg.parameters = params;
  nextConfig.function = functionCfg;
  nextConfig.x_backend_config = runtime;
  return nextConfig;
}

// ---- API editor types & helpers (fetch_api_data) ----

type ApiEnumValueRow = {
  value: string;
  description: string;
  requires: string[];  // aliases of query_params this enum value requires
};

type ApiPathParamRow = {
  name: string;
  description: string;
  useEnum: boolean;
  enumValues: ApiEnumValueRow[];
};

type ApiQueryParamRow = {
  alias: string;       // public name shown to LLM
  target: string;      // actual query parameter sent to remote API
  description: string;
};

type ApiHeaderRow = {
  name: string;
  value: string;
};

type ApiStaticQueryRow = {
  key: string;
  value: string;
};

type ApiBodyParamRow = {
  alias: string;
  target: string;
  description: string;
};

type ApiStaticBodyRow = {
  key: string;
  value: string;
};

function isApiEditorConfig(config: Record<string, unknown> | null | undefined): boolean {
  if (!isRecord(config)) return false;
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  return runtime.handler === 'fetch_api_data';
}

function readApiRuntime(config: Record<string, unknown> | null | undefined): Record<string, unknown> {
  if (!isRecord(config)) return {};
  return isRecord(config.x_backend_config) ? config.x_backend_config : {};
}

function readApiFunctionProps(
  config: Record<string, unknown> | null | undefined,
  key: 'path_values' | 'query_params',
): Record<string, unknown> {
  if (!isRecord(config)) return {};
  const fn = isRecord(config.function) ? config.function : {};
  const params = isRecord(fn.parameters) ? fn.parameters : {};
  const props = isRecord(params.properties) ? params.properties : {};
  const target = isRecord(props[key]) ? props[key] : {};
  const inner = isRecord(target.properties) ? target.properties : {};
  return inner;
}

function readApiBaseUrl(config: Record<string, unknown> | null | undefined): string {
  const runtime = readApiRuntime(config);
  return typeof runtime.base_url === 'string' ? runtime.base_url : '';
}

function readApiEndpoint(config: Record<string, unknown> | null | undefined): string {
  const runtime = readApiRuntime(config);
  return typeof runtime.endpoint === 'string' ? runtime.endpoint : '';
}

function readApiMethod(config: Record<string, unknown> | null | undefined): string {
  const runtime = readApiRuntime(config);
  return typeof runtime.method === 'string' ? runtime.method : 'GET';
}

function readApiTimeout(config: Record<string, unknown> | null | undefined): number {
  const runtime = readApiRuntime(config);
  return typeof runtime.timeout_seconds === 'number' ? runtime.timeout_seconds : 15;
}

function readApiResultPath(config: Record<string, unknown> | null | undefined): string {
  const runtime = readApiRuntime(config);
  return typeof runtime.result_path === 'string' ? runtime.result_path : '';
}

function readApiMaxResponseChars(config: Record<string, unknown> | null | undefined): number {
  const runtime = readApiRuntime(config);
  return typeof runtime.max_response_chars === 'number' ? runtime.max_response_chars : 16000;
}

function readApiPathParamRows(config: Record<string, unknown> | null | undefined): ApiPathParamRow[] {
  const runtime = readApiRuntime(config);
  const list = Array.isArray(runtime.path_params) ? runtime.path_params : [];
  const descs = readApiFunctionProps(config, 'path_values');
  const enumStore = isRecord(runtime.enum_values) ? runtime.enum_values : {};
  const baseDescStore = isRecord(runtime.enum_base_descriptions) ? runtime.enum_base_descriptions : {};
  return list.filter((s): s is string => typeof s === 'string').map((name) => {
    const propCfg = isRecord(descs[name]) ? descs[name] : {};
    const fullDesc = typeof propCfg.description === 'string' ? propCfg.description : '';
    const baseDesc = typeof baseDescStore[name] === 'string' ? (baseDescStore[name] as string) : fullDesc;
    const enumRaw = Array.isArray(enumStore[name]) ? (enumStore[name] as unknown[]) : [];
    let enumValues: ApiEnumValueRow[] = enumRaw.filter(isRecord).map((item) => ({
      value: typeof item.value === 'string' ? item.value : '',
      description: typeof item.description === 'string' ? item.description : '',
      requires: Array.isArray(item.requires) ? (item.requires as unknown[]).filter((s): s is string => typeof s === 'string') : [],
    }));
    // Fallback: tool was saved with enum but no rich enum_values metadata —
    // populate value-only rows so editor isn't empty.
    if (enumValues.length === 0 && Array.isArray(propCfg.enum)) {
      enumValues = (propCfg.enum as unknown[])
        .filter((s): s is string => typeof s === 'string')
        .map((value) => ({ value, description: '', requires: [] }));
    }
    const useEnum = Array.isArray(propCfg.enum) || enumValues.length > 0;
    return {
      name,
      description: useEnum ? baseDesc : fullDesc,
      useEnum,
      enumValues,
    };
  });
}

function readApiQueryParamRows(config: Record<string, unknown> | null | undefined): ApiQueryParamRow[] {
  const runtime = readApiRuntime(config);
  const map = isRecord(runtime.query_params) ? runtime.query_params : {};
  const descs = readApiFunctionProps(config, 'query_params');
  return Object.entries(map).map(([alias, target]) => {
    const propCfg = isRecord(descs[alias]) ? descs[alias] : {};
    return {
      alias,
      target: typeof target === 'string' ? target : alias,
      description: typeof propCfg.description === 'string' ? propCfg.description : '',
    };
  });
}

function readApiHeaderRows(config: Record<string, unknown> | null | undefined): ApiHeaderRow[] {
  const runtime = readApiRuntime(config);
  const headers = isRecord(runtime.headers) ? runtime.headers : {};
  return Object.entries(headers).map(([name, value]) => ({
    name,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }));
}

function readApiStaticQueryRows(config: Record<string, unknown> | null | undefined): ApiStaticQueryRow[] {
  const runtime = readApiRuntime(config);
  const sq = isRecord(runtime.static_query) ? runtime.static_query : {};
  return Object.entries(sq).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }));
}

function readApiBodyFormat(config: Record<string, unknown> | null | undefined): 'json' | 'form' {
  const runtime = readApiRuntime(config);
  return runtime.body_format === 'form' ? 'form' : 'json';
}

function readApiBodyParamRows(config: Record<string, unknown> | null | undefined): ApiBodyParamRow[] {
  const runtime = readApiRuntime(config);
  const map = isRecord(runtime.body_params) ? runtime.body_params : {};
  if (!isRecord(config)) return [];
  const fn = isRecord(config.function) ? config.function : {};
  const params = isRecord(fn.parameters) ? fn.parameters : {};
  const props = isRecord(params.properties) ? params.properties : {};
  const bodyProps = isRecord(props.body_params) ? props.body_params : {};
  const descs = isRecord(bodyProps.properties) ? bodyProps.properties : {};
  return Object.entries(map).map(([alias, target]) => {
    const propCfg = isRecord(descs[alias]) ? descs[alias] : {};
    return {
      alias,
      target: typeof target === 'string' ? target : alias,
      description: typeof propCfg.description === 'string' ? propCfg.description : '',
    };
  });
}

function readApiStaticBodyRows(config: Record<string, unknown> | null | undefined): ApiStaticBodyRow[] {
  const runtime = readApiRuntime(config);
  const sb = isRecord(runtime.static_body) ? runtime.static_body : {};
  return Object.entries(sb).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : JSON.stringify(value),
  }));
}

function applyApiEditor(
  config: Record<string, unknown>,
  baseUrl: string,
  endpoint: string,
  method: string,
  timeoutSeconds: number,
  resultPath: string,
  pathParams: ApiPathParamRow[],
  queryParams: ApiQueryParamRow[],
  headers: ApiHeaderRow[],
  staticQuery: ApiStaticQueryRow[],
  bodyParams: ApiBodyParamRow[],
  staticBody: ApiStaticBodyRow[],
  bodyFormat: 'json' | 'form',
  maxResponseChars: number,
): Record<string, unknown> {
  const nextConfig = JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
  const runtime = isRecord(nextConfig.x_backend_config) ? { ...nextConfig.x_backend_config } : {};
  const functionCfg = isRecord(nextConfig.function) ? { ...nextConfig.function } : {};
  const params = isRecord(functionCfg.parameters) ? { ...functionCfg.parameters } : {};
  const props = isRecord(params.properties) ? { ...params.properties } : {};

  // Runtime values
  if (baseUrl.trim()) runtime.base_url = baseUrl.trim();
  else delete runtime.base_url;
  if (endpoint.trim()) runtime.endpoint = endpoint.trim();
  else delete runtime.endpoint;
  runtime.method = (method || 'GET').toUpperCase();
  runtime.timeout_seconds = timeoutSeconds;
  if (resultPath.trim()) runtime.result_path = resultPath.trim();
  else delete runtime.result_path;
  if (maxResponseChars && maxResponseChars > 0) runtime.max_response_chars = maxResponseChars;
  else delete runtime.max_response_chars;

  // Path params -> runtime.path_params (array) + function.parameters.properties.path_values
  const validPath = pathParams.filter((r) => r.name.trim());
  runtime.path_params = validPath.map((r) => r.name.trim());
  const enumStore: Record<string, unknown> = {};
  const baseDescStore: Record<string, string> = {};
  if (validPath.length > 0) {
    const pathProps: Record<string, unknown> = {};
    const requiredPath: string[] = [];
    for (const row of validPath) {
      const name = row.name.trim();
      const baseDesc = row.description.trim();
      const validEnums = row.useEnum
        ? row.enumValues.filter((v) => v.value.trim()).map((v) => ({
            value: v.value.trim(),
            description: v.description.trim(),
            requires: Array.from(new Set(v.requires.filter(Boolean))),
          }))
        : [];

      // Reject duplicate enum values for this path-param
      const dupSeen = new Set<string>();
      const dupFound: string[] = [];
      for (const ev of validEnums) {
        if (dupSeen.has(ev.value)) dupFound.push(ev.value);
        else dupSeen.add(ev.value);
      }
      if (dupFound.length > 0) {
        throw new Error(
          `Дубликаты enum-значений в path-параметре «${name}»: ${Array.from(new Set(dupFound)).join(', ')}. Каждое значение должно быть уникальным.`,
        );
      }

      let compiledDesc = baseDesc;
      if (validEnums.length > 0) {
        const lines = validEnums.map((v) => {
          const req = v.requires.length ? ` (требует: ${v.requires.join(', ')})` : '';
          return `- ${v.value}: ${v.description || '—'}${req}`;
        });
        compiledDesc = (baseDesc ? `${baseDesc}\n\n` : '') + 'Допустимые значения:\n' + lines.join('\n');
      }

      const propCfg: Record<string, unknown> = { type: 'string' };
      if (compiledDesc) propCfg.description = compiledDesc;
      if (validEnums.length > 0) propCfg.enum = validEnums.map((v) => v.value);
      pathProps[name] = propCfg;
      requiredPath.push(name);

      if (validEnums.length > 0) {
        enumStore[name] = validEnums;
        if (baseDesc) baseDescStore[name] = baseDesc;
      }
    }
    props.path_values = {
      type: 'object',
      description: 'Значения для path-плейсхолдеров endpoint',
      properties: pathProps,
      required: requiredPath,
      additionalProperties: false,
    };
  } else {
    delete props.path_values;
  }
  if (Object.keys(enumStore).length > 0) {
    runtime.enum_values = enumStore;
    if (Object.keys(baseDescStore).length > 0) runtime.enum_base_descriptions = baseDescStore;
    else delete runtime.enum_base_descriptions;
  } else {
    delete runtime.enum_values;
    delete runtime.enum_base_descriptions;
  }

  // Query params -> runtime.query_params (alias->target) + function schema descriptions
  const validQuery = queryParams.filter((r) => r.alias.trim());
  if (validQuery.length > 0) {
    const queryMap: Record<string, string> = {};
    const queryProps: Record<string, unknown> = {};
    for (const row of validQuery) {
      const alias = row.alias.trim();
      const target = row.target.trim() || alias;
      queryMap[alias] = target;
      queryProps[alias] = {
        type: 'string',
        ...(row.description.trim() ? { description: row.description.trim() } : {}),
      };
    }
    runtime.query_params = queryMap;
    props.query_params = {
      type: 'object',
      description: 'Разрешённые query-параметры',
      properties: queryProps,
      additionalProperties: false,
    };
  } else {
    delete runtime.query_params;
    delete props.query_params;
  }

  // Headers
  const validHeaders = headers.filter((h) => h.name.trim());
  if (validHeaders.length > 0) {
    const headerMap: Record<string, string> = {};
    for (const row of validHeaders) headerMap[row.name.trim()] = row.value;
    runtime.headers = headerMap;
  } else {
    delete runtime.headers;
  }

  // Static query (always-applied, hidden from LLM)
  const validStatic = staticQuery.filter((s) => s.key.trim());
  if (validStatic.length > 0) {
    const sq: Record<string, unknown> = {};
    for (const row of validStatic) {
      try { sq[row.key.trim()] = JSON.parse(row.value); }
      catch { sq[row.key.trim()] = row.value; }
    }
    runtime.static_query = sq;
  } else {
    delete runtime.static_query;
  }

  // Body params + static body — only meaningful for POST
  const upperMethod = (method || 'GET').toUpperCase();
  const validBody = bodyParams.filter((r) => r.alias.trim());
  if (upperMethod === 'POST' && validBody.length > 0) {
    const bodyMap: Record<string, string> = {};
    const bodyProps: Record<string, unknown> = {};
    const requiredBody: string[] = [];
    for (const row of validBody) {
      const alias = row.alias.trim();
      const target = row.target.trim() || alias;
      bodyMap[alias] = target;
      bodyProps[alias] = {
        type: 'string',
        ...(row.description.trim() ? { description: row.description.trim() } : {}),
      };
      requiredBody.push(alias);
    }
    runtime.body_params = bodyMap;
    runtime.body_format = bodyFormat;
    props.body_params = {
      type: 'object',
      description: 'Параметры тела запроса',
      properties: bodyProps,
      required: requiredBody,
      additionalProperties: false,
    };
    if (!Array.isArray(params.required)) params.required = [];
    const reqArr = params.required as string[];
    if (!reqArr.includes('body_params')) reqArr.push('body_params');
  } else {
    delete runtime.body_params;
    delete runtime.body_format;
    delete props.body_params;
    if (Array.isArray(params.required)) {
      params.required = (params.required as unknown[]).filter((r) => r !== 'body_params');
    }
  }

  const validStaticBody = staticBody.filter((s) => s.key.trim());
  if (upperMethod === 'POST' && validStaticBody.length > 0) {
    const sb: Record<string, unknown> = {};
    for (const row of validStaticBody) {
      try { sb[row.key.trim()] = JSON.parse(row.value); }
      catch { sb[row.key.trim()] = row.value; }
    }
    runtime.static_body = sb;
  } else {
    delete runtime.static_body;
  }

  params.properties = props;
  if (!Array.isArray(params.required)) params.required = [];
  if (params.type !== 'object') params.type = 'object';
  if (typeof params.additionalProperties === 'undefined') params.additionalProperties = false;
  functionCfg.parameters = params;
  nextConfig.function = functionCfg;
  nextConfig.x_backend_config = runtime;
  return nextConfig;
}

function ToolsTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [toolName, setToolName] = useState('');
  const [toolDesc, setToolDesc] = useState('');
  const [toolGroup, setToolGroup] = useState('');
  const [toolType, setToolType] = useState('function');
  const [toolCapabilityTags, setToolCapabilityTags] = useState<string[]>([]);
  const [configJson, setConfigJson] = useState('{}');
  const [toolActive, setToolActive] = useState(true);
  const [toolPinned, setToolPinned] = useState(false);
  const [groupFilter, setGroupFilter] = useState<string | null>(null);
  const [dataSourceFilter, setDataSourceFilter] = useState<string | null>(null);
  const [toolSearchInput, setToolSearchInput] = useState('');
  const [toolSearch, setToolSearch] = useState('');
  // Debounce search input to avoid hitting backend on every keystroke
  useEffect(() => {
    const t = setTimeout(() => {
      setToolSearch(toolSearchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [toolSearchInput]);
  const [templateDataSourceId, setTemplateDataSourceId] = useState<string | null>(null);
  const [existingGroupChoice, setExistingGroupChoice] = useState<string | null>(null);
  const [isSearchToolEditor, setIsSearchToolEditor] = useState(false);
  const [searchFilterRows, setSearchFilterRows] = useState<SearchFilterFieldRow[]>([]);
  const [searchResultRows, setSearchResultRows] = useState<SearchResultFieldRow[]>([]);
  const [searchTable, setSearchTable] = useState('');
  const [searchTableAlias, setSearchTableAlias] = useState('');
  const [searchColumns, setSearchColumns] = useState<string[]>([]);
  const [searchJoinRows, setSearchJoinRows] = useState<SearchJoinRow[]>([]);
  const [searchStaticFilters, setSearchStaticFilters] = useState<SearchStaticFilterRow[]>([]);
  const [searchDateWindow, setSearchDateWindow] = useState<{ column: string; days: number } | null>(null);
  const [searchDefaultLimit, setSearchDefaultLimit] = useState<number>(10);
  const [searchMaxLimit, setSearchMaxLimit] = useState<number>(25);
  const [searchUnlimitedResults, setSearchUnlimitedResults] = useState(false);
  const [searchSortBy, setSearchSortBy] = useState('');
  const [dragJoinIndex, setDragJoinIndex] = useState<number | null>(null);
  const [isCmdEditor, setIsCmdEditor] = useState(false);
  const [cmdRows, setCmdRows] = useState<CmdEditorRow[]>([]);
  const [cmdTimeout, setCmdTimeout] = useState(15);
  const [cmdVendor, setCmdVendor] = useState('');
  const [dragCmdIndex, setDragCmdIndex] = useState<number | null>(null);
  const [isApiEditor, setIsApiEditor] = useState(false);
  const [apiBaseUrl, setApiBaseUrl] = useState('');
  const [apiEndpoint, setApiEndpoint] = useState('');
  const [apiMethod, setApiMethod] = useState('GET');
  const [apiTimeout, setApiTimeout] = useState(15);
  const [apiResultPath, setApiResultPath] = useState('');
  const [apiMaxResponseChars, setApiMaxResponseChars] = useState(16000);
  const [apiPathRows, setApiPathRows] = useState<ApiPathParamRow[]>([]);
  const [apiQueryRows, setApiQueryRows] = useState<ApiQueryParamRow[]>([]);
  const [apiHeaderRows, setApiHeaderRows] = useState<ApiHeaderRow[]>([]);
  const [apiStaticQueryRows, setApiStaticQueryRows] = useState<ApiStaticQueryRow[]>([]);
  const [apiBodyRows, setApiBodyRows] = useState<ApiBodyParamRow[]>([]);
  const [apiStaticBodyRows, setApiStaticBodyRows] = useState<ApiStaticBodyRow[]>([]);
  const [apiBodyFormat, setApiBodyFormat] = useState<'json' | 'form'>('json');
  const [dragApiPathIndex, setDragApiPathIndex] = useState<number | null>(null);
  const [dragApiQueryIndex, setDragApiQueryIndex] = useState<number | null>(null);
  const [dragApiBodyIndex, setDragApiBodyIndex] = useState<number | null>(null);
  const [renameGroupOpen, setRenameGroupOpen] = useState(false);
  const [renameGroupFrom, setRenameGroupFrom] = useState('');
  const [renameGroupTo, setRenameGroupTo] = useState('');
  const [editorSectionOpen, setEditorSectionOpen] = useState(true);
  const [previewSectionOpen, setPreviewSectionOpen] = useState(false);
  const [jsonSectionOpen, setJsonSectionOpen] = useState(false);
  const [testSectionOpen, setTestSectionOpen] = useState(false);
  const [testArgsJson, setTestArgsJson] = useState('{\n  "filters": {}\n}');
  const [testResult, setTestResult] = useState('');
  const [testResultSuccess, setTestResultSuccess] = useState<boolean | null>(null);
  const [dragFilterIndex, setDragFilterIndex] = useState<number | null>(null);
  const [dragResultIndex, setDragResultIndex] = useState<number | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'tools', page, toolSearch, groupFilter, dataSourceFilter],
    queryFn: () => toolsApi.list(tenantId, page, 100, {
      search: toolSearch || undefined,
      group: groupFilter || undefined,
      data_source_id: dataSourceFilter || undefined,
    }),
  });
  const { data: toolGroupsData } = useQuery({
    queryKey: ['tenants', tenantId, 'tools', 'groups'],
    queryFn: () => toolsApi.listGroups(tenantId),
  });
  const { data: dataSourcesData } = useQuery({
    queryKey: ['tenants', tenantId, 'data-sources', 'for-tools'],
    queryFn: () => dataSourcesApi.list(tenantId, 1, 100),
  });

  // Resolve the active data source ID for schema introspection
  const activeDataSourceId = (() => {
    // From template dropdown
    if (templateDataSourceId) return templateDataSourceId;
    // From the tool's config
    try {
      const parsed = JSON.parse(configJson);
      const runtime = typeof parsed?.x_backend_config === 'object' ? parsed.x_backend_config : {};
      if (typeof runtime?.data_source_id === 'string') return runtime.data_source_id;
    } catch { /* ignore */ }
    return null;
  })();

  const { data: schemaData } = useQuery({
    queryKey: ['tenants', tenantId, 'data-sources', activeDataSourceId, 'schema'],
    queryFn: () => dataSourcesApi.getSchema(tenantId, activeDataSourceId!),
    enabled: !!activeDataSourceId && isSearchToolEditor,
    staleTime: 5 * 60 * 1000,
  });

  const tableSuggestions = Array.from(new Set((schemaData?.tables || []).map((t) => t.full_name)));
  const allColumnSuggestions = Array.from(new Set((schemaData?.columns || []).map((c) => c.column)));
  const columnSuggestionsForTable = (tableName: string): string[] => {
    if (!schemaData) return [];
    // Collect columns from the main table and all join tables
    const tables = new Set<string>();
    tables.add(tableName);
    for (const j of searchJoinRows) {
      if (j.table.trim()) tables.add(j.table.trim());
    }
    const cols = schemaData.columns.filter((c) => tables.has(c.table));
    // Build suggestions with table alias prefix if we have joins
    const suggestions: string[] = [];
    for (const col of cols) {
      suggestions.push(col.column);
      // Also add with table alias prefix
      if (searchTableAlias && col.table === tableName) {
        suggestions.push(`${searchTableAlias}.${col.column}`);
      }
      for (const j of searchJoinRows) {
        if (j.table.trim() === col.table && j.alias.trim()) {
          suggestions.push(`${j.alias.trim()}.${col.column}`);
        }
      }
    }
    return [...new Set(suggestions)];
  };
  const activeColumnSuggestions = columnSuggestionsForTable(searchTable);

  const openCreate = () => {
    setEditId(null);
    setToolName('');
    setToolDesc('');
    setToolGroup('');
    setToolType('function');
    setToolCapabilityTags([]);
    setConfigJson('{}');
    setToolActive(true); setToolPinned(false);
    setExistingGroupChoice(null);
    setTemplateDataSourceId(null);
    setIsSearchToolEditor(false);
    setSearchFilterRows([]);
    setSearchResultRows([]);
    setSearchTable('');
    setSearchTableAlias('');
    setSearchColumns([]);
    setSearchJoinRows([]);
    setSearchStaticFilters([]);
    setSearchDateWindow(null);
    setSearchDefaultLimit(10);
    setSearchMaxLimit(25);
    setSearchUnlimitedResults(false);
    setSearchSortBy('');
    setIsCmdEditor(false);
    setCmdRows([]);
    setCmdTimeout(15);
    setCmdVendor('');
    setIsApiEditor(false);
    setApiBaseUrl('');
    setApiEndpoint('');
    setApiMethod('GET');
    setApiTimeout(15);
    setApiResultPath(''); setApiMaxResponseChars(16000);
    setApiPathRows([]);
    setApiQueryRows([]);
    setApiHeaderRows([]);
    setApiStaticQueryRows([]);
    setApiBodyRows([]);
    setApiStaticBodyRows([]);
    setApiBodyFormat('json');
    setEditorSectionOpen(true);
    setPreviewSectionOpen(false);
    setJsonSectionOpen(false);
    setTestSectionOpen(false);
    setTestArgsJson('{\n  "filters": {}\n}');
    setTestResult('');
    setTestResultSuccess(null);
    setModalOpen(true);
  };

  const fillDbTemplate = () => {
    const config = JSON.parse(JSON.stringify(DB_SEARCH_TOOL_TEMPLATE)) as Record<string, unknown>;
    const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
    if (templateDataSourceId) runtime.data_source_id = templateDataSourceId;
    config.x_backend_config = applyToolCapabilityTags(runtime, ['data_search', 'db_search']);
    setToolName('search_records');
    setToolDesc('Безопасный read-only поиск записей в БД по whitelist-фильтрам и свободному query.');
    setToolGroup('Data');
    setExistingGroupChoice('Data');
    setToolType('function');
    setToolCapabilityTags(['data_search', 'db_search']);
    setConfigJson(JSON.stringify(config, null, 2));
    setToolActive(true); setToolPinned(false);
    setIsSearchToolEditor(true);
    setIsCmdEditor(false);
    setIsApiEditor(false);
    setSearchFilterRows(readSearchFilterRows(config));
    setSearchResultRows(readSearchResultRows(config));
    setSearchTable(readSearchTable(config));
    setSearchTableAlias(readSearchTableAlias(config));
    setSearchColumns(readSearchColumns(config));
    setSearchJoinRows(readSearchJoinRows(config));
    setSearchStaticFilters(readSearchStaticFilters(config));
    setSearchDateWindow(readSearchDateWindow(config));
    const limits = readSearchLimits(config);
    setSearchDefaultLimit(limits.defaultLimit);
    setSearchMaxLimit(limits.maxLimit);
    setSearchUnlimitedResults(limits.unlimitedResults);
    setSearchSortBy(readSearchSortBy(config));
    setTestArgsJson('{\n  "filters": {}\n}');
    setTestResult('');
    setTestResultSuccess(null);
  };

  const fillApiTemplate = () => {
    const config = JSON.parse(JSON.stringify(API_FETCH_TOOL_TEMPLATE)) as Record<string, unknown>;
    const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
    if (templateDataSourceId) runtime.data_source_id = templateDataSourceId;
    config.x_backend_config = applyToolCapabilityTags(runtime, ['api_search', 'data_search']);
    setToolName('fetch_api_data');
    setToolDesc('Получение read-only данных из внешнего API по разрешённым path/query параметрам.');
    setToolGroup('Data');
    setExistingGroupChoice('Data');
    setToolType('function');
    setToolCapabilityTags(['api_search', 'data_search']);
    setConfigJson(JSON.stringify(config, null, 2));
    setToolActive(true); setToolPinned(false);
    setIsSearchToolEditor(false);
    setSearchFilterRows([]);
    setSearchResultRows([]);
    setIsCmdEditor(false);
    setCmdRows([]);
    setCmdTimeout(15);
    setCmdVendor('');
    setIsApiEditor(true);
    setApiBaseUrl(readApiBaseUrl(config));
    setApiEndpoint(readApiEndpoint(config));
    setApiMethod(readApiMethod(config));
    setApiTimeout(readApiTimeout(config));
    setApiResultPath(readApiResultPath(config)); setApiMaxResponseChars(readApiMaxResponseChars(config));
    setApiPathRows(readApiPathParamRows(config));
    setApiQueryRows(readApiQueryParamRows(config));
    setApiHeaderRows(readApiHeaderRows(config));
    setApiStaticQueryRows(readApiStaticQueryRows(config));
    setApiBodyRows(readApiBodyParamRows(config));
    setApiStaticBodyRows(readApiStaticBodyRows(config));
    setApiBodyFormat(readApiBodyFormat(config));
    setTestArgsJson('{\n  "path_values": {},\n  "query_params": {}\n}');
    setTestResult('');
    setTestResultSuccess(null);
  };

  const fillSshTemplate = () => {
    const config = JSON.parse(JSON.stringify(SSH_EXEC_TOOL_TEMPLATE)) as Record<string, unknown>;
    const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
    if (templateDataSourceId) runtime.data_source_id = templateDataSourceId;
    config.x_backend_config = applyToolCapabilityTags(runtime, ['network', 'ssh']);
    setToolName('ssh_exec');
    setToolDesc('Выполняет разрешённые команды на удалённом сервере/устройстве по SSH.');
    setToolGroup('Network');
    setExistingGroupChoice('Network');
    setToolType('function');
    setToolCapabilityTags(['network', 'ssh']);
    setConfigJson(JSON.stringify(config, null, 2));
    setToolActive(true); setToolPinned(false);
    setIsSearchToolEditor(false);
    setSearchFilterRows([]);
    setSearchResultRows([]);
    setIsApiEditor(false);
    setIsCmdEditor(true);
    setCmdRows(readCmdEditorRows(config));
    setCmdTimeout(readCmdTimeout(config));
    setCmdVendor('');
    setTestArgsJson('{\n  "command_name": "show_interfaces",\n  "params": {}\n}');
    setTestResult('');
    setTestResultSuccess(null);
  };

  const fillTelnetTemplate = () => {
    const config = JSON.parse(JSON.stringify(TELNET_EXEC_TOOL_TEMPLATE)) as Record<string, unknown>;
    const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
    if (templateDataSourceId) runtime.data_source_id = templateDataSourceId;
    config.x_backend_config = applyToolCapabilityTags(runtime, ['network', 'telnet']);
    setToolName('telnet_exec');
    setToolDesc('Выполняет разрешённые команды на сетевом оборудовании по Telnet.');
    setToolGroup('Network');
    setExistingGroupChoice('Network');
    setToolType('function');
    setToolCapabilityTags(['network', 'telnet']);
    setConfigJson(JSON.stringify(config, null, 2));
    setToolActive(true); setToolPinned(false);
    setIsSearchToolEditor(false);
    setSearchFilterRows([]);
    setSearchResultRows([]);
    setIsApiEditor(false);
    setIsCmdEditor(true);
    setCmdRows(readCmdEditorRows(config));
    setCmdTimeout(readCmdTimeout(config));
    setCmdVendor(readCmdVendor(config));
    setTestArgsJson('{\n  "command_name": "show_ports",\n  "params": {}\n}');
    setTestResult('');
    setTestResultSuccess(null);
  };

  const fillSnmpTemplate = () => {
    const config = JSON.parse(JSON.stringify(SNMP_TOOL_TEMPLATE)) as Record<string, unknown>;
    const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
    if (templateDataSourceId) runtime.data_source_id = templateDataSourceId;
    config.x_backend_config = applyToolCapabilityTags(runtime, ['network', 'snmp']);
    setToolName('snmp_query');
    setToolDesc('Запрашивает данные с сетевого оборудования по SNMP из списка разрешённых OID.');
    setToolGroup('Network');
    setExistingGroupChoice('Network');
    setToolType('function');
    setToolCapabilityTags(['network', 'snmp']);
    setConfigJson(JSON.stringify(config, null, 2));
    setToolActive(true); setToolPinned(false);
    setIsSearchToolEditor(false);
    setSearchFilterRows([]);
    setSearchResultRows([]);
    setIsCmdEditor(false);
    setCmdRows([]);
    setCmdTimeout(10);
    setCmdVendor('');
    setIsApiEditor(false);
    setTestArgsJson('{\n  "oid_name": "sys_name",\n  "params": {}\n}');
    setTestResult('');
    setTestResultSuccess(null);
  };

  const openEdit = (tool: Tool) => {
    setEditId(tool.id);
    setToolName(tool.name);
    setToolDesc(tool.description || '');
    setToolGroup(tool.group || '');
    setExistingGroupChoice(tool.group || null);
    setToolType(tool.tool_type);
    setConfigJson(JSON.stringify(tool.config_json ?? {}, null, 2));
    setToolActive(tool.is_active);
    setToolPinned(Boolean(tool.is_pinned));
    setToolCapabilityTags(readToolCapabilityTags(tool.config_json));
    // Restore data source ID from tool config
    const toolRuntime = isRecord(tool.config_json) && isRecord((tool.config_json as Record<string, unknown>).x_backend_config)
      ? (tool.config_json as Record<string, unknown>).x_backend_config as Record<string, unknown>
      : {};
    setTemplateDataSourceId(typeof toolRuntime.data_source_id === 'string' ? toolRuntime.data_source_id : null);
    const searchEditor = isSearchRecordsConfig(tool.config_json);
    setIsSearchToolEditor(searchEditor);
    setSearchFilterRows(searchEditor ? readSearchFilterRows(tool.config_json) : []);
    setSearchResultRows(searchEditor ? readSearchResultRows(tool.config_json) : []);
    if (searchEditor) {
      setSearchTable(readSearchTable(tool.config_json));
      setSearchTableAlias(readSearchTableAlias(tool.config_json));
      setSearchColumns(readSearchColumns(tool.config_json));
      setSearchJoinRows(readSearchJoinRows(tool.config_json));
      setSearchStaticFilters(readSearchStaticFilters(tool.config_json));
      setSearchDateWindow(readSearchDateWindow(tool.config_json));
      const limits = readSearchLimits(tool.config_json);
      setSearchDefaultLimit(limits.defaultLimit);
      setSearchMaxLimit(limits.maxLimit);
      setSearchUnlimitedResults(limits.unlimitedResults);
      setSearchSortBy(readSearchSortBy(tool.config_json));
    } else {
      setSearchTable('');
      setSearchTableAlias('');
      setSearchColumns([]);
      setSearchJoinRows([]);
      setSearchStaticFilters([]);
      setSearchDateWindow(null);
      setSearchDefaultLimit(10);
      setSearchMaxLimit(25);
      setSearchUnlimitedResults(false);
      setSearchSortBy('');
    }
    const cmdEditor = isCmdEditorConfig(tool.config_json);
    setIsCmdEditor(cmdEditor);
    if (cmdEditor) {
      setCmdRows(readCmdEditorRows(tool.config_json));
      setCmdTimeout(readCmdTimeout(tool.config_json));
      setCmdVendor(readCmdVendor(tool.config_json));
    } else {
      setCmdRows([]);
      setCmdTimeout(15);
      setCmdVendor('');
    }
    const apiEditor = isApiEditorConfig(tool.config_json);
    setIsApiEditor(apiEditor);
    if (apiEditor) {
      setApiBaseUrl(readApiBaseUrl(tool.config_json));
      setApiEndpoint(readApiEndpoint(tool.config_json));
      setApiMethod(readApiMethod(tool.config_json));
      setApiTimeout(readApiTimeout(tool.config_json));
      setApiResultPath(readApiResultPath(tool.config_json)); setApiMaxResponseChars(readApiMaxResponseChars(tool.config_json));
      setApiPathRows(readApiPathParamRows(tool.config_json));
      setApiQueryRows(readApiQueryParamRows(tool.config_json));
      setApiHeaderRows(readApiHeaderRows(tool.config_json));
      setApiStaticQueryRows(readApiStaticQueryRows(tool.config_json));
      setApiBodyRows(readApiBodyParamRows(tool.config_json));
      setApiStaticBodyRows(readApiStaticBodyRows(tool.config_json));
      setApiBodyFormat(readApiBodyFormat(tool.config_json));
    } else {
      setApiBaseUrl('');
      setApiEndpoint('');
      setApiMethod('GET');
      setApiTimeout(15);
      setApiResultPath(''); setApiMaxResponseChars(16000);
      setApiPathRows([]);
      setApiQueryRows([]);
      setApiHeaderRows([]);
      setApiStaticQueryRows([]);
      setApiBodyRows([]);
      setApiStaticBodyRows([]);
      setApiBodyFormat('json');
    }
    setEditorSectionOpen(true);
    setPreviewSectionOpen(false);
    setJsonSectionOpen(false);
    setTestSectionOpen(false);
    setTestResult('');
    setTestResultSuccess(null);
    setModalOpen(true);
  };

  const composeToolConfig = () => {
    let parsedConfig: Record<string, unknown>;
    try {
      parsedConfig = JSON.parse(configJson);
    } catch {
      throw new Error('Некорректный JSON конфигурации');
    }

    const nextConfig = JSON.parse(JSON.stringify(parsedConfig)) as Record<string, unknown>;
    const runtime = isRecord(nextConfig.x_backend_config) ? { ...nextConfig.x_backend_config } : {};
    if (templateDataSourceId) {
      runtime.data_source_id = templateDataSourceId;
    } else {
      delete runtime.data_source_id;
    }
    const normalizedTags = Array.from(
      new Set(toolCapabilityTags.map((tag) => tag.trim().toLowerCase()).filter(Boolean)),
    );

    nextConfig.x_backend_config = applyToolCapabilityTags(runtime, normalizedTags);

    // Sync name and description into function schema
    const functionCfg = isRecord(nextConfig.function) ? { ...nextConfig.function } : {};
    if (toolName.trim()) functionCfg.name = toolName.trim();
    if (toolDesc.trim()) functionCfg.description = toolDesc.trim();
    if (Object.keys(functionCfg).length > 0) nextConfig.function = functionCfg;

    if (isSearchToolEditor) {
      return applySearchRecordsEditor(
        nextConfig, searchFilterRows, searchResultRows,
        searchTable, searchTableAlias, searchColumns,
        searchJoinRows, searchStaticFilters, searchDateWindow,
        searchDefaultLimit, searchMaxLimit, searchUnlimitedResults,
        searchSortBy,
      );
    }

    if (isCmdEditor) {
      return applyCmdEditor(nextConfig, cmdRows, cmdTimeout, cmdVendor);
    }

    if (isApiEditor) {
      return applyApiEditor(
        nextConfig,
        apiBaseUrl, apiEndpoint, apiMethod, apiTimeout, apiResultPath,
        apiPathRows, apiQueryRows, apiHeaderRows, apiStaticQueryRows,
        apiBodyRows, apiStaticBodyRows, apiBodyFormat,
        apiMaxResponseChars,
      );
    }

    return nextConfig;
  };

  const createMutation = useMutation({
    mutationFn: (data: ToolCreate) => toolsApi.create(tenantId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'tools'] });
      setModalOpen(false);
      notifications.show({ title: 'Создано', message: 'Инструмент создан', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать инструмент', color: 'red' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ toolId, data }: { toolId: string; data: ToolUpdate }) =>
      toolsApi.update(tenantId, toolId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'tools'] });
      setModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Инструмент обновлён', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось обновить инструмент', color: 'red' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (toolId: string) => toolsApi.delete(tenantId, toolId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'tools'] });
      notifications.show({ title: 'Удалено', message: 'Инструмент удалён', color: 'green' });
    },
  });

  const renameGroupMutation = useMutation({
    mutationFn: async ({ from, to }: { from: string; to: string }) => {
      const toolsToRename = (data?.items || []).filter((tool) => (tool.group || 'Без группы') === from);
      for (const tool of toolsToRename) {
        await toolsApi.update(tenantId, tool.id, { group: to || undefined });
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'tools'] });
      setRenameGroupOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Название группы изменено', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось переименовать группу', color: 'red' });
    },
  });

  const testMutation = useMutation({
    mutationFn: async () => {
      let parsedArgs: Record<string, unknown>;
      const parsedConfig = composeToolConfig();
      try {
        parsedArgs = JSON.parse(testArgsJson);
      } catch {
        throw new Error('Некорректный JSON аргументов теста');
      }
      return toolsApi.test(tenantId, {
        config_json: parsedConfig,
        arguments: parsedArgs,
      });
    },
    onSuccess: (res) => {
      setTestResultSuccess(res.success);
      setTestResult(res.success ? res.output : (res.error || res.output || 'Ошибка'));
    },
    onError: (err: Error) => {
      setTestResultSuccess(false);
      setTestResult(err.message || 'Не удалось выполнить тест');
    },
  });

  const handleSave = () => {
    try {
      const parsedConfig = composeToolConfig();
      const trimmedToolName = toolName.trim();
      if (!trimmedToolName) {
        notifications.show({ title: 'Ошибка', message: 'Укажите название инструмента', color: 'red' });
        return;
      }
      setConfigJson(JSON.stringify(parsedConfig, null, 2));

      const toolData = {
        name: trimmedToolName,
        description: toolDesc,
        group: toolGroup || undefined,
        tool_type: toolType,
        config_json: parsedConfig,
        is_active: toolActive,
        is_pinned: toolPinned,
      };
      if (editId) {
        updateMutation.mutate({ toolId: editId, data: toolData });
      } else {
        createMutation.mutate(toolData);
      }
    } catch (error) {
      notifications.show({
        title: 'Ошибка',
        message: error instanceof Error ? error.message : 'Некорректная конфигурация инструмента',
        color: 'red',
      });
      return;
    }
  };

  // page_size matches the value passed to toolsApi.list above (100).
  // Was 20 here — produced phantom pages: clicking page 2 hit an empty
  // backend offset and the list went blank until the tab was switched.
  const totalPages = data ? Math.ceil(data.total_count / 100) : 0;
  const existingGroups = Array.from(new Set((data?.items || []).map((t) => t.group).filter(Boolean) as string[]))
    .sort()
    .map((g) => ({ value: g, label: g }));
  const previewConfig = (() => {
    try {
      const parsed = composeToolConfig();
      return JSON.stringify(parsed, null, 2);
    } catch {
      return 'Некорректный JSON конфигурации';
    }
  })();

  const moveItem = <T,>(items: T[], from: number, to: number): T[] => {
    const next = [...items];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    return next;
  };

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group>
          <Text fw={500}>Инструменты</Text>
          <TextInput
            placeholder="Поиск по name/description"
            size="xs"
            w={240}
            value={toolSearchInput}
            onChange={(e) => setToolSearchInput(e.currentTarget.value)}
          />
          <Select
            placeholder="Все группы"
            clearable
            size="xs"
            w={180}
            value={groupFilter}
            onChange={(v) => { setGroupFilter(v); setPage(1); }}
            data={(toolGroupsData || []).map((g) => ({ value: g, label: g }))}
          />
          <Select
            placeholder="Все источники"
            clearable
            size="xs"
            w={200}
            value={dataSourceFilter}
            onChange={(v) => { setDataSourceFilter(v); setPage(1); }}
            data={(dataSourcesData?.items || []).map((ds) => ({
              value: ds.id,
              label: `${ds.name} (${ds.kind})`,
            }))}
          />
          {data && (
            <Text size="xs" c="dimmed">Найдено: {data.total_count}</Text>
          )}
        </Group>
        <Button leftSection={<IconPlus size={16} />} size="sm" onClick={openCreate}>
          Добавить инструмент
        </Button>
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">
          {(toolSearch || groupFilter || dataSourceFilter) ? 'По фильтрам ничего не найдено.' : 'Инструменты не настроены.'}
        </Text>
      ) : (() => {
        // Filtering is done server-side; here we just group by category for display.
        const filtered = data.items;
        const groups = new Map<string, typeof filtered>();
        for (const tool of filtered) {
          const g = tool.group || 'Без группы';
          if (!groups.has(g)) groups.set(g, []);
          groups.get(g)!.push(tool);
        }
        const sortedGroups = Array.from(groups.entries()).sort(([a], [b]) => {
          if (a === 'Без группы') return 1;
          if (b === 'Без группы') return -1;
          return a.localeCompare(b);
        });
        return (
          <Stack gap="sm">
            {sortedGroups.map(([groupName, groupTools]) => (
              <Card key={groupName} withBorder padding="xs">
                <Group justify="space-between" mb="xs">
                  <Text size="sm" fw={600} c="dimmed">{groupName} ({groupTools.length})</Text>
                  {groupName !== 'Без группы' && (
                    <ActionIcon
                      variant="subtle"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setRenameGroupFrom(groupName);
                        setRenameGroupTo(groupName);
                        setRenameGroupOpen(true);
                      }}
                    >
                      <IconEdit size={14} />
                    </ActionIcon>
                  )}
                </Group>
                <Table striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Название</Table.Th>
                      <Table.Th>Описание</Table.Th>
                      <Table.Th>Протокол</Table.Th>
                      <Table.Th>Метки</Table.Th>
                      <Table.Th>Статус</Table.Th>
                      <Table.Th>Действия</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {groupTools.map((tool) => (
                        <Table.Tr
                          key={tool.id}
                          style={{ cursor: 'pointer' }}
                          onClick={() => openEdit(tool)}
                        >
                          <Table.Td><Text size="sm" fw={500}>{tool.name}</Text></Table.Td>
                          <Table.Td><Text size="sm" c="dimmed" lineClamp={1}>{tool.description || '-'}</Text></Table.Td>
                          <Table.Td>
                            {(() => {
                              const info = getToolHandlerInfo(tool.config_json);
                              const Icon = info.icon;
                              return (
                                <Badge variant="light" color={info.color} size="sm" leftSection={<Icon size={12} />}>
                                  {info.label}
                                </Badge>
                              );
                            })()}
                          </Table.Td>
                          <Table.Td>
                            {readToolCapabilityTags(tool.config_json).length ? (
                              <Group gap={4}>
                                {readToolCapabilityTags(tool.config_json).map((tag) => (
                                  <Badge key={tag} variant="light" size="sm">{tag}</Badge>
                                ))}
                              </Group>
                            ) : (
                              <Text size="sm" c="dimmed">—</Text>
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Group gap={4} wrap="nowrap">
                              <Badge color={tool.is_active ? 'green' : 'gray'} size="sm">
                                {tool.is_active ? 'Активный' : 'Неактивный'}
                              </Badge>
                              {tool.is_pinned && (
                                <Tooltip label="Закреплён в LLM-контексте">
                                  <Badge color="blue" size="sm" variant="light">📌</Badge>
                                </Tooltip>
                              )}
                            </Group>
                          </Table.Td>
                        <Table.Td>
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (window.confirm(`Удалить инструмент "${tool.name}"?`)) {
                                deleteMutation.mutate(tool.id);
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
              </Card>
            ))}
            {totalPages > 1 && (
              <Center><Pagination total={totalPages} value={page} onChange={setPage} /></Center>
            )}
          </Stack>
        );
      })()}

      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editId ? 'Редактировать инструмент' : 'Создать инструмент'}
        size="90%"
        styles={{ body: { maxHeight: 'calc(100vh - 120px)', overflowY: 'auto' } }}
      >
        <Stack gap="sm">
          <Group justify="space-between" align="center">
            <Select
              placeholder="Источник данных"
              size="xs"
              clearable
              w={220}
              value={templateDataSourceId}
              onChange={setTemplateDataSourceId}
              data={(dataSourcesData?.items || []).map((ds) => ({
                value: ds.id,
                label: `${ds.name} (${ds.kind})`,
              }))}
            />
            <Group gap={4}>
              <Button variant="light" size="xs" onClick={fillDbTemplate}>SELECT из БД</Button>
              <Button variant="light" size="xs" onClick={fillApiTemplate}>API</Button>
              <Button variant="light" size="xs" color="teal" onClick={fillSshTemplate}>SSH</Button>
              <Button variant="light" size="xs" color="orange" onClick={fillTelnetTemplate}>Telnet</Button>
              <Button variant="light" size="xs" color="grape" onClick={fillSnmpTemplate}>SNMP</Button>
            </Group>
          </Group>
          <SimpleGrid cols={3}>
            <TextInput
              label="Название"
              placeholder="search_clients"
              value={toolName}
              onChange={(e) => setToolName(e.currentTarget.value)}
              required
            />
            <TextInput
              label="Группа"
              placeholder="Data"
              value={toolGroup}
              onChange={(e) => setToolGroup(e.currentTarget.value)}
              rightSection={existingGroups.length > 0 ? undefined : undefined}
            />
            {existingGroups.length > 0 ? (
              <Select
                label="Или выбрать группу"
                clearable
                data={existingGroups}
                value={existingGroupChoice}
                onChange={(value) => {
                  setExistingGroupChoice(value);
                  if (value) setToolGroup(value);
                }}
              />
            ) : (
              <TextInput
                label="Тип"
                placeholder="function"
                value={toolType}
                onChange={(e) => setToolType(e.currentTarget.value)}
              />
            )}
          </SimpleGrid>
          <SimpleGrid cols={existingGroups.length > 0 ? 2 : 1}>
            <Textarea
              label="Описание для LLM"
              placeholder="Что делает инструмент — LLM использует это для решения, когда его вызвать"
              value={toolDesc}
              onChange={(e) => setToolDesc(e.currentTarget.value)}
              autosize
              minRows={1}
              maxRows={3}
            />
            {existingGroups.length > 0 && (
              <SimpleGrid cols={2}>
                <TextInput
                  label="Тип"
                  placeholder="function"
                  value={toolType}
                  onChange={(e) => setToolType(e.currentTarget.value)}
                />
                <TagsInput
                  label="Метки"
                  placeholder="data_search, billing"
                  value={toolCapabilityTags}
                  onChange={setToolCapabilityTags}
                  splitChars={[' ', ',', ';']}
                />
              </SimpleGrid>
            )}
          </SimpleGrid>
          {!existingGroups.length && (
            <TagsInput
              label="Метки возможностей"
              placeholder="network, data_search, billing"
              value={toolCapabilityTags}
              onChange={setToolCapabilityTags}
              splitChars={[' ', ',', ';']}
            />
          )}
          {isSearchToolEditor && (
            <Card withBorder padding="xs">
              <Stack gap="xs">
                <Group justify="space-between">
                  <Text size="sm" fw={600}>Query Builder — search_records</Text>
                  <Button variant="subtle" size="xs" onClick={() => setEditorSectionOpen((v) => !v)}>
                    {editorSectionOpen ? 'Свернуть' : 'Развернуть'}
                  </Button>
                </Group>
                {editorSectionOpen && (
                  <Stack gap="xs">
                    {/* === Source + Limits row === */}
                    <SimpleGrid cols={6}>
                      <Autocomplete
                        label="Таблица / view"
                        placeholder="public.contracts_view"
                        value={searchTable}
                        onChange={setSearchTable}
                        data={tableSuggestions}
                        limit={20}
                        required
                      />
                      <TextInput
                        label="Алиас"
                        placeholder="c"
                        value={searchTableAlias}
                        onChange={(e) => setSearchTableAlias(e.currentTarget.value)}
                      />
                      <NumberInput
                        label="Лимит"
                        min={1} max={1000}
                        value={searchDefaultLimit}
                        onChange={(val) => setSearchDefaultLimit(typeof val === 'number' ? val : 10)}
                      />
                      <NumberInput
                        label="Макс. лимит"
                        min={1} max={10000}
                        value={searchMaxLimit}
                        onChange={(val) => setSearchMaxLimit(typeof val === 'number' ? val : 25)}
                      />
                      <Autocomplete
                        label="Сортировка"
                        placeholder="id"
                        value={searchSortBy}
                        onChange={setSearchSortBy}
                        data={activeColumnSuggestions}
                        limit={20}
                      />
                      <Stack gap={0} justify="flex-end" pb={1}>
                        <Switch
                          label="Без лимита"
                          checked={searchUnlimitedResults}
                          onChange={(e) => setSearchUnlimitedResults(e.currentTarget.checked)}
                        />
                      </Stack>
                    </SimpleGrid>

                    {/* === Joins === */}
                    <Group justify="space-between">
                      <Text size="xs" fw={600} c="dimmed" tt="uppercase">Joins</Text>
                      <Button
                        variant="light"
                        size="xs"
                        leftSection={<IconPlus size={14} />}
                        onClick={() => setSearchJoinRows([...searchJoinRows, { type: 'left', table: '', alias: '', left_column: '', right_column: '' }])}
                      >
                        Добавить
                      </Button>
                    </Group>
                    {searchJoinRows.length > 0 && (
                      <Table striped withTableBorder>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th w={36}></Table.Th>
                            <Table.Th w={100}>Тип</Table.Th>
                            <Table.Th>Таблица</Table.Th>
                            <Table.Th w={80}>Алиас</Table.Th>
                            <Table.Th>Левая колонка</Table.Th>
                            <Table.Th>Правая колонка</Table.Th>
                            <Table.Th w={44}></Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {searchJoinRows.map((row, index) => (
                            <Table.Tr
                              key={`join-${index}`}
                              onDragOver={(e) => e.preventDefault()}
                              onDrop={() => {
                                if (dragJoinIndex === null || dragJoinIndex === index) return;
                                setSearchJoinRows(moveItem(searchJoinRows, dragJoinIndex, index));
                                setDragJoinIndex(null);
                              }}
                            >
                              <Table.Td>
                                <ActionIcon variant="subtle" draggable onDragStart={() => setDragJoinIndex(index)} onDragEnd={() => setDragJoinIndex(null)}>
                                  <IconGripVertical size={14} />
                                </ActionIcon>
                              </Table.Td>
                              <Table.Td>
                                <Select
                                  data={[{ value: 'left', label: 'LEFT' }, { value: 'inner', label: 'INNER' }]}
                                  value={row.type}
                                  onChange={(value) => {
                                    const next = [...searchJoinRows];
                                    next[index] = { ...row, type: (value as 'left' | 'inner') || 'left' };
                                    setSearchJoinRows(next);
                                  }}
                                />
                              </Table.Td>
                              <Table.Td>
                                <Autocomplete placeholder="public.streets" value={row.table} data={tableSuggestions} limit={20} onChange={(val) => {
                                  const next = [...searchJoinRows]; next[index] = { ...row, table: val }; setSearchJoinRows(next);
                                }} />
                              </Table.Td>
                              <Table.Td>
                                <TextInput placeholder="s" value={row.alias} onChange={(e) => {
                                  const next = [...searchJoinRows]; next[index] = { ...row, alias: e.currentTarget.value }; setSearchJoinRows(next);
                                }} />
                              </Table.Td>
                              <Table.Td>
                                <Autocomplete placeholder="c.street_id" value={row.left_column} data={activeColumnSuggestions} limit={20} onChange={(val) => {
                                  const next = [...searchJoinRows]; next[index] = { ...row, left_column: val }; setSearchJoinRows(next);
                                }} />
                              </Table.Td>
                              <Table.Td>
                                <Autocomplete placeholder="s.id" value={row.right_column} data={activeColumnSuggestions} limit={20} onChange={(val) => {
                                  const next = [...searchJoinRows]; next[index] = { ...row, right_column: val }; setSearchJoinRows(next);
                                }} />
                              </Table.Td>
                              <Table.Td>
                                <ActionIcon variant="subtle" color="red" onClick={() => setSearchJoinRows(searchJoinRows.filter((_, i) => i !== index))}>
                                  <IconTrash size={14} />
                                </ActionIcon>
                              </Table.Td>
                            </Table.Tr>
                          ))}
                        </Table.Tbody>
                      </Table>
                    )}

                    {/* === Filter Fields === */}
                    <Group justify="space-between">
                      <Text size="xs" fw={600} c="dimmed" tt="uppercase">Фильтры (whitelist для LLM)</Text>
                      <Button
                        variant="light"
                        size="xs"
                        leftSection={<IconPlus size={14} />}
                        onClick={() => setSearchFilterRows([...searchFilterRows, { alias: '', column: '', mode: 'contains', description: '' }])}
                      >
                        Добавить фильтр
                      </Button>
                    </Group>
                    <Table striped withTableBorder>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th w={36}></Table.Th>
                          <Table.Th>Алиас</Table.Th>
                          <Table.Th>Колонка</Table.Th>
                          <Table.Th w={130}>Режим</Table.Th>
                          <Table.Th>Описание для LLM</Table.Th>
                          <Table.Th w={44}></Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {searchFilterRows.map((row, index) => (
                          <Table.Tr
                            key={`filter-${index}`}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={() => {
                              if (dragFilterIndex === null || dragFilterIndex === index) return;
                              setSearchFilterRows(moveItem(searchFilterRows, dragFilterIndex, index));
                              setDragFilterIndex(null);
                            }}
                          >
                            <Table.Td>
                              <ActionIcon variant="subtle" draggable onDragStart={() => setDragFilterIndex(index)} onDragEnd={() => setDragFilterIndex(null)}>
                                <IconGripVertical size={14} />
                              </ActionIcon>
                            </Table.Td>
                            <Table.Td>
                              <TextInput value={row.alias} placeholder="contract_number" onChange={(e) => {
                                const next = [...searchFilterRows]; next[index] = { ...row, alias: e.currentTarget.value }; setSearchFilterRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <Autocomplete value={row.column} placeholder="c.dogovor_num" data={activeColumnSuggestions} limit={20} onChange={(val) => {
                                const next = [...searchFilterRows]; next[index] = { ...row, column: val }; setSearchFilterRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <Select
                                data={[
                                  { value: 'exact', label: 'Точное' },
                                  { value: 'contains', label: 'Содержит' },
                                  { value: 'starts_with', label: 'Начинается' },
                                  { value: 'eq', label: '= число' },
                                  { value: 'gte', label: '>= число' },
                                  { value: 'lte', label: '<= число' },
                                ]}
                                value={row.mode}
                                onChange={(value) => {
                                  const next = [...searchFilterRows]; next[index] = { ...row, mode: value || 'contains' }; setSearchFilterRows(next);
                                }}
                              />
                            </Table.Td>
                            <Table.Td>
                              <TextInput value={row.description} placeholder="Номер договора или его часть" onChange={(e) => {
                                const next = [...searchFilterRows]; next[index] = { ...row, description: e.currentTarget.value }; setSearchFilterRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <ActionIcon variant="subtle" color="red" onClick={() => setSearchFilterRows(searchFilterRows.filter((_, i) => i !== index))}>
                                <IconTrash size={14} />
                              </ActionIcon>
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>

                    {/* === Search Columns === */}
                    <TagsInput
                      label="Колонки свободного поиска (query → ILIKE)"
                      placeholder="name, address, notes"
                      value={searchColumns}
                      onChange={setSearchColumns}
                      splitChars={[',', ';']}
                    />

                    {/* === Result Columns === */}
                    <Group justify="space-between">
                      <Text size="xs" fw={600} c="dimmed" tt="uppercase">Колонки результата (SELECT)</Text>
                      <Button
                        variant="light"
                        size="xs"
                        leftSection={<IconPlus size={14} />}
                        onClick={() => setSearchResultRows([...searchResultRows, { alias: '', column: '', description: '' }])}
                      >
                        Добавить колонку
                      </Button>
                    </Group>
                    <Table striped withTableBorder>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th w={36}></Table.Th>
                          <Table.Th w="22%">Алиас (имя в выдаче)</Table.Th>
                          <Table.Th w="28%">Колонка в БД</Table.Th>
                          <Table.Th>Описание для LLM (интерпретация значений)</Table.Th>
                          <Table.Th w={44}></Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {searchResultRows.map((row, index) => (
                          <Table.Tr
                            key={`result-${index}`}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={() => {
                              if (dragResultIndex === null || dragResultIndex === index) return;
                              setSearchResultRows(moveItem(searchResultRows, dragResultIndex, index));
                              setDragResultIndex(null);
                            }}
                          >
                            <Table.Td>
                              <ActionIcon variant="subtle" draggable onDragStart={() => setDragResultIndex(index)} onDragEnd={() => setDragResultIndex(null)}>
                                <IconGripVertical size={14} />
                              </ActionIcon>
                            </Table.Td>
                            <Table.Td>
                              <TextInput value={row.alias} placeholder="street" onChange={(e) => {
                                const next = [...searchResultRows]; next[index] = { ...row, alias: e.currentTarget.value }; setSearchResultRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <Autocomplete value={row.column} placeholder="s.name" data={activeColumnSuggestions} limit={20} onChange={(val) => {
                                const next = [...searchResultRows]; next[index] = { ...row, column: val }; setSearchResultRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <TextInput
                                value={row.description}
                                placeholder="например: 1=активен, 0=отключен"
                                onChange={(e) => {
                                  const next = [...searchResultRows]; next[index] = { ...row, description: e.currentTarget.value }; setSearchResultRows(next);
                                }}
                              />
                            </Table.Td>
                            <Table.Td>
                              <ActionIcon variant="subtle" color="red" onClick={() => setSearchResultRows(searchResultRows.filter((_, i) => i !== index))}>
                                <IconTrash size={14} />
                              </ActionIcon>
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>

                    {/* === Static Filters + Date Window === */}
                    <SimpleGrid cols={2}>
                      <div>
                        <Group justify="space-between" mb={4}>
                          <Text size="xs" fw={600} c="dimmed" tt="uppercase">Статические фильтры</Text>
                          <Button variant="light" size="xs" leftSection={<IconPlus size={14} />}
                            onClick={() => setSearchStaticFilters([...searchStaticFilters, { key: '', value: '' }])}
                          >
                            Добавить
                          </Button>
                        </Group>
                        {searchStaticFilters.length > 0 && (
                          <Table striped withTableBorder>
                            <Table.Thead>
                              <Table.Tr>
                                <Table.Th>Колонка</Table.Th>
                                <Table.Th>Значение</Table.Th>
                                <Table.Th w={36}></Table.Th>
                              </Table.Tr>
                            </Table.Thead>
                            <Table.Tbody>
                              {searchStaticFilters.map((row, index) => (
                                <Table.Tr key={`sf-${index}`}>
                                  <Table.Td>
                                    <Autocomplete size="xs" placeholder="is_active" value={row.key} data={allColumnSuggestions} limit={20} onChange={(val) => {
                                      const next = [...searchStaticFilters]; next[index] = { ...row, key: val }; setSearchStaticFilters(next);
                                    }} />
                                  </Table.Td>
                                  <Table.Td>
                                    <TextInput size="xs" placeholder="true" value={row.value} onChange={(e) => {
                                      const next = [...searchStaticFilters]; next[index] = { ...row, value: e.currentTarget.value }; setSearchStaticFilters(next);
                                    }} />
                                  </Table.Td>
                                  <Table.Td>
                                    <ActionIcon variant="subtle" color="red" size="sm" onClick={() => setSearchStaticFilters(searchStaticFilters.filter((_, i) => i !== index))}>
                                      <IconTrash size={14} />
                                    </ActionIcon>
                                  </Table.Td>
                                </Table.Tr>
                              ))}
                            </Table.Tbody>
                          </Table>
                        )}
                        {searchStaticFilters.length === 0 && (
                          <Text size="xs" c="dimmed">Нет статических фильтров</Text>
                        )}
                      </div>
                      <div>
                        <Group justify="space-between" mb={4}>
                          <Text size="xs" fw={600} c="dimmed" tt="uppercase">Окно по дате</Text>
                          <Switch
                            size="xs"
                            checked={searchDateWindow !== null}
                            onChange={(e) => setSearchDateWindow(e.currentTarget.checked ? { column: '', days: 30 } : null)}
                          />
                        </Group>
                        {searchDateWindow && (
                          <SimpleGrid cols={2}>
                            <Autocomplete
                              label="Колонка"
                              size="xs"
                              placeholder="created_at"
                              value={searchDateWindow.column}
                              onChange={(val) => setSearchDateWindow({ ...searchDateWindow, column: val })}
                              data={activeColumnSuggestions}
                              limit={20}
                            />
                            <NumberInput
                              label="Дней"
                              size="xs"
                              min={1}
                              value={searchDateWindow.days}
                              onChange={(val) => setSearchDateWindow({ ...searchDateWindow, days: typeof val === 'number' ? val : 30 })}
                            />
                          </SimpleGrid>
                        )}
                        {!searchDateWindow && (
                          <Text size="xs" c="dimmed">Выключено</Text>
                        )}
                      </div>
                    </SimpleGrid>
                  </Stack>
                )}
              </Stack>
            </Card>
          )}
          {isCmdEditor && (
            <Card withBorder padding="xs">
              <Stack gap="xs">
                <Group justify="space-between">
                  <Text size="sm" fw={600}>Command Builder — {cmdVendor ? `${cmdVendor} / ` : ''}SSH/Telnet</Text>
                  <Button variant="subtle" size="xs" onClick={() => setEditorSectionOpen((v) => !v)}>
                    {editorSectionOpen ? 'Свернуть' : 'Развернуть'}
                  </Button>
                </Group>
                {editorSectionOpen && (
                  <Stack gap="xs">
                    <SimpleGrid cols={3}>
                      <NumberInput label="Таймаут (сек)" min={1} max={30} value={cmdTimeout} onChange={(val) => setCmdTimeout(typeof val === 'number' ? val : 15)} />
                      <TextInput label="Vendor" placeholder="dlink, bdcom, juniper" value={cmdVendor} onChange={(e) => setCmdVendor(e.currentTarget.value)} />
                      <Stack gap={0} justify="flex-end">
                        <Button variant="light" size="xs" leftSection={<IconPlus size={14} />} onClick={() => setCmdRows([...cmdRows, { name: '', command: '', description: '', params: '' }])}>
                          Добавить команду
                        </Button>
                      </Stack>
                    </SimpleGrid>
                    <Table striped withTableBorder>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th w={36}></Table.Th>
                          <Table.Th w={160}>Имя</Table.Th>
                          <Table.Th>Шаблон команды</Table.Th>
                          <Table.Th w={140}>Параметры</Table.Th>
                          <Table.Th>Описание для LLM</Table.Th>
                          <Table.Th w={36}></Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {cmdRows.map((row, index) => (
                          <Table.Tr
                            key={`cmd-${index}`}
                            onDragOver={(e) => e.preventDefault()}
                            onDrop={() => {
                              if (dragCmdIndex === null || dragCmdIndex === index) return;
                              setCmdRows(moveItem(cmdRows, dragCmdIndex, index));
                              setDragCmdIndex(null);
                            }}
                          >
                            <Table.Td>
                              <ActionIcon variant="subtle" draggable onDragStart={() => setDragCmdIndex(index)} onDragEnd={() => setDragCmdIndex(null)}>
                                <IconGripVertical size={14} />
                              </ActionIcon>
                            </Table.Td>
                            <Table.Td>
                              <TextInput size="xs" placeholder="tail_messages" value={row.name} ff="monospace" onChange={(e) => {
                                const next = [...cmdRows]; next[index] = { ...row, name: e.currentTarget.value }; setCmdRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <TextInput size="xs" placeholder="tail -n {lines} /var/log/messages" value={row.command} ff="monospace" onChange={(e) => {
                                const next = [...cmdRows]; next[index] = { ...row, command: e.currentTarget.value }; setCmdRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <TextInput size="xs" placeholder="lines, keyword" value={row.params} onChange={(e) => {
                                const next = [...cmdRows]; next[index] = { ...row, params: e.currentTarget.value }; setCmdRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <TextInput size="xs" placeholder="Последние N строк лога" value={row.description} onChange={(e) => {
                                const next = [...cmdRows]; next[index] = { ...row, description: e.currentTarget.value }; setCmdRows(next);
                              }} />
                            </Table.Td>
                            <Table.Td>
                              <ActionIcon variant="subtle" color="red" size="sm" onClick={() => setCmdRows(cmdRows.filter((_, i) => i !== index))}>
                                <IconTrash size={14} />
                              </ActionIcon>
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                    {cmdRows.length === 0 && (
                      <Text size="xs" c="dimmed" ta="center">Нет команд. Нажмите «Добавить команду».</Text>
                    )}
                  </Stack>
                )}
              </Stack>
            </Card>
          )}
          {isApiEditor && (
            <Card withBorder padding="xs">
              <Stack gap="xs">
                <Group justify="space-between">
                  <Text size="sm" fw={600}>API Builder — fetch_api_data</Text>
                  <Button variant="subtle" size="xs" onClick={() => setEditorSectionOpen((v) => !v)}>
                    {editorSectionOpen ? 'Свернуть' : 'Развернуть'}
                  </Button>
                </Group>
                {editorSectionOpen && (
                  <Stack gap="xs">
                    {/* === Endpoint rows === */}
                    <SimpleGrid cols={12}>
                      <TextInput
                        label="Base URL"
                        placeholder="https://api.example.com"
                        value={apiBaseUrl}
                        onChange={(e) => setApiBaseUrl(e.currentTarget.value)}
                        description={templateDataSourceId ? 'Можно оставить пустым — берётся из источника данных' : ' '}
                        style={{ gridColumn: 'span 7' }}
                      />
                      <TextInput
                        label="Endpoint"
                        placeholder="/clients/{client_id}/contracts"
                        value={apiEndpoint}
                        onChange={(e) => setApiEndpoint(e.currentTarget.value)}
                        ff="monospace"
                        required
                        description=" "
                        style={{ gridColumn: 'span 5' }}
                      />
                    </SimpleGrid>
                    <SimpleGrid cols={12}>
                      <Select
                        label="Метод"
                        data={[{ value: 'GET', label: 'GET' }, { value: 'POST', label: 'POST' }]}
                        value={apiMethod}
                        onChange={(val) => setApiMethod(val || 'GET')}
                        description=" "
                        style={{ gridColumn: apiMethod === 'POST' ? 'span 3' : 'span 4' }}
                      />
                      {apiMethod === 'POST' && (
                        <Select
                          label="Формат body"
                          data={[{ value: 'json', label: 'JSON' }, { value: 'form', label: 'form-urlencoded' }]}
                          value={apiBodyFormat}
                          onChange={(v) => setApiBodyFormat(v === 'form' ? 'form' : 'json')}
                          description=" "
                          style={{ gridColumn: 'span 3' }}
                        />
                      )}
                      <NumberInput
                        label="Таймаут (сек)"
                        min={1} max={120}
                        value={apiTimeout}
                        onChange={(val) => setApiTimeout(typeof val === 'number' ? val : 15)}
                        description=" "
                        style={{ gridColumn: apiMethod === 'POST' ? 'span 2' : 'span 3' }}
                      />
                      <TextInput
                        label="Result path"
                        placeholder="data"
                        value={apiResultPath}
                        onChange={(e) => setApiResultPath(e.currentTarget.value)}
                        description="Ключ в JSON, из которого брать массив/объект"
                        style={{ gridColumn: apiMethod === 'POST' ? 'span 2' : 'span 3' }}
                      />
                      <NumberInput
                        label="Лимит ответа (симв.)"
                        min={1000} max={200000} step={1000}
                        value={apiMaxResponseChars}
                        onChange={(val) => setApiMaxResponseChars(typeof val === 'number' ? val : 16000)}
                        description="Сколько символов API-ответа уйдёт в LLM"
                        style={{ gridColumn: apiMethod === 'POST' ? 'span 2' : 'span 2' }}
                      />
                    </SimpleGrid>

                    {/* === Path params === */}
                    <Group justify="space-between">
                      <Text size="xs" fw={600} c="dimmed" tt="uppercase">Path-параметры (плейсхолдеры в endpoint)</Text>
                      <Button
                        variant="light"
                        size="xs"
                        leftSection={<IconPlus size={14} />}
                        onClick={() => setApiPathRows([...apiPathRows, { name: '', description: '', useEnum: false, enumValues: [] }])}
                      >
                        Добавить
                      </Button>
                    </Group>
                    {apiPathRows.length > 0 && (
                      <Table striped withTableBorder>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th w={36}></Table.Th>
                            <Table.Th w="22%">Имя плейсхолдера</Table.Th>
                            <Table.Th>Описание для LLM</Table.Th>
                            <Table.Th w={120}>Enum значения</Table.Th>
                            <Table.Th w={44}></Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {apiPathRows.map((row, index) => {
                            const queryAliases = apiQueryRows
                              .map((q) => q.alias.trim())
                              .filter((a, i, arr) => a && arr.indexOf(a) === i);
                            const enumDupCounts = new Map<string, number>();
                            for (const ev of row.enumValues) {
                              const v = ev.value.trim();
                              if (!v) continue;
                              enumDupCounts.set(v, (enumDupCounts.get(v) || 0) + 1);
                            }
                            const duplicateEnumValues = Array.from(enumDupCounts.entries())
                              .filter(([, c]) => c > 1)
                              .map(([v]) => v);
                            return (
                              <Fragment key={`apipath-frag-${index}`}>
                                <Table.Tr
                                  onDragOver={(e) => e.preventDefault()}
                                  onDrop={() => {
                                    if (dragApiPathIndex === null || dragApiPathIndex === index) return;
                                    setApiPathRows(moveItem(apiPathRows, dragApiPathIndex, index));
                                    setDragApiPathIndex(null);
                                  }}
                                >
                                  <Table.Td>
                                    <ActionIcon variant="subtle" draggable onDragStart={() => setDragApiPathIndex(index)} onDragEnd={() => setDragApiPathIndex(null)}>
                                      <IconGripVertical size={14} />
                                    </ActionIcon>
                                  </Table.Td>
                                  <Table.Td>
                                    <TextInput placeholder="client_id" value={row.name} ff="monospace" onChange={(e) => {
                                      const next = [...apiPathRows]; next[index] = { ...row, name: e.currentTarget.value }; setApiPathRows(next);
                                    }} />
                                  </Table.Td>
                                  <Table.Td>
                                    <Textarea
                                      placeholder="Описание поля. Если включён enum — это базовое описание перед списком значений."
                                      value={row.description}
                                      autosize
                                      minRows={1}
                                      maxRows={10}
                                      onChange={(e) => {
                                        const next = [...apiPathRows]; next[index] = { ...row, description: e.currentTarget.value }; setApiPathRows(next);
                                      }}
                                    />
                                  </Table.Td>
                                  <Table.Td>
                                    <Switch
                                      size="xs"
                                      label={row.useEnum ? `${row.enumValues.length}` : 'Включить'}
                                      checked={row.useEnum}
                                      onChange={(e) => {
                                        const next = [...apiPathRows];
                                        next[index] = { ...row, useEnum: e.currentTarget.checked };
                                        setApiPathRows(next);
                                      }}
                                    />
                                  </Table.Td>
                                  <Table.Td>
                                    <ActionIcon variant="subtle" color="red" onClick={() => setApiPathRows(apiPathRows.filter((_, i) => i !== index))}>
                                      <IconTrash size={14} />
                                    </ActionIcon>
                                  </Table.Td>
                                </Table.Tr>
                                {row.useEnum && (
                                  <Table.Tr>
                                    <Table.Td></Table.Td>
                                    <Table.Td colSpan={4} style={{ background: 'light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-6))' }}>
                                      <Stack gap="xs" p="xs">
                                        <Group justify="space-between">
                                          <Text size="xs" fw={600} c="dimmed">
                                            Допустимые значения для <Code>{row.name || '?'}</Code>
                                            {queryAliases.length > 0 && (
                                              <Text component="span" size="xs" c="dimmed" fw={400}>
                                                {' '}— отметьте, какие query-параметры обязательны для каждого значения
                                              </Text>
                                            )}
                                          </Text>
                                          <Button
                                            variant="light"
                                            size="xs"
                                            leftSection={<IconPlus size={12} />}
                                            onClick={() => {
                                              const next = [...apiPathRows];
                                              next[index] = {
                                                ...row,
                                                enumValues: [...row.enumValues, { value: '', description: '', requires: [] }],
                                              };
                                              setApiPathRows(next);
                                            }}
                                          >
                                            Добавить значение
                                          </Button>
                                        </Group>
                                        {duplicateEnumValues.length > 0 && (
                                          <Alert color="red" p="xs" title="Дубликаты значений" icon={<IconAlertCircle size={16} />}>
                                            <Text size="xs">
                                              Повторяются: {duplicateEnumValues.map((v) => <Code key={v}>{v}</Code>).reduce((acc, el, i) => i === 0 ? [el] : [...acc, ', ', el], [] as React.ReactNode[])}.
                                              {' '}Каждое значение enum должно быть уникальным — иначе схема инструмента будет невалидной.
                                            </Text>
                                          </Alert>
                                        )}
                                        {row.enumValues.length === 0 ? (
                                          <Text size="xs" c="dimmed">Нет значений. Включите enum и добавьте варианты — LLM получит их в схеме инструмента.</Text>
                                        ) : (
                                          <Table withTableBorder striped>
                                            <Table.Thead>
                                              <Table.Tr>
                                                <Table.Th w="20%">Значение</Table.Th>
                                                <Table.Th>Описание</Table.Th>
                                                {queryAliases.map((alias) => (
                                                  <Table.Th key={`enum-col-${alias}`} w={90} ta="center" style={{ fontFamily: 'monospace' }}>
                                                    {alias}
                                                  </Table.Th>
                                                ))}
                                                <Table.Th w={36}></Table.Th>
                                              </Table.Tr>
                                            </Table.Thead>
                                            <Table.Tbody>
                                              {row.enumValues.map((ev, evIndex) => {
                                                const trimmedValue = ev.value.trim();
                                                const isDuplicate = !!trimmedValue && (enumDupCounts.get(trimmedValue) || 0) > 1;
                                                return (
                                                <Table.Tr key={`enumval-${index}-${evIndex}`}>
                                                  <Table.Td>
                                                    <TextInput
                                                      size="xs"
                                                      placeholder="log"
                                                      ff="monospace"
                                                      value={ev.value}
                                                      error={isDuplicate ? 'дубликат' : undefined}
                                                      onChange={(e) => {
                                                        const next = [...apiPathRows];
                                                        const evs = [...row.enumValues];
                                                        evs[evIndex] = { ...ev, value: e.currentTarget.value };
                                                        next[index] = { ...row, enumValues: evs };
                                                        setApiPathRows(next);
                                                      }}
                                                    />
                                                  </Table.Td>
                                                  <Table.Td>
                                                    <Textarea
                                                      size="xs"
                                                      placeholder="Системный лог свича (~200 строк)"
                                                      value={ev.description}
                                                      autosize
                                                      minRows={1}
                                                      maxRows={6}
                                                      onChange={(e) => {
                                                        const next = [...apiPathRows];
                                                        const evs = [...row.enumValues];
                                                        evs[evIndex] = { ...ev, description: e.currentTarget.value };
                                                        next[index] = { ...row, enumValues: evs };
                                                        setApiPathRows(next);
                                                      }}
                                                    />
                                                  </Table.Td>
                                                  {queryAliases.map((alias) => (
                                                    <Table.Td key={`enumval-${index}-${evIndex}-${alias}`} ta="center">
                                                      <Checkbox
                                                        size="xs"
                                                        checked={ev.requires.includes(alias)}
                                                        onChange={(e) => {
                                                          const next = [...apiPathRows];
                                                          const evs = [...row.enumValues];
                                                          const cur = new Set(ev.requires);
                                                          if (e.currentTarget.checked) cur.add(alias);
                                                          else cur.delete(alias);
                                                          evs[evIndex] = { ...ev, requires: Array.from(cur) };
                                                          next[index] = { ...row, enumValues: evs };
                                                          setApiPathRows(next);
                                                        }}
                                                      />
                                                    </Table.Td>
                                                  ))}
                                                  <Table.Td>
                                                    <ActionIcon
                                                      variant="subtle"
                                                      color="red"
                                                      size="sm"
                                                      onClick={() => {
                                                        const next = [...apiPathRows];
                                                        next[index] = {
                                                          ...row,
                                                          enumValues: row.enumValues.filter((_, i) => i !== evIndex),
                                                        };
                                                        setApiPathRows(next);
                                                      }}
                                                    >
                                                      <IconTrash size={12} />
                                                    </ActionIcon>
                                                  </Table.Td>
                                                </Table.Tr>
                                                );
                                              })}
                                            </Table.Tbody>
                                          </Table>
                                        )}
                                      </Stack>
                                    </Table.Td>
                                  </Table.Tr>
                                )}
                              </Fragment>
                            );
                          })}
                        </Table.Tbody>
                      </Table>
                    )}

                    {/* === Query params (whitelist for LLM) === */}
                    <Group justify="space-between">
                      <Text size="xs" fw={600} c="dimmed" tt="uppercase">Query-параметры (whitelist для LLM)</Text>
                      <Button
                        variant="light"
                        size="xs"
                        leftSection={<IconPlus size={14} />}
                        onClick={() => setApiQueryRows([...apiQueryRows, { alias: '', target: '', description: '' }])}
                      >
                        Добавить
                      </Button>
                    </Group>
                    {apiQueryRows.length > 0 && (
                      <Table striped withTableBorder>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th w={36}></Table.Th>
                            <Table.Th w="22%">Алиас (имя для LLM)</Table.Th>
                            <Table.Th w="22%">Целевой параметр API</Table.Th>
                            <Table.Th>Описание для LLM</Table.Th>
                            <Table.Th w={44}></Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {apiQueryRows.map((row, index) => (
                            <Table.Tr
                              key={`apiq-${index}`}
                              onDragOver={(e) => e.preventDefault()}
                              onDrop={() => {
                                if (dragApiQueryIndex === null || dragApiQueryIndex === index) return;
                                setApiQueryRows(moveItem(apiQueryRows, dragApiQueryIndex, index));
                                setDragApiQueryIndex(null);
                              }}
                            >
                              <Table.Td>
                                <ActionIcon variant="subtle" draggable onDragStart={() => setDragApiQueryIndex(index)} onDragEnd={() => setDragApiQueryIndex(null)}>
                                  <IconGripVertical size={14} />
                                </ActionIcon>
                              </Table.Td>
                              <Table.Td>
                                <TextInput placeholder="ip" value={row.alias} ff="monospace" onChange={(e) => {
                                  const next = [...apiQueryRows]; next[index] = { ...row, alias: e.currentTarget.value }; setApiQueryRows(next);
                                }} />
                              </Table.Td>
                              <Table.Td>
                                <TextInput placeholder="ip_address" value={row.target} ff="monospace" onChange={(e) => {
                                  const next = [...apiQueryRows]; next[index] = { ...row, target: e.currentTarget.value }; setApiQueryRows(next);
                                }} />
                              </Table.Td>
                              <Table.Td>
                                <Textarea
                                  placeholder="IPv4 клиента. Поддерживается перенос строк."
                                  value={row.description}
                                  autosize
                                  minRows={1}
                                  maxRows={10}
                                  onChange={(e) => {
                                    const next = [...apiQueryRows]; next[index] = { ...row, description: e.currentTarget.value }; setApiQueryRows(next);
                                  }}
                                />
                              </Table.Td>
                              <Table.Td>
                                <ActionIcon variant="subtle" color="red" onClick={() => setApiQueryRows(apiQueryRows.filter((_, i) => i !== index))}>
                                  <IconTrash size={14} />
                                </ActionIcon>
                              </Table.Td>
                            </Table.Tr>
                          ))}
                        </Table.Tbody>
                      </Table>
                    )}

                    {/* === Headers + static query === */}
                    <SimpleGrid cols={2}>
                      <div>
                        <Group justify="space-between" mb={4}>
                          <Text size="xs" fw={600} c="dimmed" tt="uppercase">HTTP заголовки</Text>
                          <Button variant="light" size="xs" leftSection={<IconPlus size={14} />}
                            onClick={() => setApiHeaderRows([...apiHeaderRows, { name: '', value: '' }])}
                          >
                            Добавить
                          </Button>
                        </Group>
                        {apiHeaderRows.length > 0 ? (
                          <Table striped withTableBorder>
                            <Table.Thead>
                              <Table.Tr>
                                <Table.Th>Имя</Table.Th>
                                <Table.Th>Значение</Table.Th>
                                <Table.Th w={36}></Table.Th>
                              </Table.Tr>
                            </Table.Thead>
                            <Table.Tbody>
                              {apiHeaderRows.map((row, index) => (
                                <Table.Tr key={`hdr-${index}`}>
                                  <Table.Td>
                                    <TextInput size="xs" placeholder="Accept" value={row.name} ff="monospace" onChange={(e) => {
                                      const next = [...apiHeaderRows]; next[index] = { ...row, name: e.currentTarget.value }; setApiHeaderRows(next);
                                    }} />
                                  </Table.Td>
                                  <Table.Td>
                                    <TextInput size="xs" placeholder="application/json" value={row.value} ff="monospace" onChange={(e) => {
                                      const next = [...apiHeaderRows]; next[index] = { ...row, value: e.currentTarget.value }; setApiHeaderRows(next);
                                    }} />
                                  </Table.Td>
                                  <Table.Td>
                                    <ActionIcon variant="subtle" color="red" size="sm" onClick={() => setApiHeaderRows(apiHeaderRows.filter((_, i) => i !== index))}>
                                      <IconTrash size={14} />
                                    </ActionIcon>
                                  </Table.Td>
                                </Table.Tr>
                              ))}
                            </Table.Tbody>
                          </Table>
                        ) : (
                          <Text size="xs" c="dimmed">Нет заголовков (auth подмешивается из источника данных)</Text>
                        )}
                      </div>
                      <div>
                        <Group justify="space-between" mb={4}>
                          <Text size="xs" fw={600} c="dimmed" tt="uppercase">Статические query-параметры</Text>
                          <Button variant="light" size="xs" leftSection={<IconPlus size={14} />}
                            onClick={() => setApiStaticQueryRows([...apiStaticQueryRows, { key: '', value: '' }])}
                          >
                            Добавить
                          </Button>
                        </Group>
                        {apiStaticQueryRows.length > 0 ? (
                          <Table striped withTableBorder>
                            <Table.Thead>
                              <Table.Tr>
                                <Table.Th>Параметр</Table.Th>
                                <Table.Th>Значение</Table.Th>
                                <Table.Th w={36}></Table.Th>
                              </Table.Tr>
                            </Table.Thead>
                            <Table.Tbody>
                              {apiStaticQueryRows.map((row, index) => (
                                <Table.Tr key={`sq-${index}`}>
                                  <Table.Td>
                                    <TextInput size="xs" placeholder="format" value={row.key} ff="monospace" onChange={(e) => {
                                      const next = [...apiStaticQueryRows]; next[index] = { ...row, key: e.currentTarget.value }; setApiStaticQueryRows(next);
                                    }} />
                                  </Table.Td>
                                  <Table.Td>
                                    <TextInput size="xs" placeholder="json" value={row.value} ff="monospace" onChange={(e) => {
                                      const next = [...apiStaticQueryRows]; next[index] = { ...row, value: e.currentTarget.value }; setApiStaticQueryRows(next);
                                    }} />
                                  </Table.Td>
                                  <Table.Td>
                                    <ActionIcon variant="subtle" color="red" size="sm" onClick={() => setApiStaticQueryRows(apiStaticQueryRows.filter((_, i) => i !== index))}>
                                      <IconTrash size={14} />
                                    </ActionIcon>
                                  </Table.Td>
                                </Table.Tr>
                              ))}
                            </Table.Tbody>
                          </Table>
                        ) : (
                          <Text size="xs" c="dimmed">Нет статических параметров</Text>
                        )}
                      </div>
                    </SimpleGrid>

                    {/* === Body params + static body (POST) === */}
                    {apiMethod === 'POST' && (
                      <SimpleGrid cols={2}>
                        <div>
                          <Group justify="space-between" mb={4}>
                            <Text size="xs" fw={600} c="dimmed" tt="uppercase">Body-параметры</Text>
                            <Button
                              variant="light"
                              size="xs"
                              leftSection={<IconPlus size={14} />}
                              onClick={() => setApiBodyRows([...apiBodyRows, { alias: '', target: '', description: '' }])}
                            >
                              Добавить
                            </Button>
                          </Group>
                          {apiBodyRows.length > 0 ? (
                            <Table striped withTableBorder>
                              <Table.Thead>
                                <Table.Tr>
                                  <Table.Th w={36}></Table.Th>
                                  <Table.Th>Alias</Table.Th>
                                  <Table.Th>Target</Table.Th>
                                  <Table.Th>Описание</Table.Th>
                                  <Table.Th w={36}></Table.Th>
                                </Table.Tr>
                              </Table.Thead>
                              <Table.Tbody>
                                {apiBodyRows.map((row, index) => (
                                  <Table.Tr
                                    key={`apibody-${index}`}
                                    onDragOver={(e) => e.preventDefault()}
                                    onDrop={() => {
                                      if (dragApiBodyIndex === null || dragApiBodyIndex === index) return;
                                      setApiBodyRows(moveItem(apiBodyRows, dragApiBodyIndex, index));
                                      setDragApiBodyIndex(null);
                                    }}
                                  >
                                    <Table.Td>
                                      <ActionIcon
                                        variant="subtle"
                                        size="sm"
                                        draggable
                                        onDragStart={() => setDragApiBodyIndex(index)}
                                        onDragEnd={() => setDragApiBodyIndex(null)}
                                      >
                                        <IconGripVertical size={14} />
                                      </ActionIcon>
                                    </Table.Td>
                                    <Table.Td>
                                      <TextInput size="xs" placeholder="text" value={row.alias} ff="monospace" onChange={(e) => {
                                        const next = [...apiBodyRows]; next[index] = { ...row, alias: e.currentTarget.value }; setApiBodyRows(next);
                                      }} />
                                    </Table.Td>
                                    <Table.Td>
                                      <TextInput size="xs" placeholder="text" value={row.target} ff="monospace" onChange={(e) => {
                                        const next = [...apiBodyRows]; next[index] = { ...row, target: e.currentTarget.value }; setApiBodyRows(next);
                                      }} />
                                    </Table.Td>
                                    <Table.Td>
                                      <TextInput size="xs" placeholder="Описание для LLM" value={row.description} onChange={(e) => {
                                        const next = [...apiBodyRows]; next[index] = { ...row, description: e.currentTarget.value }; setApiBodyRows(next);
                                      }} />
                                    </Table.Td>
                                    <Table.Td>
                                      <ActionIcon variant="subtle" color="red" size="sm" onClick={() => setApiBodyRows(apiBodyRows.filter((_, i) => i !== index))}>
                                        <IconTrash size={14} />
                                      </ActionIcon>
                                    </Table.Td>
                                  </Table.Tr>
                                ))}
                              </Table.Tbody>
                            </Table>
                          ) : (
                            <Text size="xs" c="dimmed">Нет body-параметров</Text>
                          )}
                        </div>
                        <div>
                          <Group justify="space-between" mb={4}>
                            <Text size="xs" fw={600} c="dimmed" tt="uppercase">Статические body-параметры</Text>
                            <Button
                              variant="light"
                              size="xs"
                              leftSection={<IconPlus size={14} />}
                              onClick={() => setApiStaticBodyRows([...apiStaticBodyRows, { key: '', value: '' }])}
                            >
                              Добавить
                            </Button>
                          </Group>
                          {apiStaticBodyRows.length > 0 ? (
                            <Table striped withTableBorder>
                              <Table.Thead>
                                <Table.Tr>
                                  <Table.Th>Параметр</Table.Th>
                                  <Table.Th>Значение</Table.Th>
                                  <Table.Th w={36}></Table.Th>
                                </Table.Tr>
                              </Table.Thead>
                              <Table.Tbody>
                                {apiStaticBodyRows.map((row, index) => (
                                  <Table.Tr key={`sb-${index}`}>
                                    <Table.Td>
                                      <TextInput size="xs" placeholder="parse_mode" value={row.key} ff="monospace" onChange={(e) => {
                                        const next = [...apiStaticBodyRows]; next[index] = { ...row, key: e.currentTarget.value }; setApiStaticBodyRows(next);
                                      }} />
                                    </Table.Td>
                                    <Table.Td>
                                      <TextInput size="xs" placeholder="HTML / true / 42" value={row.value} ff="monospace" onChange={(e) => {
                                        const next = [...apiStaticBodyRows]; next[index] = { ...row, value: e.currentTarget.value }; setApiStaticBodyRows(next);
                                      }} />
                                    </Table.Td>
                                    <Table.Td>
                                      <ActionIcon variant="subtle" color="red" size="sm" onClick={() => setApiStaticBodyRows(apiStaticBodyRows.filter((_, i) => i !== index))}>
                                        <IconTrash size={14} />
                                      </ActionIcon>
                                    </Table.Td>
                                  </Table.Tr>
                                ))}
                              </Table.Tbody>
                            </Table>
                          ) : (
                            <Text size="xs" c="dimmed">Нет статических полей body</Text>
                          )}
                        </div>
                      </SimpleGrid>
                    )}
                  </Stack>
                )}
              </Stack>
            </Card>
          )}
          <SimpleGrid cols={2}>
            <Card withBorder padding="xs">
              <Group justify="space-between" mb={4}>
                <Text size="sm" fw={600}>Preview</Text>
                <Button variant="subtle" size="xs" onClick={() => setPreviewSectionOpen((v) => !v)}>
                  {previewSectionOpen ? 'Свернуть' : 'Показать'}
                </Button>
              </Group>
              {previewSectionOpen && (
                <Textarea
                  value={previewConfig}
                  readOnly
                  autosize
                  minRows={6}
                  maxRows={16}
                  ff="monospace"
                  styles={{ input: { fontFamily: 'monospace', fontSize: '12px' } }}
                />
              )}
            </Card>
            <Card withBorder padding="xs">
              <Group justify="space-between" mb={4}>
                <Text size="sm" fw={600}>Raw JSON</Text>
                <Button variant="subtle" size="xs" onClick={() => setJsonSectionOpen((v) => !v)}>
                  {jsonSectionOpen ? 'Свернуть' : 'Показать'}
                </Button>
              </Group>
              {jsonSectionOpen && (
                <Textarea
                  placeholder='{"type":"function","function":{...}}'
                  value={configJson}
                  onChange={(e) => setConfigJson(e.currentTarget.value)}
                  autosize
                  minRows={6}
                  maxRows={16}
                  ff="monospace"
                  styles={{ input: { fontFamily: 'monospace', fontSize: '12px' } }}
                />
              )}
            </Card>
          </SimpleGrid>
          <Card withBorder padding="xs">
            <Group justify="space-between" mb={4}>
              <Text size="sm" fw={600}>Тест</Text>
              <Button variant="subtle" size="xs" onClick={() => setTestSectionOpen((v) => !v)}>
                {testSectionOpen ? 'Свернуть' : 'Показать'}
              </Button>
            </Group>
            {testSectionOpen && (
              <SimpleGrid cols={2}>
                <Textarea
                  placeholder='{"filters": {"ip": "10.0.0.1"}}'
                  value={testArgsJson}
                  onChange={(e) => setTestArgsJson(e.currentTarget.value)}
                  autosize
                  minRows={3}
                  maxRows={10}
                  ff="monospace"
                  styles={{ input: { fontFamily: 'monospace', fontSize: '12px' } }}
                />
                <Stack gap="xs">
                  <Button variant="light" onClick={() => testMutation.mutate()} loading={testMutation.isPending} fullWidth>
                    Запустить тест
                  </Button>
                  {testResult && (
                    <Alert color={testResultSuccess ? 'green' : 'red'} title={testResultSuccess ? 'OK' : 'Ошибка'} p="xs">
                      <Code block style={{ fontSize: '12px', maxHeight: 200, overflow: 'auto' }}>{testResult}</Code>
                    </Alert>
                  )}
                </Stack>
              </SimpleGrid>
            )}
          </Card>
          <Group justify="space-between">
            <Group gap="lg">
              <Switch label="Активный" checked={toolActive} onChange={(e) => setToolActive(e.currentTarget.checked)} />
              <Tooltip
                label="Закреплённые tools всегда добавляются в LLM-контекст, минуя фильтр релевантности. Используй для общих/часто-нужных tools (например, search_clients)."
                multiline
                w={320}
              >
                <Switch
                  label="Закреплён в контексте"
                  checked={toolPinned}
                  onChange={(e) => setToolPinned(e.currentTarget.checked)}
                />
              </Tooltip>
            </Group>
            <Group gap="xs">
              <Button variant="default" onClick={() => setModalOpen(false)}>Отмена</Button>
              <Button onClick={handleSave} loading={createMutation.isPending || updateMutation.isPending}>
                {editId ? 'Обновить' : 'Создать'}
              </Button>
            </Group>
          </Group>
        </Stack>
      </Modal>
      <Modal
        opened={renameGroupOpen}
        onClose={() => setRenameGroupOpen(false)}
        title="Переименовать группу"
        size="sm"
      >
        <Stack gap="md">
          <Text size="sm" c="dimmed">
            Новое название будет применено ко всем инструментам в группе `{renameGroupFrom}`.
          </Text>
          <TextInput
            label="Название группы"
            value={renameGroupTo}
            onChange={(e) => setRenameGroupTo(e.currentTarget.value)}
            required
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRenameGroupOpen(false)}>Отмена</Button>
            <Button
              loading={renameGroupMutation.isPending}
              onClick={() => {
                const nextName = renameGroupTo.trim();
                if (!nextName) {
                  notifications.show({ title: 'Ошибка', message: 'Укажите новое название группы', color: 'red' });
                  return;
                }
                renameGroupMutation.mutate({ from: renameGroupFrom, to: nextName });
              }}
            >
              Сохранить
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
