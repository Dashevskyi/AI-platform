import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Code, Group, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

export interface PendingAction {
  id: string;
  tool_name: string;
  command_name: string | null;
  command_text: string | null;
  status: string;
  result_text: string | null;
  error_text: string | null;
}

interface Props {
  tenantId: string;
  chatId: string;
  mode: 'admin' | 'end-user';
  apiBase?: string;
  apiKey?: string;
  /** Called after an action is approved (executed) so the host can refresh messages. */
  onResolved?: () => void;
}

/** Banner over the composer listing tool commands that need human approval
 * before they run (see backend HITL gate). Polls while any are pending. */
export function PendingActions({ tenantId, chatId, mode, apiBase = '', apiKey, onResolved }: Props) {
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const base = mode === 'admin'
    ? `${apiBase}/api/admin/tenants/${tenantId}/chats/${chatId}/pending-actions`
    : `${apiBase}/api/tenants/${tenantId}/chats/${chatId}/pending-actions`;
  const headers: Record<string, string> = mode === 'admin' || !apiKey ? {} : { 'X-API-Key': apiKey };

  const refresh = useCallback(async () => {
    if (!chatId) return;
    try {
      const res = await fetch(`${base}/`, { headers });
      if (res.ok) setActions(await res.json());
    } catch { /* transient — keep last state */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, chatId]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  const decide = async (id: string, action: 'approve' | 'reject') => {
    setBusy(id);
    try {
      const res = await fetch(`${base}/${id}/${action}`, { method: 'POST', headers });
      if (res.ok && action === 'approve') onResolved?.();
    } catch { /* ignore */ }
    finally {
      setBusy(null);
      void refresh();
    }
  };

  if (!actions.length) return null;

  return (
    <Stack gap="xs" px="md" pt="sm">
      {actions.map((a) => (
        <Alert key={a.id} icon={<IconAlertTriangle size={16} />} color="orange" variant="light" p="sm">
          <Group justify="space-between" wrap="nowrap" gap="sm">
            <div style={{ minWidth: 0 }}>
              <Text size="sm" fw={600}>Требуется подтверждение: {a.command_name || a.tool_name}</Text>
              {a.command_text && (
                <Code block style={{ marginTop: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {a.command_text}
                </Code>
              )}
            </div>
            <Group gap="xs" wrap="nowrap">
              <Button size="xs" color="green" loading={busy === a.id} onClick={() => decide(a.id, 'approve')}>
                Выполнить
              </Button>
              <Button size="xs" color="gray" variant="default" loading={busy === a.id} onClick={() => decide(a.id, 'reject')}>
                Отклонить
              </Button>
            </Group>
          </Group>
        </Alert>
      ))}
    </Stack>
  );
}
