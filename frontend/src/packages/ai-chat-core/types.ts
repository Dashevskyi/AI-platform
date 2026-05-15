/**
 * Public types of @it-invest/ai-chat-core.
 *
 * All types are inlined — the package has zero internal dependencies on the
 * host application's types or HTTP clients. Peer deps: react ^18, @tanstack/react-query ^5.
 */

// ----- Pagination wrapper used by list endpoints -----
export interface PaginatedResponse<T> {
  items: T[];
  total_count: number;
  page: number;
  page_size: number;
}

// ----- Domain entities -----
export interface Chat {
  id: string;
  tenant_id: string;
  api_key_id: string | null;
  title: string | null;
  description: string | null;
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  tenant_id: string;
  chat_id: string;
  role: string;
  content: string;
  /** Internal-only metadata (model, reasoning, events trail).
   *  Tenant API strips this; admin API exposes it. */
  metadata_json: Record<string, unknown> | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number | null;
  time_to_first_token_ms: number | null;
  provider_type: string | null;
  model_name: string | null;
  correlation_id: string | null;
  tool_calls_count: number | null;
  finish_reason: string | null;
  status: string;
  created_at: string;
}

export interface MessageSend {
  content: string;
  idempotency_key?: string;
}

export interface AttachmentBrief {
  id: string;
  filename: string;
  file_type: string;
  file_size_bytes: number;
  processing_status: string;
  summary: string | null;
}

// ----- Hook configuration types -----

/** Operating mode of the chat hooks. */
export type AiChatMode = 'admin' | 'end-user';

/** Underlying API path style: tenant (X-API-Key) or admin (Bearer). */
export type AiChatApiVariant = 'admin' | 'tenant';

/** Auth strategies — pick one per ConnectionOptions. */
export type AuthMode =
  | { type: 'apiKey'; apiKey: string }
  | { type: 'bearer'; token: string }
  | { type: 'custom'; getHeaders: () => Record<string, string> };

/** A single SSE event seen during streaming. */
export type StreamEvent = {
  type: string;
  payload: Record<string, unknown>;
  ts: number;
};

/** Status of an attachment as it is processed by the server. */
export type AttachmentStatus = 'processing' | 'done' | 'error';

/** Args for sending a message. idempotencyKey defaults to crypto.randomUUID(). */
export type SendArgs = {
  content: string;
  /** Raw files to upload + process inline server-side (legacy path). */
  files?: File[];
  /** IDs of drafts already uploaded via uploadDraftAttachment + processed in background. */
  attachmentIds?: string[];
  idempotencyKey?: string;
};

/** Common option block accepted by every hook. */
export type ConnectionOptions = {
  /** 'admin' (talks to /api/admin/...) or 'end-user' (talks to /api/tenants/...).
   *  Default: 'end-user'. */
  mode?: AiChatMode;
  /** API base URL. Default: same origin (''). Pass full origin like
   *  'https://ai.it-invest.ua' when embedding cross-origin. */
  apiBase?: string;
  /** Auth strategy. */
  auth?: AuthMode;
  /** Shorthand for `auth: { type: 'apiKey', apiKey }`. */
  apiKey?: string;
};
