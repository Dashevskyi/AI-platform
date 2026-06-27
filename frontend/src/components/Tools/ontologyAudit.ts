import type { AuditCaseRow } from '../../shared/api/endpoints';
import type { ToolCallAuditItem, ToolCallAuditResponse } from '../../shared/api/types';

export type { ToolCallAuditItem, ToolCallAuditResponse };

export type AuditCaseForSuggest = {
  question: string;
  expected_tool?: string | null;
  called?: string[];
  failure_class?: string;
};

export function auditItemToSuggest(item: ToolCallAuditItem): AuditCaseForSuggest {
  return {
    question: item.query,
    expected_tool: item.expected_tool,
    called: item.called,
    failure_class: item.failure_class,
  };
}

export function failedAuditCases(rows: AuditCaseRow[]): AuditCaseForSuggest[] {
  return rows
    .filter((c) => c.active && c.last_result && !c.last_result.passed)
    .map((c) => {
      const expected = c.expected_tools?.[0] || null;
      const called = c.last_result?.called || [];
      let failure_class = 'wrong_tool';
      if (expected && called.length === 0) failure_class = 'no_tool_call';
      else if (expected && called.length > 0 && !called.includes(expected)) failure_class = 'wrong_tool';
      else if (!expected && called.length > 0) failure_class = 'unexpected_tool';
      return {
        question: c.question,
        expected_tool: expected,
        called,
        failure_class,
      };
    });
}

export async function loadDefaultAssistantId(tenantId: string): Promise<string | null> {
  const { assistantsApi } = await import('../../shared/api/endpoints');
  const list = await assistantsApi.list(tenantId);
  const def = list.find((a) => a.is_default) || list.find((a) => a.is_active) || list[0];
  return def?.id ?? null;
}

export async function fetchToolCallAudit(
  tenantId: string,
  opts?: { days?: number; limit?: number; includeLogs?: boolean; includeAuditCases?: boolean; assistantId?: string },
): Promise<ToolCallAuditResponse> {
  const { shellApi } = await import('../../shared/api/endpoints');
  return shellApi.ontologyToolCallAudit(tenantId, opts);
}

export async function fetchFailedAuditCases(tenantId: string, assistantId?: string | null): Promise<{
  assistantId: string;
  assistantName: string;
  cases: AuditCaseForSuggest[];
}> {
  const { assistantsApi, auditSuiteApi } = await import('../../shared/api/endpoints');
  const assistants = await assistantsApi.list(tenantId);
  const assistant = assistantId
    ? assistants.find((a) => a.id === assistantId)
    : assistants.find((a) => a.is_default) || assistants.find((a) => a.is_active) || assistants[0];
  if (!assistant) {
    throw new Error('Нет ассистентов — создайте ассистента для аудита');
  }
  const { cases } = await auditSuiteApi.list(tenantId, assistant.id);
  const failed = failedAuditCases(cases);
  if (!failed.length) {
    throw new Error('Нет проваленных активных кейсов аудита — запустите прогон в «Аудит ассистента»');
  }
  return { assistantId: assistant.id, assistantName: assistant.name, cases: failed };
}
