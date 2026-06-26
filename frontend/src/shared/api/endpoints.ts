import apiClient from './client';
import type {
  OntologyJson,
  LoginRequest,
  LoginResponse,
  AdminUser,
  AdminUserListItem,
  AdminUserCreatePayload,
  AdminUserUpdatePayload,
  Tenant,
  TenantCreate,
  TenantUpdate,
  TenantApiKey,
  TenantApiKeyCreate,
  TenantApiKeyCreated,
  TenantApiKeyUpdate,
  TenantApiKeyGroup,
  TenantApiKeyGroupCreate,
  TenantApiKeyGroupUpdate,
  ShellConfig,
  ShellConfigUpdate,
  ShellVersionItem,
  ShellVersionDetail,
  Tool,
  ToolMetric,
  ToolCreate,
  ToolUpdate,
  ToolTestRequest,
  ToolTestResponse,
  TenantDataSource,
  TenantDataSourceCreate,
  TenantDataSourceUpdate,
  DataSourceSchema,
  KBDocument,
  KBPreviewChunk,
  KBChunkRow,
  KBDocumentCreate,
  KBDocumentUpdate,
  MemoryEntry,
  MemoryEntryCreate,
  MemoryEntryUpdate,
  Chat,
  ChatCreate,
  Message,
  MessageSend,
  LLMLog,
  LLMLogDetail,
  LLMLogSummary,
  AuditLog,
  PaginatedResponse,
  HealthStatus,
  TestConnectionResult,
  LLMModel,
  LLMModelCreate,
  LLMModelUpdate,
  LLMModelBrief,
  ModelHealthCheckResult,
  TenantCustomModel,
  TenantCustomModelCreate,
  TenantCustomModelUpdate,
  TenantModelConfig,
  TenantModelConfigUpdate,
  AttachmentBrief,
  TenantStatsResponse,
} from './types';

// Auth
export const authApi = {
  login: async (data: LoginRequest): Promise<LoginResponse> => {
    const res = await apiClient.post('/api/admin/auth/login', data);
    return res.data;
  },
  me: async (): Promise<AdminUser> => {
    const res = await apiClient.get('/api/admin/auth/me');
    return res.data;
  },
  permissions: async (): Promise<string[]> => {
    const res = await apiClient.get('/api/admin/auth/permissions');
    return res.data;
  },
  changePassword: async (data: { current_password: string; new_password: string }): Promise<void> => {
    await apiClient.post('/api/admin/auth/change-password', data);
  },
  logout: async (): Promise<void> => {
    // Server bumps token_version (revokes the JWT) and clears the cookie.
    await apiClient.post('/api/admin/auth/logout');
  },
};

// Admin users (per-tenant, available to tenant_admin with `users` permission)
export const adminUsersApi = {
  list: async (tenantId: string, page = 1, pageSize = 50): Promise<PaginatedResponse<AdminUserListItem>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/users/`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
  create: async (tenantId: string, data: AdminUserCreatePayload): Promise<AdminUserListItem> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/users/`, data);
    return res.data;
  },
  update: async (tenantId: string, userId: string, data: AdminUserUpdatePayload): Promise<AdminUserListItem> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/users/${userId}`, data);
    return res.data;
  },
  delete: async (tenantId: string, userId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/users/${userId}`);
  },
};

