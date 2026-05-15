import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Group,
  Loader,
  Modal,
  PasswordInput,
  Select,
  SimpleGrid,
  Slider,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertCircle, IconDeviceFloppy, IconEdit, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { customModelsApi, modelConfigApi, modelsApi } from '../../shared/api/endpoints';
import type {
  LLMModelBrief,
  TenantCustomModel,
  TenantCustomModelCreate,
  TenantCustomModelUpdate,
  TenantModelConfigUpdate,
} from '../../shared/api/types';

const PROVIDER_OPTIONS_MODEL = [
  { value: 'ollama', label: 'Ollama (локальный)' },
  { value: 'openai_compatible', label: 'OpenAI Compatible' },
  { value: 'deepseek_compatible', label: 'DeepSeek Compatible' },
];

type ModelConfigTabProps = {
  tenantId: string;
};

export function ModelConfigTab({ tenantId }: ModelConfigTabProps) {
  const queryClient = useQueryClient();

  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'model-config'],
    queryFn: () => modelConfigApi.get(tenantId),
  });

  const { data: catalogModels } = useQuery({
    queryKey: ['models', 'brief'],
    queryFn: () => modelsApi.brief(),
  });

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
      notifications.show({
        title: 'Сохранено',
        message: 'Конфигурация модели обновлена',
        color: 'green',
      });
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

  const openEditCustom = (model: TenantCustomModel) => {
    setEditCustomId(model.id);
    setCmName(model.name);
    setCmProvider(model.provider_type);
    setCmBaseUrl(model.base_url || '');
    setCmApiKey('');
    setCmModelId(model.model_id);
    setCmTier(model.tier);
    setCmTools(model.supports_tools);
    setCmVision(model.supports_vision);
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

  const catalogOptions = (catalogModels || []).map((model: LLMModelBrief) => ({
    value: model.id,
    label: `${model.name} (${model.model_id}) [${model.tier}]`,
  }));

  const customModels = customModelsData?.items || [];
  const customOptions = customModels.map((model: TenantCustomModel) => ({
    value: model.id,
    label: `${model.name} (${model.model_id}) [приватная]`,
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
              onChange={(value) => {
                setMode(value || 'manual');
                markDirty();
              }}
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
                  onChange={(value) => {
                    setManualModelId(value);
                    setManualCustomModelId(null);
                    markDirty();
                  }}
                  clearable
                  searchable
                />
                <Text size="xs" c="dimmed" ta="center">— или приватная модель —</Text>
                <Select
                  label="Приватная модель"
                  placeholder="Выберите модель..."
                  data={customOptions}
                  value={manualCustomModelId}
                  onChange={(value) => {
                    setManualCustomModelId(value);
                    setManualModelId(null);
                    markDirty();
                  }}
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
                      onChange={(value) => {
                        setAutoLightModelId(value);
                        setAutoLightCustomId(null);
                        markDirty();
                      }}
                      clearable
                      searchable
                    />
                    <Select
                      label="Или приватная"
                      placeholder="Выберите..."
                      data={customOptions}
                      value={autoLightCustomId}
                      onChange={(value) => {
                        setAutoLightCustomId(value);
                        setAutoLightModelId(null);
                        markDirty();
                      }}
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
                      onChange={(value) => {
                        setAutoHeavyModelId(value);
                        setAutoHeavyCustomId(null);
                        markDirty();
                      }}
                      clearable
                      searchable
                    />
                    <Select
                      label="Или приватная"
                      placeholder="Выберите..."
                      data={customOptions}
                      value={autoHeavyCustomId}
                      onChange={(value) => {
                        setAutoHeavyCustomId(value);
                        setAutoHeavyModelId(null);
                        markDirty();
                      }}
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
                    onChange={(value) => {
                      setThreshold(value);
                      markDirty();
                    }}
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
                  {customModels.map((model: TenantCustomModel) => (
                    <Table.Tr key={model.id} style={{ cursor: 'pointer' }} onClick={() => openEditCustom(model)}>
                      <Table.Td><Text size="sm" fw={500}>{model.name}</Text></Table.Td>
                      <Table.Td><Badge variant="light" size="sm">{model.provider_type}</Badge></Table.Td>
                      <Table.Td><Text size="sm" ff="monospace">{model.model_id}</Text></Table.Td>
                      <Table.Td><Badge size="sm">{model.tier}</Badge></Table.Td>
                      <Table.Td>
                        <Badge color={model.is_active ? 'green' : 'gray'} size="sm">
                          {model.is_active ? 'Активна' : 'Выкл'}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          <ActionIcon variant="subtle" color="blue" size="sm" onClick={(e) => { e.stopPropagation(); openEditCustom(model); }}>
                            <IconEdit size={14} />
                          </ActionIcon>
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (window.confirm(`Удалить "${model.name}"?`)) {
                                deleteCustomMutation.mutate(model.id);
                              }
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
              onChange={(value) => setCmProvider(value || 'ollama')}
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
              onChange={(value) => setCmTier(value || 'medium')}
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
            placeholder={cmProvider === 'ollama' ? 'qwen2.5:14b' : 'gpt-4o'}
            description={
              cmProvider === 'ollama'
                ? 'Для Ollama нужен точный tag модели из `ollama list`, например `qwen2.5:14b`.'
                : 'Точное имя модели у провайдера, например `gpt-4o` или `deepseek-chat`.'
            }
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
