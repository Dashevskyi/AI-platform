import { useState } from 'react';
import {
  Title,
  Group,
  Button,
  Table,
  Badge,
  Modal,
  Stack,
  Text,
  TextInput,
  PasswordInput,
  Select,
  Switch,
  NumberInput,
  Loader,
  Center,
  Card,
  ActionIcon,
  Tooltip,
  Pagination,
} from '@mantine/core';
import {
  IconPlus,
  IconTrash,
  IconPlugConnected,
  IconEdit,
} from '@tabler/icons-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { modelsApi } from '../shared/api/endpoints';
import type { LLMModel, LLMModelCreate, LLMModelUpdate } from '../shared/api/types';

const PROVIDER_OPTIONS = [
  { value: 'ollama', label: 'Ollama (локальный)' },
  { value: 'openai_compatible', label: 'OpenAI Compatible' },
  { value: 'deepseek_compatible', label: 'DeepSeek Compatible' },
];

const TIER_OPTIONS = [
  { value: 'light', label: 'Light (быстрая/дешёвая)' },
  { value: 'medium', label: 'Medium (сбалансированная)' },
  { value: 'heavy', label: 'Heavy (мощная/дорогая)' },
];

const TIER_COLORS: Record<string, string> = {
  light: 'green',
  medium: 'blue',
  heavy: 'violet',
};