// Tenants
export const tenantsApi = {
  list: async (page = 1, pageSize = 20, search?: string): Promise<PaginatedResponse<Tenant>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (search) params.search = search;
    const res = await apiClient.get('/api/admin/tenants/', { params });
    return res.data;
  },
  get: async (id: string): Promise<Tenant> => {
    const res = await apiClient.get(`/api/admin/tenants/${id}`);
    return res.data;
  },
  create: async (data: TenantCreate): Promise<Tenant> => {
    const res = await apiClient.post('/api/admin/tenants/', data);
    return res.data;
  },
  update: async (id: string, data: TenantUpdate): Promise<Tenant> => {
    const res = await apiClient.patch(`/api/admin/tenants/${id}`, data);
    return res.data;
  },
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${id}`);
  },
};

// API Keys
export const keysApi = {
  list: async (tenantId: string, page = 1, pageSize = 20): Promise<PaginatedResponse<TenantApiKey>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/keys/`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
  create: async (tenantId: string, data: TenantApiKeyCreate): Promise<TenantApiKeyCreated> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/keys/`, data);
    return res.data;
  },
  update: async (tenantId: string, keyId: string, data: TenantApiKeyUpdate): Promise<TenantApiKey> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/keys/${keyId}`, data);
    return res.data;
  },
  deactivate: async (tenantId: string, keyId: string): Promise<TenantApiKey> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/keys/${keyId}`, {
      is_active: false,
    });
    return res.data;
  },
  delete: async (tenantId: string, keyId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/keys/${keyId}`);
  },
  rotate: async (tenantId: string, keyId: string): Promise<TenantApiKeyCreated> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/keys/${keyId}/rotate`);
    return res.data;
  },
};

export const keyGroupsApi = {
  list: async (tenantId: string, page = 1, pageSize = 100): Promise<PaginatedResponse<TenantApiKeyGroup>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/key-groups/`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
  create: async (tenantId: string, data: TenantApiKeyGroupCreate): Promise<TenantApiKeyGroup> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/key-groups/`, data);
    return res.data;
  },
  update: async (tenantId: string, groupId: string, data: TenantApiKeyGroupUpdate): Promise<TenantApiKeyGroup> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/key-groups/${groupId}`, data);
    return res.data;
  },
  delete: async (tenantId: string, groupId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/key-groups/${groupId}`);
  },
};

// Shell Config
export const shellApi = {
  get: async (tenantId: string): Promise<ShellConfig> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/shell/`);
    return res.data;
  },
  update: async (tenantId: string, data: ShellConfigUpdate): Promise<ShellConfig> => {
    const res = await apiClient.put(`/api/admin/tenants/${tenantId}/shell/`, data);
    return res.data;
  },
  ontologyPreview: async (tenantId: string, ontology_json: OntologyJson | null): Promise<{ text: string }> =>
    (await apiClient.post(`/api/admin/tenants/${tenantId}/shell/ontology/preview`, { ontology_json })).data,
  ontologyImport: async (tenantId: string): Promise<{ ontology_json: OntologyJson }> =>
    (await apiClient.post(`/api/admin/tenants/${tenantId}/shell/ontology/import`)).data,
  testConnection: async (tenantId: string): Promise<TestConnectionResult> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/shell/test-connection`);
    return res.data;
  },
  rebuildSttVocab: async (tenantId: string): Promise<{ terms_count: number; sample: string[]; cached_at: number }> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/shell/rebuild-stt-vocab`);
    return res.data;
  },
  listVersions: async (tenantId: string, page = 1, pageSize = 20): Promise<PaginatedResponse<ShellVersionItem>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/shell/versions`, { params: { page, page_size: pageSize } });
    return res.data;
  },
  getVersion: async (tenantId: string, versionId: string): Promise<ShellVersionDetail> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/shell/versions/${versionId}`);
    return res.data;
  },
  restoreVersion: async (tenantId: string, versionId: string): Promise<ShellConfig> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/shell/versions/${versionId}/restore`);
    return res.data;
  },
};

// Tools
export const toolsApi = {
  list: async (
    tenantId: string,
    page = 1,
    pageSize = 20,
    filters?: { search?: string; group?: string; data_source_id?: string },
  ): Promise<PaginatedResponse<Tool>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (filters?.search && filters.search.trim()) params.search = filters.search.trim();
    if (filters?.group) params.group = filters.group;
    if (filters?.data_source_id) params.data_source_id = filters.data_source_id;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tools/`, { params });
    return res.data;
  },
  listGroups: async (tenantId: string): Promise<string[]> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tools/groups`);
    return res.data;
  },
  get: async (tenantId: string, toolId: string): Promise<Tool> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tools/${toolId}`);
    return res.data;
  },
  create: async (tenantId: string, data: ToolCreate): Promise<Tool> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tools/`, data);
    return res.data;
  },
  update: async (tenantId: string, toolId: string, data: ToolUpdate): Promise<Tool> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/tools/${toolId}`, data);
    return res.data;
  },
  test: async (tenantId: string, data: ToolTestRequest): Promise<ToolTestResponse> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tools/test`, data);
    return res.data;
  },
  simulate: async (tenantId: string, data: { message: string; config_json: Record<string, unknown> }): Promise<SimulateResponse> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tools/simulate`, data);
    return res.data;
  },
  semanticTest: async (tenantId: string, query: string, limit = 20): Promise<SemanticTestResponse> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tools/semantic-test`, { query, limit });
    return res.data;
  },
  metrics: async (
    tenantId: string,
    filters?: { date_from?: string; date_to?: string },
  ): Promise<ToolMetric[]> => {
    const params: Record<string, unknown> = {};
    if (filters?.date_from) params.date_from = filters.date_from;
    if (filters?.date_to) params.date_to = filters.date_to;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tools/metrics`, { params });
    return res.data;
  },
  calls: async (
    tenantId: string,
    name: string,
    filters?: { status?: 'success' | 'error'; limit?: number; date_from?: string; date_to?: string },
  ): Promise<ToolCallRecord[]> => {
    const params: Record<string, unknown> = { name };
    if (filters?.status) params.status = filters.status;
    if (filters?.limit) params.limit = filters.limit;
    if (filters?.date_from) params.date_from = filters.date_from;
    if (filters?.date_to) params.date_to = filters.date_to;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tools/calls`, { params });
    return res.data;
  },
  delete: async (tenantId: string, toolId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/tools/${toolId}`);
  },
};

