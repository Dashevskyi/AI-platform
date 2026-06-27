import type { ShellConfigUpdate } from '../../../shared/api/types';

export const SHELL_SECTION_FIELDS: Record<string, (keyof ShellConfigUpdate)[]> = {
  provider: ['provider_type', 'provider_base_url', 'provider_api_key'],
  prompts: ['system_prompt', 'ontology_json', 'rules_text'],
  generation: [
    'temperature', 'max_context_messages', 'history_budget_tokens', 'max_tokens',
    'context_mode', 'enable_thinking', 'response_language', 'timezone',
  ],
  'tools-routing': [
    'tool_semantic_floor', 'tool_routing_temperature', 'lazy_tool_catalog_topk', 'max_tool_rounds',
    'tool_limit_auto', 'tool_limit_max_failures', 'tool_limit_max_per_tool', 'tool_limit_plan_rounds',
  ],
  tier0: ['tier0_enabled', 'tier0_min_tool_score', 'tier0_max_score_gap'],
  security: ['pii_routing_enabled'],
  'memory-kb': [
    'memory_enabled', 'knowledge_base_enabled', 'kb_inject_auto',
    'embedding_model_name', 'kb_max_chunks', 'debug_enabled', 'vision_model_name',
  ],
};

export const SHELL_TAB_FIELDS: Record<string, (keyof ShellConfigUpdate)[]> = {
  llm: Object.values(SHELL_SECTION_FIELDS).flat(),
  stt: ['stt_initial_prompt', 'stt_hotwords', 'stt_vocab_source', 'stt_vocab_source_dsn', 'stt_fuzzy_threshold'],
  tts: [
    'tts_provider', 'tts_voice_id', 'tts_model', 'tts_speed', 'tts_pitch',
    'voice_hold_enabled', 'voice_hold_delay_ms', 'voice_hold_phrases', 'tts_fish_url', 'tts_api_key',
  ],
};

export function pickShellFields(form: ShellConfigUpdate, fields: (keyof ShellConfigUpdate)[]): ShellConfigUpdate {
  const payload: ShellConfigUpdate = {};
  for (const field of fields) {
    if (field in form) {
      (payload as Record<string, unknown>)[field] = form[field];
    }
  }
  return payload;
}

export function sectionHasDirtyFields(
  dirtyFields: Set<keyof ShellConfigUpdate>,
  sectionId: string,
): boolean {
  return (SHELL_SECTION_FIELDS[sectionId] ?? []).some((field) => dirtyFields.has(field));
}

export function tabHasDirtyFields(
  dirtyFields: Set<keyof ShellConfigUpdate>,
  tabId: string,
): boolean {
  return (SHELL_TAB_FIELDS[tabId] ?? []).some((field) => dirtyFields.has(field));
}
