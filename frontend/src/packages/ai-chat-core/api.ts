/**
 * Self-contained HTTP client for @it-invest/ai-chat-core.
 *
 * Uses plain fetch — no axios, no host-app dependencies. Two path strategies:
 *  - 'tenant' → /api/tenants/{tid}/chats/...   (requires X-API-Key auth)
 *  - 'admin'  → /api/admin/tenants/{tid}/chats/... (requires Bearer auth)
 */
import type {
  AiChatApiVariant,
  AttachmentBrief,
  ArtifactBrief,
  ArtifactDetail,
  AuthMode,
  Chat,
  Message,
  MessageSend,
  PaginatedResponse,
} from './types';

export type AiChatApi = {
  list: (tenantId: string, page?: number, pageSize?: number) => Promise<PaginatedResponse<Chat>>;
  create: (tenantId: string, data?: Record<string, unknown>) => Promise<Chat>;
  update: (tenantId: string, chatId: string, data: { title?: string; description?: string }) => Promise<Chat>;
  listMessages: (tenantId: string, chatId: string, page?: number, pageSize?: number) => Promise<PaginatedResponse<Message>>;
  listAttachments: (tenantId: string, chatId: string) => Promise<AttachmentBrief[]>;
  sendMessage: (tenantId: string, chatId: string, data: MessageSend) => Promise<Message>;
  sendMessageWithFiles: (tenantId: string, chatId: string, content: string, files: File[], idempotencyKey?: string, attachmentIds?: string[]) => Promise<Message>;
  listArtifacts: (tenantId: string, chatId: string) => Promise<ArtifactBrief[]>;
  getArtifact: (tenantId: string, chatId: string, artifactId: string) => Promise<ArtifactDetail>;
  uploadDraftAttachment: (tenantId: string, chatId: string, file: File) => Promise<AttachmentBrief>;
  getDraftAttachment: (tenantId: string, chatId: string, attachmentId: string) => Promise<AttachmentBrief>;
  deleteDraftAttachment: (tenantId: string, chatId: string, attachmentId: string) => Promise<void>;
  sendMessageStream: (
    tenantId: string,
    chatId: string,
    data: MessageSend,
    onEvent: (eventType: string, payload: Record<string, unknown>) => void,
    signal?: AbortSignal,
  ) => Promise<void>;
};

function buildAuthHeaders(auth?: AuthMode): Record<string, string> {
  if (!auth) return {};
  if (auth.type === 'apiKey') return { 'X-API-Key': auth.apiKey };
  if (auth.type === 'bearer') return { Authorization: `Bearer ${auth.token}` };
  if (auth.type === 'custom') return auth.getHeaders();
  return {};
}

function pathPrefix(variant: AiChatApiVariant, tenantId: string): string {
  return variant === 'admin'
    ? `/api/admin/tenants/${tenantId}/chats`
    : `/api/tenants/${tenantId}/chats`;
}