export interface ToolCallRecord {
  created_at: string;
  chat_id: string | null;
  message_id: string | null;
  ok: boolean;
  args_preview: string | null;
  output_chars: number | null;
  latency_ms: number | null;
  round: number | null;
}

export interface SimulateResponse {
  tool_called: boolean;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: string | null;
  tool_error: string | null;
  llm_thinking: string | null;
  llm_preamble: string | null;
  llm_final_response: string;
  model_name: string;
  round1_tokens: number;
  round2_tokens: number;
  total_tokens: number;
  latency_ms: number;
}

export interface SemanticTestRow {
  name: string;
  cosine: number | null;
  tag_bonus: number;
  final_score: number;
  passes_floor: boolean;
  matched_tags: string[];
  description_preview: string;
  tool_id: string;
}

export interface SemanticTestResponse {
  query: string;
  floor: number;
  embedding_model: string | null;
  top_k: number;
  results: SemanticTestRow[];
}

// ─── Tool-builder agent (chat → search_records tool) ──────────────────────────
export interface ToolBuilderMessage {
  role: 'user' | 'assistant' | 'tool';
  content: string;
}
export interface ToolBuilderTraceStep {
  tool: string;
  args: Record<string, unknown>;
  result_preview: string;
}
export interface ToolBuilderProposal {
  name: string;
  description: string | null;
  config_json: Record<string, unknown>;
}
export interface ToolBuilderChatResponse {
  reply: string;
  trace: ToolBuilderTraceStep[];
  proposed: ToolBuilderProposal | null;
}

export const toolBuilderApi = {
  chat: async (tenantId: string, messages: ToolBuilderMessage[]): Promise<ToolBuilderChatResponse> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tool-builder/chat`, { messages });
    return res.data;
  },
  create: async (
    tenantId: string,
    body: { name: string; description?: string | null; config_json: Record<string, unknown>; is_active?: boolean },
  ): Promise<{ id: string; name: string; is_active: boolean }> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tool-builder/create`, body);
    return res.data;
  },
};

// ─── Voice usage (STT/TTS metering) ───────────────────────────────────────────
export interface VoiceUsageRow {
  kind: 'stt' | 'tts';
  unit_type: string;       // 'chars' | 'seconds'
  provider: string | null;
  calls: number;
  units: number;
  cost_usd: number;
}

export const voiceApi = {
  usage: async (
    tenantId: string,
    filters?: { date_from?: string; date_to?: string },
  ): Promise<{ items: VoiceUsageRow[] }> => {
    const params: Record<string, unknown> = {};
    if (filters?.date_from) params.date_from = filters.date_from;
    if (filters?.date_to) params.date_to = filters.date_to;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/voice/usage`, { params });
    return res.data;
  },
};

// ─── Schema notes (semantic layer over a data source) ─────────────────────────
export interface SchemaNote {
  id: string;
  table_name: string | null;
  column_name: string | null;
  description: string | null;
  references: string | null;
  source: string;
}
export interface SchemaNotesResponse {
  notes: SchemaNote[];
  digest: string;
  count: number;
}

export const schemaNotesApi = {
  list: async (tenantId: string, dataSourceId: string): Promise<SchemaNotesResponse> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}/schema-notes`);
    return res.data;
  },
  upsert: async (
    tenantId: string,
    dataSourceId: string,
    body: { table_name?: string | null; column_name?: string | null; description?: string | null; references?: string | null },
  ): Promise<SchemaNote> => {
    const res = await apiClient.put(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}/schema-notes`, body);
    return res.data;
  },
  remove: async (tenantId: string, dataSourceId: string, noteId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}/schema-notes/${noteId}`);
  },
  seed: async (
    tenantId: string,
    dataSourceId: string,
  ): Promise<{ columns_seeded: number; relations_seeded: number; total: number }> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}/schema-notes/seed`);
    return res.data;
  },
};

export const dataSourcesApi = {
  list: async (tenantId: string, page = 1, pageSize = 50): Promise<PaginatedResponse<TenantDataSource>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/data-sources/`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
  get: async (tenantId: string, dataSourceId: string): Promise<TenantDataSource> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}`);
    return res.data;
  },
  create: async (tenantId: string, data: TenantDataSourceCreate): Promise<TenantDataSource> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/data-sources/`, data);
    return res.data;
  },
  update: async (tenantId: string, dataSourceId: string, data: TenantDataSourceUpdate): Promise<TenantDataSource> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}`, data);
    return res.data;
  },
  delete: async (tenantId: string, dataSourceId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}`);
  },
  getSchema: async (tenantId: string, dataSourceId: string): Promise<DataSourceSchema> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}/schema`);
    return res.data;
  },
  test: async (tenantId: string, dataSourceId: string): Promise<{ ok: boolean | null; detail: string; latency_ms: number }> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/data-sources/${dataSourceId}/test`);
    return res.data;
  },
};

