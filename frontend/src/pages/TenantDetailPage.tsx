import { useState, useCallback } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
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
  Box,
  ScrollArea,
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
  IconBolt,
  IconSearch,
  IconRobot,
} from '@tabler/icons-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import {
  tenantsApi,
  keysApi,
  keyGroupsApi,
  toolsApi,
  assistantsApi,
} from '../shared/api/endpoints';
import type {
  TenantApiKey,
  TenantApiKeyGroup,
} from '../shared/api/types';
import { GeneralTab } from './tenant-detail/GeneralTab';
import { OverviewTab } from './tenant-detail/OverviewTab';
import { ChatsTab } from './tenant-detail/ChatsTab';
import { DataSourcesTab } from './tenant-detail/DataSourcesTab';
import { KBTab } from './tenant-detail/KBTab';
import { LogsTab } from './tenant-detail/LogsTab';
import { MemoryTab } from './tenant-detail/MemoryTab';
import { ModelConfigTab } from './tenant-detail/ModelConfigTab';
import { ShellSettingsTab } from './tenant-detail/ShellSettingsTab';
import { Tier0Tab } from './tenant-detail/Tier0Tab';
import { RetrievalTab } from './tenant-detail/RetrievalTab';
import { AssistantsTab } from './tenant-detail/AssistantsTab';
import { StatsTab } from './TenantStatsTab';
import { ApiInfoTab } from './TenantApiInfoTab';
import { UsersTab } from './tenant-detail/UsersTab';
import { usePermissions } from '../shared/hooks/usePermissions';
import { ToolsTab } from './tenant-detail/ToolsTab';
import {
  TOOL_PERMISSION_PRESETS,
  readToolCapabilityTags,
  type ToolPreset,
} from './tenant-detail/toolCapabilityTags';

