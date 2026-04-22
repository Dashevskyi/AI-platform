import apiClient from './client';
import type {
  LoginRequest,
  LoginResponse,
  AdminUser,
  Tenant,
  TenantCreate,
  TenantUpdate,
  TenantApiKey,
  TenantApiKeyCreate,
  TenantApiKeyCreated,
  ShellConfig,
  ShellConfigUpdate,
  Tool,
  ToolCreate,
  ToolUpdate,
  KBDocument,
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
  AuditLog,
  PaginatedResponse,
  HealthStatus,
  TestConnectionResult,
  LLMModel,
  LLMModelCreate,
  LLMModelUpdate,
  LLMModelBrief,
  TenantCustomModel,
  TenantCustomModelCreate,
  TenantCustomModelUpdate,
  TenantModelConfig,
  TenantModelConfigUpdate,
  AttachmentBrief,
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
  logout: () => {
    localStorage.removeItem('auth_token');
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
};

// Tools
export const toolsApi = {
  list: async (tenantId: string, page = 1, pageSize = 20): Promise<PaginatedResponse<Tool>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/tools/`, {
      params: { page, page_size: pageSize },
    });
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
  delete: async (tenantId: string, toolId: string): Promise<void> => {
    await apiClient.delete(`/api/admin/tenants/${tenantId}/tools/${toolId}`);
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
  list: async (tenantId: string, page = 1, pageSize = 20, memoryType?: string): Promise<PaginatedResponse<MemoryEntry>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (memoryType) params.memory_type = memoryType;
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
  listAdmin: async (tenantId: string, page = 1, pageSize = 20): Promise<PaginatedResponse<Chat>> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/chats/`, {
      params: { page, page_size: pageSize },
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
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/chats/${chatId}/messages/`, {
      params: { page, page_size: pageSize },
    });
    return res.data;
  },
  sendMessage: async (tenantId: string, chatId: string, data: MessageSend): Promise<Message> => {
    const res = await apiClient.post(`/api/admin/tenants/${tenantId}/chats/${chatId}/messages/`, data);
    return res.data;
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
export const logsApi = {
  list: async (tenantId: string, page = 1, pageSize = 20, filters?: { chat_id?: string; date_from?: string; date_to?: string }): Promise<PaginatedResponse<LLMLog>> => {
    const params: Record<string, unknown> = { page, page_size: pageSize };
    if (filters?.chat_id) params.chat_id = filters.chat_id;
    if (filters?.date_from) params.date_from = filters.date_from;
    if (filters?.date_to) params.date_to = filters.date_to;
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/logs/`, { params });
    return res.data;
  },
  getDetail: async (tenantId: string, logId: string): Promise<LLMLogDetail> => {
    const res = await apiClient.get(`/api/admin/tenants/${tenantId}/logs/${logId}`);
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
