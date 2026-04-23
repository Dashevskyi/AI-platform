import { useState, useEffect, useCallback } from 'react';
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
  Slider,
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
  PasswordInput,
  ActionIcon,
  Tooltip,
  Pagination,
  Code,
  Drawer,
  CopyButton,
  ScrollArea,
  SimpleGrid,
} from '@mantine/core';
import {
  IconDeviceFloppy,
  IconPlus,
  IconTrash,
  IconRefresh,
  IconPlayerStop,
  IconPlugConnected,
  IconArrowLeft,
  IconAlertCircle,
  IconCopy,
  IconCheck,
  IconEdit,
} from '@tabler/icons-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import {
  tenantsApi,
  keysApi,
  shellApi,
  toolsApi,
  kbApi,
  memoryApi,
  chatsApi,
  logsApi,
  modelsApi,
  modelConfigApi,
  customModelsApi,
} from '../shared/api/endpoints';
import type {
  ShellConfigUpdate,
  Tool,
  ToolCreate,
  ToolUpdate,
  KBDocument,
  KBDocumentCreate,
  KBDocumentUpdate,
  MemoryEntry,
  MemoryEntryCreate,
  MemoryEntryUpdate,
  LLMLogDetail,
  LLMModelBrief,
  TenantModelConfigUpdate,
  TenantCustomModel,
  TenantCustomModelCreate,
  TenantCustomModelUpdate,
} from '../shared/api/types';

export function TenantDetailPage() {
  const { id } = useParams<{ id: string }>();
  const tenantId = id!;
  const navigate = useNavigate();

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
        <ActionIcon variant="subtle" onClick={() => navigate('/tenants')}>
          <IconArrowLeft size={20} />
        </ActionIcon>
        <Title order={2}>{tenant.name}</Title>
        <Badge color={tenant.is_active ? 'green' : 'gray'}>
          {tenant.is_active ? 'Активный' : 'Неактивный'}
        </Badge>
      </Group>

      <Tabs defaultValue="general" keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="general">Общее</Tabs.Tab>
          <Tabs.Tab value="keys">API Ключи</Tabs.Tab>
          <Tabs.Tab value="model">Модель</Tabs.Tab>
          <Tabs.Tab value="shell">Настройки оболочки</Tabs.Tab>
          <Tabs.Tab value="tools">Инструменты</Tabs.Tab>
          <Tabs.Tab value="kb">База знаний</Tabs.Tab>
          <Tabs.Tab value="memory">Память</Tabs.Tab>
          <Tabs.Tab value="chats">Чаты</Tabs.Tab>
          <Tabs.Tab value="logs">Логи</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="general" pt="md">
          <GeneralTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="keys" pt="md">
          <ApiKeysTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="model" pt="md">
          <ModelConfigTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="shell" pt="md">
          <ShellSettingsTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="tools" pt="md">
          <ToolsTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="kb" pt="md">
          <KBTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="memory" pt="md">
          <MemoryTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="chats" pt="md">
          <ChatsTab tenantId={tenantId} />
        </Tabs.Panel>
        <Tabs.Panel value="logs" pt="md">
          <LogsTab tenantId={tenantId} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

// ===== GENERAL TAB =====

function GeneralTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const { data: tenant } = useQuery({
    queryKey: ['tenants', tenantId],
    queryFn: () => tenantsApi.get(tenantId),
  });

  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [description, setDescription] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (tenant) {
      setName(tenant.name);
      setSlug(tenant.slug);
      setDescription(tenant.description || '');
      setIsActive(tenant.is_active);
      setDirty(false);
    }
  }, [tenant]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const mutation = useMutation({
    mutationFn: () =>
      tenantsApi.update(tenantId, { name, slug, description, is_active: isActive }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId] });
      queryClient.invalidateQueries({ queryKey: ['tenants', 'list'] });
      setDirty(false);
      notifications.show({
        title: 'Сохранено',
        message: 'Тенант успешно обновлён',
        color: 'green',
      });
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Не удалось сохранить';
      notifications.show({ title: 'Ошибка', message, color: 'red' });
    },
  });

  const markDirty = useCallback(
    <T,>(setter: React.Dispatch<React.SetStateAction<T>>) =>
      (val: T) => {
        setter(val);
        setDirty(true);
      },
    []
  );

  return (
    <Card withBorder padding="lg" maw={600}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate();
        }}
      >
        <Stack gap="md">
          {dirty && (
            <Alert icon={<IconAlertCircle size={16} />} color="yellow" variant="light">
              У вас есть несохранённые изменения.
            </Alert>
          )}
          <TextInput
            label="Название"
            value={name}
            onChange={(e) => markDirty(setName)(e.currentTarget.value)}
            required
          />
          <TextInput
            label="Slug"
            value={slug}
            onChange={(e) => markDirty(setSlug)(e.currentTarget.value)}
            required
          />
          <Textarea
            label="Описание"
            value={description}
            onChange={(e) => markDirty(setDescription)(e.currentTarget.value)}
            autosize
            minRows={2}
          />
          <Switch
            label="Активный"
            checked={isActive}
            onChange={(e) => markDirty(setIsActive)(e.currentTarget.checked)}
          />
          <Group justify="flex-end">
            <Button
              type="submit"
              leftSection={<IconDeviceFloppy size={16} />}
              loading={mutation.isPending}
              disabled={!dirty}
            >
              Сохранить изменения
            </Button>
          </Group>
        </Stack>
      </form>
    </Card>
  );
}

// ===== API KEYS TAB =====

function ApiKeysTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [createOpen, setCreateOpen] = useState(false);
  const [keyName, setKeyName] = useState('');
  const [rawKey, setRawKey] = useState('');
  const [rawKeyModalOpen, setRawKeyModalOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'keys', page],
    queryFn: () => keysApi.list(tenantId, page),
  });

  const createMutation = useMutation({
    mutationFn: () => keysApi.create(tenantId, { name: keyName }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'keys'] });
      setCreateOpen(false);
      setKeyName('');
      setRawKey(result.raw_key);
      setRawKeyModalOpen(true);
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать ключ', color: 'red' });
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

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>API Ключи</Text>
        <Button
          leftSection={<IconPlus size={16} />}
          size="sm"
          onClick={() => setCreateOpen(true)}
        >
          Создать ключ
        </Button>
      </Group>

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
                <Table.Th>Префикс</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Истекает</Table.Th>
                <Table.Th>Последнее использование</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((key) => (
                <Table.Tr key={key.id}>
                  <Table.Td>{key.name}</Table.Td>
                  <Table.Td>
                    <Code>{key.key_prefix}...</Code>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={key.is_active ? 'green' : 'gray'}>
                      {key.is_active ? 'Активный' : 'Неактивный'}
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
        title="Создать API ключ"
      >
        <Text size="sm" c="dimmed" mb="md">
          API ключ используется для доступа к чату от имени тенанта через REST API.
          Полный ключ будет показан только один раз — сохраните его!
        </Text>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
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

// ===== MODEL CONFIG TAB =====

const PROVIDER_OPTIONS_MODEL = [
  { value: 'ollama', label: 'Ollama (локальный)' },
  { value: 'openai_compatible', label: 'OpenAI Compatible' },
  { value: 'deepseek_compatible', label: 'DeepSeek Compatible' },
];

function ModelConfigTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();

  // Load model config
  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'model-config'],
    queryFn: () => modelConfigApi.get(tenantId),
  });

  // Load available models from catalog
  const { data: catalogModels } = useQuery({
    queryKey: ['models', 'brief'],
    queryFn: () => modelsApi.brief(),
  });

  // Load tenant custom models
  const { data: customModelsData } = useQuery({
    queryKey: ['tenants', tenantId, 'custom-models'],
    queryFn: () => customModelsApi.list(tenantId, 1, 100),
  });

  const [mode, setMode] = useState<string>('manual');
  const [manualModelId, setManualModelId] = useState<string | null>(null);
  const [manualCustomModelId, setManualCustomModelId] = useState<string | null>(null);
  const [autoLightModelId, setAutoLightModelId] = useState<string | null>(null);
  const [autoHeavyModelId, setAutoHeavyModelId] = useState<string | null>(null);
  const [autoLightCustomId, setAutoLightCustomId] = useState<string | null>(null);
  const [autoHeavyCustomId, setAutoHeavyCustomId] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(0.5);
  const [dirty, setDirty] = useState(false);

  // Custom model modal state
  const [customModalOpen, setCustomModalOpen] = useState(false);
  const [editCustomId, setEditCustomId] = useState<string | null>(null);
  const [cmName, setCmName] = useState('');
  const [cmProvider, setCmProvider] = useState('ollama');
  const [cmBaseUrl, setCmBaseUrl] = useState('');
  const [cmApiKey, setCmApiKey] = useState('');
  const [cmModelId, setCmModelId] = useState('');
  const [cmTier, setCmTier] = useState('medium');
  const [cmTools, setCmTools] = useState(false);
  const [cmVision, setCmVision] = useState(false);

  useEffect(() => {
    if (config) {
      setMode(config.mode);
      setManualModelId(config.manual_model_id);
      setManualCustomModelId(config.manual_custom_model_id);
      setAutoLightModelId(config.auto_light_model_id);
      setAutoHeavyModelId(config.auto_heavy_model_id);
      setAutoLightCustomId(config.auto_light_custom_model_id);
      setAutoHeavyCustomId(config.auto_heavy_custom_model_id);
      setThreshold(config.complexity_threshold);
      setDirty(false);
    }
  }, [config]);

  const saveMutation = useMutation({
    mutationFn: (data: TenantModelConfigUpdate) => modelConfigApi.update(tenantId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'model-config'] });
      setDirty(false);
      notifications.show({ title: 'Сохранено', message: 'Конфигурация модели обновлена', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось сохранить', color: 'red' });
    },
  });

  const createCustomMutation = useMutation({
    mutationFn: (data: TenantCustomModelCreate) => customModelsApi.create(tenantId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'custom-models'] });
      setCustomModalOpen(false);
      notifications.show({ title: 'Создано', message: 'Приватная модель добавлена', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать модель', color: 'red' });
    },
  });

  const updateCustomMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: TenantCustomModelUpdate }) =>
      customModelsApi.update(tenantId, id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'custom-models'] });
      setCustomModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Модель обновлена', color: 'green' });
    },
  });

  const deleteCustomMutation = useMutation({
    mutationFn: (id: string) => customModelsApi.delete(tenantId, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'custom-models'] });
      notifications.show({ title: 'Удалено', message: 'Приватная модель удалена', color: 'green' });
    },
  });

  const handleSave = () => {
    saveMutation.mutate({
      mode,
      manual_model_id: manualModelId,
      manual_custom_model_id: manualCustomModelId,
      auto_light_model_id: autoLightModelId,
      auto_heavy_model_id: autoHeavyModelId,
      auto_light_custom_model_id: autoLightCustomId,
      auto_heavy_custom_model_id: autoHeavyCustomId,
      complexity_threshold: threshold,
    });
  };

  const openCreateCustom = () => {
    setEditCustomId(null);
    setCmName('');
    setCmProvider('ollama');
    setCmBaseUrl('');
    setCmApiKey('');
    setCmModelId('');
    setCmTier('medium');
    setCmTools(false);
    setCmVision(false);
    setCustomModalOpen(true);
  };

  const openEditCustom = (m: TenantCustomModel) => {
    setEditCustomId(m.id);
    setCmName(m.name);
    setCmProvider(m.provider_type);
    setCmBaseUrl(m.base_url || '');
    setCmApiKey('');
    setCmModelId(m.model_id);
    setCmTier(m.tier);
    setCmTools(m.supports_tools);
    setCmVision(m.supports_vision);
    setCustomModalOpen(true);
  };

  const handleSaveCustom = () => {
    const data = {
      name: cmName,
      provider_type: cmProvider,
      base_url: cmBaseUrl || undefined,
      api_key: cmApiKey || undefined,
      model_id: cmModelId,
      tier: cmTier,
      supports_tools: cmTools,
      supports_vision: cmVision,
    };
    if (editCustomId) {
      updateCustomMutation.mutate({ id: editCustomId, data });
    } else {
      createCustomMutation.mutate(data);
    }
  };

  // Build select options
  const catalogOptions = (catalogModels || []).map((m: LLMModelBrief) => ({
    value: m.id,
    label: `${m.name} (${m.model_id}) [${m.tier}]`,
  }));

  const customModels = customModelsData?.items || [];
  const customOptions = customModels.map((m: TenantCustomModel) => ({
    value: m.id,
    label: `${m.name} (${m.model_id}) [приватная]`,
  }));

  const markDirty = () => setDirty(true);

  if (configLoading) {
    return <Center py="md"><Loader /></Center>;
  }

  return (
    <>
      <Stack gap="lg">
        <Card withBorder padding="lg" maw={800}>
          <Stack gap="md">
            <Title order={4}>Выбор модели</Title>
            <Text size="sm" c="dimmed">
              Выберите режим работы: конкретная модель или автоматический выбор на основе сложности запроса.
            </Text>

            {dirty && (
              <Alert icon={<IconAlertCircle size={16} />} color="yellow" variant="light">
                У вас есть несохранённые изменения.
              </Alert>
            )}

            <Select
              label="Режим выбора модели"
              data={[
                { value: 'manual', label: 'Вручную — одна конкретная модель' },
                { value: 'auto', label: 'Автоматически — по сложности запроса' },
              ]}
              value={mode}
              onChange={(v) => { setMode(v || 'manual'); markDirty(); }}
              allowDeselect={false}
            />

            {mode === 'manual' && (
              <Stack gap="sm">
                <Text size="sm" fw={500}>Выберите модель из каталога:</Text>
                <Select
                  label="Модель из каталога"
                  placeholder="Выберите модель..."
                  data={catalogOptions}
                  value={manualModelId}
                  onChange={(v) => { setManualModelId(v); setManualCustomModelId(null); markDirty(); }}
                  clearable
                  searchable
                />
                <Text size="xs" c="dimmed" ta="center">— или приватная модель —</Text>
                <Select
                  label="Приватная модель"
                  placeholder="Выберите модель..."
                  data={customOptions}
                  value={manualCustomModelId}
                  onChange={(v) => { setManualCustomModelId(v); setManualModelId(null); markDirty(); }}
                  clearable
                  searchable
                />
              </Stack>
            )}

            {mode === 'auto' && (
              <Stack gap="md">
                <Text size="sm" c="dimmed">
                  Система классифицирует сложность запроса (0-1).
                  Если ниже порога — используется лёгкая модель, иначе — мощная.
                </Text>

                <Card withBorder padding="sm">
                  <Text size="sm" fw={500} mb="xs" c="green">Лёгкая модель (простые запросы)</Text>
                  <Stack gap="xs">
                    <Select
                      label="Из каталога"
                      placeholder="Выберите..."
                      data={catalogOptions}
                      value={autoLightModelId}
                      onChange={(v) => { setAutoLightModelId(v); setAutoLightCustomId(null); markDirty(); }}
                      clearable
                      searchable
                    />
                    <Select
                      label="Или приватная"
                      placeholder="Выберите..."
                      data={customOptions}
                      value={autoLightCustomId}
                      onChange={(v) => { setAutoLightCustomId(v); setAutoLightModelId(null); markDirty(); }}
                      clearable
                      searchable
                    />
                  </Stack>
                </Card>

                <Card withBorder padding="sm">
                  <Text size="sm" fw={500} mb="xs" c="violet">Мощная модель (сложные запросы)</Text>
                  <Stack gap="xs">
                    <Select
                      label="Из каталога"
                      placeholder="Выберите..."
                      data={catalogOptions}
                      value={autoHeavyModelId}
                      onChange={(v) => { setAutoHeavyModelId(v); setAutoHeavyCustomId(null); markDirty(); }}
                      clearable
                      searchable
                    />
                    <Select
                      label="Или приватная"
                      placeholder="Выберите..."
                      data={customOptions}
                      value={autoHeavyCustomId}
                      onChange={(v) => { setAutoHeavyCustomId(v); setAutoHeavyModelId(null); markDirty(); }}
                      clearable
                      searchable
                    />
                  </Stack>
                </Card>

                <div>
                  <Text size="sm" fw={500} mb={2}>
                    Порог сложности: {threshold.toFixed(2)}
                  </Text>
                  <Text size="xs" c="dimmed" mb="xs">
                    Запросы с complexity &lt; {threshold.toFixed(2)} → лёгкая модель, остальные → мощная
                  </Text>
                  <Slider
                    min={0}
                    max={1}
                    step={0.05}
                    value={threshold}
                    onChange={(v) => { setThreshold(v); markDirty(); }}
                    marks={[
                      { value: 0, label: '0' },
                      { value: 0.25, label: '0.25' },
                      { value: 0.5, label: '0.5' },
                      { value: 0.75, label: '0.75' },
                      { value: 1, label: '1' },
                    ]}
                    mb="xl"
                  />
                </div>
              </Stack>
            )}

            <Group justify="flex-end">
              <Button
                leftSection={<IconDeviceFloppy size={16} />}
                onClick={handleSave}
                loading={saveMutation.isPending}
                disabled={!dirty}
              >
                Сохранить
              </Button>
            </Group>
          </Stack>
        </Card>

        {/* Custom models section */}
        <Card withBorder padding="lg" maw={800}>
          <Stack gap="md">
            <Group justify="space-between">
              <div>
                <Title order={4}>Приватные модели тенанта</Title>
                <Text size="sm" c="dimmed">
                  Модели, добавленные этим тенантом. Видны только ему.
                </Text>
              </div>
              <Button leftSection={<IconPlus size={16} />} size="sm" onClick={openCreateCustom}>
                Добавить
              </Button>
            </Group>

            {!customModels.length ? (
              <Text c="dimmed" ta="center" py="md">Приватных моделей нет.</Text>
            ) : (
              <Table striped>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Название</Table.Th>
                    <Table.Th>Провайдер</Table.Th>
                    <Table.Th>Model ID</Table.Th>
                    <Table.Th>Уровень</Table.Th>
                    <Table.Th>Статус</Table.Th>
                    <Table.Th>Действия</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {customModels.map((m: TenantCustomModel) => (
                    <Table.Tr key={m.id} style={{ cursor: 'pointer' }} onClick={() => openEditCustom(m)}>
                      <Table.Td><Text size="sm" fw={500}>{m.name}</Text></Table.Td>
                      <Table.Td><Badge variant="light" size="sm">{m.provider_type}</Badge></Table.Td>
                      <Table.Td><Text size="sm" ff="monospace">{m.model_id}</Text></Table.Td>
                      <Table.Td><Badge size="sm">{m.tier}</Badge></Table.Td>
                      <Table.Td>
                        <Badge color={m.is_active ? 'green' : 'gray'} size="sm">
                          {m.is_active ? 'Активна' : 'Выкл'}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          <ActionIcon variant="subtle" color="blue" size="sm" onClick={(e) => { e.stopPropagation(); openEditCustom(m); }}>
                            <IconEdit size={14} />
                          </ActionIcon>
                          <ActionIcon
                            variant="subtle" color="red" size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (window.confirm(`Удалить "${m.name}"?`)) deleteCustomMutation.mutate(m.id);
                            }}
                          >
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Stack>
        </Card>
      </Stack>

      {/* Custom Model Create/Edit Modal — вне Stack */}
      <Modal
        opened={customModalOpen}
        onClose={() => setCustomModalOpen(false)}
        title={editCustomId ? 'Редактировать приватную модель' : 'Добавить приватную модель'}
        size="lg"
      >
        <Stack gap="md">
          <TextInput
            label="Название"
            placeholder="My GPT-4o"
            value={cmName}
            onChange={(e) => setCmName(e.currentTarget.value)}
            required
          />
          <SimpleGrid cols={2}>
            <Select
              label="Провайдер"
              data={PROVIDER_OPTIONS_MODEL}
              value={cmProvider}
              onChange={(v) => setCmProvider(v || 'ollama')}
              allowDeselect={false}
            />
            <Select
              label="Уровень"
              data={[
                { value: 'light', label: 'Light' },
                { value: 'medium', label: 'Medium' },
                { value: 'heavy', label: 'Heavy' },
              ]}
              value={cmTier}
              onChange={(v) => setCmTier(v || 'medium')}
              allowDeselect={false}
            />
          </SimpleGrid>
          <TextInput
            label="Базовый URL"
            placeholder="http://localhost:11434"
            value={cmBaseUrl}
            onChange={(e) => setCmBaseUrl(e.currentTarget.value)}
          />
          <PasswordInput
            label="API ключ"
            description={editCustomId ? 'Оставьте пустым, чтобы не менять' : ''}
            value={cmApiKey}
            onChange={(e) => setCmApiKey(e.currentTarget.value)}
          />
          <TextInput
            label="Model ID"
            placeholder="gpt-4o"
            value={cmModelId}
            onChange={(e) => setCmModelId(e.currentTarget.value)}
            required
          />
          <Group>
            <Switch label="Tools" checked={cmTools} onChange={(e) => setCmTools(e.currentTarget.checked)} />
            <Switch label="Vision" checked={cmVision} onChange={(e) => setCmVision(e.currentTarget.checked)} />
          </Group>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setCustomModalOpen(false)}>Отмена</Button>
            <Button
              onClick={handleSaveCustom}
              loading={createCustomMutation.isPending || updateCustomMutation.isPending}
            >
              {editCustomId ? 'Обновить' : 'Создать'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}

// ===== SHELL SETTINGS TAB =====

function ShellSettingsTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const { data: config, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'shell'],
    queryFn: () => shellApi.get(tenantId),
  });

  const [form, setForm] = useState<ShellConfigUpdate>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (config) {
      setForm({
        provider_type: config.provider_type,
        provider_base_url: config.provider_base_url ?? undefined,
        model_name: config.model_name,
        system_prompt: config.system_prompt ?? undefined,
        rules_text: config.rules_text ?? undefined,
        temperature: config.temperature,
        max_context_messages: config.max_context_messages,
        max_tokens: config.max_tokens,
        memory_enabled: config.memory_enabled,
        knowledge_base_enabled: config.knowledge_base_enabled,
        embedding_model_name: config.embedding_model_name ?? undefined,
        kb_max_chunks: config.kb_max_chunks,
      });
      setDirty(false);
    }
  }, [config]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const updateField = <K extends keyof ShellConfigUpdate>(
    key: K,
    value: ShellConfigUpdate[K]
  ) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => shellApi.update(tenantId, form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'shell'] });
      setDirty(false);
      notifications.show({ title: 'Сохранено', message: 'Настройки оболочки обновлены', color: 'green' });
    },
    onError: (err: unknown) => {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Не удалось сохранить';
      notifications.show({ title: 'Ошибка', message, color: 'red' });
    },
  });

  const testMutation = useMutation({
    mutationFn: () => shellApi.testConnection(tenantId),
    onSuccess: (result) => {
      notifications.show({
        title: result.success ? 'Соединение установлено' : 'Ошибка соединения',
        message: result.message,
        color: result.success ? 'green' : 'red',
      });
    },
    onError: () => {
      notifications.show({
        title: 'Ошибка',
        message: 'Тест соединения не удался',
        color: 'red',
      });
    },
  });

  if (isLoading) {
    return (
      <Center py="md">
        <Loader />
      </Center>
    );
  }

  return (
    <Card withBorder padding="lg" maw={800}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          saveMutation.mutate();
        }}
      >
        <Stack gap="md">
          {dirty && (
            <Alert icon={<IconAlertCircle size={16} />} color="yellow" variant="light">
              У вас есть несохранённые изменения.
            </Alert>
          )}

          <Select
            label="Тип провайдера"
            description="Ollama — локальные модели, OpenAI Compatible — любой OpenAI-совместимый API, DeepSeek — API DeepSeek"
            data={[
              { value: 'ollama', label: 'Ollama (локальный)' },
              { value: 'openai_compatible', label: 'OpenAI Compatible' },
              { value: 'deepseek_compatible', label: 'DeepSeek Compatible' },
            ]}
            value={form.provider_type || ''}
            onChange={(val) => updateField('provider_type', val || '')}
          />

          <TextInput
            label="Базовый URL провайдера"
            description="Для Ollama: http://localhost:11434, для DeepSeek: https://api.deepseek.com"
            placeholder="http://localhost:11434"
            value={form.provider_base_url || ''}
            onChange={(e) => updateField('provider_base_url', e.currentTarget.value)}
          />

          <PasswordInput
            label="API ключ провайдера"
            description="Ключ аутентификации у провайдера. Для локального Ollama не требуется"
            placeholder="sk-..."
            value={form.provider_api_key || ''}
            onChange={(e) => updateField('provider_api_key', e.currentTarget.value)}
          />

          <TextInput
            label="Название модели"
            description="Точное имя модели. Ollama: qwen2.5:32b, DeepSeek: deepseek-chat, OpenAI: gpt-4o"
            placeholder="qwen2.5:32b"
            value={form.model_name || ''}
            onChange={(e) => updateField('model_name', e.currentTarget.value)}
          />

          <Textarea
            label="Системный промпт"
            description="Основная инструкция для LLM — роль, стиль общения, язык ответов"
            placeholder="Ты AI-ассистент компании. Отвечай вежливо и по делу."
            value={form.system_prompt || ''}
            onChange={(e) => updateField('system_prompt', e.currentTarget.value)}
            autosize
            minRows={4}
            maxRows={12}
          />

          <Textarea
            label="Текст правил"
            description="Дополнительные ограничения, добавляются после системного промпта"
            placeholder="Не обсуждай конкурентов. Отвечай кратко."
            value={form.rules_text || ''}
            onChange={(e) => updateField('rules_text', e.currentTarget.value)}
            autosize
            minRows={3}
            maxRows={8}
          />

          <div>
            <Text size="sm" fw={500} mb={2}>
              Температура: {form.temperature?.toFixed(2) ?? '0.70'}
            </Text>
            <Text size="xs" c="dimmed" mb="xs">
              0 — строгие, предсказуемые ответы, 1 — сбалансировано, 2 — максимально креативно
            </Text>
            <Slider
              min={0}
              max={2}
              step={0.01}
              value={form.temperature ?? 0.7}
              onChange={(val) => updateField('temperature', val)}
              marks={[
                { value: 0, label: '0' },
                { value: 1, label: '1' },
                { value: 2, label: '2' },
              ]}
            />
          </div>

          <Group grow>
            <NumberInput
              label="Макс. сообщений контекста"
              description="Сколько последних сообщений чата отправлять в LLM"
              value={form.max_context_messages ?? 20}
              onChange={(val) => updateField('max_context_messages', Number(val))}
              min={1}
              max={200}
            />
            <NumberInput
              label="Макс. токенов ответа"
              description="Максимальная длина ответа LLM в токенах"
              value={form.max_tokens ?? 4096}
              onChange={(val) => updateField('max_tokens', Number(val))}
              min={1}
              max={128000}
            />
          </Group>

          <Group>
            <Switch
              label="Память включена"
              checked={form.memory_enabled ?? false}
              onChange={(e) => updateField('memory_enabled', e.currentTarget.checked)}
            />
            <Switch
              label="База знаний включена"
              checked={form.knowledge_base_enabled ?? false}
              onChange={(e) => updateField('knowledge_base_enabled', e.currentTarget.checked)}
            />
          </Group>

          {form.knowledge_base_enabled && (
            <Group grow>
              <TextInput
                label="Модель эмбеддингов"
                description="Модель для генерации эмбеддингов (например, nomic-embed-text)"
                placeholder="nomic-embed-text"
                value={form.embedding_model_name ?? ''}
                onChange={(e) => updateField('embedding_model_name', e.currentTarget.value || undefined)}
              />
              <NumberInput
                label="Макс. чанков KB"
                description="Сколько релевантных чанков подмешивать в контекст"
                min={1}
                max={50}
                value={form.kb_max_chunks ?? 10}
                onChange={(val) => updateField('kb_max_chunks', typeof val === 'number' ? val : 10)}
              />
            </Group>
          )}

          <Group justify="space-between">
            <Button
              variant="outline"
              leftSection={<IconPlugConnected size={16} />}
              onClick={() => testMutation.mutate()}
              loading={testMutation.isPending}
            >
              Тест соединения
            </Button>
            <Button
              type="submit"
              leftSection={<IconDeviceFloppy size={16} />}
              loading={saveMutation.isPending}
              disabled={!dirty}
            >
              Сохранить изменения
            </Button>
          </Group>
        </Stack>
      </form>
    </Card>
  );
}