// Knowledge Base
export const kbApi = {
  list: async (tenantId: string, page = 1, pageSize = 20, docType?: string, sourceType?: string): Promise<PaginatedResponse<KBDocument>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (docType) params.doc_type = docType;
    if (sourceType) params.source_type = sourceType;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/kb/`, { params });
    return res.data;
  },
  get: async (tenantId: string, docId: string): Promise<KBDocument> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/kb/${docId}`);
    return res.data;
  },
  searchPreview: async (tenantId: string, query: string, limit = 8): Promise<KBPreviewChunk[]> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/kb/search-preview`, { query, limit });
    return res.data;
  },
  chunks: async (tenantId: string, docId: string): Promise<KBChunkRow[]> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/kb/${docId}/chunks`);
    return res.data;
  },
  create: async (tenantId: string, data: KBDocumentCreate): Promise<KBDocument> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/kb/`, data);
    return res.data;
  },
  upload: async (tenantId: string, file: File, title: string, sourceType = 'manual'): Promise<KBDocument> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('title', title);
    formData.append('source_type', sourceType);
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/kb/upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return res.data;
  },
  update: async (tenantId: string, docId: string, data: KBDocumentUpdate): Promise<KBDocument> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/kb/${docId}`, data);
    return res.data;
  },
  reembed: async (tenantId: string, docId: string): Promise<KBDocument> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/kb/${docId}/reembed`);
    return res.data;
  },
  reembedAll: async (tenantId: string): Promise<{ total: number; success: number; error: number }> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/kb/reembed-all`);
    return res.data;
  },
  delete: async (tenantId: string, docId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/kb/${docId}`);
  },
};

// Memory
export const memoryApi = {
  list: async (tenantId: string, page = 1, pageSize = 20, memoryType?: string, search?: string): Promise<PaginatedResponse<MemoryEntry>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (memoryType) params.memory_type = memoryType;
    if (search && search.trim()) params.search = search.trim();
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/memory/`, { params });
    return res.data;
  },
  get: async (tenantId: string, entryId: string): Promise<MemoryEntry> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/memory/${entryId}`);
    return res.data;
  },
  create: async (tenantId: string, data: MemoryEntryCreate): Promise<MemoryEntry> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/memory/`, data);
    return res.data;
  },
  update: async (tenantId: string, entryId: string, data: MemoryEntryUpdate): Promise<MemoryEntry> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/memory/${entryId}`, data);
    return res.data;
  },
  delete: async (tenantId: string, entryId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/memory/${entryId}`);
  },
};

