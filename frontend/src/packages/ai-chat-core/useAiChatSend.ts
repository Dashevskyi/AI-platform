import { useCallback, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getAiChatApi } from './api';
import type {
  AuthMode,
  ConnectionOptions,
  Message,
  SendArgs,
  StreamEvent,
} from './types';

function resolveAuth(options: ConnectionOptions): AuthMode | undefined {
  if (options.auth) return options.auth;
  if (options.apiKey) return { type: 'apiKey', apiKey: options.apiKey };
  return undefined;
}

export type AttachmentProgressEvent = {
  status: 'processing' | 'done' | 'error';
  detail?: string;
};

export type UseAiChatSendOptions = ConnectionOptions & {
  tenantId: string;
  chatId: string | null;
  /** When true (default) — uses SSE /messages/stream and emits chunk events.
   *  When false — sends via /messages and waits for the final reply. */
  streaming?: boolean;
  /** Called for every text chunk while streaming. Not called when streaming=false. */
  onChunk?: (text: string) => void;
  /** Called for every reasoning chunk while streaming. */
  onReasoning?: (text: string) => void;
  /** Called for every SSE event (full event object) — useful for progress trail UI. */
  onTrail?: (event: StreamEvent) => void;
  /** Called once when the assistant response is complete. Receives the persisted Message. */
  onComplete?: (msg: Message) => void;
  /** Called on any error (network, throttle, server). Receives an Error. */
  onError?: (err: Error) => void;
  /** Optional success notification — invoked after onComplete with a short string. */
  onSuccess?: (msg: string) => void;
  /** Reserved for future use: per-attachment processing progress.
   *  Will fire when the backend exposes attachment_processing SSE events. */
  onAttachmentProgress?: (fileId: string, ev: AttachmentProgressEvent) => void;
};

export type UseAiChatSendResult = {
  /** Send a user message. Returns a promise that resolves on completion. */
  send: (args: SendArgs) => Promise<void>;
  /** Cancel an in-flight stream. No-op for non-streaming send. */
  cancel: () => void;
  /** True while a request is in flight (both modes). */
  isLoading: boolean;
  /** True specifically while streaming (subset of isLoading). */
  streaming: boolean;
  /** Accumulated content from content_chunk events. Reset on each send. */
  streamingContent: string;
  /** Accumulated reasoning text from reasoning_chunk events. */
  streamingReasoning: string;
  /** All SSE events seen during the current stream. */
  streamEvents: StreamEvent[];
  /** Last error, if any. */
  error: Error | null;
};

/**
 * useAiChatSend — single hook for sending messages.
 * Streaming or non-streaming based on `streaming` flag.
 */
