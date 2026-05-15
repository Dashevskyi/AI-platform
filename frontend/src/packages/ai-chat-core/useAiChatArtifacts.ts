import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAiChatApi } from './api';
import type { ArtifactBrief, AuthMode, ConnectionOptions } from './types';

function resolveAuth(options: ConnectionOptions): AuthMode | undefined {
  if (options.auth) return options.auth;
  if (options.apiKey) return { type: 'apiKey', apiKey: options.apiKey };
  return undefined;
}

export type UseAiChatArtifactsResult = {
  artifacts: ArtifactBrief[];
  isLoading: boolean;
  refetch: () => Promise<unknown>;
};

/**
 * useAiChatArtifacts — list of artifacts (scripts, configs, SQL, ...) produced
 * by the LLM in this chat. Brief list; expand via api.getArtifact for content.
 */
export function useAiChatArtifacts(
  tenantId: string,
  chatId: string | null,
  options: ConnectionOptions = {},
): UseAiChatArtifactsResult {
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
    queryKey: ['ai-chat-core', 'artifacts', tenantId, chatId, mode] as const,
    queryFn: () => api.listArtifacts(tenantId, chatId!),
    enabled: !!tenantId && !!chatId,
  });

  return {
    artifacts: data || [],
    isLoading,
    refetch: () => refetch(),
  };
}