// Chats
export const chatsApi = {
  listAdmin: async (
    tenantId: string,
    page = 1,
    pageSize = 20,
    filters?: { api_key_id?: string; status?: string; search?: string },
  ): Promise<PaginatedResponse<Chat>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (filters?.api_key_id) params.api_key_id = filters.api_key_id;
    if (filters?.status) params.status = filters.status;
    if (filters?.search) params.search = filters.search;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/chats/`, {
      params,
    });
    return res.data;
  },
  list: async (tenantId: string, page = 1, pageSize = 20, assistantId?: string | null): Promise<PaginatedResponse<Chat>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (assistantId) params.assistant_id = assistantId;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/chats/`, { params });
    return res.data;
  },
  create: async (tenantId: string, data: ChatCreate): Promise<Chat> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/chats/`, data);
    return res.data;
  },
  listMessages: async (tenantId: string, chatId: string, page = 1, pageSize = 50): Promise<PaginatedResponse<Message>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/chats/${chatId}/messages`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
  sendMessage: async (tenantId: string, chatId: string, data: MessageSend): Promise<Message> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/chats/${chatId}/messages`, data);
    return res.data;
  },
  sendMessageStream: async (
    tenantId: string,
    chatId: string,
    data: MessageSend,
    onEvent: (eventType: string, payload: Record<string, unknown>) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const token = localStorage.getItem('auth_token');
    const res = await fetch(`/api/admin/tenants/${tenantId}/chats/${chatId}/messages/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(data),
      signal,
    });
    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => '');
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
      // SSE event delimited by blank line ("\n\n")
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
            console.warn('SSE parse error', err, block);
          }
        }
      }
    }
  },
  update: async (tenantId: string, chatId: string, data: { title?: string; description?: string }): Promise<Chat> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/chats/${chatId}`, data);
    return res.data;
  },
  sendMessageWithFiles: async (
    tenantId: string,
    chatId: string,
    content: string,
    files: File[],
    idempotencyKey?: string,
  ): Promise<Message> => {
    const formData = new FormData();
    formData.append('content', content);
    if (idempotencyKey) formData.append('idempotency_key', idempotencyKey);
    for (const file of files) {
      formData.append('files', file);
    }
    const res = await apiClient.post(
      `/api/admin/tenants/${tenantId}/chats/${chatId}/messages/upload`,
      formData,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    );
    return res.data;
  },
  listAttachments: async (tenantId: string, chatId: string): Promise<AttachmentBrief[]> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/chats/${chatId}/attachments`);
    return res.data;
  },
};

// Logs
export interface LogFilters {
  chat_id?: string;
  api_key_id?: string;
  date_from?: string;
  date_to?: string;
  status?: string;     // 'success' | 'error'
  served_by?: string;  // 'tier0_template' | 'llm'
  has_tool_calls?: boolean;
  correlation_id?: string;
}

function logParams(filters?: LogFilters): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  if (filters?.chat_id) params.chat_id = filters.chat_id;
  if (filters?.api_key_id) params.api_key_id = filters.api_key_id;
  if (filters?.date_from) params.date_from = filters.date_from;
  if (filters?.date_to) params.date_to = filters.date_to;
  if (filters?.status) params.status = filters.status;
  if (filters?.served_by) params.served_by = filters.served_by;
  if (filters?.has_tool_calls !== undefined) params.has_tool_calls = filters.has_tool_calls;
  if (filters?.correlation_id) params.correlation_id = filters.correlation_id;
  return params;
}

export const logsApi = {
  list: async (
    tenantId: string,
    page = 1,
    pageSize = 20,
    filters?: LogFilters,
  ): Promise<PaginatedResponse<LLMLog>> => {
    const params = { page, page_size: pageSize, ...logParams(filters) };
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/logs/`, { params });
    return res.data;
  },
  summary: async (tenantId: string, filters?: LogFilters): Promise<LLMLogSummary> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/logs/summary`, { params: logParams(filters) });
    return res.data;
  },
  getDetail: async (tenantId: string, logId: string): Promise<LLMLogDetail> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/logs/${logId}`);
    return res.data;
  },
};

// Stats
export const statsApi = {
  get: async (tenantId: string, dateFrom?: string, dateTo?: string): Promise<TenantStatsResponse> => {
    const params: Record<string, unknown> = {};
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/stats/`, { params });
    return res.data;
  },
};

export interface Tier0StatsResponse {
  enabled: boolean;
  min_tool_score: number;
  max_score_gap: number;
  lookback_days: number;
  total_assistant_messages: number;
  tier0_hits: number;
  hit_rate_pct: number;
  avg_latency_ms: number | null;
  by_tool: Array<{ tool: string; count: number; avg_ms: number | null }>;
  recent_hits: Array<{
    message_id: string;
    chat_id: string | null;
    ts: string | null;
    tool: string | null;
    confidence: number | null;
    latency_ms: number | null;
    user_query: string;
    entities: Record<string, string[]> | null;
    rendered_output: string;
  }>;
}

export interface Tier0AuditCandidate {
  tool_name: string;
  call_count: number;
  unique_query_count: number;
  has_tier0: boolean;
  priority: 'high' | 'medium' | 'low' | 'configured';
  sample_queries: string[];
  sample_args: string[];
}

export interface Tier0AuditResponse {
  candidates: Tier0AuditCandidate[];
  period_days: number;
  min_calls: number;
  total_rows_analyzed: number;
}