// ===== TOOLS TAB =====

function ToolsTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [toolName, setToolName] = useState('');
  const [toolDesc, setToolDesc] = useState('');
  const [toolGroup, setToolGroup] = useState('');
  const [toolType, setToolType] = useState('function');
  const [configJson, setConfigJson] = useState('{}');
  const [toolActive, setToolActive] = useState(true);
  const [groupFilter, setGroupFilter] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'tools', page],
    queryFn: () => toolsApi.list(tenantId, page),
  });

  const openCreate = () => {
    setEditId(null);
    setToolName('');
    setToolDesc('');
    setToolGroup('');
    setToolType('function');
    setConfigJson('{}');
    setToolActive(true);
    setModalOpen(true);
  };

  const openEdit = (tool: Tool) => {
    setEditId(tool.id);
    setToolName(tool.name);
    setToolDesc(tool.description || '');
    setToolGroup(tool.group || '');
    setToolType(tool.tool_type);
    setConfigJson(JSON.stringify(tool.config_json ?? {}, null, 2));
    setToolActive(tool.is_active);
    setModalOpen(true);
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

  const handleSave = () => {
    let parsedConfig: Record<string, unknown>;
    try {
      parsedConfig = JSON.parse(configJson);
    } catch {
      notifications.show({ title: 'Ошибка', message: 'Некорректный JSON в конфигурации', color: 'red' });
      return;
    }

    const toolData = {
      name: toolName,
      description: toolDesc,
      group: toolGroup || undefined,
      tool_type: toolType,
      config_json: parsedConfig,
      is_active: toolActive,
    };
    if (editId) {
      updateMutation.mutate({ toolId: editId, data: toolData });
    } else {
      createMutation.mutate(toolData);
    }
  };

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Group>
          <Text fw={500}>Инструменты</Text>
          {data?.items && data.items.length > 0 && (
            <Select
              placeholder="Все группы"
              clearable
              size="xs"
              w={180}
              value={groupFilter}
              onChange={setGroupFilter}
              data={
                Array.from(new Set(data.items.map((t) => t.group).filter(Boolean) as string[]))
                  .sort()
                  .map((g) => ({ value: g, label: g }))
              }
            />
          )}
        </Group>
        <Button leftSection={<IconPlus size={16} />} size="sm" onClick={openCreate}>
          Добавить инструмент
        </Button>
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">Инструменты не настроены.</Text>
      ) : (() => {
        const filtered = groupFilter
          ? data.items.filter((t) => t.group === groupFilter)
          : data.items;
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
                <Text size="sm" fw={600} c="dimmed" mb="xs">{groupName} ({groupTools.length})</Text>
                <Table striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Название</Table.Th>
                      <Table.Th>Описание</Table.Th>
                      <Table.Th>Тип</Table.Th>
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
                        <Table.Td><Badge variant="light" size="sm">{tool.tool_type}</Badge></Table.Td>
                        <Table.Td>
                          <Badge color={tool.is_active ? 'green' : 'gray'} size="sm">
                            {tool.is_active ? 'Активный' : 'Неактивный'}
                          </Badge>
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
        size="lg"
      >
        <Text size="sm" c="dimmed" mb="md">
          Инструменты — это функции, которые LLM может вызывать для выполнения действий.
          Опишите инструмент и укажите его JSON-схему в формате OpenAI function calling.
        </Text>
        <Stack gap="md">
          <TextInput
            label="Название"
            description="Уникальное имя инструмента, например: get_weather, search_docs"
            placeholder="get_weather"
            value={toolName}
            onChange={(e) => setToolName(e.currentTarget.value)}
            required
          />
          <Textarea
            label="Описание"
            description="Что делает инструмент — LLM использует это для решения, когда его вызвать"
            placeholder="Получает текущую погоду для указанного города"
            value={toolDesc}
            onChange={(e) => setToolDesc(e.currentTarget.value)}
          />
          <TextInput
            label="Группа"
            description="Группа для организации инструментов, например: Сеть, Диагностика, Биллинг"
            placeholder="Сеть"
            value={toolGroup}
            onChange={(e) => setToolGroup(e.currentTarget.value)}
          />
          <TextInput
            label="Тип инструмента"
            description="Обычно 'function'. Другие типы: retrieval, code_interpreter"
            placeholder="function"
            value={toolType}
            onChange={(e) => setToolType(e.currentTarget.value)}
            required
          />
          <Textarea
            label="Конфигурация JSON"
            description='JSON-схема параметров в формате OpenAI. Пример: {"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}'
            placeholder='{"type":"function","function":{"name":"...","parameters":{...}}}'
            value={configJson}
            onChange={(e) => setConfigJson(e.currentTarget.value)}
            autosize
            minRows={6}
            maxRows={15}
            ff="monospace"
            styles={{ input: { fontFamily: 'monospace' } }}
          />
          <Switch label="Активный" checked={toolActive} onChange={(e) => setToolActive(e.currentTarget.checked)} />
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

// ===== KB TAB =====

const SOURCE_TYPE_OPTIONS = [
  { value: 'manual', label: 'Ручной ввод' },
  { value: 'faq', label: 'FAQ' },
  { value: 'solution', label: 'Решение' },
  { value: 'procedure', label: 'Процедура' },
  { value: 'reference', label: 'Справка' },
];

const DOC_TYPE_OPTIONS = [
  { value: 'text', label: 'Текст' },
  { value: 'url', label: 'Ссылка (URL)' },
  { value: 'file', label: 'Файл' },
];

const EMBEDDING_STATUS_MAP: Record<string, { color: string; label: string }> = {
  pending: { color: 'yellow', label: 'Ожидание' },
  processing: { color: 'blue', label: 'Обработка' },
  done: { color: 'green', label: 'Готово' },
  error: { color: 'red', label: 'Ошибка' },
};

function KBTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [docType, setDocType] = useState<string>('text');
  const [sourceType, setSourceType] = useState<string>('manual');
  const [sourceUrl, setSourceUrl] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [docActive, setDocActive] = useState(true);
  const [filterDocType, setFilterDocType] = useState<string | null>(null);
  const [filterSourceType, setFilterSourceType] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'kb', page, filterDocType, filterSourceType],
    queryFn: () => kbApi.list(tenantId, page, 20, filterDocType || undefined, filterSourceType || undefined),
  });

  const openCreate = () => {
    setEditId(null);
    setTitle('');
    setContent('');
    setDocType('text');
    setSourceType('manual');
    setSourceUrl('');
    setUploadFile(null);
    setDocActive(true);
    setModalOpen(true);
  };

  const openEdit = (doc: KBDocument) => {
    setEditId(doc.id);
    setTitle(doc.title);
    setContent(doc.content);
    setDocType(doc.doc_type);
    setSourceType(doc.source_type);
    setSourceUrl(doc.source_url || '');
    setUploadFile(null);
    setDocActive(doc.is_active);
    setModalOpen(true);
  };

  const invalidateKB = () => queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'kb'] });

  const createMutation = useMutation({
    mutationFn: (data: KBDocumentCreate) => kbApi.create(tenantId, data),
    onSuccess: () => {
      invalidateKB();
      setModalOpen(false);
      notifications.show({ title: 'Создано', message: 'Документ создан и отправлен на индексацию', color: 'green' });
    },
    onError: (err: Error) => {
      notifications.show({ title: 'Ошибка', message: err.message || 'Не удалось создать документ', color: 'red' });
    },
  });

  const uploadMutation = useMutation({
    mutationFn: ({ file, title, sourceType }: { file: File; title: string; sourceType: string }) =>
      kbApi.upload(tenantId, file, title, sourceType),
    onSuccess: () => {
      invalidateKB();
      setModalOpen(false);
      notifications.show({ title: 'Загружено', message: 'Файл загружен и отправлен на индексацию', color: 'green' });
    },
    onError: (err: Error) => {
      notifications.show({ title: 'Ошибка', message: err.message || 'Не удалось загрузить файл', color: 'red' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ docId, data }: { docId: string; data: KBDocumentUpdate }) =>
      kbApi.update(tenantId, docId, data),
    onSuccess: () => {
      invalidateKB();
      setModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Документ обновлён', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось обновить документ', color: 'red' });
    },
  });

  const reembedMutation = useMutation({
    mutationFn: (docId: string) => kbApi.reembed(tenantId, docId),
    onSuccess: () => {
      invalidateKB();
      notifications.show({ title: 'Переиндексация', message: 'Документ переиндексирован', color: 'green' });
    },
  });

  const reembedAllMutation = useMutation({
    mutationFn: () => kbApi.reembedAll(tenantId),
    onSuccess: (res) => {
      invalidateKB();
      notifications.show({
        title: 'Переиндексация всех',
        message: `Готово: ${res.success} успешно, ${res.error} ошибок из ${res.total}`,
        color: res.error > 0 ? 'yellow' : 'green',
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (docId: string) => kbApi.delete(tenantId, docId),
    onSuccess: () => {
      invalidateKB();
      notifications.show({ title: 'Удалено', message: 'Документ удалён', color: 'green' });
    },
  });

  const handleSave = () => {
    if (editId) {
      updateMutation.mutate({
        docId: editId,
        data: { title, content, source_type: sourceType, is_active: docActive },
      });
    } else if (docType === 'file' && uploadFile) {
      uploadMutation.mutate({ file: uploadFile, title, sourceType });
    } else {
      createMutation.mutate({
        title,
        doc_type: docType,
        source_type: sourceType,
        source_url: docType === 'url' ? sourceUrl : undefined,
        content: docType === 'url' ? '' : content,
        is_active: docActive,
      });
    }
  };

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;
  const isSaving = createMutation.isPending || updateMutation.isPending || uploadMutation.isPending;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>База знаний (RAG)</Text>
        <Group gap="xs">
          <Button
            variant="light"
            size="sm"
            leftSection={<IconRefresh size={16} />}
            onClick={() => reembedAllMutation.mutate()}
            loading={reembedAllMutation.isPending}
          >
            Переиндексировать всё
          </Button>
          <Button leftSection={<IconPlus size={16} />} size="sm" onClick={openCreate}>
            Добавить
          </Button>
        </Group>
      </Group>

      <Text size="sm" c="dimmed">
        Релевантные фрагменты документов автоматически подбираются по смыслу запроса пользователя (семантический поиск).
      </Text>

      <Group gap="xs">
        <Select
          placeholder="Тип источника"
          data={[{ value: '', label: 'Все типы' }, ...DOC_TYPE_OPTIONS]}
          value={filterDocType || ''}
          onChange={(v) => { setFilterDocType(v || null); setPage(1); }}
          size="xs"
          w={160}
          clearable
        />
        <Select
          placeholder="Категория"
          data={[{ value: '', label: 'Все категории' }, ...SOURCE_TYPE_OPTIONS]}
          value={filterSourceType || ''}
          onChange={(v) => { setFilterSourceType(v || null); setPage(1); }}
          size="xs"
          w={160}
          clearable
        />
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">Документов базы знаний нет.</Text>
      ) : (
        <>
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Заголовок</Table.Th>
                <Table.Th>Тип</Table.Th>
                <Table.Th>Категория</Table.Th>
                <Table.Th>Индексация</Table.Th>
                <Table.Th>Чанков</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((doc) => {
                const embStatus = EMBEDDING_STATUS_MAP[doc.embedding_status] || { color: 'gray', label: doc.embedding_status };
                return (
                  <Table.Tr key={doc.id} style={{ cursor: 'pointer' }} onClick={() => openEdit(doc)}>
                    <Table.Td>
                      <Text size="sm" fw={500}>{doc.title}</Text>
                      {doc.source_url && (
                        <Text size="xs" c="dimmed" truncate="end" maw={250}>{doc.source_url}</Text>
                      )}
                      {doc.source_filename && (
                        <Text size="xs" c="dimmed">{doc.source_filename}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light" size="sm">
                        {DOC_TYPE_OPTIONS.find((o) => o.value === doc.doc_type)?.label || doc.doc_type}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="dot" size="sm">
                        {SOURCE_TYPE_OPTIONS.find((o) => o.value === doc.source_type)?.label || doc.source_type}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={doc.embedding_error || ''} disabled={!doc.embedding_error}>
                        <Badge color={embStatus.color} size="sm">{embStatus.label}</Badge>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td>{doc.chunks_count}</Table.Td>
                    <Table.Td>
                      <Badge color={doc.is_active ? 'green' : 'gray'} size="sm">
                        {doc.is_active ? 'Активный' : 'Выкл'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        <Tooltip label="Переиндексировать">
                          <ActionIcon
                            variant="subtle"
                            color="blue"
                            size="sm"
                            onClick={(e) => { e.stopPropagation(); reembedMutation.mutate(doc.id); }}
                          >
                            <IconRefresh size={14} />
                          </ActionIcon>
                        </Tooltip>
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (window.confirm(`Удалить "${doc.title}"?`)) deleteMutation.mutate(doc.id);
                          }}
                        >
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
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
        title={editId ? 'Редактировать документ' : 'Добавить в базу знаний'}
        size="lg"
      >
        <Stack gap="md">
          {!editId && (
            <Select
              label="Тип источника"
              data={DOC_TYPE_OPTIONS}
              value={docType}
              onChange={(v) => setDocType(v || 'text')}
            />
          )}

          <TextInput
            label="Заголовок"
            placeholder="Инструкция по настройке роутера"
            value={title}
            onChange={(e) => setTitle(e.currentTarget.value)}
            required
          />

          <Select
            label="Категория"
            description="Помогает LLM понять тип информации"
            data={SOURCE_TYPE_OPTIONS}
            value={sourceType}
            onChange={(v) => setSourceType(v || 'manual')}
          />

          {docType === 'url' && !editId && (
            <TextInput
              label="URL страницы"
              description="Содержимое страницы будет автоматически извлечено"
              placeholder="https://docs.example.com/article"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.currentTarget.value)}
              required
            />
          )}

          {docType === 'file' && !editId && (
            <div>
              <Text size="sm" fw={500} mb={4}>Файл</Text>
              <Text size="xs" c="dimmed" mb={8}>PDF, TXT, MD, CSV, HTML — до 10 МБ</Text>
              <input
                type="file"
                accept=".pdf,.txt,.md,.csv,.log,.json,.xml,.html"
                onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
              />
            </div>
          )}

          {(docType === 'text' || editId) && (
            <Textarea
              label="Содержание"
              description="Текст будет автоматически разбит на чанки и проиндексирован"
              placeholder="Для настройки роутера TP-Link выполните следующие шаги..."
              value={content}
              onChange={(e) => setContent(e.currentTarget.value)}
              autosize
              minRows={8}
              maxRows={20}
            />
          )}

          <Switch
            label="Активный"
            checked={docActive}
            onChange={(e) => setDocActive(e.currentTarget.checked)}
          />

          <Group justify="flex-end">
            <Button variant="default" onClick={() => setModalOpen(false)}>Отмена</Button>
            <Button onClick={handleSave} loading={isSaving}>
              {editId ? 'Обновить' : docType === 'file' ? 'Загрузить' : 'Создать'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}

// ===== MEMORY TAB =====

function MemoryTab({ tenantId }: { tenantId: string }) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [memType, setMemType] = useState('');
  const [memContent, setMemContent] = useState('');
  const [memPriority, setMemPriority] = useState(0);
  const [memPinned, setMemPinned] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'memory', page, typeFilter],
    queryFn: () => memoryApi.list(tenantId, page, 20, typeFilter || undefined),
  });

  const openCreate = () => {
    setEditId(null);
    setMemType('');
    setMemContent('');
    setMemPriority(0);
    setMemPinned(false);
    setModalOpen(true);
  };

  const openEdit = (entry: MemoryEntry) => {
    setEditId(entry.id);
    setMemType(entry.memory_type);
    setMemContent(entry.content);
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
        data: { memory_type: memType, content: memContent, priority: memPriority, is_pinned: memPinned },
      });
    } else {
      createMutation.mutate({
        memory_type: memType,
        content: memContent,
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
            onChange={(val) => {
              setTypeFilter(val);
              setPage(1);
            }}
            size="sm"
            w={180}
          />
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
                        if (window.confirm('Удалить эту запись?')) deleteMutation.mutate(entry.id);
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

      <Modal opened={modalOpen} onClose={() => setModalOpen(false)} title={editId ? 'Редактировать запись памяти' : 'Создать запись памяти'}>
        <Text size="sm" c="dimmed" mb="md">
          Память позволяет LLM помнить факты о тенанте или чате.
          Записи подмешиваются в системный промпт при каждом запросе.
        </Text>
        <Stack gap="md">
          <Select
            label="Тип памяти"
            description="short_term — текущий контекст, long_term — постоянные факты, episodic — выжимки из прошлых сессий"
            data={['short_term', 'long_term', 'episodic']}
            value={memType}
            onChange={(val) => setMemType(val || '')}
            required
            allowDeselect={false}
          />
          <Textarea
            label="Содержание"
            description="Текст записи, например: 'Клиент предпочитает общение на русском языке'"
            placeholder="Клиент использует тариф «Бизнес 100»"
            value={memContent}
            onChange={(e) => setMemContent(e.currentTarget.value)}
            autosize
            minRows={3}
            required
          />
          <NumberInput
            label="Приоритет"
            description="Чем выше число, тем раньше запись попадёт в контекст (0 — обычный)"
            value={memPriority}
            onChange={(val) => setMemPriority(Number(val))}
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

// ===== CHATS TAB =====

function ChatsTab({ tenantId }: { tenantId: string }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'admin', page],
    queryFn: () => chatsApi.listAdmin(tenantId, page),
  });

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;

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
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Заголовок</Table.Th>
                <Table.Th>Описание</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Создан</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((chat) => (
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
              ))}
            </Table.Tbody>
          </Table>
          {totalPages > 1 && (
            <Center><Pagination total={totalPages} value={page} onChange={setPage} /></Center>
          )}
        </>
      )}
    </Stack>
  );
}

