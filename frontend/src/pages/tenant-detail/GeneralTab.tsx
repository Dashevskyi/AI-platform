import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import {
  Alert,
  Button,
  Card,
  Divider,
  Group,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { IconAlertCircle, IconDeviceFloppy } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { tenantsApi } from '../../shared/api/endpoints';

type GeneralTabProps = {
  tenantId: string;
};

export function GeneralTab({ tenantId }: GeneralTabProps) {
  const queryClient = useQueryClient();
  const { data: tenant } = useQuery({
    queryKey: ['tenants', tenantId],
    queryFn: () => tenantsApi.get(tenantId),
  });

  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [description, setDescription] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [throttleEnabled, setThrottleEnabled] = useState(false);
  const [throttleMaxConcurrent, setThrottleMaxConcurrent] = useState(5);
  const [throttleOverflowPolicy, setThrottleOverflowPolicy] = useState<'reject_429' | 'queue_fifo'>('reject_429');
  const [throttleQueueMax, setThrottleQueueMax] = useState(20);
  const [mergeEnabled, setMergeEnabled] = useState(false);
  const [mergeWindowMs, setMergeWindowMs] = useState(1500);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (tenant) {
      setName(tenant.name);
      setSlug(tenant.slug);
      setDescription(tenant.description || '');
      setIsActive(tenant.is_active);
      setThrottleEnabled(tenant.throttle_enabled);
      setThrottleMaxConcurrent(tenant.throttle_max_concurrent);
      setThrottleOverflowPolicy(
        tenant.throttle_overflow_policy === 'queue_fifo' ? 'queue_fifo' : 'reject_429',
      );
      setThrottleQueueMax(tenant.throttle_queue_max);
      setMergeEnabled(tenant.merge_messages_enabled);
      setMergeWindowMs(tenant.merge_window_ms);
      setDirty(false);
    }
  }, [tenant]);

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

  const mutation = useMutation({
    mutationFn: () =>
      tenantsApi.update(tenantId, {
        name,
        slug,
        description,
        is_active: isActive,
        throttle_enabled: throttleEnabled,
        throttle_max_concurrent: throttleMaxConcurrent,
        throttle_overflow_policy: throttleOverflowPolicy,
        throttle_queue_max: throttleQueueMax,
        merge_messages_enabled: mergeEnabled,
        merge_window_ms: mergeWindowMs,
      }),
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
    <T,>(setter: Dispatch<SetStateAction<T>>) =>
      (value: T) => {
        setter(value);
        setDirty(true);
      },
    [],
  );

  return (
    <Card withBorder padding="lg" maw={620}>
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

          <Divider my={4} label="Лимиты параллельных запросов" labelPosition="left" />

          <Switch
            label="Включить лимит на параллельные LLM-запросы"
            checked={throttleEnabled}
            onChange={(e) => markDirty(setThrottleEnabled)(e.currentTarget.checked)}
          />
          {throttleEnabled && (
            <Stack gap="xs" pl="md" style={{ borderLeft: '2px solid var(--mantine-color-default-border)' }}>
              <Group grow align="flex-start">
                <NumberInput
                  label="Максимум параллельных"
                  description="LLM-запросов одновременно"
                  value={throttleMaxConcurrent}
                  onChange={(v) => markDirty(setThrottleMaxConcurrent)(typeof v === 'number' ? v : 5)}
                  min={1}
                  max={100}
                />
                <Select
                  label="Стратегия при превышении"
                  description="Что делать при достижении лимита"
                  data={[
                    { value: 'reject_429', label: 'Отклонять (HTTP 429)' },
                    { value: 'queue_fifo', label: 'Ставить в очередь' },
                  ]}
                  value={throttleOverflowPolicy}
                  onChange={(v) =>
                    markDirty(setThrottleOverflowPolicy)(v === 'queue_fifo' ? 'queue_fifo' : 'reject_429')
                  }
                />
              </Group>
              {throttleOverflowPolicy === 'queue_fifo' && (
                <NumberInput
                  label="Глубина очереди"
                  description="Максимум ожидающих запросов; свыше — HTTP 429"
                  value={throttleQueueMax}
                  onChange={(v) => markDirty(setThrottleQueueMax)(typeof v === 'number' ? v : 20)}
                  min={0}
                  max={1000}
                />
              )}
              <Text size="xs" c="dimmed">
                Применяется к чату через как `/messages`, так и `/messages/stream`. На длительные tools и LLM-вызовы.
              </Text>
            </Stack>
          )}

          <Divider my={4} label="Объединение сообщений" labelPosition="left" />

          <Switch
            label="Склеивать подряд идущие сообщения в один LLM-запрос"
            checked={mergeEnabled}
            onChange={(e) => markDirty(setMergeEnabled)(e.currentTarget.checked)}
          />
          {mergeEnabled && (
            <Stack gap="xs" pl="md" style={{ borderLeft: '2px solid var(--mantine-color-default-border)' }}>
              <NumberInput
                label="Окно ожидания (мс)"
                description="Если в этот интервал приходят новые сообщения, они объединяются (debounce)"
                value={mergeWindowMs}
                onChange={(v) => markDirty(setMergeWindowMs)(typeof v === 'number' ? v : 1500)}
                min={100}
                max={30000}
                step={100}
              />
              <Text size="xs" c="dimmed">
                Объединение применяется в рамках одного чата + API-ключа. Все объединённые сообщения сохраняются в истории, но в LLM уходит один склеенный запрос.
              </Text>
            </Stack>
          )}

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
