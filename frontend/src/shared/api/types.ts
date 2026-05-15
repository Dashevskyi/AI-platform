export interface PaginatedResponse<T> {
  items: T[];
  total_count: number;
  page: number;
  page_size: number;
}

export interface LoginRequest {
  login: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface AdminUser {
  id: string;
  login: string;
  role: string;
  tenant_id: string | null;
  permissions: string[];
  is_active: boolean;
}

export interface AdminUserListItem {
  id: string;
  login: string;
  role: string;
  tenant_id: string | null;
  permissions: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminUserCreatePayload {
  login: string;
  password: string;
  role?: 'superadmin' | 'tenant_admin';
  tenant_id?: string | null;
  permissions?: string[];
  is_active?: boolean;
}

export interface AdminUserUpdatePayload {
  password?: string;
  role?: 'superadmin' | 'tenant_admin';
  tenant_id?: string | null;
  permissions?: string[];
  is_active?: boolean;
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  is_active: boolean;
  throttle_enabled: boolean;
  throttle_max_concurrent: number;
  throttle_overflow_policy: string;
  throttle_queue_max: number;
  merge_messages_enabled: boolean;
  merge_window_ms: number;
  created_at: string;
  updated_at: string;
}

export interface TenantCreate {
  name: string;
  slug: string;
  description?: string;
}

export interface TenantUpdate {
  name?: string;
  slug?: string;
  description?: string;
  is_active?: boolean;
  throttle_enabled?: boolean;
  throttle_max_concurrent?: number;
  throttle_overflow_policy?: 'reject_429' | 'queue_fifo';
  throttle_queue_max?: number;
  merge_messages_enabled?: boolean;
  merge_window_ms?: number;
}

export interface TenantApiKey {
  id: string;
  tenant_id: string;
  name: string;
  key_prefix: string;
  group_id: string | null;
  group_name: string | null;
  memory_prompt: string | null;
  allowed_tool_ids: string[] | null;
  is_active: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface TenantApiKeyCreate {
  name: string;
  expires_at?: string;
  group_id?: string;
  memory_prompt?: string;
  allowed_tool_ids?: string[] | null;
}

export interface TenantApiKeyCreated extends TenantApiKey {
  raw_key: string;
}

export interface TenantApiKeyUpdate {
  name?: string;
  expires_at?: string | null;
  is_active?: boolean;
  group_id?: string | null;
  memory_prompt?: string | null;
  allowed_tool_ids?: string[] | null;
}

export interface TenantApiKeyGroup {
  id: string;
  tenant_id: string;
  name: string;
  memory_prompt: string | null;
  allowed_tool_ids: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface TenantApiKeyGroupCreate {
  name: string;
  memory_prompt?: string;
  allowed_tool_ids?: string[] | null;
}

export interface TenantApiKeyGroupUpdate {
  name?: string;
  memory_prompt?: string | null;
  allowed_tool_ids?: string[] | null;
}

export interface ShellConfig {
  id: string;
  tenant_id: string;
  provider_type: string;
  provider_base_url: string | null;
  provider_api_key_masked: string | null;
  model_name: string;
  system_prompt: string | null;
  ontology_prompt: string | null;
  rules_text: string | null;
  temperature: number;
  max_context_messages: number;
  max_tokens: number;
  summary_model_name: string | null;
  context_mode: string;
  memory_enabled: boolean;
  knowledge_base_enabled: boolean;
  embedding_model_name: string | null;
  vision_model_name: string | null;
  kb_max_chunks: number;
  tools_policy: string;
  enable_thinking: string;
  response_language: string;
}

export interface ShellConfigUpdate {
  provider_type?: string;
  provider_base_url?: string;
  provider_api_key?: string;
  model_name?: string;
  system_prompt?: string;
  ontology_prompt?: string;
  rules_text?: string;
  temperature?: number;
  max_context_messages?: number;
  max_tokens?: number;
  summary_model_name?: string;
  context_mode?: string;
  memory_enabled?: boolean;
  knowledge_base_enabled?: boolean;
  embedding_model_name?: string;
  vision_model_name?: string;
  kb_max_chunks?: number;
  tools_policy?: string;
  enable_thinking?: string;
  response_language?: string;
}

export interface Tool {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  group: string | null;
  config_json: Record<string, unknown> | null;
  tool_type: string;
  is_active: boolean;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface ToolCreate {
  name: string;
  description?: string;
  group?: string;
  config_json?: Record<string, unknown>;
  tool_type?: string;
  is_active?: boolean;
  is_pinned?: boolean;
}

export interface ToolUpdate {
  name?: string;
  description?: string;
  group?: string;
  config_json?: Record<string, unknown>;
  tool_type?: string;
  is_active?: boolean;
  is_pinned?: boolean;
}

export interface ToolTestRequest {
  config_json: Record<string, unknown>;
  arguments?: Record<string, unknown>;
}

export interface ToolTestResponse {
  success: boolean;
  output: string;
  error: string | null;
}

export interface TenantDataSource {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  kind: string;
  config_json: Record<string, unknown> | null;
  secret_json_masked: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TenantDataSourceCreate {
  name: string;
  description?: string;
  kind: string;
  config_json?: Record<string, unknown>;
  secret_json?: Record<string, unknown>;
  is_active?: boolean;
}

export interface TenantDataSourceUpdate {
  name?: string;
  description?: string;
  kind?: string;
  config_json?: Record<string, unknown>;
  secret_json?: Record<string, unknown> | null;
  is_active?: boolean;
}

export interface DataSourceSchemaTable {
  schema: string;
  name: string;
  full_name: string;
  type: 'table' | 'view';
}

export interface DataSourceSchemaColumn {
  table: string;
  column: string;
  type: string;
  nullable: boolean;
}

export interface DataSourceSchema {
  tables: DataSourceSchemaTable[];
  columns: DataSourceSchemaColumn[];
}

export interface KBDocument {
  id: string;
  tenant_id: string;
  title: string;
  doc_type: string;
  source_type: string;
  source_url: string | null;
  source_filename: string | null;
  content: string;
  metadata_json: Record<string, unknown> | null;
  is_active: boolean;
  embedding_status: string;
  embedding_error: string | null;
  chunks_count: number;
  created_at: string;
  updated_at: string;
}

export interface KBDocumentCreate {
  title: string;
  doc_type?: string;
  source_type?: string;
  source_url?: string;
  content?: string;
  metadata_json?: Record<string, unknown>;
  is_active?: boolean;
}

export interface KBDocumentUpdate {
  title?: string;
  content?: string;
  source_type?: string;
  metadata_json?: Record<string, unknown>;
  is_active?: boolean;
}

export interface MemoryEntry {
  id: string;
  tenant_id: string;
  chat_id: string | null;
  memory_type: string;
  content: string;
  metadata_json: Record<string, unknown> | null;
  priority: number;
  is_pinned: boolean;
  expires_at: string | null;
  created_at: string;
}

export interface MemoryEntryCreate {
  memory_type?: string;
  content: string;
  chat_id?: string;
  metadata_json?: Record<string, unknown>;
  priority?: number;
  is_pinned?: boolean;
  expires_at?: string;
}

export interface MemoryEntryUpdate {
  memory_type?: string;
  content?: string;
  metadata_json?: Record<string, unknown>;
  priority?: number;
  is_pinned?: boolean;
  expires_at?: string;
}

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

export interface ChatCreate {
  title?: string;
  description?: string;
}

export interface Message {
  id: string;
  tenant_id: string;
  chat_id: string;
  role: string;
  content: string;
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

export interface LLMLog {
  id: string;
  tenant_id: string;
  chat_id: string | null;
  api_key_id: string | null;
  message_id: string | null;
  correlation_id: string | null;
  provider_type: string;
  model_name: string;
  status: string;
  error_text: string | null;
  latency_ms: number | null;
  time_to_first_token_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  tool_calls_count: number | null;
  finish_reason: string | null;
  estimated_cost: number | null;
  created_at: string;
}

export interface LLMLogDetail extends LLMLog {
  raw_request: Record<string, unknown> | null;
  raw_response: Record<string, unknown> | null;
  normalized_request: Record<string, unknown> | null;
  normalized_response: Record<string, unknown> | null;
  request_size_bytes: number | null;
  response_size_bytes: number | null;
  context_messages_count: number | null;
  context_memory_count: number | null;
  context_kb_count: number | null;
  context_tools_count: number | null;
  tokens_system: number | null;
  tokens_tools: number | null;
  tokens_memory: number | null;
  tokens_kb: number | null;
  tokens_history: number | null;
  tokens_user: number | null;
}

export interface AuditLog {
  id: string;
  actor_id: string | null;
  actor_role: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  tenant_id: string | null;
  before_json: Record<string, unknown> | null;
  after_json: Record<string, unknown> | null;
  created_at: string;
}

export interface HealthStatus {
  status: string;
  database: string;
  ollama: string | null;
}

export interface TestConnectionResult {
  success: boolean;
  message: string;
  models: string[] | null;
}

// LLM Model Catalog
export interface LLMModel {
  id: string;
  name: string;
  provider_type: string;
  base_url: string | null;
  api_key_masked: string | null;
  model_id: string;
  tier: string;
  supports_tools: boolean;
  supports_vision: boolean;
  max_context_tokens: number | null;
  cost_per_1k_input: number | null;
  cost_per_1k_output: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LLMModelCreate {
  name: string;
  provider_type: string;
  base_url?: string;
  api_key?: string;
  model_id: string;
  tier?: string;
  supports_tools?: boolean;
  supports_vision?: boolean;
  max_context_tokens?: number;
  cost_per_1k_input?: number;
  cost_per_1k_output?: number;
  is_active?: boolean;
}

export interface LLMModelUpdate {
  name?: string;
  provider_type?: string;
  base_url?: string;
  api_key?: string;
  model_id?: string;
  tier?: string;
  supports_tools?: boolean;
  supports_vision?: boolean;
  max_context_tokens?: number;
  cost_per_1k_input?: number;
  cost_per_1k_output?: number;
  is_active?: boolean;
}

export interface LLMModelBrief {
  id: string;
  name: string;
  provider_type: string;
  model_id: string;
  tier: string;
  supports_tools: boolean;
  supports_vision: boolean;
}

// Tenant Custom Models
export interface TenantCustomModel {
  id: string;
  tenant_id: string;
  name: string;
  provider_type: string;
  base_url: string | null;
  api_key_masked: string | null;
  model_id: string;
  tier: string;
  supports_tools: boolean;
  supports_vision: boolean;
  max_context_tokens: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TenantCustomModelCreate {
  name: string;
  provider_type: string;
  base_url?: string;
  api_key?: string;
  model_id: string;
  tier?: string;
  supports_tools?: boolean;
  supports_vision?: boolean;
  max_context_tokens?: number;
}

export interface TenantCustomModelUpdate {
  name?: string;
  provider_type?: string;
  base_url?: string;
  api_key?: string;
  model_id?: string;
  tier?: string;
  supports_tools?: boolean;
  supports_vision?: boolean;
  max_context_tokens?: number;
  is_active?: boolean;
}

// Tenant Model Config (manual/auto mode)
export interface TenantModelConfig {
  id: string;
  tenant_id: string;
  mode: string;
  manual_model_id: string | null;
  manual_custom_model_id: string | null;
  auto_light_model_id: string | null;
  auto_heavy_model_id: string | null;
  auto_light_custom_model_id: string | null;
  auto_heavy_custom_model_id: string | null;
  complexity_threshold: number;
  manual_model_name: string | null;
  manual_custom_model_name: string | null;
  auto_light_model_name: string | null;
  auto_heavy_model_name: string | null;
  auto_light_custom_model_name: string | null;
  auto_heavy_custom_model_name: string | null;
}

// Attachments
export interface AttachmentBrief {
  id: string;
  filename: string;
  file_type: string;
  file_size_bytes: number;
  processing_status: string;
  summary: string | null;
}

// Tenant Stats
export interface DailyModelStats {
  date: string;
  model_name: string;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost: number;
  request_count: number;
}

export interface StatsSummary {
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost: number;
  request_count: number;
}

export interface TenantStatsResponse {
  summary: StatsSummary;
  daily: DailyModelStats[];
}

export interface TenantModelConfigUpdate {
  mode?: string;
  manual_model_id?: string | null;
  manual_custom_model_id?: string | null;
  auto_light_model_id?: string | null;
  auto_heavy_model_id?: string | null;
  auto_light_custom_model_id?: string | null;
  auto_heavy_custom_model_id?: string | null;
  complexity_threshold?: number;
}
