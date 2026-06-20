import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { AiChat } from '../components/AiChat';
import { usePermissions } from '../shared/hooks/usePermissions';
import { MessageLogDrawer } from '../components/MessageLogDrawer';

/**
 * ChatPage — thin admin wrapper over the embeddable <AiChat> component.
 *
 * All chat UI lives in AiChat (reused by external clients). The page layer adds
 * admin-only extras that must NOT ship to clients — e.g. the superadmin "log of
 * this answer" drawer, opened from the log icon on each assistant message.
 */
export function ChatPage() {
  const { id, chatId } = useParams<{ id: string; chatId?: string }>();
  const tenantId = id!;
  const navigate = useNavigate();
  const { isSuperadmin } = usePermissions();

  const [logCorrelationId, setLogCorrelationId] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);

  return (
    <>
      <AiChat
        tenantId={tenantId}
        chatId={chatId || null}
        mode="admin"
        onChatCreated={(newChatId) => navigate(`/tenants/${tenantId}/chat/${newChatId}`)}
        onOpenMessageLog={
          isSuperadmin
            ? (msg) => {
                const corr =
                  (msg.metadata_json as Record<string, unknown> | null)?.correlation_id;
                setLogCorrelationId(typeof corr === 'string' ? corr : null);
                setLogOpen(true);
              }
            : undefined
        }
      />
      {isSuperadmin && (
        <MessageLogDrawer
          tenantId={tenantId}
          correlationId={logCorrelationId}
          opened={logOpen}
          onClose={() => setLogOpen(false)}
        />
      )}
    </>
  );
}