export function TenantDetailPage() {
  const { id } = useParams<{ id: string }>();
  const tenantId = id!;
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { isSuperadmin, has } = usePermissions();

  const defaultTab = has('logs') ? 'overview' : 'general';

  const isAllowedTab = useCallback((tab: string) => {
    if (tab === 'overview') return has('logs');
    if (tab === 'general') return true;
    if (tab === 'keys') return has('keys');
    if (tab === 'model') return has('model_config');
    if (tab === 'shell' || tab === 'assistants') return has('shell_config');
    if (tab === 'data-sources') return has('data_sources');
    if (tab === 'tools') return has('tools');
    if (tab === 'kb') return has('kb');
    if (tab === 'memory') return has('memory');
    if (tab === 'chats') return has('chats');
    if (tab === 'logs' || tab === 'tier0' || tab === 'retrieval') return has('logs');
    if (tab === 'stats' || tab === 'api-info') return isSuperadmin;
    if (tab === 'users') return has('users');
    return false;
  }, [has, isSuperadmin]);

  const tabParam = searchParams.get('tab');
  const activeTab = tabParam && isAllowedTab(tabParam) ? tabParam : defaultTab;
  const shellSection = searchParams.get('section');

  const setActiveTab = useCallback((tab: string | null) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const value = tab ?? defaultTab;
      if (!tab || tab === defaultTab) next.delete('tab');
      else next.set('tab', tab);
      if (value !== 'shell') next.delete('section');
      return next;
    }, { replace: true });
  }, [defaultTab, setSearchParams]);

  const setShellSection = useCallback((section: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('tab', 'shell');
      next.set('section', section);
      return next;
    }, { replace: true });
  }, [setSearchParams]);

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

      <Tabs value={activeTab} onChange={setActiveTab} keepMounted={false}>
        <Box
          style={{
            position: 'sticky',
            // Stick just below the fixed AppShell header (60px), not under it.
            top: 'var(--app-shell-header-height, 60px)',
            zIndex: 3,
            background: 'var(--mantine-color-body)',
          }}
        >
          <ScrollArea type="auto" scrollbarSize={6} offsetScrollbars="x">
            <Tabs.List style={{ flexWrap: 'nowrap', width: 'max-content', minWidth: '100%' }}>
          {has('logs') && <Tabs.Tab value="overview">Обзор</Tabs.Tab>}
          <Tabs.Tab value="general">Общее</Tabs.Tab>
          {has('keys') && <Tabs.Tab value="keys">API Ключи</Tabs.Tab>}
          {has('model_config') && <Tabs.Tab value="model">Модель</Tabs.Tab>}
          {has('shell_config') && <Tabs.Tab value="shell">Настройки оболочки</Tabs.Tab>}
          {has('shell_config') && <Tabs.Tab value="assistants" leftSection={<IconRobot size={14} />}>Ассистенты</Tabs.Tab>}
          {has('data_sources') && <Tabs.Tab value="data-sources">Источники данных</Tabs.Tab>}
          {has('tools') && <Tabs.Tab value="tools">Инструменты</Tabs.Tab>}
          {has('kb') && <Tabs.Tab value="kb">База знаний</Tabs.Tab>}
          {has('memory') && <Tabs.Tab value="memory">Память</Tabs.Tab>}
          {has('chats') && <Tabs.Tab value="chats">Чаты</Tabs.Tab>}
          {has('logs') && <Tabs.Tab value="logs">Логи</Tabs.Tab>}
          {has('logs') && <Tabs.Tab value="tier0" leftSection={<IconBolt size={14} />}>Tier 0</Tabs.Tab>}
          {has('logs') && <Tabs.Tab value="retrieval" leftSection={<IconSearch size={14} />}>Поиск</Tabs.Tab>}
          {isSuperadmin && <Tabs.Tab value="stats">Статистика</Tabs.Tab>}
          {has('users') && <Tabs.Tab value="users">Пользователи</Tabs.Tab>}
          {isSuperadmin && <Tabs.Tab value="api-info">API</Tabs.Tab>}
            </Tabs.List>
          </ScrollArea>
        </Box>

        {has('logs') && (
          <Tabs.Panel value="overview" pt="md">
            <OverviewTab tenantId={tenantId} />
          </Tabs.Panel>
        )}
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
          <Tabs.Panel value="shell" pt="md" keepMounted>
            <ShellSettingsTab
              tenantId={tenantId}
              activeSection={shellSection}
              onSectionChange={setShellSection}
            />
          </Tabs.Panel>
        )}
        {has('shell_config') && (
          <Tabs.Panel value="assistants" pt="md">
            <AssistantsTab tenantId={tenantId} />
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
        {has('logs') && (
          <Tabs.Panel value="tier0" pt="md">
            <Tier0Tab tenantId={tenantId} />
          </Tabs.Panel>
        )}
        {has('logs') && (
          <Tabs.Panel value="retrieval" pt="md">
            <RetrievalTab tenantId={tenantId} />
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
  const [keyAssistantId, setKeyAssistantId] = useState<string | null>(null);
  const [keyMemoryPrompt, setKeyMemoryPrompt] = useState('');
  const [keyAllowedToolsRestricted, setKeyAllowedToolsRestricted] = useState(false);
  const [keyAllowedToolIds, setKeyAllowedToolIds] = useState<string[]>([]);
  const [keyActorTrusted, setKeyActorTrusted] = useState(false);
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

  const { data: assistantsForKeys } = useQuery({
    queryKey: ['tenants', tenantId, 'assistants'],
    queryFn: () => assistantsApi.list(tenantId),
  });
  const assistantOptions = (assistantsForKeys || []).map((a) => ({
    value: a.id, label: a.name + (a.is_default ? ' (по умолчанию)' : ''),
  }));

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
      assistant_id: keyAssistantId,
      memory_prompt: keyMemoryPrompt || undefined,
      allowed_tool_ids: keyAllowedToolsRestricted ? keyAllowedToolIds : null,
      actor_trusted: keyActorTrusted,
    }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      setCreateOpen(false);
      setKeyName('');
      setKeyGroupId(null);
      setKeyAssistantId(null);
      setKeyMemoryPrompt('');
      setKeyAllowedToolsRestricted(false);
      setKeyAllowedToolIds([]);
      setKeyActorTrusted(false);
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
      setCreateOpen(false);
      setEditKey(null);
      setKeyName('');
      setKeyGroupId(null);
      setKeyAssistantId(null);
      setKeyMemoryPrompt('');
      setKeyAllowedToolsRestricted(false);
      setKeyAllowedToolIds([]);
      setKeyActorTrusted(false);
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
    setKeyAssistantId(null);
    setKeyMemoryPrompt('');
    setKeyAllowedToolsRestricted(false);
    setKeyAllowedToolIds([]);
    setKeyActorTrusted(false);
    setCreateOpen(true);
  };

  const openEditKey = (key: TenantApiKey) => {
    setEditKey(key);
    setKeyName(key.name);
    setKeyGroupId(key.group_id);
    setKeyAssistantId(key.assistant_id ?? null);
    setKeyMemoryPrompt(key.memory_prompt || '');
    const keyPermissions = normalizePermissionList(key.allowed_tool_ids);
    setKeyAllowedToolsRestricted(keyPermissions.restricted);
    setKeyAllowedToolIds(keyPermissions.ids);
    setKeyActorTrusted(!!key.actor_trusted);
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
                  assistant_id: keyAssistantId,
                  memory_prompt: keyMemoryPrompt || null,
                  allowed_tool_ids: keyAllowedToolsRestricted ? keyAllowedToolIds : null,
                  actor_trusted: keyActorTrusted,
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
            <Select
              label="Ассистент (персона канала)"
              description="Чаты по этому ключу пойдут от имени выбранного ассистента; пусто = ассистент по умолчанию"
              placeholder="Ассистент по умолчанию"
              data={assistantOptions}
              value={keyAssistantId}
              onChange={setKeyAssistantId}
              clearable
              searchable
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
              label="Доверять actor (идентичность пользователя)"
              description="Включай ТОЛЬКО для server-to-server интеграции (CRM-бэкенд, который сам аутентифицировал пользователя). Тогда платформа примет actor из запроса и тулы смогут фильтровать по нему ({actor.external_id}). НЕ включай для встраиваемого/браузерного ключа — клиент сможет подделать идентичность. По умолчанию выключено."
              checked={keyActorTrusted}
              onChange={(e) => setKeyActorTrusted(e.currentTarget.checked)}
              color="orange"
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

