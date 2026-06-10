import { useEffect, useState, type ReactNode } from 'react';
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Center,
  Fieldset,
  Group,
  Loader,
  Modal,
  NumberInput,
  PasswordInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Slider,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertCircle,
  IconDeviceFloppy,
  IconEdit,
  IconHelpCircle,
  IconPlus,
  IconTrash,
} from '@tabler/icons-react';
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

function Hint({ children, hint }: { children: ReactNode; hint: ReactNode }) {
  return (
    <Group gap={4} wrap="nowrap" align="center">
      <Text component="span" size="sm" fw={500}>{children}</Text>
      <Tooltip label={hint} multiline w={360} withArrow position="right" openDelay={150}>
        <ActionIcon size="xs" variant="subtle" color="gray" tabIndex={-1} aria-label="Подсказка">
          <IconHelpCircle size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

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
  const [sizeThreshold, setSizeThreshold] = useState(24000);
  const [useClassifier, setUseClassifier] = useState(false);
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
      setSizeThreshold(config.auto_size_threshold ?? 24000);
      setUseClassifier(config.use_complexity_classifier ?? false);
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
      auto_size_threshold: sizeThreshold,
      use_complexity_classifier: useClassifier,
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
    label: `${model.name} [${model.tier}]`,
  }));

  const customModels = customModelsData?.items || [];
  const customOptions = customModels.map((model: TenantCustomModel) => ({
    value: model.id,
    label: `${model.name} [приватная]`,
  }));

  const markDirty = () => setDirty(true);

  if (configLoading) {
    return <Center py="md"><Loader /></Center>;
  }

  const ModelPicker = ({
    color,
    catalogValue,
    customValue,
    onCatalog,
    onCustom,
    hint,
    title,
  }: {
    color: string;
    catalogValue: string | null;
    customValue: string | null;
    onCatalog: (v: string | null) => void;
    onCustom: (v: string | null) => void;
    hint: string;
    title: string;
  }) => (
    <Fieldset
      legend={
        <Group gap={6} wrap="nowrap">
          <Badge color={color} variant="filled" size="sm">{title}</Badge>
          <Tooltip label={hint} multiline w={320} withArrow position="right">
            <ActionIcon size="xs" variant="subtle" color="gray"><IconHelpCircle size={14} /></ActionIcon>
          </Tooltip>
        </Group>
      }
    >
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
        <Select
          label="Из каталога"
          placeholder="—"
          data={catalogOptions}
          value={catalogValue}
          onChange={(v) => { onCatalog(v); onCustom(null); markDirty(); }}
          clearable
          searchable
          size="sm"
        />
        <Select
          label="Или приватная"
          placeholder="—"
          data={customOptions}
          value={customValue}
          onChange={(v) => { onCustom(v); onCatalog(null); markDirty(); }}
          clearable
          searchable
          size="sm"
        />
      </SimpleGrid>
    </Fieldset>
  );

  return (
    <>
      <Stack gap="lg" maw={1100}>
        <Card withBorder padding="lg">
          <Stack gap="md">
            <Group justify="space-between" align="flex-end">
              <div>
                <Title order={4}>Выбор модели</Title>
                <Text size="xs" c="dimmed">Manual — фиксированная модель. Auto — классификатор complexity (0-1) выбирает light/heavy.</Text>
              </div>
              <SegmentedControl
                value={mode}
                onChange={(v) => { setMode(v); markDirty(); }}
                data={[
                  { value: 'manual', label: 'Manual' },
                  { value: 'auto', label: 'Auto' },
                ]}
                size="sm"
              />
            </Group>

            {dirty && (
              <Alert icon={<IconAlertCircle size={14} />} color="yellow" variant="light" py={6}>
                Несохранённые изменения.
              </Alert>
            )}

            {mode === 'manual' && (
              <ModelPicker
                color="blue"
                title="Модель"
                hint="Все запросы тенанта идут в эту модель."
                catalogValue={manualModelId}
                customValue={manualCustomModelId}
                onCatalog={setManualModelId}
                onCustom={setManualCustomModelId}
              />
            )}

            {mode === 'auto' && (
              <Stack gap="md">
                <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
                  <ModelPicker
                    color="green"
                    title="Light"
                    hint="Простые запросы (greeting, арифметика, короткие факты). Также используется классификатором complexity."
                    catalogValue={autoLightModelId}
                    customValue={autoLightCustomId}
                    onCatalog={setAutoLightModelId}
                    onCustom={setAutoLightCustomId}
                  />
                  <ModelPicker
                    color="violet"
                    title="Heavy"
                    hint="Сложные запросы (multi-step reasoning, кодогенерация, большие выборки)."
                    catalogValue={autoHeavyModelId}
                    customValue={autoHeavyCustomId}
                    onCatalog={setAutoHeavyModelId}
                    onCustom={setAutoHeavyCustomId}
                  />
                </SimpleGrid>

                <Fieldset legend="Роутинг light → heavy">
                  <Stack gap="sm">
                    <NumberInput
                      label={
                        <Hint hint="Перед каждым раундом оценивается размер prompt'а (tiktoken). Если выше порога — переключаемся на heavy и не возвращаемся обратно в этом запросе. 0 = выключить size-routing.">
                          Порог размера контекста, токены
                        </Hint>
                      }
                      value={sizeThreshold}
                      onChange={(v) => { setSizeThreshold(typeof v === 'number' ? v : 0); markDirty(); }}
                      min={0}
                      max={120000}
                      step={1000}
                      w={260}
                    />
                    <Switch
                      label={
                        <Hint hint="Legacy: классификатор сложности — отдельный LLM-вызов light-моделью оценивает запрос (0-1) и при ≥ порога эскалирует в heavy. Менее предсказуем чем size-routing. Off по умолчанию.">
                          Использовать classifier сложности (legacy)
                        </Hint>
                      }
                      checked={useClassifier}
                      onChange={(e) => { setUseClassifier(e.currentTarget.checked); markDirty(); }}
                    />
                    {useClassifier && (
                      <div>
                        <Hint hint="Запросы со score < threshold → light, остальные → heavy. Используется только если classifier включён.">
                          Порог сложности: {threshold.toFixed(2)}
                        </Hint>
                        <Slider
                          min={0}
                          max={1}
                          step={0.05}
                          value={threshold}
                          onChange={(v) => { setThreshold(v); markDirty(); }}
                          marks={[
                            { value: 0, label: '0' },
                            { value: 0.25, label: '.25' },
                            { value: 0.5, label: '.5' },
                            { value: 0.75, label: '.75' },
                            { value: 1, label: '1' },
                          ]}
                          mt={6}
                          mb="lg"
                        />
                      </div>
                    )}
                  </Stack>
                </Fieldset>
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

        <Card withBorder padding="lg">
          <Stack gap="sm">
            <Group justify="space-between">
              <div>
                <Title order={5}>Приватные модели тенанта</Title>
                <Text size="xs" c="dimmed">Видны только этому тенанту. Используются как альтернативы каталогу.</Text>
              </div>
              <Button leftSection={<IconPlus size={14} />} size="xs" onClick={openCreateCustom}>
                Добавить
              </Button>
            </Group>

            {!customModels.length ? (
              <Text c="dimmed" ta="center" py="md" size="sm">Приватных моделей нет.</Text>
            ) : (
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Название</Table.Th>
                    <Table.Th>Провайдер</Table.Th>
                    <Table.Th>Model ID</Table.Th>
                    <Table.Th>Уровень</Table.Th>
                    <Table.Th>Статус</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {customModels.map((model: TenantCustomModel) => (
                    <Table.Tr key={model.id} style={{ cursor: 'pointer' }} onClick={() => openEditCustom(model)}>
                      <Table.Td><Text size="sm" fw={500}>{model.name}</Text></Table.Td>
                      <Table.Td><Badge variant="light" size="xs">{model.provider_type}</Badge></Table.Td>
                      <Table.Td><Text size="xs" ff="monospace">{model.model_id}</Text></Table.Td>
                      <Table.Td><Badge size="xs">{model.tier}</Badge></Table.Td>
                      <Table.Td>
                        <Badge color={model.is_active ? 'green' : 'gray'} size="xs">
                          {model.is_active ? 'Активна' : 'Выкл'}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Group gap={2} justify="flex-end">
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
          <SimpleGrid cols={2} spacing="sm">
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
            label={
              <Hint hint={cmProvider === 'ollama'
                ? 'Точный tag модели из `ollama list`, например `qwen2.5:14b`.'
                : 'Точное имя модели у провайдера, например `gpt-4o` или `deepseek-chat`.'}
              >
                Model ID
              </Hint>
            }
            placeholder={cmProvider === 'ollama' ? 'qwen2.5:14b' : 'gpt-4o'}
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