export function useAiChatSend(options: UseAiChatSendOptions): UseAiChatSendResult {
  const {
    tenantId,
    chatId,
    streaming: streamingMode = true,
    mode = 'end-user',
    apiBase,
    onChunk,
    onReasoning,
    onTrail,
    onComplete,
    onError,
    onSuccess,
    // onAttachmentProgress, // reserved
  } = options;

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

  const [isLoading, setIsLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const [streamingReasoning, setStreamingReasoning] = useState('');
  const [streamEvents, setStreamEvents] = useState<StreamEvent[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const invalidateAfterSend = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['ai-chat-core', 'messages', tenantId, chatId, mode] });
    queryClient.invalidateQueries({ queryKey: ['ai-chat-core', 'attachments', tenantId, chatId, mode] });
    queryClient.invalidateQueries({ queryKey: ['ai-chat-core', 'list', tenantId, mode] });
    // Also invalidate legacy admin keys used by the existing Mantine AiChat for back-compat.
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', chatId, 'messages'] });
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', chatId, 'attachments'] });
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', 'list'] });
  }, [queryClient, tenantId, chatId, mode]);

  const generateIdempotencyKey = useCallback((): string => {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }, []);

  const send = useCallback(
    async ({ content, files = [], attachmentIds = [], idempotencyKey }: SendArgs): Promise<void> => {
      if (!chatId) {
        const err = new Error('chatId is required to send a message');
        setError(err);
        onError?.(err);
        return;
      }
      const idemKey = idempotencyKey || generateIdempotencyKey();
      setError(null);
      setIsLoading(true);
      setStreamingContent('');
      setStreamingReasoning('');
      setStreamEvents([]);

      try {
        // Files or draft attachments always use the upload endpoint (no streaming variant yet).
        if (files.length > 0 || attachmentIds.length > 0) {
          const msg = await api.sendMessageWithFiles(tenantId, chatId, content, files, idemKey, attachmentIds);
          onComplete?.(msg);
          onSuccess?.('Сообщение отправлено');
          return;
        }

        if (!streamingMode) {
          const msg = await api.sendMessage(tenantId, chatId, { content, idempotency_key: idemKey });
          onComplete?.(msg);
          onSuccess?.('Сообщение отправлено');
          return;
        }

        // Streaming path
        setStreaming(true);
        const controller = new AbortController();
        abortRef.current = controller;

        let assistantMessageId: string | null = null;

        await api.sendMessageStream(
          tenantId,
          chatId,
          { content, idempotency_key: idemKey },
          (eventType, payload) => {
            const ev: StreamEvent = { type: eventType, payload, ts: Date.now() };
            setStreamEvents((prev) => [...prev, ev]);
            onTrail?.(ev);

            if (eventType === 'content_chunk' && typeof payload.text === 'string') {
              const piece = payload.text as string;
              setStreamingContent((prev) => prev + piece);
              onChunk?.(piece);
            } else if (eventType === 'reasoning_chunk' && typeof payload.text === 'string') {
              const piece = payload.text as string;
              setStreamingReasoning((prev) => prev + piece);
              onReasoning?.(piece);
            } else if (eventType === 'done' && typeof payload.content === 'string') {
              setStreamingContent(payload.content as string);
            } else if (eventType === 'final' && typeof payload.assistant_message_id === 'string') {
              assistantMessageId = payload.assistant_message_id as string;
            } else if (eventType === 'error' && typeof payload.message === 'string') {
              const err = new Error(String(payload.message));
              setError(err);
              onError?.(err);
            } else if (eventType === 'throttle_rejected') {
              const msg = (payload.message as string) || 'Превышен лимит запросов';
              const retry = typeof payload.retry_after === 'number' ? ` Повтор через ${payload.retry_after}с.` : '';
              const err = new Error(`${msg}.${retry}`);
              setError(err);
              onError?.(err);
            }
          },
          controller.signal,
        );

        // Build a synthetic Message reference for onComplete from the final event payload.
        // Caller will refetch from /messages to get the persisted full row.
        if (assistantMessageId) {
          onComplete?.({
            id: assistantMessageId,
            tenant_id: tenantId,
            chat_id: chatId,
            role: 'assistant',
            content: '',
            metadata_json: null,
            prompt_tokens: null,
            completion_tokens: null,
            total_tokens: null,
            latency_ms: null,
            time_to_first_token_ms: null,
            provider_type: null,
            model_name: null,
            correlation_id: null,
            tool_calls_count: null,
            finish_reason: null,
            status: 'sent',
            created_at: new Date().toISOString(),
          } as Message);
        }
        onSuccess?.('Ответ получен');
      } catch (e) {
        const err = e instanceof Error ? e : new Error(String(e));
        setError(err);
        onError?.(err);
      } finally {
        setStreaming(false);
        setIsLoading(false);
        abortRef.current = null;
        invalidateAfterSend();
      }
    },
    [
      tenantId,
      chatId,
      streamingMode,
      api,
      generateIdempotencyKey,
      invalidateAfterSend,
      onChunk,
      onReasoning,
      onTrail,
      onComplete,
      onError,
      onSuccess,
    ],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setIsLoading(false);
  }, []);

  return {
    send,
    cancel,
    isLoading,
    streaming,
    streamingContent,
    streamingReasoning,
    streamEvents,
    error,
  };
}