export interface Tier0RankRow {
  name: string;
  raw_score: number;
  entity_boost: number;
  total_score: number;
  rank: number;
  has_tier0: boolean;
  required_entity: string | null;
  matched_entities: string[];
}
export interface Tier0RegexMatch {
  name: string;
  extracted: string | null;
  in_topk: boolean;
  rank: number | null;
  score: number | null;
  blocked_by: string | null;
}
export interface Tier0Step {
  label: string;
  status: 'ok' | 'fail' | 'info';
  detail: string;
}
export interface Tier0Decision {
  fired: boolean;
  tool: string | null;
  path: 'regex-first' | 'semantic-gate' | 'none';
  reason: string;
  extracted_keyword: string | null;
  arguments: Record<string, unknown> | null;
  tool_output: string | null;
  rendered: string | null;
}
export interface Tier0Recommendation {
  severity: 'error' | 'warning' | 'info';
  text: string;
}
export interface Tier0ExplainResult {
  tenant_tier0_enabled: boolean;
  min_tool_score: number;
  max_score_gap: number;
  query: string;
  entities: Record<string, string[]>;
  ranking: Tier0RankRow[];
  regex_matches: Tier0RegexMatch[];
  decision: Tier0Decision;
  steps: Tier0Step[];
  recommendations: Tier0Recommendation[];
  focus_tool: string | null;
}
export interface Tier0TestLLMResult {
  served_by: 'tier0' | 'llm';
  content: string;
  model_name: string | null;
  provider_type: string | null;
  tool_calls_count: number;
  total_tokens: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  latency_ms: number | null;
  tier0: unknown;
  reasoning: string | null;
  events: { type: string; payload: Record<string, unknown> }[];
}

export const tier0Api = {
  getStats: async (tenantId: string, days = 7, recentLimit = 20): Promise<Tier0StatsResponse> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tier0/stats`, {
      params: { days, recent_limit: recentLimit },
    });
    return res.data;
  },

  getAudit: async (tenantId: string, days = 30, minCalls = 3): Promise<Tier0AuditResponse> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tier0/audit`, {
      params: { days, min_calls: minCalls },
    });
    return res.data;
  },

  explain: async (
    tenantId: string, query: string, focusTool?: string, runTool = true,
    overrideTier0?: Record<string, unknown> | null,
  ): Promise<Tier0ExplainResult> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tier0/explain`, {
      query, focus_tool: focusTool ?? null, run_tool: runTool,
      override_tier0: overrideTier0 ?? null,
    });
    return res.data;
  },

  testLlm: async (tenantId: string, query: string): Promise<Tier0TestLLMResult> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/tier0/test-llm`, { query });
    return res.data;
  },
};

// ─── Retrieval diagnostic bench (KB / memory / chat-history / artifacts) ───────
export interface RetrievalSourceResult {
  source: string;
  tool: string;
  scope: string;
  success: boolean;
  output: string;
  error: string | null;
  latency_ms: number;
}
export interface RetrievalTestResponse {
  query: string;
  embedding_model: string | null;
  recall_cross_chat_enabled: boolean;
  results: RetrievalSourceResult[];
}

export const retrievalApi = {
  test: async (
    tenantId: string,
    query: string,
    sources?: string[],
    chatId?: string | null,
    limit = 5,
  ): Promise<RetrievalTestResponse> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/retrieval/test`, {
      query,
      sources: sources ?? null,
      chat_id: chatId ?? null,
      limit,
    });
    return res.data;
  },
};

// ─── Assistants (persona/config profiles under a tenant) ──────────────────────
export interface Assistant {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  is_default: boolean;
  is_active: boolean;
  overrides: Record<string, unknown>;
  allowed_tool_ids: string[] | null;
}

export const assistantsApi = {
  list: async (tenantId: string): Promise<Assistant[]> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/assistants/`);
    return res.data;
  },
  create: async (tenantId: string, body: Partial<Assistant>): Promise<Assistant> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/assistants/`, body);
    return res.data;
  },
  update: async (tenantId: string, assistantId: string, body: Partial<Assistant>): Promise<Assistant> => {
    const res = await apiClient.put(`/api/admin/tenants/${tenantId}/assistants/${assistantId}`, body);
    return res.data;
  },
  remove: async (tenantId: string, assistantId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/assistants/${assistantId}`);
  },
};

