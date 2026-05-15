import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Center,
  Group,
  Loader,
  NumberInput,
  PasswordInput,
  Select,
  Slider,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { IconAlertCircle, IconDeviceFloppy, IconPlugConnected } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { shellApi } from '../../shared/api/endpoints';
import type { ShellConfigUpdate } from '../../shared/api/types';

type ShellSettingsTabProps = {
  tenantId: string;
};

export function ShellSettingsTab({ tenantId }: ShellSettingsTabProps) {
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
        ontology_prompt: config.ontology_prompt ?? undefined,
        rules_text: config.rules_text ?? undefined,
        temperature: config.temperature,
        max_context_messages: config.max_context_messages,
        max_tokens: config.max_tokens,
        context_mode: config.context_mode,
        memory_enabled: config.memory_enabled,
        knowledge_base_enabled: config.knowledge_base_enabled,
        embedding_model_name: config.embedding_model_name ?? undefined,
        vision_model_name: config.vision_model_name ?? undefined,
        kb_max_chunks: config.kb_max_chunks,
        enable_thinking: config.enable_thinking || 'on',
        response_language: config.response_language || 'ru',
      });
      setDirty(false);
    }
  }, [config]);

  useEffect(() => {
    if (!dirty) {
      return;
    }
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  const updateField = <K extends keyof ShellConfigUpdate>(
    key: K,
    value: ShellConfigUpdate[K],
  ) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => shellApi.update(tenantId, form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'shell'] });
      setDirty(false);
      notifications.show({
        title: 'Сохранено',
        message: 'Настройки оболочки обновлены',
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

          <Alert icon={<IconAlertCircle size={16} />} color="blue" variant="light">
            Основная модель для чата выбирается во вкладке «Модель». Здесь настраиваются
            провайдер, промпты, лимиты и параметры памяти/базы знаний.
          </Alert>

          <Textarea
            label="Системный промпт — кто ассистент"
            description="Идентичность и общий стиль. Короткое — 1-3 предложения. Уходит в LLM первым блоком."
            placeholder="Ты — AI техспециалист компании X. Отвечай на языке запроса, используй эмодзи где уместно."
            value={form.system_prompt || ''}
            onChange={(e) => updateField('system_prompt', e.currentTarget.value)}
            autosize
            minRows={2}
            maxRows={8}
          />

          <Textarea
            label="Онтология / Domain knowledge"
            description="Структура данных, термины, mapping tool→аргументы. Только то, что не вынести в базу знаний/память. Уходит вторым блоком."
            placeholder={'OLT — головное PON-устройство. От него растёт PON-дерево.\nSplitter — пассивный делитель, у него СВОЙ id (отличный от OLT).\n\nТема ↔ tool:\n• клиенты — search_clients\n• ближайшие splitters — pon_nearby'}
            value={form.ontology_prompt || ''}
            onChange={(e) => updateField('ontology_prompt', e.currentTarget.value)}
            autosize
            minRows={3}
            maxRows={14}
          />

          <Textarea
            label="Правила формата ответов"
            description="Длина, форматирование, стилевые исключения. Уходит третьим блоком, с префиксом «Rules:»."
            placeholder="Отвечай короткими фразами по 4-5 предложений. Исключения — код, таблицы, инструкции."
            value={form.rules_text || ''}
            onChange={(e) => updateField('rules_text', e.currentTarget.value)}
            autosize
            minRows={2}
            maxRows={6}
          />

          <div>
            <Text size="sm" fw={500} mb={2}>
              Температура: {form.temperature?.toFixed(2) ?? '0.30'}
            </Text>
            <Text size="xs" c="dimmed" mb="xs">
              Для support-ассистента температура ограничена сверху 0.7, чтобы снизить галлюцинации и шум.
            </Text>
            <Slider
              min={0}
              max={0.7}
              step={0.01}
              value={Math.min(form.temperature ?? 0.3, 0.7)}
              onChange={(val) => updateField('temperature', val)}
              marks={[
                { value: 0, label: '0' },
                { value: 0.3, label: '0.3' },
                { value: 0.7, label: '0.7' },
              ]}
            />
          </div>

          <Select
            label="Режим контекста"
            description="Определяет, что именно подмешивать из истории в следующий запрос к LLM."
            data={[
              { value: 'recent_only', label: 'Только последние сообщения' },
              { value: 'summary_plus_recent', label: 'Резюме + последние сообщения' },
              { value: 'summary_only', label: 'Только резюме истории' },
            ]}
            value={form.context_mode || 'summary_plus_recent'}
            onChange={(val) => updateField('context_mode', val || 'summary_plus_recent')}
            allowDeselect={false}
          />

          <Select
            label="Reasoning (thinking mode)"
            description={
              'on — модель всегда «думает» перед ответом (точнее, медленнее). off — сразу отвечает (быстро). ' +
              'auto — думает только на сложных запросах (короткий вопрос без тулзов = off, всё остальное = on). ' +
              'Влияет на Qwen3 и подобные; на DeepSeek и моделях без thinking — игнорируется.'
            }
            data={[
              { value: 'on', label: 'On — всегда (точнее, медленнее)' },
              { value: 'off', label: 'Off — никогда (быстро)' },
              { value: 'auto', label: 'Auto — по эвристике' },
            ]}
            value={form.enable_thinking || 'on'}
            onChange={(val) => updateField('enable_thinking', val || 'on')}
            allowDeselect={false}
          />

          <Select
            label="Язык ответов модели"
            description={
              'Жёсткая привязка языка для ВСЕХ LLM-вызовов: основной чат, резюме обменов, ' +
              'описание вложений. Multilingual-модели (Qwen, Llama) без явного пина срываются ' +
              'на китайский на технических темах — этот select это лечит.'
            }
            data={[
              { value: 'ru', label: 'Русский' },
              { value: 'uk', label: 'Українська' },
              { value: 'en', label: 'English' },
              { value: 'pl', label: 'Polski' },
              { value: 'de', label: 'Deutsch' },
              { value: 'es', label: 'Español' },
              { value: 'fr', label: 'Français' },
            ]}
            value={form.response_language || 'ru'}
            onChange={(val) => updateField('response_language', val || 'ru')}
            allowDeselect={false}
          />

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

          <TextInput
            label="Vision-модель (для анализа картинок)"
            description="Ollama-модель для описания изображений вложений. Если пусто — авто-выбор: qwen2-vl > llava > moondream"
            placeholder="llava:13b"
            value={form.vision_model_name ?? ''}
            onChange={(e) => updateField('vision_model_name', e.currentTarget.value || undefined)}
          />

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
