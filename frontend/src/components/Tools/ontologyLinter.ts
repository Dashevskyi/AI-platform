import type { LogicGraph, OntologyJson, OntologySection, Tool } from '../../shared/api/types';
import { flowRefCycleIssue } from './ontologyFlowRefs';

export type OntologyIssueSeverity = 'error' | 'warning' | 'info';

export type OntologyIssue = {
  severity: OntologyIssueSeverity;
  sectionId?: string;
  sectionTitle?: string;
  sectionIndex?: number;
  rowIndex?: number;
  logicNodeId?: string;
  logicFlowId?: string;
  toolName?: string;
  message: string;
};

const PHONE_RE = /(?:\+?\d[\d\s\-()]{8,}\d)/;
const TOKEN_WARN_CHARS = 16000;

function sectionMeta(sec: OntologySection, index: number) {
  return {
    sectionId: sec.id || String(index),
    sectionTitle: sec.title || sec.type,
    sectionIndex: index,
  };
}

function collectToolsInLogic(
  graph: LogicGraph | undefined,
  issues: OntologyIssue[],
  meta: ReturnType<typeof sectionMeta>,
  toolNames: Set<string>,
  logicFlowId?: string,
  flowIds?: Set<string>,
) {
  if (!graph?.nodes) return;
  for (const [nodeId, node] of Object.entries(graph.nodes)) {
    if (node.type === 'action' && node.tool && !toolNames.has(node.tool)) {
      issues.push({
        severity: 'error',
        ...meta,
        logicNodeId: nodeId,
        logicFlowId,
        message: `Узел «${nodeId}»: инструмент «${node.tool}» не найден`,
      });
    }
    if (node.type === 'ref') {
      if (!node.flowId) {
        issues.push({
          severity: 'error',
          ...meta,
          logicNodeId: nodeId,
          logicFlowId,
          message: `Узел «${nodeId}»: не выбран сценарий`,
        });
      } else if (flowIds && !flowIds.has(node.flowId)) {
        issues.push({
          severity: 'error',
          ...meta,
          logicNodeId: nodeId,
          logicFlowId,
          message: `Узел «${nodeId}»: сценарий «${node.flowId}» не найден`,
        });
      } else if (logicFlowId && node.flowId === logicFlowId) {
        issues.push({
          severity: 'error',
          ...meta,
          logicNodeId: nodeId,
          logicFlowId,
          message: `Узел «${nodeId}»: ссылка на текущий сценарий`,
        });
      }
    }
  }
  if (Object.keys(graph.nodes).length > 0 && !graph.start) {
    issues.push({
      severity: 'warning',
      ...meta,
      message: 'В графе логики не задан стартовый узел',
    });
  }
}

function lintLogicSection(sec: Extract<OntologySection, { type: 'logic' }>, index: number, toolNames: Set<string>): OntologyIssue[] {
  const issues: OntologyIssue[] = [];
  const meta = sectionMeta(sec, index);
  const flows = sec.flows?.length ? sec.flows : sec.graph ? [{ id: 'legacy', name: sec.title, graph: sec.graph }] : [];
  const flowIds = new Set(flows.map((f) => f.id));
  for (const flow of flows) {
    collectToolsInLogic(flow.graph, issues, meta, toolNames, flow.id, flowIds);
  }
  for (const flow of flows) {
    for (const [nodeId, node] of Object.entries(flow.graph?.nodes || {})) {
      if (node.type !== 'ref' || !node.flowId || node.flowId === flow.id) continue;
      const cycleMsg = flowRefCycleIssue(flows, flow.id, node.flowId);
      if (cycleMsg && !cycleMsg.includes('текущий сценарий')) {
        issues.push({
          severity: 'error',
          ...meta,
          logicNodeId: nodeId,
          logicFlowId: flow.id,
          message: `Узел «${nodeId}»: ${cycleMsg}`,
        });
      }
    }
  }
  if (!flows.length) {
    issues.push({ severity: 'info', ...meta, message: 'Секция логики пуста — добавьте сценарий' });
  }
  return issues;
}