// Audit
export const auditApi = {
  list: async (page = 1, pageSize = 20): Promise<PaginatedResponse<AuditLog>> => {
    const res = await apiClient.get('/api/admin/audit/', {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
};

// Built-in tools — registry lives in code; only `description` is overridable per-tenant.
export interface BuiltinToolItem {
  name: string;
  default_description: string;
  effective_description: string;
  is_overridden: boolean;
  overridden_at: string | null;
  parameters: Record<string, unknown>;
  handler: string;
}

export const builtinToolsApi = {
  list: async (tenantId: string): Promise<BuiltinToolItem[]> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/builtin-tools/`);
    return res.data;
  },
  setDescription: async (tenantId: string, toolName: string, description: string): Promise<BuiltinToolItem> => {
    const res = await apiClient.patch(
      `/api/admin/tenants/${tenantId}/builtin-tools/${toolName}`,
      { description },
    );
    return res.data;
  },
  resetDescription: async (tenantId: string, toolName: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/builtin-tools/${toolName}`);
  },
};

// LLM Model Catalog (admin)
export const modelsApi = {
  list: async (page = 1, pageSize = 50, params?: { is_active?: boolean; tier?: string }): Promise<PaginatedResponse<LLMModel>> => {
    const p: Record<string, unknown> = { page, page_size: pageSize };
    if (params?.is_active !== undefined) p.is_active = params.is_active;
    if (params?.tier) p.tier = params.tier;
    const res = await apiClient.get('/api/admin/models/', { params: p });
    return res.data;
  },
  brief: async (): Promise<LLMModelBrief[]> => {
    const res = await apiClient.get('/api/admin/models/brief');
    return res.data;
  },
  get: async (modelId: string): Promise<LLMModel> => {
    const res = await apiClient.get(`/api/admin/models/${modelId}`);
    return res.data;
  },
  create: async (data: LLMModelCreate): Promise<LLMModel> => {
    const res = await apiClient.post('/api/admin/models/', data);
    return res.data;
  },
  update: async (modelId: string, data: LLMModelUpdate): Promise<LLMModel> => {
    const res = await apiClient.patch(`/api/admin/models/${modelId}`, data);
    return res.data;
  },
  delete: async (modelId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/models/${modelId}`);
  },
  testConnection: async (data: { provider_type: string; base_url?: string; api_key?: string; model_id?: string }): Promise<TestConnectionResult> => {
    const res = await apiClient.post('/api/admin/models/test-connection', data);
    return res.data;
  },
  healthCheck: async (modelId: string): Promise<ModelHealthCheckResult> => {
    const res = await apiClient.post(`/api/admin/models/${modelId}/test`);
    return res.data;
  },
};

// Tenant Model Config (admin)
export const modelConfigApi = {
  get: async (tenantId: string): Promise<TenantModelConfig> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/model-config/`);
    return res.data;
  },
  update: async (tenantId: string, data: TenantModelConfigUpdate): Promise<TenantModelConfig> => {
    const res = await apiClient.put(`/api/admin/tenants/${tenantId}/model-config/`, data);
    return res.data;
  },
};

