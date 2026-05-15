import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAiChatApi } from './api';
import type { AttachmentBrief, AuthMode, ConnectionOptions } from './types';

function resolveAuth(options: ConnectionOptions): AuthMode | undefined {
  if (options.auth) return options.auth;
  if (options.apiKey) return { type: 'apiKey', apiKey: options.apiKey };
  return undefined;
}

export type UseAiChatAttachmentsResult = {
  attachments: AttachmentBrief[];
  isLoading: boolean;
  refetch: () => Promise<unknown>;
};

/**
 * useAiChatAttachments — list of files attached to a chat.
 */
export function useAiChatAttachments(
  tenantId: string,
  chatId: string | null,
  options: ConnectionOptions = {},
): UseAiChatAttachmentsResult {
  const { mode = 'end-user', apiBase } = options;
  const auth = resolveAuth(options);

  const api = useMemo(
    () => getAiChatApi({
      variant: mode === 'admin' ? 'admin' : 'tenant',
      apiBase,
      auth,
    }),
    [mode, apiBase, auth],
  );

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['ai-chat-core', 'attachments', tenantId, chatId, mode] as const,
    queryFn: () => api.listAttachments(tenantId, chatId!),
    enabled: !!tenantId && !!chatId,
  });

  return {
    attachments: data || [],
    isLoading,
    refetch: () => refetch(),
  };
}