// ===== LOGS TAB =====

function LogsTab({ tenantId }: { tenantId: string }) {
  const [page, setPage] = useState(1);
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedLogId, setSelectedLogId] = useState<string | null>(null);
  const [chatFilter, setChatFilter] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const filters = {
    chat_id: chatFilter || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  };

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'logs', page, chatFilter, dateFrom, dateTo],
    queryFn: () => logsApi.list(tenantId, page, 20, filters),
  });

  const { data: logDetail, isLoading: detailLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'logs', selectedLogId, 'detail'],
    queryFn: () => logsApi.getDetail(tenantId, selectedLogId!),
    enabled: !!selectedLogId,
  });

  // Load chats for filter dropdown
  const { data: chatsData } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'admin', 1],
    queryFn: () => chatsApi.listAdmin(tenantId, 1, 100),
  });

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;

  // Map chat_id to title for display
  const chatMap = new Map(
    (chatsData?.items || []).map((c) => [c.id, c.title || c.description || c.id.slice(0, 8)])
  );

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>LLM Логи</Text>
        <Group gap="xs">
          <Select
            placeholder="Все чаты"
            clearable
            size="xs"
            w={200}
            value={chatFilter}
            onChange={(val) => { setChatFilter(val); setPage(1); }}
            data={(chatsData?.items || []).map((c) => ({
              value: c.id,
              label: c.title || c.description || c.id.slice(0, 8),
            }))}
          />
          <TextInput
            type="date"
            size="xs"
            w={140}
            placeholder="Дата от"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.currentTarget.value); setPage(1); }}
          />
          <TextInput
            type="date"
            size="xs"
            w={140}
            placeholder="Дата до"
            value={dateTo}
            onChange={(e) => { setDateTo(e.currentTarget.value); setPage(1); }}
          />
          {(chatFilter || dateFrom || dateTo) && (
            <Button variant="subtle" size="xs" onClick={() => { setChatFilter(null); setDateFrom(''); setDateTo(''); setPage(1); }}>
              Сбросить
            </Button>
          )}
        </Group>
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">Логов пока нет.</Text>
      ) : (
        <>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Время</Table.Th>
                <Table.Th>Чат</Table.Th>
                <Table.Th>Модель</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Токены</Table.Th>
                <Table.Th>Задержка</Table.Th>
                <Table.Th>Tools</Table.Th>
                <Table.Th>Стоимость</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((log) => (
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
                  <Table.Td>
                    <Text size="sm" lineClamp={1}>
                      {log.chat_id ? (chatMap.get(log.chat_id) || log.chat_id.slice(0, 8)) : '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td><Text size="sm" ff="monospace">{log.model_name}</Text></Table.Td>
                  <Table.Td>
                    <Badge color={log.status === 'success' ? 'green' : 'red'}>
                      {log.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {log.prompt_tokens ?? '-'} / {log.completion_tokens ?? '-'} / {log.total_tokens ?? '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">
                      {log.latency_ms != null ? `${log.latency_ms.toFixed(2)}ms` : '-'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{log.tool_calls_count || '-'}</Text>
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
      >
        {detailLoading ? (
          <Center py="md"><Loader /></Center>
        ) : logDetail ? (
          <LogDetailView logDetail={logDetail} />
        ) : (
          <Text c="dimmed">Нет данных.</Text>
        )}
      </Drawer>
    </Stack>
  );
}

function LogDetailView({ logDetail }: { logDetail: LLMLogDetail }) {
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
                Промпт: {logDetail.prompt_tokens} | Ответ: {logDetail.completion_tokens} | Всего: {logDetail.total_tokens}
              </Text>
            </Group>
            <Group>
              <Text size="sm" fw={500}>Задержка:</Text>
              <Text size="sm">{logDetail.latency_ms}ms</Text>
            </Group>
            {logDetail.estimated_cost != null && (
              <Group>
                <Text size="sm" fw={500}>Стоимость:</Text>
                <Text size="sm">${logDetail.estimated_cost.toFixed(6)}</Text>
              </Group>
            )}
            {logDetail.error_text && (
              <Alert color="red" variant="light">
                {logDetail.error_text}
              </Alert>
            )}
          </Stack>
        </Card>

        {logDetail.raw_request && (
          <div>
            <Text size="sm" fw={500} mb="xs">Исходный запрос</Text>
            <Code block>{JSON.stringify(logDetail.raw_request, null, 2)}</Code>
          </div>
        )}

        {logDetail.raw_response && (
          <div>
            <Text size="sm" fw={500} mb="xs">Исходный ответ</Text>
            <Code block>{JSON.stringify(logDetail.raw_response, null, 2)}</Code>
          </div>
        )}

        {logDetail.normalized_request && (
          <div>
            <Text size="sm" fw={500} mb="xs">Нормализованный запрос</Text>
            <Code block>{JSON.stringify(logDetail.normalized_request, null, 2)}</Code>
          </div>
        )}

        {logDetail.normalized_response && (
          <div>
            <Text size="sm" fw={500} mb="xs">Нормализованный ответ</Text>
            <Code block>{JSON.stringify(logDetail.normalized_response, null, 2)}</Code>
          </div>
        )}
      </Stack>
    </ScrollArea>
  );
}