// Tenant Custom Models (admin view — same endpoint, admin auth)
export const customModelsApi = {
  list: async (tenantId: string, page = 1, pageSize = 20): Promise<PaginatedResponse<TenantCustomModel>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/custom-models/`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
  create: async (tenantId: string, data: TenantCustomModelCreate): Promise<TenantCustomModel> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/custom-models/`, data);
    return res.data;
  },
  update: async (tenantId: string, modelId: string, data: TenantCustomModelUpdate): Promise<TenantCustomModel> => {
    const res = await apiClient.patch(`/api/admin/tenants/${tenantId}/custom-models/${modelId}`, data);
    return res.data;
  },
  delete: async (tenantId: string, modelId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/custom-models/${modelId}`);
  },
};

// Health
export const healthApi = {
  check: async (): Promise<HealthStatus> => {
    const res = await apiClient.get('/health');
    return res.data;
  },
};

export interface GpuLive {
  ts: string;
  gpus: Array<{
    idx: number;
    uuid: string;
    name: string;
    util_pct: number;
    util_memory_pct: number;
    memory_used_bytes: number;
    memory_total_bytes: number;
    temperature_c: number;
    power_w: number;
  }>;
  vllm: {
    running: number;
    waiting: number;
    kv_cache_usage: number | null;
    prompt_tokens_total: number;
    generation_tokens_total: number;
    prefix_cache_hit_rate: number | null;
  } | null;
}

export interface GpuHistoryPoint {
  ts: string;
  gpus: GpuLive['gpus'];
  vllm: (GpuLive['vllm'] & { generation_tps?: number }) | null;
}

export const gpuApi = {
  live: async (): Promise<GpuLive> => {
    const r = await apiClient.get('/api/admin/gpu/stats');
    return r.data;
  },
  history: async (range: '15m' | '1h' | '6h' | '24h' | '7d'): Promise<{ range: string; points: GpuHistoryPoint[] }> => {
    const r = await apiClient.get('/api/admin/gpu/history', { params: { range } });
    return r.data;
  },
};

export interface AuditCaseResult {
  question: string;
  expect_tool: string | null;
  surfaced: { name: string; score: number }[];
  tier0: { decision?: { fired?: boolean; tool?: string | null }; enabled?: boolean };
  verdict: { level: 'ok' | 'warn' | 'error' | 'info'; msg: string };
}
export interface AuditResult {
  assistant: { id: string; name: string; tool_count: number };
  tier0_enabled: boolean;
  summary: Record<string, number>;
  results: AuditCaseResult[];
}

export const toolAuditApi = {
  preview: async (
    tenantId: string,
    assistantId: string,
    cases: { question: string; expect_tool?: string | null }[],
  ): Promise<AuditResult> => {
    const r = await apiClient.post(
      `/api/admin/tenants/${tenantId}/assistants/${assistantId}/tool-audit/preview`,
      { cases },
    );
    return r.data;
  },
};

export interface AuditCaseRow {
  id: string;
  active: boolean;
  question: string;
  expected_tools: string[];
  actor: { role?: string; external_id?: string; phone?: string } | null;
  notes: string | null;
  order_index: number;
  last_result: {
    passed: boolean; pass_rate: number; repeats: number; called: string[];
    debug?: any; ts?: string;
  } | null;
}

const auditBase = (t: string, a: string) => `/api/admin/tenants/${t}/assistants/${a}/tool-audit`;

export const auditSuiteApi = {
  list: async (t: string, a: string): Promise<{ cases: AuditCaseRow[] }> =>
    (await apiClient.get(`${auditBase(t, a)}/cases`)).data,
  create: async (t: string, a: string, body: Partial<AuditCaseRow>): Promise<AuditCaseRow> =>
    (await apiClient.post(`${auditBase(t, a)}/cases`, body)).data,
  update: async (t: string, a: string, id: string, body: Partial<AuditCaseRow>): Promise<AuditCaseRow> =>
    (await apiClient.patch(`${auditBase(t, a)}/cases/${id}`, body)).data,
  remove: async (t: string, a: string, id: string): Promise<void> => {
    await apiClient.delete(`${auditBase(t, a)}/cases/${id}`);
  },
  run: async (t: string, a: string, id: string, repeats = 1): Promise<AuditCaseRow['last_result']> =>
    (await apiClient.post(`${auditBase(t, a)}/cases/${id}/run`, null, { params: { repeats } })).data,
  toolLog: async (t: string, a: string, id: string): Promise<any> =>
    (await apiClient.get(`${auditBase(t, a)}/cases/${id}/tool-log`)).data,
  seed: async (t: string, a: string, limit = 30): Promise<{ created: number; scanned: number }> =>
    (await apiClient.post(`${auditBase(t, a)}/seed-from-logs`, { limit })).data,
  snapshot: async (t: string, a: string): Promise<any> =>
    (await apiClient.post(`${auditBase(t, a)}/runs`)).data,
  stats: async (t: string, a: string): Promise<{
    active: number; ran: number; passed: number; pass_pct: number;
    by_tool: { total_failed: number; by_tool: { tool: string; misses: number; share: number; called_instead: Record<string, number> }[] };
    trend: { ts: string | null; passed: number; total: number }[];
  }> => (await apiClient.get(`${auditBase(t, a)}/stats`)).data,

  // ----- auto-tuning (read-only analysis → staged recommendations → apply) -----
  tune: async (t: string, a: string): Promise<{
    ran: number; failed: number; diagnosed: number; recommendations: number;
    diagnoser: string | null; failures_by_class: Record<string, number>;
  }> => (await apiClient.post(`${auditBase(t, a)}/tune`)).data,
  recommendations: async (t: string, a: string, status = 'pending'): Promise<{ recommendations: TuneRec[] }> =>
    (await apiClient.get(`${auditBase(t, a)}/recommendations`, { params: { status } })).data,
  applyRec: async (t: string, a: string, id: string): Promise<{ ok: boolean; reembedded?: boolean }> =>
    (await apiClient.post(`${auditBase(t, a)}/recommendations/${id}/apply`)).data,
  dismissRec: async (t: string, a: string, id: string): Promise<{ ok: boolean }> =>
    (await apiClient.post(`${auditBase(t, a)}/recommendations/${id}/dismiss`)).data,
};

export interface TuneRec {
  id: string;
  scope: 'tool' | 'assistant';
  tool_name: string | null;
  change_type: string;
  json_path: string | null;
  param_name: string | null;
  current_value: unknown;
  proposed_value: unknown;
  rationale: string | null;
  deterministic: boolean;
  failing_case_ids: string[];
  status: string;
}
