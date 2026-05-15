import { useParams, useNavigate } from 'react-router-dom';
import { AiChat } from '../components/AiChat';

/**
 * ChatPage — thin wrapper over the embeddable <AiChat> component.
 *
 * The page layer pulls tenantId/chatId from the URL and provides admin-side
 * navigation. All chat UI (messages, streaming, attachments, reasoning, stats)
 * lives in the AiChat component so it can be reused by external clients.
 */
export function ChatPage() {
  const { id, chatId } = useParams<{ id: string; chatId?: string }>();
  const tenantId = id!;
  const navigate = useNavigate();

  return (
    <AiChat
      tenantId={tenantId}
      chatId={chatId || null}
      mode="admin"
      onChatCreated={(newChatId) => navigate(`/tenants/${tenantId}/chat/${newChatId}`)}
    />
  );
}