export function ModelsPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);

  // Form state
  const [name, setName] = useState('');
  const [providerType, setProviderType] = useState('ollama');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [modelId, setModelId] = useState('');
  const [tier, setTier] = useState('medium');
  const [supportsTools, setSupportsTools] = useState(false);
  const [supportsVision, setSupportsVision] = useState(false);
  const [maxContextTokens, setMaxContextTokens] = useState<number | ''>('');
  const [costInput, setCostInput] = useState<number | ''>('');
  const [costOutput, setCostOutput] = useState<number | ''>('');
  const [isActive, setIsActive] = useState(true);

  const { data, isLoading } = useQuery({
    queryKey: ['models', page],
    queryFn: () => modelsApi.list(page),
  });

  const resetForm = () => {
    setName('');
    setProviderType('ollama');
    setBaseUrl('');
    setApiKey('');
    setModelId('');
    setTier('medium');
    setSupportsTools(false);
    setSupportsVision(false);
    setMaxContextTokens('');
    setCostInput('');
    setCostOutput('');
    setIsActive(true);
  };

  const openCreate = () => {
    setEditId(null);
    resetForm();
    setModalOpen(true);
  };

  const openEdit = (m: LLMModel) => {
    setEditId(m.id);
    setName(m.name);
    setProviderType(m.provider_type);
    setBaseUrl(m.base_url || '');
    setApiKey('');
    setModelId(m.model_id);
    setTier(m.tier);
    setSupportsTools(m.supports_tools);
    setSupportsVision(m.supports_vision);
    setMaxContextTokens(m.max_context_tokens ?? '');
    setCostInput(m.cost_per_1k_input ?? '');
    setCostOutput(m.cost_per_1k_output ?? '');
    setIsActive(m.is_active);
    setModalOpen(true);
  };

  const createMutation = useMutation({
    mutationFn: (data: LLMModelCreate) => modelsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['models'] });
      setModalOpen(false);
      notifications.show({ title: 'Создано', message: 'Модель добавлена в каталог', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать модель', color: 'red' });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: LLMModelUpdate }) => modelsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['models'] });
      setModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Модель обновлена', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось обновить модель', color: 'red' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => modelsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['models'] });
      notifications.show({ title: 'Удалено', message: 'Модель удалена', color: 'green' });
    },
  });

  const testMutation = useMutation({
    mutationFn: () =>
      modelsApi.testConnection({
        provider_type: providerType,
        base_url: baseUrl || undefined,
        api_key: apiKey || undefined,
        model_id: modelId || undefined,
      }),
    onSuccess: (result) => {
      notifications.show({
        title: result.success ? 'Подключение успешно' : 'Ошибка подключения',
        message: result.message,
        color: result.success ? 'green' : 'red',
      });
    },
  });

  const handleSave = () => {
    const payload = {
      name,
      provider_type: providerType,
      base_url: baseUrl || undefined,
      api_key: apiKey || undefined,
      model_id: modelId,
      tier,
      supports_tools: supportsTools,
      supports_vision: supportsVision,
      max_context_tokens: maxContextTokens !== '' ? Number(maxContextTokens) : undefined,
      cost_per_1k_input: costInput !== '' ? Number(costInput) : undefined,
      cost_per_1k_output: costOutput !== '' ? Number(costOutput) : undefined,
      is_active: isActive,
    };
    if (editId) {
      updateMutation.mutate({ id: editId, data: payload });
    } else {
      createMutation.mutate(payload);
    }
  };

  const totalPages = data ? Math.ceil(data.total_count / 50) : 0;

  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <Title order={2}>Каталог моделей</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
          Добавить модель
        </Button>
      </Group>

      <Text size="sm" c="dimmed">
        Глобальный каталог LLM-моделей. Модели из каталога доступны для выбора всем тенантам.
        Каждая модель может иметь свой провайдер, ключ и настройки.
      </Text>

      {isLoading ? (
        <Center py="xl"><Loader /></Center>
      ) : !data?.items.length ? (
        <Card withBorder p="xl">
          <Text ta="center" c="dimmed">Каталог пуст. Добавьте первую модель.</Text>
        </Card>
      ) : (
        <>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Название</Table.Th>
                <Table.Th>Провайдер</Table.Th>
                <Table.Th>Model ID</Table.Th>
                <Table.Th>Уровень</Table.Th>
                <Table.Th>Возможности</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((m) => (
                <Table.Tr key={m.id} style={{ cursor: 'pointer' }} onClick={() => openEdit(m)}>
                  <Table.Td>
                    <Text size="sm" fw={500}>{m.name}</Text>
                    {m.base_url && (
                      <Text size="xs" c="dimmed" truncate="end" maw={200}>{m.base_url}</Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" size="sm">
                      {PROVIDER_OPTIONS.find((o) => o.value === m.provider_type)?.label || m.provider_type}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" ff="monospace">{m.model_id}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={TIER_COLORS[m.tier] || 'gray'} size="sm">{m.tier}</Badge>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      {m.supports_tools && <Badge variant="dot" size="xs" color="blue">Tools</Badge>}
                      {m.supports_vision && <Badge variant="dot" size="xs" color="grape">Vision</Badge>}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={m.is_active ? 'green' : 'gray'} size="sm">
                      {m.is_active ? 'Активна' : 'Выкл'}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      <Tooltip label="Редактировать">
                        <ActionIcon variant="subtle" color="blue" size="sm" onClick={(e) => { e.stopPropagation(); openEdit(m); }}>
                          <IconEdit size={14} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Удалить">
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (window.confirm(`Удалить модель "${m.name}"?`)) deleteMutation.mutate(m.id);
                          }}
                        >
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
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

      {/* Create/Edit Modal */}
      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editId ? 'Редактировать модель' : 'Добавить модель в каталог'}
        size="lg"
      >
        <Stack gap="md">
          <TextInput
            label="Название"
            description="Отображаемое имя модели, например: GPT-4o, Qwen 32B, DeepSeek Chat"
            placeholder="GPT-4o"
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            required
          />

          <Group grow>
            <Select
              label="Провайдер"
              data={PROVIDER_OPTIONS}
              value={providerType}
              onChange={(v) => setProviderType(v || 'ollama')}
              required
              allowDeselect={false}
            />
            <Select
              label="Уровень"
              description="Определяет выбор модели в авто-режиме"
              data={TIER_OPTIONS}
              value={tier}
              onChange={(v) => setTier(v || 'medium')}
              required
              allowDeselect={false}
            />
          </Group>

          <TextInput
            label="Базовый URL"
            description="Для Ollama: http://localhost:11434, для OpenAI: https://api.openai.com"
            placeholder="http://localhost:11434"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.currentTarget.value)}
          />

          <PasswordInput
            label="API ключ"
            description={editId ? 'Оставьте пустым, чтобы не менять' : 'Для Ollama не требуется'}
            placeholder="sk-..."
            value={apiKey}
            onChange={(e) => setApiKey(e.currentTarget.value)}
          />

          <TextInput
            label="Model ID"
            description="Точный идентификатор модели у провайдера"
            placeholder="gpt-4o / qwen2.5:32b / deepseek-chat"
            value={modelId}
            onChange={(e) => setModelId(e.currentTarget.value)}
            required
          />

          <Group>
            <Switch
              label="Поддержка Tools (function calling)"
              checked={supportsTools}
              onChange={(e) => setSupportsTools(e.currentTarget.checked)}
            />
            <Switch
              label="Поддержка Vision (изображения)"
              checked={supportsVision}
              onChange={(e) => setSupportsVision(e.currentTarget.checked)}
            />
          </Group>

          <Group grow>
            <NumberInput
              label="Макс. контекст (токенов)"
              placeholder="128000"
              value={maxContextTokens}
              onChange={(v) => setMaxContextTokens(v === '' ? '' : Number(v))}
              min={0}
            />
            <NumberInput
              label="Стоимость ввода ($/1K токенов)"
              placeholder="0.0025"
              value={costInput}
              onChange={(v) => setCostInput(v === '' ? '' : Number(v))}
              min={0}
              decimalScale={6}
              step={0.0001}
            />
            <NumberInput
              label="Стоимость вывода ($/1K токенов)"
              placeholder="0.01"
              value={costOutput}
              onChange={(v) => setCostOutput(v === '' ? '' : Number(v))}
              min={0}
              decimalScale={6}
              step={0.0001}
            />
          </Group>

          <Switch
            label="Активна"
            checked={isActive}
            onChange={(e) => setIsActive(e.currentTarget.checked)}
          />

          <Group justify="space-between">
            <Button
              variant="outline"
              leftSection={<IconPlugConnected size={16} />}
              onClick={() => testMutation.mutate()}
              loading={testMutation.isPending}
            >
              Тест подключения
            </Button>
            <Group>
              <Button variant="default" onClick={() => setModalOpen(false)}>Отмена</Button>
              <Button
                onClick={handleSave}
                loading={createMutation.isPending || updateMutation.isPending}
              >
                {editId ? 'Обновить' : 'Создать'}
              </Button>
            </Group>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
