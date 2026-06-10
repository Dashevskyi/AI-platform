import apiClient from './client';
import type {
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
  testConnection: async (tenantId: string): Promise<TestConnectionResult> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/shell/test-connection`);
    return res.data;
  },
  rebuildSttVocab: async (tenantId: string): Promise<{ terms_count: number; sample: string[]; cached_at: number }> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/shell/rebuild-stt-vocab`);
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
  delete: async (tenantId: string, toolId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/tools/${toolId}`);
  },
};

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
  list: async (tenantId: string, page = 1, pageSize = 20): Promise<PaginatedResponse<Chat>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/chats/`, {
      params: { page, page_size: pageSize },
    });
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
