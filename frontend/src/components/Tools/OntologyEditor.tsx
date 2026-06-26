import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Stack, Group, Button, Accordion, TextInput, Textarea, ActionIcon, Badge, Text, Select,
  Menu, Tooltip, Modal, Code, ScrollArea, Popover, SegmentedControl, Checkbox,
} from '@mantine/core';
import {
  IconPlus, IconTrash, IconEye, IconDownload, IconArrowsSplit,
  IconBolt, IconNote, IconFlag, IconInfoCircle, IconArrowsMaximize, IconArrowsSort,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import {
  ReactFlow, Background, Controls, MiniMap, addEdge, useNodesState, useEdgesState,
  type Node, type Edge, type Connection, Handle, Position, ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { shellApi, toolsApi } from '../../shared/api/endpoints';
import type { OntologyJson, OntologySection, LogicGraph, LogicNode, LogicFlow, Tool } from '../../shared/api/types';

const SECTION_META: Record<string, { label: string; icon: string }> = {
  glossary: { label: '📖 Глоссарий', icon: '📖' },
  entities: { label: '🗃️ Сущности', icon: '🗃️' },
  relations: { label: '🔗 Связи', icon: '🔗' },
  logic: { label: '⚖️ Логика (граф)', icon: '⚖️' },
  examples: { label: '💬 Примеры', icon: '💬' },
  freeform: { label: '📝 Свободный текст', icon: '📝' },
};

const uid = () => `n${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;

function emptySection(type: string): OntologySection {
  const base = { id: uid(), title: SECTION_META[type]?.label.replace(/^\S+\s/, '') || type };
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
  const { node, onPatch, onStart, onExpand, isStart, toolNames, toolMap } = data;
  const color = node.type === 'condition' ? '#fd7e14' : node.type === 'action' ? '#4263eb' : '#868e96';
  return (
    <div style={{ border: `2px solid ${color}`, borderRadius: 8, background: 'var(--mantine-color-body)', minWidth: 210, padding: 8 }}>
      <Handle type="target" position={Position.Top} />
      <Group justify="space-between" gap={4} mb={4}>
        <Badge size="xs" color={node.type === 'condition' ? 'orange' : node.type === 'action' ? 'indigo' : 'gray'} variant="filled">
          {node.type === 'condition' ? 'условие' : node.type === 'action' ? 'действие' : 'заметка'}
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
            <Select size="xs" className="nodrag" searchable placeholder="тул (поиск)" data={toolNames} value={node.tool || null}
              onChange={(v) => onPatch(id, { tool: v || undefined })} style={{ flex: 1 }} maxDropdownHeight={200} clearable />
            <Popover width={360} position="bottom-end" withArrow>
              <Popover.Target><ActionIcon size="sm" variant="subtle" className="nodrag" disabled={!node.tool}><IconInfoCircle size={14} /></ActionIcon></Popover.Target>
              <Popover.Dropdown className="nodrag">
                <ParamPicker tool={toolMap[node.tool || '']} onInsert={(s) => onPatch(id, { hint: s })} />
              </Popover.Dropdown>
            </Popover>
          </Group>
          <Textarea size="xs" className="nodrag" placeholder="подсказка по аргументам (пример)" autosize minRows={1} maxRows={3}
            value={node.hint || ''} onChange={(e) => onPatch(id, { hint: e.currentTarget.value })} />
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

function LogicGraphEditor({ graph, onChange, toolNames, toolMap }:
  { graph: LogicGraph; onChange: (g: LogicGraph) => void; toolNames: string[]; toolMap: ToolMap }) {
  const init = useMemo(() => graphToFlow(graph), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [nodes, setNodes, onNodesChange] = useNodesState(init.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(init.edges);
  const [start, setStart] = useState<string | null>(graph.start);
  const [selEdge, setSelEdge] = useState<Edge | null>(null);
  const [editId, setEditId] = useState<string | null>(null);

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

  // inject callbacks into node data
  const rfNodes = nodes.map((n) => ({
    ...n, data: { ...n.data, onPatch: patchNode, onStart: (id: string) => { setStart(id); sync(nodes, edges, id); },
      onExpand: setEditId, isStart: n.id === start, toolNames, toolMap },
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
    <Stack gap={4}>
      <Group gap={4}>
        <Button size="compact-xs" variant="light" color="orange" leftSection={<IconArrowsSplit size={12} />} onClick={() => addNode('condition')}>условие</Button>
        <Button size="compact-xs" variant="light" color="indigo" leftSection={<IconBolt size={12} />} onClick={() => addNode('action')}>действие</Button>
        <Button size="compact-xs" variant="light" color="gray" leftSection={<IconNote size={12} />} onClick={() => addNode('note')}>заметка</Button>
        <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconTrash size={12} />} onClick={delSelected}>удалить выбранное</Button>
        {selEdge && (
          <TextInput size="xs" w={180} placeholder="метка ветки (case)" value={(selEdge.label as string) || ''}
            onChange={(e) => setEdgeCase(e.currentTarget.value)} />
        )}
        <Text size="xs" c="dimmed">тяни от низа узла к верху другого — это ветка/переход</Text>
      </Group>
      <div style={{ height: 420, border: '1px solid var(--mantine-color-default-border)', borderRadius: 8 }}>
        <ReactFlowProvider>
          <ReactFlow nodes={rfNodes} edges={edges} nodeTypes={nodeTypes}
            onNodesChange={(c) => { onNodesChange(c); }}
            onNodeDragStop={() => sync(nodes, edges, start)}
            onEdgesChange={(c) => { onEdgesChange(c); }}
            onConnect={onConnect}
            onEdgeClick={(_, e) => setSelEdge(e)}
            fitView proOptions={{ hideAttribution: true }}>
            <Background /><Controls /><MiniMap pannable zoomable />
          </ReactFlow>
        </ReactFlowProvider>
      </div>

      <Modal opened={!!editId} onClose={() => setEditId(null)} size="lg"
        title={en ? (en.type === 'condition' ? 'Условие' : en.type === 'action' ? 'Действие' : 'Заметка') : ''}>
        {en && editId && (
          <Stack gap="sm">
            <TextInput label={en.type === 'condition' ? 'Условие' : 'Метка'} value={en.label || ''}
              onChange={(e) => patchNode(editId, { label: e.currentTarget.value })} />
            {en.type === 'action' && (
              <>
                <Select label="Инструмент (поиск)" searchable clearable data={toolNames} value={en.tool || null}
                  onChange={(v) => patchNode(editId, { tool: v || undefined })} maxDropdownHeight={260} />
                {en.tool && (
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

function LogicFlowsEditor({ flows, onChange, toolNames, toolMap }:
  { flows: LogicFlow[]; onChange: (f: LogicFlow[]) => void; toolNames: string[]; toolMap: ToolMap }) {
  const [sel, setSel] = useState<string | null>(flows[0]?.id || null);
  const cur = flows.find((f) => f.id === sel) || flows[0] || null;
  const addFlow = () => {
    const nf: LogicFlow = { id: uid(), name: `Сценарий ${flows.length + 1}`, graph: { start: null, nodes: {} } };
    onChange([...flows, nf]); setSel(nf.id);
  };
  const delFlow = (id: string) => {
    const next = flows.filter((f) => f.id !== id);
    onChange(next); setSel(next[0]?.id || null);
  };
  return (
    <Stack gap={6}>
      <Group gap={4}>
        {flows.map((f) => (
          <Button key={f.id} size="compact-xs" variant={f.id === cur?.id ? 'filled' : 'light'} color="grape"
            onClick={() => setSel(f.id)}>{f.name || 'без имени'}</Button>
        ))}
        <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={12} />} onClick={addFlow}>сценарий</Button>
      </Group>
      {cur && (
        <>
          <Group gap={4}>
            <TextInput size="xs" w={300} placeholder="название сценария (напр. «MAC-таблица на свиче»)" value={cur.name}
              onChange={(e) => onChange(flows.map((f) => f.id === cur.id ? { ...f, name: e.currentTarget.value } : f))} />
            <Tooltip label="Удалить сценарий"><ActionIcon variant="subtle" color="red" onClick={() => delFlow(cur.id)}><IconTrash size={14} /></ActionIcon></Tooltip>
          </Group>
          <LogicGraphEditor key={cur.id} graph={cur.graph} toolNames={toolNames} toolMap={toolMap}
            onChange={(graph) => onChange(flows.map((f) => f.id === cur.id ? { ...f, graph } : f))} />
        </>
      )}
    </Stack>
  );
}

/* ----------------------- per-type section editors ----------------------- */

function RowList<T>({ rows, onChange, render, empty, addLabel, blank }:
  { rows: T[]; onChange: (r: T[]) => void; render: (r: T, set: (p: Partial<T>) => void) => React.ReactNode;
    empty: string; addLabel: string; blank: () => T }) {
  return (
    <Stack gap={4}>
      {rows.length === 0 && <Text size="xs" c="dimmed">{empty}</Text>}
      {rows.map((r, i) => (
        <Group key={i} gap={4} wrap="nowrap" align="flex-start">
          {render(r, (p) => onChange(rows.map((x, k) => k === i ? { ...x, ...p } : x)))}
          <ActionIcon variant="subtle" color="red" onClick={() => onChange(rows.filter((_, k) => k !== i))}><IconTrash size={14} /></ActionIcon>
        </Group>
      ))}
      <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={12} />} onClick={() => onChange([...rows, blank()])} style={{ alignSelf: 'flex-start' }}>{addLabel}</Button>
    </Stack>
  );
}

function SectionEditor({ section, onChange, toolNames, toolMap }:
  { section: OntologySection; onChange: (s: OntologySection) => void; toolNames: string[]; toolMap: ToolMap }) {
  const s = section as any;
  if (s.type === 'glossary')
    return <RowList rows={s.items} onChange={(items) => onChange({ ...s, items })} empty="нет терминов" addLabel="термин" blank={() => ({ term: '', definition: '' })}
      render={(r: any, set) => (<>
        <TextInput size="xs" w={160} placeholder="термин" value={r.term} onChange={(e) => set({ term: e.currentTarget.value })} />
        <TextInput size="xs" style={{ flex: 1 }} placeholder="определение" value={r.definition} onChange={(e) => set({ definition: e.currentTarget.value })} />
      </>)} />;
  if (s.type === 'relations')
    return <RowList rows={s.items} onChange={(items) => onChange({ ...s, items })} empty="нет связей" addLabel="связь" blank={() => ({ from: '', relation: '', to: '' })}
      render={(r: any, set) => (<>
        <TextInput size="xs" style={{ flex: 1 }} placeholder="от" value={r.from} onChange={(e) => set({ from: e.currentTarget.value })} />
        <TextInput size="xs" w={140} placeholder="отношение" value={r.relation} onChange={(e) => set({ relation: e.currentTarget.value })} />
        <TextInput size="xs" style={{ flex: 1 }} placeholder="к" value={r.to} onChange={(e) => set({ to: e.currentTarget.value })} />
      </>)} />;
  if (s.type === 'examples')
    return <RowList rows={s.items} onChange={(items) => onChange({ ...s, items })} empty="нет примеров" addLabel="пример" blank={() => ({ query: '', expected_tool: '', note: '' })}
      render={(r: any, set) => (<>
        <TextInput size="xs" style={{ flex: 1 }} placeholder="запрос" value={r.query} onChange={(e) => set({ query: e.currentTarget.value })} />
        <Select size="xs" w={170} searchable clearable placeholder="тул" data={toolNames} value={r.expected_tool || null} onChange={(v) => set({ expected_tool: v || '' })} />
        <TextInput size="xs" w={140} placeholder="заметка" value={r.note} onChange={(e) => set({ note: e.currentTarget.value })} />
      </>)} />;
  if (s.type === 'freeform')
    return <Textarea autosize minRows={3} placeholder="текст" value={s.text} onChange={(e) => onChange({ ...s, text: e.currentTarget.value })} />;
  if (s.type === 'entities')
    return (
      <Stack gap={6}>
        {(s.entities || []).map((ent: any, ei: number) => (
          <Stack key={ei} gap={2} p={6} style={{ border: '1px solid var(--mantine-color-default-border)', borderRadius: 6 }}>
            <Group gap={4}>
              <TextInput size="xs" w={220} placeholder="сущность" value={ent.name}
                onChange={(e) => onChange({ ...s, entities: s.entities.map((x: any, k: number) => k === ei ? { ...x, name: e.currentTarget.value } : x) })} />
              <ActionIcon variant="subtle" color="red" onClick={() => onChange({ ...s, entities: s.entities.filter((_: any, k: number) => k !== ei) })}><IconTrash size={14} /></ActionIcon>
            </Group>
            <RowList rows={ent.fields} empty="нет полей" addLabel="поле" blank={() => ({ name: '', type: 'string', description: '' })}
              onChange={(fields) => onChange({ ...s, entities: s.entities.map((x: any, k: number) => k === ei ? { ...x, fields } : x) })}
              render={(f: any, set) => (<>
                <TextInput size="xs" w={150} placeholder="поле" value={f.name} onChange={(e) => set({ name: e.currentTarget.value })} />
                <Select size="xs" w={110} data={['string', 'integer', 'number', 'boolean', 'date', 'array', 'object']} value={f.type} onChange={(v) => set({ type: v || 'string' })} />
                <TextInput size="xs" style={{ flex: 1 }} placeholder="описание" value={f.description} onChange={(e) => set({ description: e.currentTarget.value })} />
              </>)} />
          </Stack>
        ))}
        <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={12} />} style={{ alignSelf: 'flex-start' }}
          onClick={() => onChange({ ...s, entities: [...(s.entities || []), { name: 'Сущность', fields: [] }] })}>сущность</Button>
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
}

export function OntologyEditor({ tenantId, value, fallbackText, onChange }: Props) {
  const [mode, setMode] = useState<'structured' | 'text'>(value?.sections?.length ? 'structured' : 'text');
  const [toolMap, setToolMap] = useState<ToolMap>({});
  const [preview, setPreview] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const sections = value?.sections || [];
  const toolNames = useMemo(() => Object.keys(toolMap).sort(), [toolMap]);

  useEffect(() => {
    (async () => {
      const m: ToolMap = {};
      for (let page = 1; page <= 20; page++) {
        try {
          const r = await toolsApi.list(tenantId, page, 100); // endpoint caps page_size at 100
          r.items.forEach((t) => { m[t.name] = t; });
          if (r.items.length < 100) break;
        } catch { break; }
      }
      setToolMap(m);
    })();
  }, [tenantId]);

  const setSections = (secs: OntologySection[]) => onChange({ version: 1, sections: secs });
  const patchSection = (i: number, s: OntologySection) => setSections(sections.map((x, k) => k === i ? s : x));
  const TYPE_ORDER = ['glossary', 'entities', 'relations', 'logic', 'examples', 'freeform'];
  const groupByType = () => setSections(
    [...sections].sort((a, b) => TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type)));

  const doImport = async () => {
    setImporting(true);
    try {
      const r = await shellApi.ontologyImport(tenantId);
      onChange(r.ontology_json);
      setMode('structured');
      notifications.show({ color: 'green', message: `Импортировано секций: ${r.ontology_json.sections.length}` });
    } catch (e: any) { notifications.show({ color: 'red', message: e?.response?.data?.detail || 'Ошибка импорта' }); }
    finally { setImporting(false); }
  };

  const showPreview = async () => {
    try { const r = await shellApi.ontologyPreview(tenantId, value); setPreview(r.text || '(пусто)'); }
    catch (e: any) { notifications.show({ color: 'red', message: 'Ошибка предпросмотра' }); }
  };

  return (
    <Stack gap="xs"
      onKeyDown={(e) => {
        // The editor lives inside the shell-settings <form>; Enter in an input
        // would implicitly submit (save everything). Block it (textarea keeps newlines).
        if (e.key === 'Enter' && (e.target as HTMLElement).tagName !== 'TEXTAREA') e.preventDefault();
      }}>
      <Group justify="space-between">
        <SegmentedControl size="xs" value={mode} onChange={(v) => setMode(v as any)}
          data={[{ label: 'Структура', value: 'structured' }, { label: 'Текст (LLM)', value: 'text' }]} />
        <Group gap={4}>
          <Tooltip label="Распарсить текущий текст онтологии в структуру">
            <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} loading={importing} onClick={doImport}>Импорт из текста</Button>
          </Tooltip>
          <Button size="xs" variant="light" leftSection={<IconEye size={14} />} onClick={showPreview}>Предпросмотр для LLM</Button>
        </Group>
      </Group>

      {mode === 'text' && (
        <Stack gap={4}>
          <Text size="xs" c="dimmed">Это итоговый текст, который видит модель (генерируется из структуры). Прямую правочную онтологию редактируй в «Структуре».</Text>
          <Code block style={{ whiteSpace: 'pre-wrap' }}>{fallbackText || '(пусто — заполни структуру или импортируй)'}</Code>
        </Stack>
      )}

      {mode === 'structured' && (
        <>
          <Accordion variant="separated" multiple chevronPosition="left">
            {sections.map((sec, i) => (
              <Accordion.Item key={sec.id || i} value={String(sec.id || i)}>
                <Accordion.Control>
                  <Group gap={6}>
                    <Text>{SECTION_META[sec.type]?.icon}</Text>
                    <TextInput size="xs" variant="unstyled" value={(sec as any).title || ''} onClick={(e) => e.stopPropagation()}
                      onChange={(e) => patchSection(i, { ...(sec as any), title: e.currentTarget.value })} placeholder="заголовок секции" w={300} />
                    <Badge size="xs" variant="light">{sec.type}</Badge>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Group justify="flex-end" mb={4}>
                    <ActionIcon size="sm" variant="subtle" color="red" onClick={() => setSections(sections.filter((_, k) => k !== i))}><IconTrash size={14} /></ActionIcon>
                  </Group>
                  <SectionEditor section={sec} onChange={(s) => patchSection(i, s)} toolNames={toolNames} toolMap={toolMap} />
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>
          <Group gap={6}>
            <Menu>
              <Menu.Target><Button size="xs" variant="light" leftSection={<IconPlus size={14} />}>Добавить секцию</Button></Menu.Target>
              <Menu.Dropdown>
                {Object.entries(SECTION_META).map(([t, m]) => (
                  <Menu.Item key={t} onClick={() => setSections([...sections, emptySection(t)])}>{m.label}</Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
            {sections.length > 1 && (
              <Tooltip label="Сгруппировать одинаковые секции вместе (глоссарий → сущности → связи → логика → примеры → текст)">
                <Button size="xs" variant="subtle" leftSection={<IconArrowsSort size={14} />} onClick={groupByType}>Группировать по типу</Button>
              </Tooltip>
            )}
          </Group>
        </>
      )}

      <Modal opened={!!preview} onClose={() => setPreview(null)} size="lg" title="Предпросмотр — что уйдёт модели">
        <ScrollArea.Autosize mah={500}><Code block style={{ whiteSpace: 'pre-wrap' }}>{preview}</Code></ScrollArea.Autosize>
      </Modal>
    </Stack>
  );
}