export function lintOntology(
  ontology: OntologyJson | null | undefined,
  tools: Record<string, Tool>,
  savedPreviewText?: string | null,
): OntologyIssue[] {
  const issues: OntologyIssue[] = [];
  const sections = ontology?.sections || [];
  const toolNames = new Set(Object.keys(tools));
  const activeTools = new Set(Object.values(tools).filter((t) => t.is_active !== false).map((t) => t.name));
  const glossaryTerms = new Map<string, { sectionIndex: number; rowIndex: number }>();
  const toolsInExamples = new Set<string>();
  const examplesSectionIndex = sections.findIndex((s) => s.type === 'examples');
  const examplesMeta = examplesSectionIndex >= 0
    ? sectionMeta(sections[examplesSectionIndex], examplesSectionIndex)
    : null;

  sections.forEach((sec, index) => {
    const meta = sectionMeta(sec, index);

    if (sec.type === 'glossary') {
      sec.items.forEach((item, rowIndex) => {
        const term = item.term?.trim();
        if (!term) {
          issues.push({ severity: 'error', ...meta, rowIndex, message: `Строка ${rowIndex + 1}: пустой термин` });
        }
        if (!item.definition?.trim()) {
          issues.push({ severity: 'warning', ...meta, rowIndex, message: `«${term || `строка ${rowIndex + 1}`}»: нет определения` });
        }
        if (term) {
          const key = term.toLowerCase();
          const prev = glossaryTerms.get(key);
          if (prev) {
            issues.push({
              severity: 'warning',
              ...meta,
              rowIndex,
              message: `Дубликат термина «${term}» (см. также секцию «${sections[prev.sectionIndex]?.title || prev.sectionIndex + 1}»)`,
            });
          } else {
            glossaryTerms.set(key, { sectionIndex: index, rowIndex });
          }
        }
      });
    }

    if (sec.type === 'entities') {
      sec.entities.forEach((ent, ei) => {
        if (!ent.name?.trim()) {
          issues.push({ severity: 'error', ...meta, message: `Сущность ${ei + 1}: пустое название` });
        }
        ent.fields?.forEach((f, fi) => {
          if (!f.name?.trim()) {
            issues.push({ severity: 'error', ...meta, message: `Сущность «${ent.name || ei + 1}», поле ${fi + 1}: пустое имя` });
          } else if (!f.description?.trim()) {
            issues.push({
              severity: 'warning',
              ...meta,
              message: `Сущность «${ent.name}», поле «${f.name}»: нет описания для LLM`,
            });
          }
        });
      });
    }

    if (sec.type === 'examples') {
      sec.items.forEach((item, rowIndex) => {
        if (!item.query?.trim()) {
          issues.push({ severity: 'error', ...meta, rowIndex, message: `Пример ${rowIndex + 1}: пустой запрос` });
        }
        if (item.expected_tool) {
          toolsInExamples.add(item.expected_tool);
          if (!toolNames.has(item.expected_tool)) {
            issues.push({
              severity: 'error',
              ...meta,
              rowIndex,
              message: `Пример «${item.query?.slice(0, 40) || rowIndex + 1}»: инструмент «${item.expected_tool}» не найден`,
            });
          } else if (tools[item.expected_tool]?.is_active === false) {
            issues.push({
              severity: 'warning',
              ...meta,
              rowIndex,
              message: `Пример ссылается на отключённый инструмент «${item.expected_tool}»`,
            });
          }
        }
      });
    }

    if (sec.type === 'logic') {
      issues.push(...lintLogicSection(sec, index, toolNames));
    }

    if (sec.type === 'freeform' && sec.text?.trim()) {
      if (PHONE_RE.test(sec.text)) {
        issues.push({
          severity: 'warning',
          ...meta,
          message: 'В свободном тексте возможны телефоны/PII — проверьте перед сохранением',
        });
      }
    }
  });

  activeTools.forEach((name) => {
    if (!toolsInExamples.has(name)) {
      issues.push({
        severity: 'info',
        ...(examplesMeta || {}),
        toolName: name,
        message: `Активный инструмент «${name}» не упомянут ни в одном примере`,
      });
    }
  });

  const previewLen = savedPreviewText?.length ?? estimatePreviewChars(ontology);
  if (previewLen > TOKEN_WARN_CHARS) {
    issues.push({
      severity: 'warning',
      message: `Предпросмотр ~${Math.round(previewLen / 4)} токенов — возможно, стоит сократить или вынести часть в KB`,
    });
  }

  return issues;
}

function estimatePreviewChars(ontology: OntologyJson | null | undefined): number {
  if (!ontology?.sections?.length) return 0;
  return JSON.stringify(ontology).length;
}

export function issuesForSection(issues: OntologyIssue[], sectionId: string): OntologyIssue[] {
  return issues.filter((i) => i.sectionId === sectionId);
}

export function rowHasIssue(issues: OntologyIssue[], sectionId: string, rowIndex: number, severity?: OntologyIssueSeverity): boolean {
  return issues.some((i) =>
    i.sectionId === sectionId
    && i.rowIndex === rowIndex
    && (!severity || i.severity === severity),
  );
}
