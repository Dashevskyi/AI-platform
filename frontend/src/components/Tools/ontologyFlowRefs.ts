import type { LogicFlow } from '../../shared/api/types';

export type FlowRefEdge = { fromFlowId: string; nodeId: string; toFlowId: string };

export function collectFlowRefEdges(
  flows: Pick<LogicFlow, 'id' | 'graph'>[],
  omit?: { flowId: string; nodeId: string },
): FlowRefEdge[] {
  const edges: FlowRefEdge[] = [];
  for (const flow of flows) {
    const nodes = flow.graph?.nodes || {};
    for (const [nodeId, node] of Object.entries(nodes)) {
      if (node.type !== 'ref' || !node.flowId) continue;
      if (omit && omit.flowId === flow.id && omit.nodeId === nodeId) continue;
      edges.push({ fromFlowId: flow.id, nodeId, toFlowId: node.flowId });
    }
  }
  return edges;
}

export function buildFlowRefAdjacency(
  flows: Pick<LogicFlow, 'id' | 'graph'>[],
  omit?: { flowId: string; nodeId: string },
): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>();
  for (const flow of flows) {
    if (!adj.has(flow.id)) adj.set(flow.id, new Set());
  }
  for (const { fromFlowId, toFlowId } of collectFlowRefEdges(flows, omit)) {
    if (!adj.has(fromFlowId)) adj.set(fromFlowId, new Set());
    adj.get(fromFlowId)!.add(toFlowId);
  }
  return adj;
}

export function flowRefReachable(
  adj: Map<string, Set<string>>,
  from: string,
  to: string,
  visited = new Set<string>(),
): boolean {
  if (from === to) return true;
  if (visited.has(from)) return false;
  visited.add(from);
  for (const next of adj.get(from) || []) {
    if (flowRefReachable(adj, next, to, visited)) return true;
  }
  return false;
}

/** Adding fromFlowId → toFlowId would close a cycle. */
export function wouldCreateFlowRefCycle(
  flows: Pick<LogicFlow, 'id' | 'graph'>[],
  fromFlowId: string,
  toFlowId: string,
  omit?: { flowId: string; nodeId: string },
): boolean {
  if (!toFlowId || fromFlowId === toFlowId) return true;
  const adj = buildFlowRefAdjacency(flows, omit);
  return flowRefReachable(adj, toFlowId, fromFlowId);
}

export function isAllowedFlowRefTarget(
  flows: Pick<LogicFlow, 'id' | 'graph'>[],
  fromFlowId: string,
  toFlowId: string,
  omitNode?: { flowId: string; nodeId: string },
): boolean {
  return !wouldCreateFlowRefCycle(flows, fromFlowId, toFlowId, omitNode);
}

export function flowRefCycleIssue(
  flows: Pick<LogicFlow, 'id' | 'name' | 'graph'>[],
  fromFlowId: string,
  toFlowId: string,
): string | null {
  if (!toFlowId) return null;
  if (fromFlowId === toFlowId) return 'ссылка на текущий сценарий';
  const names = new Map(flows.map((f) => [f.id, f.name?.trim() || f.id]));
  const target = names.get(toFlowId) || toFlowId;
  const source = names.get(fromFlowId) || fromFlowId;
  if (wouldCreateFlowRefCycle(flows, fromFlowId, toFlowId)) {
    return `ссылка на «${target}» замыкает цикл (из «${target}» уже можно вернуться к «${source}»)`;
  }
  return null;
}

export function buildRefFlowSelectOptions(
  flows: Pick<LogicFlow, 'id' | 'name' | 'graph'>[],
  currentFlowId: string,
  nodeId: string,
  value?: string | null,
) {
  const candidates = flows.filter((f) => f.id !== currentFlowId);
  const options = candidates.map((f) => {
    const allowed = isAllowedFlowRefTarget(flows, currentFlowId, f.id, { flowId: currentFlowId, nodeId });
    const label = f.name?.trim() || f.id;
    return {
      value: f.id,
      label: allowed ? label : `${label} (цикл)`,
      disabled: !allowed,
    };
  });
  if (value && !candidates.some((f) => f.id === value)) {
    options.unshift({ value, label: `${value} (не найден)`, disabled: false });
  } else if (value && flowRefCycleIssue(flows, currentFlowId, value)) {
    const existing = options.find((o) => o.value === value);
    if (existing) {
      existing.label = `${existing.label.replace(/ \(цикл\)$/, '')} (цикл)`;
      existing.disabled = false;
    }
  }
  return options;
}
