import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAiChatApi } from './api';
import type { AuthMode, Message, ConnectionOptions } from './types';

function resolveAuth(options: ConnectionOptions): AuthMode | undefined {
  if (options.auth) return options.auth;
  if (options.apiKey) return { type: 'apiKey', apiKey: options.apiKey };
  return undefined;
}

export type UseAiChatMessagesResult = {
  messages: Message[];
  isLoading: boolean;
  error: unknown;
  totalCount: number;
  refetch: () => Promise<unknown>;
};

/**
 * useAiChatMessages — paginated message history for a single chat.
 */
export function useAiChatMessages(
  tenantId: string,
  chatId: string | null,
  options: ConnectionOptions & { pageSize?: number } = {},
): UseAiChatMessagesResult {
  const { mode = 'end-user', apiBase, pageSize = 200 } = options;
  const auth = resolveAuth(options);

  const api = useMemo(
    () => getAiChatApi({
      variant: mode === 'admin' ? 'admin' : 'tenant',
      apiBase,
      auth,
    }),
    [mode, apiBase, auth],
  );

  const queryKey = ['ai-chat-core', 'messages', tenantId, chatId, mode] as const;

  const { data, isLoading, error, refetch } = useQuery({
    queryKey,
    queryFn: () => api.listMessages(tenantId, chatId!, 1, pageSize),
    enabled: !!tenantId && !!chatId,
  });

  return {
    messages: data?.items || [],
    isLoading,
    error,
    totalCount: data?.total_count || 0,
    refetch: () => refetch(),
  };
}
