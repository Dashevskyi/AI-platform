import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Stack, Group, Button, TextInput, Textarea, ActionIcon, Badge, Text, Select,
  Menu, Tooltip, Modal, Code, ScrollArea, Popover, SegmentedControl, Checkbox,
  Card, Alert, Table, Box, SimpleGrid, Collapse, UnstyledButton, NavLink,
} from '@mantine/core';
import {
  IconPlus, IconTrash, IconEye, IconDownload, IconArrowsSplit,
  IconBolt, IconNote, IconFlag, IconInfoCircle, IconArrowsMaximize, IconArrowsSort,
  IconBook, IconLayoutList, IconCopy, IconWand, IconDatabaseImport,
  IconAlertTriangle, IconGitCompare, IconSparkles, IconHistory, IconBug, IconChevronDown, IconLink,
  IconDeviceFloppy, IconMaximize,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import {
  ReactFlow, Background, Controls, MiniMap, addEdge, useNodesState, useEdgesState,
  type Node, type Edge, type Connection, Handle, Position, ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { shellApi, toolsApi } from '../../shared/api/endpoints';
import type { OntologyJson, OntologySection, LogicGraph, LogicNode, LogicFlow, Tool } from '../../shared/api/types';
import { LocalCollapsibleSectionNav } from '../../shared/ui/CollapsibleIconNav';
import { lintOntology, rowHasIssue, type OntologyIssue, type OntologyIssueSeverity } from './ontologyLinter';
import { duplicateSection } from './ontologyImport';
import { moveOntologySection, sortOntologySectionsByType } from './ontologySections';
import { OntologyImportModal } from './OntologyImportModal';
import { OntologyWizardModal } from './OntologyWizardModal';
import { OntologySuggestModal } from './OntologySuggestModal';
import { OntologySectionHistoryModal } from './OntologySectionHistoryModal';
import { OntologyToolCallAuditModal } from './OntologyToolCallAuditModal';
import type { AuditCaseForSuggest } from './ontologyAudit';
import { formatCountRu } from '../../shared/utils/pluralRu';
import {
  buildRefFlowSelectOptions,
  flowRefCycleIssue,
  isAllowedFlowRefTarget,
} from './ontologyFlowRefs';

type IssueFocus = {
  sectionId: string;
  rowIndex?: number;
  logicNodeId?: string;
  logicFlowId?: string;
};

const SEVERITY_LABELS: Record<OntologyIssueSeverity, string> = {
  error: 'ошибка',
  warning: 'предупреждение',
  info: 'замечание',
};

function issueNavigable(issue: OntologyIssue): boolean {
  return Boolean(issue.sectionId);
}

function buildToolSelectOptions(toolNames: string[], value?: string | null) {
  const options = toolNames.map((name) => ({ value: name, label: name }));
  if (value && !toolNames.includes(value)) {
    options.unshift({ value, label: `${value} (не найден)` });
  }
  return options;
}

function isMissingTool(toolNames: string[], tool?: string | null): boolean {
  return Boolean(tool && !toolNames.includes(tool));
}

const SECTION_META: Record<string, { label: string; icon: string; description: string }> = {
  glossary: { label: 'Глоссарий', icon: '📖', description: 'Термины и определения предметной области' },
  entities: { label: 'Сущности', icon: '🗃️', description: 'Объекты данных и их поля' },
  relations: { label: 'Связи', icon: '🔗', description: 'Как сущности связаны между собой' },
  logic: { label: 'Логика', icon: '⚖️', description: 'Сценарии: условие → действие или ссылка на другой сценарий (без дублирования)' },
  examples: { label: 'Примеры', icon: '💬', description: 'Образцы запросов и ожидаемых действий' },
  freeform: { label: 'Свободный текст', icon: '📝', description: 'Произвольные знания, не вписывающиеся в структуру' },
};

const uid = () => `n${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;

function emptySection(type: string): OntologySection {
  const base = { id: uid(), title: SECTION_META[type]?.label || type };
  switch (type) {
    case 'glossary': return { ...base, type: 'glossary', items: [] };
    case 'entities': return { ...base, type: 'entities', entities: [] };
    case 'relations': return { ...base, type: 'relations', items: [] };
    case 'logic': return { ...base, type: 'logic', flows: [{ id: uid(), name: 'Сценарий 1', graph: { start: null, nodes: {} } }] };
    case 'examples': return { ...base, type: 'examples', items: [] };
    default: return { ...base, type: 'freeform', text: '' };
  }
}

/* ----------------------- Logic graph (React Flow) ----------------------- */

type ToolMap = Record<string, Tool>;

function _placeholder(spec: any): any {
  if (Array.isArray(spec?.enum) && spec.enum.length) return spec.enum[0];
  if (spec?.example !== undefined) return spec.example;
  switch (spec?.type) {
    case 'integer': case 'number': return 0;
    case 'boolean': return false;
    case 'array': return [];
    case 'object': {
      const sub = spec?.properties || {}; const o: any = {};
      Object.keys(sub).forEach((k) => { o[k] = _placeholder(sub[k]); });
      return o;
    }
    default: return '';
  }
}

// Checklist of a tool's params (required pre-checked) + "insert template" that
// drops a JSON skeleton of the selected params into the action node's hint.
function ParamPicker({ tool, onInsert }: { tool?: Tool; onInsert: (s: string) => void }) {
  const fn = (tool?.config_json as any)?.function;
  const props: Record<string, any> = fn?.parameters?.properties || {};
  const required: string[] = fn?.parameters?.required || [];
  const keys = Object.keys(props);
  const [sel, setSel] = useState<Set<string>>(new Set(required));
  useEffect(() => { setSel(new Set(required)); }, [tool?.name]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!keys.length) return <Text size="xs" c="dimmed">нет параметров</Text>;
  const insert = () => {
    const obj: any = {};
    keys.filter((k) => sel.has(k)).forEach((k) => { obj[k] = _placeholder(props[k]); });
    onInsert(JSON.stringify(obj, null, 2));
  };
  return (
    <Stack gap={6}>
      <Text size="xs" fw={600}>Параметры {tool?.name}:</Text>
      <ScrollArea.Autosize mah={260}>
        <Stack gap={8}>
          {keys.map((k) => {
            const sp = props[k]; const req = required.includes(k);
            return (
              <Group key={k} gap={6} align="flex-start" wrap="nowrap">
                <Checkbox size="xs" mt={2} checked={sel.has(k)}
                  onChange={(e) => setSel((s) => { const n = new Set(s); if (e.currentTarget.checked) n.add(k); else n.delete(k); return n; })} />
                <div>
                  <Group gap={4}>
                    <Text size="xs" fw={600}>{k}</Text>
                    <Badge size="xs" variant="light">{sp?.type || '?'}</Badge>
                    {req && <Badge size="xs" color="red" variant="light">обяз.</Badge>}
                  </Group>
                  {sp?.description && <Text size="xs" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>{sp.description}</Text>}
                </div>
              </Group>
            );
          })}
        </Stack>
      </ScrollArea.Autosize>
      <Button size="compact-xs" onClick={insert}>Вставить шаблон выбранных в подсказку</Button>
    </Stack>
  );
}

function GraphNodeBody({ id, data }: { id: string; data: any }) {
  const { node, onPatch, onStart, onExpand, isStart, toolNames, toolMap, flows, currentFlowId } = data;
  const allFlows = (flows as LogicFlow[]) || [];
  const refCycleMsg = node.type === 'ref' && node.flowId
    ? flowRefCycleIssue(allFlows, currentFlowId, node.flowId)
    : null;
  const color = node.type === 'condition' ? '#fd7e14'
    : node.type === 'action' ? '#4263eb'
      : node.type === 'ref' ? '#12b886'
        : '#868e96';
  const missingTool = node.type === 'action' && isMissingTool(toolNames, node.tool);
  const missingFlow = node.type === 'ref' && node.flowId
    && !allFlows.some((f) => f.id === node.flowId);
  const refInvalid = Boolean(refCycleMsg || missingFlow);
  return (
    <div style={{
      border: `2px solid ${missingTool || refInvalid ? 'var(--mantine-color-red-6)' : color}`,
      borderRadius: 8,
      background: 'var(--mantine-color-body)',
      minWidth: 210,
      padding: 8,
    }}>
      <Handle type="target" position={Position.Top} />
      <Group justify="space-between" gap={4} mb={4}>
        <Badge size="xs" color={node.type === 'condition' ? 'orange' : node.type === 'action' ? 'indigo' : node.type === 'ref' ? 'teal' : 'gray'} variant="filled">
          {node.type === 'condition' ? 'условие' : node.type === 'action' ? 'действие' : node.type === 'ref' ? 'сценарий' : 'заметка'}
        </Badge>
        <Group gap={2}>
          {isStart ? <Badge size="xs" color="green" leftSection={<IconFlag size={10} />}>старт</Badge>
            : <Tooltip label="Сделать стартом"><ActionIcon size="xs" variant="subtle" className="nodrag" onClick={() => onStart(id)}><IconFlag size={12} /></ActionIcon></Tooltip>}
          <Tooltip label="Открыть в большом редакторе"><ActionIcon size="xs" variant="subtle" className="nodrag" onClick={() => onExpand(id)}><IconArrowsMaximize size={12} /></ActionIcon></Tooltip>
        </Group>
      </Group>
      <TextInput size="xs" className="nodrag" placeholder="метка / условие" value={node.label || ''}
        onChange={(e) => onPatch(id, { label: e.currentTarget.value })} mb={4} />
      {node.type === 'action' && (
        <>
          <Group gap={4} wrap="nowrap" mb={4}>
            <Select
              size="xs"
              className="nodrag"
              searchable
              placeholder="тул (поиск)"
              data={buildToolSelectOptions(toolNames, node.tool)}
              value={node.tool || null}
              onChange={(v) => onPatch(id, { tool: v || undefined })}
              style={{ flex: 1 }}
              maxDropdownHeight={200}
              clearable
              error={missingTool}
            />
            <Popover width={360} position="bottom-end" withArrow>
              <Popover.Target><ActionIcon size="sm" variant="subtle" className="nodrag" disabled={!node.tool}><IconInfoCircle size={14} /></ActionIcon></Popover.Target>
              <Popover.Dropdown className="nodrag">
                <ParamPicker tool={toolMap[node.tool || '']} onInsert={(s) => onPatch(id, { hint: s })} />
              </Popover.Dropdown>
            </Popover>
          </Group>
          {missingTool && (
            <Text size="xs" c="red" mb={4}>
              «{node.tool}» нет в списке tools
            </Text>
          )}
          <Textarea size="xs" className="nodrag" placeholder="подсказка по аргументам (пример)" autosize minRows={1} maxRows={3}
            value={node.hint || ''} onChange={(e) => onPatch(id, { hint: e.currentTarget.value })} />
        </>
      )}
      {node.type === 'ref' && (
        <>
          <Select
            size="xs"
            className="nodrag"
            searchable
            placeholder="сценарий"
            data={buildRefFlowSelectOptions(allFlows, currentFlowId, id, node.flowId)}
            value={node.flowId || null}
            onChange={(v) => {
              if (!v || isAllowedFlowRefTarget(allFlows, currentFlowId, v, { flowId: currentFlowId, nodeId: id })) {
                onPatch(id, { flowId: v || '' });
              }
            }}
            mb={4}
            error={refInvalid}
          />
          {refInvalid && (
            <Text size="xs" c="red" mb={4}>
              {missingFlow ? 'сценарий не найден' : refCycleMsg}
            </Text>
          )}
        </>
      )}
      {node.type === 'note' && (
        <Textarea size="xs" className="nodrag" placeholder="текст" autosize minRows={1} maxRows={3}
          value={node.text || ''} onChange={(e) => onPatch(id, { text: e.currentTarget.value })} />
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { graphNode: GraphNodeBody };

function graphToFlow(graph: LogicGraph): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  Object.entries(graph.nodes || {}).forEach(([id, n], i) => {
    const pos = (n as any)._pos || { x: (i % 3) * 260, y: Math.floor(i / 3) * 170 };
    nodes.push({ id, type: 'graphNode', position: pos, data: { node: n } });
    if (n.type === 'condition') {
      (n.branches || []).forEach((b, bi) => {
        if (b.next) edges.push({ id: `${id}-${bi}`, source: id, target: b.next, label: b.case || '?', animated: true });
      });
    } else if ((n as any).next) {
      edges.push({ id: `${id}-n`, source: id, target: (n as any).next });
    }
  });
  return { nodes, edges };
}

function flowToGraph(nodes: Node[], edges: Edge[], start: string | null): LogicGraph {
  const out: Record<string, LogicNode> = {};
  nodes.forEach((rn) => {
    const n: any = { ...(rn.data as any).node };
    n._pos = rn.position;
    const outgoing = edges.filter((e) => e.source === rn.id);
    if (n.type === 'condition') {
      n.branches = outgoing.map((e) => ({ case: (e.label as string) || '?', next: e.target }));
      delete n.next;
    } else {
      n.next = outgoing[0]?.target || null;
      delete n.branches;
    }
    out[rn.id] = n;
  });
  const validStart = start && out[start] ? start : (nodes[0]?.id || null);
  return { start: validStart, nodes: out };
}

function LogicGraphEditor({ graph, onChange, toolNames, toolMap, focusNodeId, currentFlowId, flows }:
  { graph: LogicGraph; onChange: (g: LogicGraph) => void; toolNames: string[]; toolMap: ToolMap;
    focusNodeId?: string | null; currentFlowId: string; flows: LogicFlow[] }) {
  const init = useMemo(() => graphToFlow(graph), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [nodes, setNodes, onNodesChange] = useNodesState(init.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(init.edges);
  const [start, setStart] = useState<string | null>(graph.start);
  const [selEdge, setSelEdge] = useState<Edge | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!focusNodeId) return;
    setEditId(focusNodeId);
    setNodes((nds) => {
      if (!nds.some((n) => n.id === focusNodeId)) return nds;
      return nds.map((n) => ({ ...n, selected: n.id === focusNodeId }));
    });
  }, [focusNodeId, setNodes]);

  const sync = useCallback((ns: Node[], es: Edge[], st: string | null) => {
    onChange(flowToGraph(ns, es, st));
  }, [onChange]);

  const patchNode = useCallback((id: string, patch: any) => {
    setNodes((nds) => {
      const next = nds.map((n) => n.id === id ? { ...n, data: { ...n.data, node: { ...(n.data as any).node, ...patch } } } : n);
      sync(next, edges, start);
      return next;
    });
  }, [edges, start, setNodes, sync]);

  const firstSafeRefTarget = flows.find(
    (f) => f.id !== currentFlowId
      && isAllowedFlowRefTarget(flows, currentFlowId, f.id),
  )?.id || '';

  // inject callbacks into node data
  const rfNodes = nodes.map((n) => ({
    ...n, data: { ...n.data, onPatch: patchNode, onStart: (id: string) => { setStart(id); sync(nodes, edges, id); },
      onExpand: setEditId, isStart: n.id === start, toolNames, toolMap, flows, currentFlowId },
  }));

  const editNode = nodes.find((n) => n.id === editId);
  const en: any = editNode ? (editNode.data as any).node : null;

  const onConnect = useCallback((c: Connection) => {
    setEdges((eds) => {
      const src = nodes.find((n) => n.id === c.source);
      const isCond = (src?.data as any)?.node?.type === 'condition';
      const ne = addEdge({ ...c, label: isCond ? 'да' : undefined, animated: isCond }, eds);
      sync(nodes, ne, start);
      return ne;
    });
  }, [nodes, start, setEdges, sync]);

  const addNode = (type: LogicNode['type']) => {
    const id = uid();
    const node: any = type === 'condition' ? { type, label: '', branches: [] }
      : type === 'action' ? { type, label: '', tool: undefined, hint: '', next: null }
        : type === 'ref' ? { type, label: '', flowId: firstSafeRefTarget, next: null }
          : { type, label: '', text: '', next: null };
    const rn: Node = { id, type: 'graphNode', position: { x: 60 + Math.random() * 120, y: 60 + Math.random() * 120 }, data: { node } };
    setNodes((nds) => { const next = [...nds, rn]; sync(next, edges, start || id); return next; });
    if (!start) setStart(id);
  };

  const delSelected = () => {
    setNodes((nds) => {
      const selIds = new Set(nds.filter((n) => n.selected).map((n) => n.id));
      if (!selIds.size && !selEdge) return nds;
      const ne = edges.filter((e) => !selIds.has(e.source) && !selIds.has(e.target) && e.id !== selEdge?.id);
      const nn = nds.filter((n) => !selIds.has(n.id));
      setEdges(ne); setSelEdge(null);
      const st = start && nn.find((n) => n.id === start) ? start : (nn[0]?.id || null);
      setStart(st); sync(nn, ne, st);
      return nn;
    });
  };

  const setEdgeCase = (val: string) => {
    if (!selEdge) return;
    setEdges((eds) => { const ne = eds.map((e) => e.id === selEdge.id ? { ...e, label: val } : e); sync(nodes, ne, start); return ne; });
    setSelEdge((e) => e ? { ...e, label: val } : e);
  };

  return (
    <Stack gap={4} style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <Group gap={4} style={{ flexShrink: 0 }}>
        <Button size="compact-xs" variant="light" color="orange" leftSection={<IconArrowsSplit size={12} />} onClick={() => addNode('condition')}>условие</Button>
        <Button size="compact-xs" variant="light" color="indigo" leftSection={<IconBolt size={12} />} onClick={() => addNode('action')}>действие</Button>
        <Button size="compact-xs" variant="light" color="teal" leftSection={<IconLink size={12} />} onClick={() => addNode('ref')}>ссылка на сценарий</Button>
        <Button size="compact-xs" variant="light" color="gray" leftSection={<IconNote size={12} />} onClick={() => addNode('note')}>заметка</Button>
        <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconTrash size={12} />} onClick={delSelected}>удалить выбранное</Button>
        <Button size="compact-xs" variant="subtle" leftSection={<IconMaximize size={12} />} onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'свернуть' : 'во весь экран'}
        </Button>
        {selEdge && (
          <TextInput size="xs" w={180} placeholder="метка ветки (case)" value={(selEdge.label as string) || ''}
            onChange={(e) => setEdgeCase(e.currentTarget.value)} />
        )}
        <Text size="xs" c="dimmed">тяни от низа узла к верху другого — это ветка/переход</Text>
      </Group>
      <Box
        style={{
          height: expanded ? '78vh' : 380,
          border: '1px solid var(--mantine-color-default-border)',
          borderRadius: 8,
          overflow: 'hidden',
        }}
      >
        <div style={{ width: '100%', height: '100%' }}>
          <ReactFlowProvider>
            <ReactFlow
            nodes={rfNodes}
            edges={edges}
            nodeTypes={nodeTypes}
            style={{ width: '100%', height: '100%' }}
            onNodesChange={(c) => { onNodesChange(c); }}
            onNodeDragStop={() => sync(nodes, edges, start)}
            onEdgesChange={(c) => { onEdgesChange(c); }}
            onConnect={onConnect}
            onEdgeClick={(_, e) => setSelEdge(e)}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background /><Controls /><MiniMap pannable zoomable />
            </ReactFlow>
          </ReactFlowProvider>
        </div>
      </Box>

      <Modal opened={!!editId} onClose={() => setEditId(null)} size="lg"
        title={en ? (en.type === 'condition' ? 'Условие' : en.type === 'action' ? 'Действие' : en.type === 'ref' ? 'Ссылка на сценарий' : 'Заметка') : ''}>
        {en && editId && (
          <Stack gap="sm">
            <TextInput label={en.type === 'condition' ? 'Условие' : 'Метка'} value={en.label || ''}
              onChange={(e) => patchNode(editId, { label: e.currentTarget.value })} />
            {en.type === 'ref' && editId && (
              <Select
                label="Сценарий"
                searchable
                clearable
                data={buildRefFlowSelectOptions(flows, currentFlowId, editId, en.flowId)}
                value={en.flowId || null}
                onChange={(v) => {
                  if (!v || isAllowedFlowRefTarget(flows, currentFlowId, v, { flowId: currentFlowId, nodeId: editId })) {
                    patchNode(editId, { flowId: v || '' });
                  }
                }}
                maxDropdownHeight={260}
                error={
                  en.flowId
                    ? (flowRefCycleIssue(flows, currentFlowId, en.flowId)
                      || (!flows.some((f) => f.id === en.flowId) ? 'Сценарий не найден' : undefined))
                    : 'Выберите сценарий'
                }
              />
            )}
            {en.type === 'action' && (
              <>
                <Select
                  label="Инструмент (поиск)"
                  searchable
                  clearable
                  data={buildToolSelectOptions(toolNames, en.tool)}
                  value={en.tool || null}
                  onChange={(v) => patchNode(editId, { tool: v || undefined })}
                  maxDropdownHeight={260}
                  error={isMissingTool(toolNames, en.tool) ? `Инструмент «${en.tool}» не найден` : undefined}
                />
                {en.tool && !isMissingTool(toolNames, en.tool) && (
                  <div style={{ border: '1px solid var(--mantine-color-default-border)', borderRadius: 6, padding: 8 }}>
                    <ParamPicker tool={toolMap[en.tool]} onInsert={(s) => patchNode(editId, { hint: s })} />
                  </div>
                )}
                <Textarea label="Аргументы / подсказка" autosize minRows={6} maxRows={20}
                  value={en.hint || ''} onChange={(e) => patchNode(editId, { hint: e.currentTarget.value })}
                  styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }} />
              </>
            )}
            {en.type === 'note' && (
              <Textarea label="Текст" autosize minRows={6} maxRows={20} value={en.text || ''}
                onChange={(e) => patchNode(editId, { text: e.currentTarget.value })} />
            )}
            {en.type === 'condition' && (
              <Text size="xs" c="dimmed">Ветки (case) задаются на рёбрах: тяни от узла к целям и кликни ребро, чтобы задать метку ветки.</Text>
            )}
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

/* --------------- logic = multiple named decision flows --------------- */

function flowNodeCount(flow: LogicFlow): number {
  return Object.keys(flow.graph?.nodes || {}).length;
}

const FLOW_LIST_WIDTH_MIN = 200;
const FLOW_LIST_WIDTH_MAX = 560;
const FLOW_LIST_WIDTH_DEFAULT = 280;

function LogicFlowsEditor({ flows, onChange, toolNames, toolMap, focusLogicNodeId, focusLogicFlowId }:
  { flows: LogicFlow[]; onChange: (f: LogicFlow[]) => void; toolNames: string[]; toolMap: ToolMap;
    focusLogicNodeId?: string | null; focusLogicFlowId?: string | null }) {
  const [sel, setSel] = useState<string | null>(flows[0]?.id || null);
  const [listWidth, setListWidth] = useState(FLOW_LIST_WIDTH_DEFAULT);
  const [dividerActive, setDividerActive] = useState(false);
  const listDragRef = useRef<{ startX: number; startW: number } | null>(null);
  const cur = flows.find((f) => f.id === sel) || flows[0] || null;

  useEffect(() => {
    if (focusLogicFlowId && flows.some((f) => f.id === focusLogicFlowId)) {
      setSel(focusLogicFlowId);
      return;
    }
    if (focusLogicNodeId) {
      const flowWithNode = flows.find((f) => focusLogicNodeId in (f.graph?.nodes || {}));
      if (flowWithNode) setSel(flowWithNode.id);
    }
  }, [focusLogicFlowId, focusLogicNodeId, flows]);

  useEffect(() => {
    if (sel && flows.some((f) => f.id === sel)) return;
    setSel(flows[0]?.id || null);
  }, [flows, sel]);

  const addFlow = () => {
    const nf: LogicFlow = { id: uid(), name: `Сценарий ${flows.length + 1}`, graph: { start: null, nodes: {} } };
    onChange([...flows, nf]); setSel(nf.id);
  };
  const delFlow = (id: string) => {
    const next = flows.filter((f) => f.id !== id);
    onChange(next); setSel(next[0]?.id || null);
  };

  const onListDividerMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    listDragRef.current = { startX: e.clientX, startW: listWidth };
    setDividerActive(true);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    const onMove = (ev: MouseEvent) => {
      if (!listDragRef.current) return;
      const next = listDragRef.current.startW + (ev.clientX - listDragRef.current.startX);
      setListWidth(Math.min(FLOW_LIST_WIDTH_MAX, Math.max(FLOW_LIST_WIDTH_MIN, next)));
    };
    const onUp = () => {
      listDragRef.current = null;
      setDividerActive(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [listWidth]);

  return (
    <Box style={{ display: 'flex', alignItems: 'stretch', width: '100%', minWidth: 0, minHeight: 640 }}>
      <Box
        style={{
          width: listWidth,
          flexShrink: 0,
          minWidth: 0,
          paddingRight: 8,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <Text size="xs" c="dimmed" fw={600} tt="uppercase" mb="xs" style={{ flexShrink: 0 }}>
          Сценарии ({flows.length})
        </Text>
        <ScrollArea style={{ flex: 1 }} type="auto" offsetScrollbars>
          <Stack gap={4}>
            {flows.map((f, i) => {
              const active = f.id === cur?.id;
              const nodes = flowNodeCount(f);
              const label = f.name?.trim() || `Сценарий ${i + 1}`;
              return (
                <Tooltip key={f.id} label={label} multiline maw={360}>
                  <NavLink
                    label={label}
                    description={nodes ? `${nodes} узл.` : 'пустой'}
                    active={active}
                    onClick={() => setSel(f.id)}
                    variant={active ? 'light' : 'subtle'}
                    color="blue"
                    styles={{
                      root: { borderRadius: 'var(--mantine-radius-sm)', overflow: 'hidden' },
                      body: { overflow: 'hidden', minWidth: 0 },
                      label: {
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        lineHeight: 1.35,
                        fontSize: 13,
                      },
                      description: { marginTop: 2 },
                    }}
                  />
                </Tooltip>
              );
            })}
          </Stack>
        </ScrollArea>
        <Button
          size="xs"
          variant="light"
          fullWidth
          mt="sm"
          style={{ flexShrink: 0 }}
          leftSection={<IconPlus size={14} />}
          onClick={addFlow}
        >
          Добавить сценарий
        </Button>
      </Box>

      <Box
        role="separator"
        aria-orientation="vertical"
        aria-valuenow={listWidth}
        onMouseDown={onListDividerMouseDown}
        style={{
          width: 6,
          flexShrink: 0,
          cursor: 'col-resize',
          borderRadius: 4,
          margin: '0 2px',
          background: dividerActive
            ? 'var(--mantine-color-blue-4)'
            : 'var(--mantine-color-default-border)',
          transition: dividerActive ? undefined : 'background 120ms ease',
        }}
        onMouseEnter={(e) => {
          if (!listDragRef.current) e.currentTarget.style.background = 'var(--mantine-color-gray-4)';
        }}
        onMouseLeave={(e) => {
          if (!listDragRef.current) e.currentTarget.style.background = 'var(--mantine-color-default-border)';
        }}
      />

      <Box style={{ flex: 1, minWidth: 0, paddingLeft: 4, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {cur ? (
          <Stack gap="sm" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <Group gap="sm" align="flex-end" wrap="nowrap" style={{ flexShrink: 0 }}>
              <TextInput
                label="Название сценария"
                size="sm"
                style={{ flex: 1 }}
                placeholder="напр. «MAC-таблица на свиче»"
                value={cur.name}
                onChange={(e) => onChange(flows.map((f) => f.id === cur.id ? { ...f, name: e.currentTarget.value } : f))}
              />
              <Tooltip label="Удалить сценарий">
                <ActionIcon variant="subtle" color="red" mb={4} onClick={() => delFlow(cur.id)}>
                  <IconTrash size={16} />
                </ActionIcon>
              </Tooltip>
            </Group>
            <Box style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <LogicGraphEditor
                key={cur.id}
                graph={cur.graph}
                toolNames={toolNames}
                toolMap={toolMap}
                currentFlowId={cur.id}
                flows={flows}
                focusNodeId={focusLogicNodeId}
                onChange={(graph) => onChange(flows.map((f) => f.id === cur.id ? { ...f, graph } : f))}
              />
            </Box>
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">Добавьте сценарий или выберите из списка слева.</Text>
        )}
      </Box>
    </Box>
  );
}

/* ----------------------- per-type section editors ----------------------- */

function TableRowEditor<T>({ rows, onChange, columns, empty, addLabel, blank, sectionId, issues, highlightRowIndex }:
  { rows: T[]; onChange: (r: T[]) => void;
    columns: { header: string; width?: string | number; render: (r: T, set: (p: Partial<T>) => void) => React.ReactNode }[];
    empty: string; addLabel: string; blank: () => T;
    sectionId?: string; issues?: OntologyIssue[]; highlightRowIndex?: number }) {
  const rowRefs = useRef<(HTMLTableRowElement | null)[]>([]);

  useEffect(() => {
    if (highlightRowIndex == null) return;
    rowRefs.current[highlightRowIndex]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [highlightRowIndex]);

  return (
    <Stack gap="xs">
      {rows.length === 0 ? (
        <Text size="sm" c="dimmed" ta="center" py="md">{empty}</Text>
      ) : (
        <Table withTableBorder withColumnBorders striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              {columns.map((col) => (
                <Table.Th key={col.header} w={col.width}>{col.header}</Table.Th>
              ))}
              <Table.Th w={40} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r, i) => {
              const hasError = sectionId && issues && rowHasIssue(issues, sectionId, i, 'error');
              const hasWarning = sectionId && issues && rowHasIssue(issues, sectionId, i, 'warning');
              const isFocused = highlightRowIndex === i;
              return (
              <Table.Tr
                key={i}
                ref={(el) => { rowRefs.current[i] = el; }}
                style={{
                  ...(hasError ? { background: 'var(--mantine-color-red-0)' } : hasWarning ? { background: 'var(--mantine-color-yellow-0)' } : undefined),
                  ...(isFocused ? { outline: '2px solid var(--mantine-color-blue-5)', outlineOffset: -2 } : undefined),
                }}
              >
                {columns.map((col) => (
                  <Table.Td key={col.header}>
                    {col.render(r, (p) => onChange(rows.map((x, k) => k === i ? { ...x, ...p } : x)))}
                  </Table.Td>
                ))}
                <Table.Td>
                  <ActionIcon variant="subtle" color="red" size="sm" onClick={() => onChange(rows.filter((_, k) => k !== i))}>
                    <IconTrash size={14} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
      <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} onClick={() => onChange([...rows, blank()])} style={{ alignSelf: 'flex-start' }}>
        {addLabel}
      </Button>
    </Stack>
  );
}

function SectionEditor({ section, onChange, toolNames, toolMap, sectionId, issues, issueFocus }:
  { section: OntologySection; onChange: (s: OntologySection) => void; toolNames: string[]; toolMap: ToolMap;
    sectionId?: string; issues?: OntologyIssue[];
    issueFocus?: IssueFocus | null }) {
  const s = section as any;
  const meta = SECTION_META[s.type];
  const highlightRowIndex = issueFocus && issueFocus.sectionId === sectionId ? issueFocus.rowIndex : undefined;
  const focusLogicNodeId = issueFocus && issueFocus.sectionId === sectionId ? issueFocus.logicNodeId : undefined;
  const focusLogicFlowId = issueFocus && issueFocus.sectionId === sectionId ? issueFocus.logicFlowId : undefined;

  if (s.type === 'glossary')
    return (
      <Stack gap="xs">
        {meta && <Text size="sm" c="dimmed">{meta.description}</Text>}
        <TableRowEditor
          rows={s.items}
          onChange={(items) => onChange({ ...s, items })}
          sectionId={sectionId}
          issues={issues}
          highlightRowIndex={highlightRowIndex}
          empty="Добавьте термины — аббревиатуры, жаргон, названия оборудования"
          addLabel="Добавить термин"
          blank={() => ({ term: '', definition: '' })}
          columns={[
            { header: 'Термин', width: '30%', render: (r: any, set) => (
              <TextInput size="sm" placeholder="GPON, VLAN…" value={r.term} onChange={(e) => set({ term: e.currentTarget.value })} />
            )},
            { header: 'Определение', render: (r: any, set) => (
              <TextInput size="sm" placeholder="Что это значит для ассистента" value={r.definition} onChange={(e) => set({ definition: e.currentTarget.value })} />
            )},
          ]}
        />
      </Stack>
    );

  if (s.type === 'relations')
    return (
      <Stack gap="xs">
        {meta && <Text size="sm" c="dimmed">{meta.description}</Text>}
        <TableRowEditor
          rows={s.items}
          onChange={(items) => onChange({ ...s, items })}
          empty="Опишите связи: «Абонент → имеет → договор»"
          addLabel="Добавить связь"
          blank={() => ({ from: '', relation: '', to: '' })}
          columns={[
            { header: 'От', width: '30%', render: (r: any, set) => (
              <TextInput size="sm" placeholder="Абонент" value={r.from} onChange={(e) => set({ from: e.currentTarget.value })} />
            )},
            { header: 'Связь', width: '25%', render: (r: any, set) => (
              <TextInput size="sm" placeholder="имеет" value={r.relation} onChange={(e) => set({ relation: e.currentTarget.value })} />
            )},
            { header: 'К', render: (r: any, set) => (
              <TextInput size="sm" placeholder="Договор" value={r.to} onChange={(e) => set({ to: e.currentTarget.value })} />
            )},
          ]}
        />
      </Stack>
    );

  if (s.type === 'examples')
    return (
      <Stack gap="xs">
        {meta && <Text size="sm" c="dimmed">{meta.description}</Text>}
        <TableRowEditor
          rows={s.items}
          onChange={(items) => onChange({ ...s, items })}
          sectionId={sectionId}
          issues={issues}
          highlightRowIndex={highlightRowIndex}
          empty="Примеры помогают модели понять, какой инструмент вызывать"
          addLabel="Добавить пример"
          blank={() => ({ query: '', expected_tool: '', note: '' })}
          columns={[
            { header: 'Запрос пользователя', render: (r: any, set) => (
              <TextInput size="sm" placeholder="Покажи MAC на свиче…" value={r.query} onChange={(e) => set({ query: e.currentTarget.value })} />
            )},
            { header: 'Инструмент', width: 180, render: (r: any, set) => (
              <Select size="sm" searchable clearable placeholder="Выберите" data={toolNames} value={r.expected_tool || null} onChange={(v) => set({ expected_tool: v || '' })} />
            )},
            { header: 'Заметка', width: 160, render: (r: any, set) => (
              <TextInput size="sm" placeholder="Необязательно" value={r.note} onChange={(e) => set({ note: e.currentTarget.value })} />
            )},
          ]}
        />
      </Stack>
    );

  if (s.type === 'freeform')
    return (
      <Stack gap="xs">
        {meta && <Text size="sm" c="dimmed">{meta.description}</Text>}
        <Textarea autosize minRows={4} placeholder="Любые знания текстом — процедуры, исключения, контекст…" value={s.text} onChange={(e) => onChange({ ...s, text: e.currentTarget.value })} />
      </Stack>
    );

  if (s.type === 'entities')
    return (
      <Stack gap="md">
        {meta && <Text size="sm" c="dimmed">{meta.description}</Text>}
        {(s.entities || []).map((ent: any, ei: number) => (
          <Card key={ei} withBorder padding="sm" radius="md">
            <Group justify="space-between" mb="xs">
              <TextInput size="sm" w={280} placeholder="Название сущности" value={ent.name}
                onChange={(e) => onChange({ ...s, entities: s.entities.map((x: any, k: number) => k === ei ? { ...x, name: e.currentTarget.value } : x) })} />
              <ActionIcon variant="subtle" color="red" onClick={() => onChange({ ...s, entities: s.entities.filter((_: any, k: number) => k !== ei) })}>
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
            <TableRowEditor
              rows={ent.fields}
              onChange={(fields) => onChange({ ...s, entities: s.entities.map((x: any, k: number) => k === ei ? { ...x, fields } : x) })}
              empty="Добавьте поля сущности"
              addLabel="Добавить поле"
              blank={() => ({ name: '', type: 'string', description: '' })}
              columns={[
                { header: 'Поле', width: '25%', render: (f: any, set) => (
                  <TextInput size="sm" placeholder="address" value={f.name} onChange={(e) => set({ name: e.currentTarget.value })} />
                )},
                { header: 'Тип', width: 130, render: (f: any, set) => (
                  <Select size="sm" data={['string', 'integer', 'number', 'boolean', 'date', 'array', 'object']} value={f.type} onChange={(v) => set({ type: v || 'string' })} />
                )},
                { header: 'Описание', render: (f: any, set) => (
                  <TextInput size="sm" placeholder="Что хранится в поле" value={f.description} onChange={(e) => set({ description: e.currentTarget.value })} />
                )},
              ]}
            />
          </Card>
        ))}
        <Button size="xs" variant="light" leftSection={<IconPlus size={14} />} style={{ alignSelf: 'flex-start' }}
          onClick={() => onChange({ ...s, entities: [...(s.entities || []), { name: '', fields: [] }] })}>Добавить сущность</Button>
      </Stack>
    );

  if (s.type === 'logic') {
    const raw = s.flows && s.flows.length ? s.flows
      : (s.graph ? [{ name: s.title || 'Сценарий 1', graph: s.graph }]
        : [{ name: 'Сценарий 1', graph: { start: null, nodes: {} } }]);
    // Ensure each flow has a STABLE id — hand-built/imported ontologies often
    // omit it, which broke flow switching (all shared an undefined id).
    const flows: LogicFlow[] = raw.map((f: any, i: number) => ({
      id: f.id || `f${i}`, name: f.name || '', graph: f.graph || { start: null, nodes: {} },
    }));
    return <LogicFlowsEditor flows={flows} toolNames={toolNames} toolMap={toolMap}
      focusLogicNodeId={focusLogicNodeId}
      focusLogicFlowId={focusLogicFlowId}
      onChange={(f) => onChange({ ...s, flows: f, graph: undefined })} />;
  }
  return null;
}

/* ----------------------- main editor ----------------------- */

interface Props {
  tenantId: string;
  value: OntologyJson | null;
  fallbackText: string | null;
  onChange: (v: OntologyJson) => void;
  /** When set, «Импорт из текста» parses this string instead of tenant shell config. */
  importSourceText?: string | null;
}

function sectionKey(sec: OntologySection, index: number) {
  return sec.id || String(index);
}

function sectionSummary(sec: OntologySection): string {
  const s = sec as any;
  switch (s.type) {
    case 'glossary': return `${s.items?.length || 0} терминов`;
    case 'entities': return `${s.entities?.length || 0} сущностей`;
    case 'relations': return `${s.items?.length || 0} связей`;
    case 'logic': return `${(s.flows?.length || (s.graph ? 1 : 0))} сценариев`;
    case 'examples': return `${s.items?.length || 0} примеров`;
    case 'freeform': return s.text?.trim() ? 'есть текст' : 'пусто';
    default: return '';
  }
}

// Unified line diff (LCS) — saved vs draft, для честного «Diff».
function diffLines(oldText: string, newText: string): { t: 'eq' | 'add' | 'del'; s: string }[] {
  const a = (oldText || '').split('\n'); const b = (newText || '').split('\n');
  const n = a.length; const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { t: 'eq' | 'add' | 'del'; s: string }[] = [];
  let i = 0; let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ t: 'eq', s: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ t: 'del', s: a[i] }); i++; }
    else { out.push({ t: 'add', s: b[j] }); j++; }
  }
  while (i < n) out.push({ t: 'del', s: a[i++] });
  while (j < m) out.push({ t: 'add', s: b[j++] });
  return out;
}

export function OntologyEditor({ tenantId, value, fallbackText, onChange, importSourceText }: Props) {
  const [mode, setMode] = useState<'structured' | 'text'>(value?.sections?.length ? 'structured' : 'text');
  const [toolMap, setToolMap] = useState<ToolMap>({});
  const [toolsList, setToolsList] = useState<Tool[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [diffText, setDiffText] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [suggestAuditCases, setSuggestAuditCases] = useState<AuditCaseForSuggest[] | null>(null);
  const [suggestAuditTask, setSuggestAuditTask] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [linterExpanded, setLinterExpanded] = useState(false);
  const [issueFocus, setIssueFocus] = useState<IssueFocus | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(value?.sections?.[0]?.id ?? null);
  const sections = value?.sections || [];
  const toolNames = useMemo(() => Object.keys(toolMap).sort(), [toolMap]);
  const [saving, setSaving] = useState(false);
  // "Dirty" = edited THROUGH this editor since load/last save. A snapshot of the
  // initial `value` is unreliable because the parent populates ontology_json
  // asynchronously (config fetch) — that would read as a phantom edit on load.
  // So we flag touched only on real mutations and clear it on save.
  const [touched, setTouched] = useState(false);
  const dirty = touched;

  // Debounce linting — it runs over the whole ontology on every keystroke,
  // which can lag typing on a large ontology.
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedValue(value), 300);
    return () => window.clearTimeout(t);
  }, [value]);
  const linterIssues = useMemo(
    () => lintOntology(debouncedValue, toolMap, fallbackText),
    [debouncedValue, toolMap, fallbackText],
  );
  const errorCount = linterIssues.filter((i) => i.severity === 'error').length;
  const warnCount = linterIssues.filter((i) => i.severity === 'warning').length;
  const infoCount = linterIssues.filter((i) => i.severity === 'info').length;
  const validationSummary = useMemo(() => {
    const parts: string[] = [];
    if (errorCount) {
      parts.push(formatCountRu(errorCount, 'ошибка', 'ошибки', 'ошибок'));
    }
    if (warnCount) {
      parts.push(formatCountRu(warnCount, 'предупреждение', 'предупреждения', 'предупреждений'));
    }
    if (infoCount) {
      parts.push(formatCountRu(infoCount, 'замечание', 'замечания', 'замечаний'));
    }
    return parts.join(', ');
  }, [errorCount, warnCount, infoCount]);

  useEffect(() => {
    if (!sections.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !sections.some((s, i) => sectionKey(s, i) === selectedId)) {
      setSelectedId(sectionKey(sections[0], 0));
    }
  }, [sections, selectedId]);

  useEffect(() => {
    (async () => {
      const m: ToolMap = {};
      const list: Tool[] = [];
      for (let page = 1; page <= 20; page++) {
        try {
          const r = await toolsApi.list(tenantId, page, 100);
          r.items.forEach((t) => { m[t.name] = t; list.push(t); });
          if (r.items.length < 100) break;
        } catch { break; }
      }
      setToolMap(m);
      setToolsList(list);
    })();
  }, [tenantId]);

  const setSections = (secs: OntologySection[]) => { setTouched(true); onChange({ version: 1, sections: secs }); };
  const patchSection = (i: number, s: OntologySection) => setSections(sections.map((x, k) => k === i ? s : x));
  const groupByType = () => {
    const next = sortOntologySectionsByType(sections);
    setSections(next);
    if (selectedId) {
      const ni = next.findIndex((s, i) => sectionKey(s, i) === selectedId || s.id === selectedId);
      if (ni >= 0) setSelectedId(sectionKey(next[ni], ni));
    }
    notifications.show({ color: 'blue', message: 'Секции сгруппированы по типу' });
  };
  const reorderSection = useCallback((from: number, to: number) => {
    const moved = sections[from];
    if (!moved) return;
    const next = moveOntologySection(sections, from, to);
    setSections(next);
    const ni = next.indexOf(moved);
    if (ni >= 0) setSelectedId(sectionKey(moved, ni));
  }, [sections]);

  const addSection = (type: string) => {
    const sec = emptySection(type);
    setSections([...sections, sec]);
    setSelectedId(sectionKey(sec, sections.length));
    setMode('structured');
  };

  const removeSection = (index: number) => {
    const sec = sections[index];
    const summary = sec ? sectionSummary(sec) : '';
    const isEmpty = !sec || /^(0 |пусто$)/.test(summary);
    if (!isEmpty) {
      const name = (sec as any).title || SECTION_META[sec.type]?.label || sec.type;
      if (!window.confirm(`Удалить секцию «${name}» (${summary})? Действие нельзя отменить до сохранения.`)) return;
    }
    const next = sections.filter((_, k) => k !== index);
    setSections(next);
    if (next.length) setSelectedId(sectionKey(next[Math.min(index, next.length - 1)], Math.min(index, next.length - 1)));
    else setSelectedId(null);
  };

  const copySection = (index: number) => {
    const copy = duplicateSection(sections[index]);
    setSections([...sections.slice(0, index + 1), copy, ...sections.slice(index + 1)]);
    setSelectedId(sectionKey(copy, index + 1));
  };

  const applyOntologyChange = (next: OntologyJson, focusSectionId?: string | null) => {
    setTouched(true);
    onChange(next);
    setMode('structured');
    if (focusSectionId) setSelectedId(focusSectionId);
    else if (next.sections[0]) setSelectedId(sectionKey(next.sections[0], 0));
  };

  const jumpToIssue = (issue: OntologyIssue) => {
    if (!issueNavigable(issue)) {
      notifications.show({ color: 'yellow', message: 'Нет привязки к секции для перехода' });
      return;
    }
    setSelectedId(issue.sectionId!);
    setMode('structured');
    setIssueFocus({
      sectionId: issue.sectionId!,
      rowIndex: issue.rowIndex,
      logicNodeId: issue.logicNodeId,
      logicFlowId: issue.logicFlowId,
    });
    setLinterExpanded(false);
  };

  useEffect(() => {
    if (!issueFocus) return;
    const t = window.setTimeout(() => setIssueFocus(null), 4000);
    return () => window.clearTimeout(t);
  }, [issueFocus]);

  const selectedIndex = sections.findIndex((s, i) => sectionKey(s, i) === selectedId);
  const selectedSection = selectedIndex >= 0 ? sections[selectedIndex] : null;

  const sectionNavItems = useMemo(
    () => sections.map((sec, i) => {
      const key = sectionKey(sec, i);
      const meta = SECTION_META[sec.type];
      const prevType = i > 0 ? sections[i - 1].type : null;
      return {
        id: key,
        orderIndex: i,
        typeHeader: sec.type !== prevType ? meta?.label : undefined,
        label: (sec as any).title || meta?.label || sec.type,
        description: sectionSummary(sec),
        icon: <Text size="sm">{meta?.icon}</Text>,
        active: selectedId === key,
        onClick: () => setSelectedId(key),
        rightSection: (
          <ActionIcon
            size="xs"
            variant="subtle"
            color="red"
            onClick={(e) => { e.stopPropagation(); removeSection(i); }}
          >
            <IconTrash size={12} />
          </ActionIcon>
        ),
      };
    }),
    [sections, selectedId],
  );

  const sectionNavFooter = (
    <Stack gap={6}>
      <Menu>
        <Menu.Target>
          <Button size="xs" variant="light" fullWidth leftSection={<IconPlus size={14} />}>Добавить</Button>
        </Menu.Target>
        <Menu.Dropdown>
          {Object.entries(SECTION_META).map(([t, m]) => (
            <Menu.Item key={t} onClick={() => addSection(t)}>
              <Group gap={6} wrap="nowrap">
                <Text>{m.icon}</Text>
                <div>
                  <Text size="sm">{m.label}</Text>
                  <Text size="xs" c="dimmed">{m.description}</Text>
                </div>
              </Group>
            </Menu.Item>
          ))}
        </Menu.Dropdown>
      </Menu>
      {sections.length > 1 && (
        <>
          <Button size="xs" variant="light" fullWidth leftSection={<IconArrowsSort size={14} />} onClick={groupByType}>
            Сгруппировать по типу
          </Button>
          <Text size="xs" c="dimmed" ta="center">или перетащите за ⋮</Text>
        </>
      )}
    </Stack>
  );

  const doImport = async () => {
    setImporting(true);
    try {
      const r = importSourceText != null
        ? await shellApi.ontologyParse(tenantId, importSourceText)
        : await shellApi.ontologyImport(tenantId);
      setTouched(true);
      onChange(r.ontology_json);
      setMode('structured');
      if (r.ontology_json.sections[0]) {
        setSelectedId(sectionKey(r.ontology_json.sections[0], 0));
      }
      notifications.show({ color: 'green', message: `Импортировано секций: ${r.ontology_json.sections.length}` });
    } catch (e: any) { notifications.show({ color: 'red', message: e?.response?.data?.detail || 'Ошибка импорта' }); }
    finally { setImporting(false); }
  };

  const showPreview = async () => {
    try { const r = await shellApi.ontologyPreview(tenantId, value); setPreview(r.text || '(пусто)'); }
    catch { notifications.show({ color: 'red', message: 'Ошибка предпросмотра' }); }
  };

  const saveOntology = async () => {
    setSaving(true);
    try {
      await shellApi.update(tenantId, { ontology_json: value });
      setTouched(false);
      notifications.show({ color: 'green', message: 'Онтология сохранена (текст для модели перегенерирован)' });
    } catch (e: any) {
      notifications.show({ color: 'red', message: e?.response?.data?.detail || 'Ошибка сохранения' });
    } finally { setSaving(false); }
  };

  const showDiff = async () => {
    try {
      const r = await shellApi.ontologyPreview(tenantId, value);
      setDiffText(r.text || '');
    } catch {
      notifications.show({ color: 'red', message: 'Ошибка сравнения' });
    }
  };

  const quickFillFromTools = () => {
    setImportOpen(true);
  };

  return (
    <Card withBorder padding="md" radius="md"
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.target as HTMLElement).tagName !== 'TEXTAREA') e.preventDefault();
      }}>
      <Stack gap="md">
        <Alert variant="light" color="blue" icon={<IconBook size={16} />} py="xs">
          Онтология — структурированные знания о вашей предметной области. Редактируете секции здесь — модель получает сгенерированный текст при сохранении.
        </Alert>

        <Group justify="space-between" wrap="wrap">
          <Group gap="sm">
            <SegmentedControl size="sm" value={mode} onChange={(v) => setMode(v as 'structured' | 'text')}
              data={[
                { label: 'Редактор секций', value: 'structured' },
                { label: 'Текст для LLM', value: 'text' },
              ]} />
            <Button size="xs" leftSection={<IconDeviceFloppy size={14} />}
              color={dirty ? 'blue' : 'gray'} variant={dirty ? 'filled' : 'light'}
              loading={saving} disabled={!dirty} onClick={saveOntology}>
              {dirty ? 'Сохранить' : 'Сохранено'}
            </Button>
            {dirty && <Badge size="sm" color="orange" variant="dot">несохранённые изменения</Badge>}
          </Group>
          <Group gap="xs">
            <Menu position="bottom-end">
              <Menu.Target>
                <Button size="xs" variant="light" color="grape" leftSection={<IconSparkles size={14} />} rightSection={<IconChevronDown size={12} />}>ИИ-помощь</Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconWand size={14} />} onClick={() => setWizardOpen(true)}>
                  Мастер «с нуля» <Text span size="xs" c="dimmed">— собрать по шагам</Text>
                </Menu.Item>
                <Menu.Item leftSection={<IconSparkles size={14} />} onClick={() => setSuggestOpen(true)}>
                  LLM-патчи <Text span size="xs" c="dimmed">— улучшения к текущей</Text>
                </Menu.Item>
                <Menu.Item leftSection={<IconBug size={14} />} onClick={() => setAuditOpen(true)}>
                  Аудит tools <Text span size="xs" c="dimmed">— проверка вызовов на кейсах</Text>
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <Menu position="bottom-end">
              <Menu.Target>
                <Button size="xs" variant="light" leftSection={<IconDatabaseImport size={14} />} rightSection={<IconChevronDown size={12} />}>Импорт</Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconDatabaseImport size={14} />} onClick={() => setImportOpen(true)}>Из tools / готовых блоков…</Menu.Item>
                <Menu.Item leftSection={<IconDownload size={14} />} onClick={doImport}>Распарсить сохранённый текст</Menu.Item>
              </Menu.Dropdown>
            </Menu>
            <Menu position="bottom-end">
              <Menu.Target>
                <Button size="xs" variant="subtle" leftSection={<IconEye size={14} />} rightSection={<IconChevronDown size={12} />}>Просмотр</Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconEye size={14} />} onClick={showPreview}>Предпросмотр <Text span size="xs" c="dimmed">— текст для модели</Text></Menu.Item>
                <Menu.Item leftSection={<IconGitCompare size={14} />} disabled={!value?.sections?.length} onClick={showDiff}>Сравнить с сохранённым</Menu.Item>
                <Menu.Item leftSection={<IconHistory size={14} />} onClick={() => setHistoryOpen(true)}>История версий</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>

        {linterIssues.length > 0 && (
          <Alert
            variant="light"
            color={errorCount ? 'red' : warnCount ? 'yellow' : 'blue'}
            icon={<IconAlertTriangle size={16} />}
            p="sm"
          >
            <UnstyledButton
              w="100%"
              onClick={() => setLinterExpanded((v) => !v)}
              style={{ textAlign: 'left' }}
            >
              <Group justify="space-between" wrap="nowrap" gap="xs">
                <Text size="sm" fw={500}>
                  Проверка онтологии: {validationSummary}
                </Text>
                <IconChevronDown
                  size={16}
                  style={{
                    flexShrink: 0,
                    transform: linterExpanded ? 'rotate(180deg)' : undefined,
                    transition: 'transform 150ms ease',
                  }}
                />
              </Group>
            </UnstyledButton>
            <Collapse expanded={linterExpanded}>
              <ScrollArea.Autosize mah={280} mt="sm">
                <Stack gap={6}>
                  {linterIssues.map((issue, i) => {
                    const navigable = issueNavigable(issue);
                    return (
                      <Group
                        key={`${issue.severity}-${issue.sectionId ?? 'global'}-${issue.rowIndex ?? ''}-${issue.logicNodeId ?? ''}-${issue.toolName ?? ''}-${i}`}
                        gap={6}
                        wrap="nowrap"
                        align="flex-start"
                        onClick={() => navigable && jumpToIssue(issue)}
                        style={{
                          cursor: navigable ? 'pointer' : 'default',
                          borderRadius: 4,
                          padding: '2px 4px',
                        }}
                      >
                        <Badge
                          size="xs"
                          variant="light"
                          color={issue.severity === 'error' ? 'red' : issue.severity === 'warning' ? 'yellow' : 'gray'}
                          style={{ flexShrink: 0, marginTop: 1 }}
                        >
                          {SEVERITY_LABELS[issue.severity]}
                        </Badge>
                        <Text
                          size="xs"
                          c={issue.severity === 'error' ? 'red' : issue.severity === 'warning' ? 'orange' : 'dimmed'}
                          style={navigable ? { textDecoration: 'underline dotted', textUnderlineOffset: 2 } : undefined}
                        >
                          {issue.sectionTitle ? `[${issue.sectionTitle}] ` : ''}{issue.message}
                        </Text>
                      </Group>
                    );
                  })}
                </Stack>
              </ScrollArea.Autosize>
            </Collapse>
          </Alert>
        )}

        {mode === 'text' && (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              Это итоговый текст, который видит модель. Редактирование — во вкладке «Редактор секций»; здесь только просмотр.
            </Text>
            <ScrollArea.Autosize mah={320}>
              <Code block style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>
                {fallbackText || '(пусто — добавьте секции или импортируйте из текста)'}
              </Code>
            </ScrollArea.Autosize>
          </Stack>
        )}

        {mode === 'structured' && sections.length === 0 && (
          <Stack gap="md" align="center" py="lg">
            <IconLayoutList size={40} stroke={1.2} style={{ opacity: 0.35 }} />
            <div style={{ textAlign: 'center' }}>
              <Text fw={500}>Онтология пока пуста</Text>
              <Text size="sm" c="dimmed" maw={420} mt={4}>
                Начните с глоссария терминов или импортируйте существующий текст. Можно добавить сущности, связи, сценарии логики и примеры запросов.
              </Text>
            </div>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs" maw={480}>
              <Button variant="light" leftSection={<IconWand size={14} />} onClick={() => setWizardOpen(true)}>Мастер «С нуля»</Button>
              <Button variant="light" leftSection={<span>📖</span>} onClick={() => addSection('glossary')}>Глоссарий терминов</Button>
              <Button variant="light" leftSection={<span>🗃️</span>} onClick={() => addSection('entities')}>Сущности данных</Button>
              <Button variant="light" leftSection={<IconDatabaseImport size={14} />} onClick={quickFillFromTools}>Заполнить из tools</Button>
              <Button variant="default" leftSection={<IconDownload size={14} />} loading={importing} onClick={doImport}>Импорт из текста</Button>
              <Button variant="subtle" leftSection={<span>📝</span>} onClick={() => addSection('freeform')}>Свободный текст</Button>
            </SimpleGrid>
          </Stack>
        )}

        {mode === 'structured' && sections.length > 0 && (
          <Stack gap="md">
            <Box hiddenFrom="sm">
              <Select
                label="Секция онтологии"
                value={selectedId}
                onChange={(v) => v && setSelectedId(v)}
                data={sections.map((sec, i) => ({
                  value: sectionKey(sec, i),
                  label: (sec as any).title || SECTION_META[sec.type]?.label || sec.type,
                }))}
                allowDeselect={false}
              />
            </Box>
            <LocalCollapsibleSectionNav
              title={`Секции (${sections.length})`}
              items={sectionNavItems}
              footer={sectionNavFooter}
              onReorder={sections.length > 1 ? reorderSection : undefined}
            >
              {selectedSection && selectedIndex >= 0 && (
                <Stack gap="md">
                  <Group gap="sm">
                    <Text size="xl">{SECTION_META[selectedSection.type]?.icon}</Text>
                    <div style={{ flex: 1 }}>
                      <TextInput
                        label="Название секции"
                        value={(selectedSection as any).title || ''}
                        onChange={(e) => patchSection(selectedIndex, { ...(selectedSection as any), title: e.currentTarget.value })}
                        placeholder={SECTION_META[selectedSection.type]?.label}
                      />
                    </div>
                    <Tooltip label="Дублировать секцию">
                      <ActionIcon variant="light" mt={22} onClick={() => copySection(selectedIndex)}>
                        <IconCopy size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Badge variant="light" mt={22}>{SECTION_META[selectedSection.type]?.label}</Badge>
                  </Group>
                  <SectionEditor
                    section={selectedSection}
                    onChange={(s) => patchSection(selectedIndex, s)}
                    toolNames={toolNames}
                    toolMap={toolMap}
                    sectionId={selectedId || undefined}
                    issues={linterIssues}
                    issueFocus={issueFocus}
                  />
                </Stack>
              )}
            </LocalCollapsibleSectionNav>
          </Stack>
        )}
      </Stack>

      <Modal opened={!!preview} onClose={() => setPreview(null)} size="lg" title="Предпросмотр — что увидит модель">
        <ScrollArea.Autosize mah={500}><Code block style={{ whiteSpace: 'pre-wrap' }}>{preview}</Code></ScrollArea.Autosize>
      </Modal>

      <Modal opened={diffText !== null} onClose={() => setDiffText(null)} size="xl" title="Diff: сохранённый текст → черновик из секций">
        {(() => {
          const rows = diffText !== null ? diffLines(fallbackText || '', diffText || '') : [];
          const adds = rows.filter((r) => r.t === 'add').length;
          const dels = rows.filter((r) => r.t === 'del').length;
          return (
            <Stack gap="xs">
              <Group gap="md">
                <Badge color="green" variant="light">+{adds} строк</Badge>
                <Badge color="red" variant="light">−{dels} строк</Badge>
                {adds === 0 && dels === 0 && <Text size="sm" c="dimmed">изменений нет — черновик совпадает с сохранённым</Text>}
              </Group>
              <ScrollArea.Autosize mah={460}>
                <Code block style={{ fontSize: 11, whiteSpace: 'pre-wrap', padding: 0, background: 'transparent' }}>
                  {rows.map((r, k) => (
                    <div key={k} style={{
                      padding: '0 6px',
                      background: r.t === 'add' ? 'rgba(46,160,67,0.18)' : r.t === 'del' ? 'rgba(248,81,73,0.18)' : 'transparent',
                      color: r.t === 'add' ? '#3fb950' : r.t === 'del' ? '#f85149' : undefined,
                    }}>
                      {(r.t === 'add' ? '+ ' : r.t === 'del' ? '− ' : '  ') + (r.s || ' ')}
                    </div>
                  ))}
                </Code>
              </ScrollArea.Autosize>
            </Stack>
          );
        })()}
      </Modal>

      <OntologyImportModal
        opened={importOpen}
        onClose={() => setImportOpen(false)}
        tenantId={tenantId}
        tools={toolsList}
        ontology={value}
        onApply={(next) => applyOntologyChange(next)}
      />
      <OntologyWizardModal
        opened={wizardOpen}
        onClose={() => setWizardOpen(false)}
        tools={toolsList}
        ontology={value}
        onApply={applyOntologyChange}
      />
      <OntologySuggestModal
        opened={suggestOpen}
        onClose={() => { setSuggestOpen(false); setSuggestAuditCases(null); setSuggestAuditTask(null); }}
        tenantId={tenantId}
        tools={toolsList}
        ontology={value}
        onApply={applyOntologyChange}
        initialAuditCases={suggestAuditCases}
        initialTask={suggestAuditTask}
      />
      <OntologyToolCallAuditModal
        opened={auditOpen}
        onClose={() => setAuditOpen(false)}
        tenantId={tenantId}
        ontology={value}
        onApply={applyOntologyChange}
        onSuggestWithAudit={(cases, task) => {
          setSuggestAuditCases(cases);
          setSuggestAuditTask(task);
          setSuggestOpen(true);
        }}
      />
      <OntologySectionHistoryModal
        opened={historyOpen}
        onClose={() => setHistoryOpen(false)}
        tenantId={tenantId}
        ontology={value}
        onApply={applyOntologyChange}
      />
    </Card>
  );
}