async function jsonFetch<T>(
  url: string,
  init: RequestInit & { authHeaders?: Record<string, string> },
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.authHeaders || {}),
    ...((init.headers as Record<string, string>) || {}),
  };
  // FormData bodies — drop Content-Type so the browser sets boundary
  if (init.body instanceof FormData) delete headers['Content-Type'];
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    let detail = '';
    try { detail = await res.text(); } catch { /* ignore */ }
    throw new Error(`HTTP ${res.status}: ${detail || res.statusText}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export type GetAiChatApiOptions = {
  variant?: AiChatApiVariant;
  apiBase?: string;
  auth?: AuthMode;
};

export function getAiChatApi(options: GetAiChatApiOptions = {}): AiChatApi {
  const variant: AiChatApiVariant = options.variant || 'tenant';
  const base = (options.apiBase || '').replace(/\/$/, '');
  const authHeaders = buildAuthHeaders(options.auth);

  const u = (path: string) => `${base}${path}`;
  const prefix = (tenantId: string) => pathPrefix(variant, tenantId);

  return {
    list: async (tenantId, page = 1, pageSize = 100) => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      return jsonFetch<PaginatedResponse<Chat>>(u(`${prefix(tenantId)}/?${qs}`), {
        method: 'GET',
        authHeaders,
      });
    },
    create: async (tenantId, data) => {
      return jsonFetch<Chat>(u(`${prefix(tenantId)}/`), {
        method: 'POST',
        body: JSON.stringify(data || {}),
        authHeaders,
      });
    },
    update: async (tenantId, chatId, data) => {
      return jsonFetch<Chat>(u(`${prefix(tenantId)}/${chatId}`), {
        method: 'PATCH',
        body: JSON.stringify(data),
        authHeaders,
      });
    },
    listMessages: async (tenantId, chatId, page = 1, pageSize = 200) => {
      const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      return jsonFetch<PaginatedResponse<Message>>(u(`${prefix(tenantId)}/${chatId}/messages?${qs}`), {
        method: 'GET',
        authHeaders,
      });
    },
    listAttachments: async (tenantId, chatId) => {
      return jsonFetch<AttachmentBrief[]>(u(`${prefix(tenantId)}/${chatId}/attachments`), {
        method: 'GET',
        authHeaders,
      });
    },
    sendMessage: async (tenantId, chatId, data) => {
      return jsonFetch<Message>(u(`${prefix(tenantId)}/${chatId}/messages`), {
        method: 'POST',
        body: JSON.stringify(data),
        authHeaders,
      });
    },
    sendMessageWithFiles: async (tenantId, chatId, content, files, idempotencyKey, attachmentIds) => {
      const fd = new FormData();
      fd.append('content', content);
      if (idempotencyKey) fd.append('idempotency_key', idempotencyKey);
      for (const f of files) fd.append('files', f);
      if (attachmentIds && attachmentIds.length) fd.append('attachment_ids', attachmentIds.join(','));
      return jsonFetch<Message>(u(`${prefix(tenantId)}/${chatId}/messages/upload`), {
        method: 'POST',
        body: fd,
        authHeaders,
      });
    },
    listArtifacts: async (tenantId, chatId) => {
      return jsonFetch<ArtifactBrief[]>(u(`${prefix(tenantId)}/${chatId}/artifacts`), {
        method: 'GET',
        authHeaders,
      });
    },
    getArtifact: async (tenantId, chatId, artifactId) => {
      return jsonFetch<ArtifactDetail>(u(`${prefix(tenantId)}/${chatId}/artifacts/${artifactId}`), {
        method: 'GET',
        authHeaders,
      });
    },
    uploadDraftAttachment: async (tenantId, chatId, file) => {
      const fd = new FormData();
      fd.append('file', file);
      return jsonFetch<AttachmentBrief>(u(`${prefix(tenantId)}/${chatId}/attachments/draft`), {
        method: 'POST',
        body: fd,
        authHeaders,
      });
    },
    getDraftAttachment: async (tenantId, chatId, attachmentId) => {
      return jsonFetch<AttachmentBrief>(u(`${prefix(tenantId)}/${chatId}/attachments/draft/${attachmentId}`), {
        method: 'GET',
        authHeaders,
      });
    },
    deleteDraftAttachment: async (tenantId, chatId, attachmentId) => {
      return jsonFetch<void>(u(`${prefix(tenantId)}/${chatId}/attachments/draft/${attachmentId}`), {
        method: 'DELETE',
        authHeaders,
      });
    },
    sendMessageStream: async (tenantId, chatId, data, onEvent, signal) => {
      const res = await fetch(u(`${prefix(tenantId)}/${chatId}/messages/stream`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify(data),
        signal,
      });
      if (!res.ok || !res.body) {
        let text = '';
        try { text = await res.text(); } catch { /* ignore */ }
        throw new Error(`Stream error ${res.status}: ${text || res.statusText}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep: number;
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          let eventType = 'message';
          const dataLines: string[] = [];
          for (const line of block.split('\n')) {
            if (line.startsWith('event: ')) eventType = line.slice(7).trim();
            else if (line.startsWith('data: ')) dataLines.push(line.slice(6));
          }
          if (dataLines.length > 0) {
            try {
              const payload = JSON.parse(dataLines.join('\n'));
              onEvent(eventType, payload);
            } catch (err) {
              // SSE parse error — keep streaming
              // eslint-disable-next-line no-console
              console.warn('SSE parse error', err, block);
            }
          }
        }
      }
    },
  };
}
