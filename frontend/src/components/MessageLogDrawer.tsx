import { Drawer, Center, Loader, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { logsApi } from '../shared/api/endpoints';
import { LogDetailView } from '../pages/tenant-detail/LogsTab';

/**
 * Superadmin-only right drawer that shows the LLM request log for ONE message,
 * opened by clicking the log icon on that assistant message. Resolves the log by
 * the message's correlation_id, then reuses LogsTab's LogDetailView (single
 * source of truth for the detail rendering).
 */
export function MessageLogDrawer({
  tenantId,
  correlationId,
  opened,
  onClose,
}: {
  tenantId: string;
  correlationId: string | null;
  opened: boolean;
  onClose: () => void;
}) {
  const { data: list, isLoading: listLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'log-by-corr', correlationId],
    queryFn: () => logsApi.list(tenantId, 1, 1, { correlation_id: correlationId! }),
    enabled: opened && !!correlationId,
  });

  const logId = list?.items?.[0]?.id ?? null;

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'logs', logId],
    queryFn: () => logsApi.getDetail(tenantId, logId!),
    enabled: opened && !!logId,
  });

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      title="Лог ответа"
      position="right"
      size="xl"
      closeOnClickOutside={false}
      closeOnEscape={false}
    >
      {!correlationId ? (
        <Text c="dimmed">Для этого сообщения нет привязанного лога (возможно, старое сообщение).</Text>
      ) : listLoading || detailLoading ? (
        <Center py="md"><Loader /></Center>
      ) : detail ? (
        <LogDetailView logDetail={detail} />
      ) : (
        <Text c="dimmed">Лог не найден.</Text>
      )}
    </Drawer>
  );
}
