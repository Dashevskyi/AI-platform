import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getAiChatApi } from './api';
import type { AuthMode, Chat, ConnectionOptions } from './types';

function resolveAuth(options: ConnectionOptions): AuthMode | undefined {
  if (options.auth) return options.auth;
  if (options.apiKey) return { type: 'apiKey', apiKey: options.apiKey };
  return undefined;
}

export type UseAiChatListResult = {
  chats: Chat[];
  isLoading: boolean;
  error: unknown;
  totalCount: number;
  /** Create a new empty chat. Returns the created chat. */
  create: () => Promise<Chat>;
  /** Rename / update title or description of an existing chat. */
  rename: (chatId: string, data: { title?: string; description?: string }) => Promise<Chat>;
  refetch: () => Promise<unknown>;
};

/**
 * useAiChatList — fetch the list of chats for a tenant + actions to create/rename.
 */
export function useAiChatList(
  tenantId: string,
  options: ConnectionOptions & { pageSize?: number } = {},
): UseAiChatListResult {
  const { mode = 'end-user', apiBase, pageSize = 100 } = options;
  const queryClient = useQueryClient();

  const auth = resolveAuth(options);
  const api = useMemo(
    () => getAiChatApi({
      variant: mode === 'admin' ? 'admin' : 'tenant',
      apiBase,
      auth,
    }),
    [mode, apiBase, auth],
  );

  const queryKey = ['ai-chat-core', 'list', tenantId, mode] as const;

  const { data, isLoading, error, refetch } = useQuery({
    queryKey,
    queryFn: () => api.list(tenantId, 1, pageSize),
    enabled: !!tenantId,
  });

  const createMut = useMutation({
    mutationFn: () => api.create(tenantId, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

  const renameMut = useMutation({
    mutationFn: ({ chatId, data }: { chatId: string; data: { title?: string; description?: string } }) =>
      api.update(tenantId, chatId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

  return {
    chats: data?.items || [],
    isLoading,
    error,
    totalCount: data?.total_count || 0,
    create: () => createMut.mutateAsync(),
    rename: (chatId, data) => renameMut.mutateAsync({ chatId, data }),
    refetch: () => refetch(),
  };
}
