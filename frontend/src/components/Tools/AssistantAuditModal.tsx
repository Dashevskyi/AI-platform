import { useEffect, useState, useCallback, Fragment } from 'react';
import {
  Modal, Button, Group, Stack, Text, Badge, Table, ScrollArea, Loader, Switch,
  TextInput, ActionIcon, Tooltip, NumberInput, Progress, Alert, Divider, Popover, Select,
} from '@mantine/core';
import {
  IconPlayerPlay, IconSearch, IconClipboardList, IconTrash, IconPlus, IconDownload, IconRefresh,
  IconArrowUp, IconArrowDown, IconX,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { auditSuiteApi, toolAuditApi, type AuditCaseRow } from '../../shared/api/endpoints';

interface Props {
  tenantId: string; assistantId: string; assistantName: string;
  opened: boolean; onClose: () => void;
}

function verdictBadge(c: AuditCaseRow) {
  const lr = c.last_result;
  if (!lr) return <Badge color="gray" variant="light">не прогнан</Badge>;
  const pct = Math.round(lr.pass_rate * 100);
  const color = lr.passed ? 'green' : pct > 0 ? 'yellow' : 'red';
  return <Badge color={color} variant="light">{Math.round(lr.pass_rate * lr.repeats)}/{lr.repeats}{lr.called.length ? ` · ${lr.called.join(',')}` : ' · ничего'}</Badge>;
}

// Ordered, reorderable list of expected tools (order = call order for multi-round).
function ToolsOrderEditor({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [add, setAdd] = useState('');
  const move = (i: number, d: number) => {
    const j = i + d; if (j < 0 || j >= value.length) return;
    const v = [...value]; [v[i], v[j]] = [v[j], v[i]]; onChange(v);
  };
  return (
    <Stack gap={2}>
      {value.map((t, i) => (
        <Group key={i} gap={2} wrap="nowrap">
          <Text size="xs" c="dimmed" w={12}>{i + 1}</Text>
          <Badge size="sm" variant="light" style={{ flex: 1, justifyContent: 'flex-start', textTransform: 'none' }}>{t}</Badge>
          <ActionIcon size="xs" variant="subtle" onClick={() => move(i, -1)} disabled={i === 0}><IconArrowUp size={12} /></ActionIcon>
          <ActionIcon size="xs" variant="subtle" onClick={() => move(i, 1)} disabled={i === value.length - 1}><IconArrowDown size={12} /></ActionIcon>
          <ActionIcon size="xs" variant="subtle" color="red" onClick={() => onChange(value.filter((_, k) => k !== i))}><IconX size={12} /></ActionIcon>
        </Group>
      ))}
      <TextInput size="xs" variant="filled" placeholder="+ тул (a|b = любой), Enter" value={add}
        onChange={(e) => setAdd(e.currentTarget.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && add.trim()) { onChange([...value, add.trim()]); setAdd(''); } }} />
    </Stack>
  );
}

// Per-case actor (client/operator + ids) — needed for forced-filter tools.
function ActorEditor({ value, onChange }: { value: AuditCaseRow['actor']; onChange: (a: AuditCaseRow['actor']) => void }) {
  const a = value || {};
  const summary = a.role === 'client' ? `клиент${a.external_id ? ':' + a.external_id : ''}` : (a.role || 'оператор');
  return (
    <Popover width={230} withArrow position="bottom-start">
      <Popover.Target><Button size="compact-xs" variant="default">{summary}</Button></Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Select size="xs" label="Роль" data={['operator', 'client']} value={a.role || 'operator'}
            onChange={(v) => onChange({ ...a, role: v || 'operator' })} />
          <TextInput size="xs" label="external_id" defaultValue={a.external_id || ''}
            onBlur={(e) => onChange({ ...a, external_id: e.currentTarget.value || undefined })} />
          <TextInput size="xs" label="phone" defaultValue={a.phone || ''}
            onBlur={(e) => onChange({ ...a, phone: e.currentTarget.value || undefined })} />
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

export function AssistantAuditModal({ tenantId, assistantId, assistantName, opened, onClose }: Props) {
  const [cases, setCases] = useState<AuditCaseRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [repeats, setRepeats] = useState(1);
  const [runningAll, setRunningAll] = useState(false);
  const [progress, setProgress] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, { name: string; score: number }[]>>({});
  const [logFor, setLogFor] = useState<{ q: string; data: any } | null>(null);
  const [stats, setStats] = useState<Awaited<ReturnType<typeof auditSuiteApi.stats>> | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [c, s] = await Promise.all([auditSuiteApi.list(tenantId, assistantId), auditSuiteApi.stats(tenantId, assistantId)]);
      setCases(c.cases); setStats(s);
    } finally { setLoading(false); }
  }, [tenantId, assistantId]);

  useEffect(() => { if (opened) reload(); }, [opened, reload]);

  const patch = async (id: string, body: Partial<AuditCaseRow>) => {
    const upd = await auditSuiteApi.update(tenantId, assistantId, id, body);
    setCases((p) => p.map((c) => (c.id === id ? upd : c)));
  };
  const addCase = async () => {
    const c = await auditSuiteApi.create(tenantId, assistantId, { question: 'новый запрос', expected_tools: [], active: true });
    setCases((p) => [...p, c]);
  };
  const del = async (id: string) => { await auditSuiteApi.remove(tenantId, assistantId, id); setCases((p) => p.filter((c) => c.id !== id)); };

  const runOne = async (id: string) => {
    setBusyId(id);
    try {
      const r = await auditSuiteApi.run(tenantId, assistantId, id, repeats);
      setCases((p) => p.map((c) => (c.id === id ? { ...c, last_result: r } : c)));
    } catch (e: any) { notifications.show({ color: 'red', message: e?.response?.data?.detail || 'Ошибка прогона' }); }
    finally { setBusyId(null); }
  };

  const showPreview = async (c: AuditCaseRow) => {
    if (preview[c.id]) { setPreview((p) => { const n = { ...p }; delete n[c.id]; return n; }); return; }
    const r = await toolAuditApi.preview(tenantId, assistantId, [{ question: c.question, expect_tool: c.expected_tools[0] || null }]);
    setPreview((p) => ({ ...p, [c.id]: r.results[0]?.surfaced || [] }));
  };

  const showLog = async (c: AuditCaseRow) => {
    const d = await auditSuiteApi.toolLog(tenantId, assistantId, c.id);
    setLogFor({ q: c.question, data: d });
  };

  const runAll = async () => {
    const active = cases.filter((c) => c.active);
    setRunningAll(true); setProgress(0);
    for (let i = 0; i < active.length; i++) {
      await runOne(active[i].id);
      setProgress(Math.round(((i + 1) / active.length) * 100));
    }
    await auditSuiteApi.snapshot(tenantId, assistantId);
    await reload();
    setRunningAll(false);
  };

  const seed = async () => {
    const r = await auditSuiteApi.seed(tenantId, assistantId, 30);
    notifications.show({ message: `Добавлено ${r.created} кейсов (выключены, проверь)`, color: 'green' });
    await reload();
  };

  return (
    <Modal opened={opened} onClose={onClose} size="90%" title={`Аудит роутинга — ${assistantName}`}>
      <Stack gap="sm">
        <Group justify="space-between">
          <Group gap="xs">
            <Button size="xs" leftSection={<IconPlus size={14} />} onClick={addCase}>Кейс</Button>
            <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} onClick={seed}>Сид из логов</Button>
            <NumberInput size="xs" w={110} min={1} max={5} value={repeats} onChange={(v) => setRepeats(Number(v) || 1)} label="повторы" />
          </Group>
          <Group gap="xs">
            <Button size="xs" variant="light" leftSection={<IconRefresh size={14} />} onClick={reload}>Обновить</Button>
            <Button size="xs" color="grape" leftSection={<IconPlayerPlay size={14} />} loading={runningAll} onClick={runAll}>
              Запустить все активные
            </Button>
          </Group>
        </Group>
        {runningAll && <Progress value={progress} animated />}
        {loading && <Loader size="sm" />}

        <ScrollArea.Autosize mah={400}>
          <Table stickyHeader striped withTableBorder verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={50}>Акт.</Table.Th>
                <Table.Th>Запрос</Table.Th>
                <Table.Th w={100}>Actor</Table.Th>
                <Table.Th w={240}>Ожидаемые тулы (порядок)</Table.Th>
                <Table.Th w={200}>Вердикт</Table.Th>
                <Table.Th w={150}>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {cases.map((c) => (
                <Fragment key={c.id}>
                  <Table.Tr key={c.id}>
                    <Table.Td><Switch checked={c.active} onChange={(e) => patch(c.id, { active: e.currentTarget.checked })} /></Table.Td>
                    <Table.Td>
                      <TextInput variant="unstyled" defaultValue={c.question}
                        onBlur={(e) => e.currentTarget.value !== c.question && patch(c.id, { question: e.currentTarget.value })} />
                    </Table.Td>
                    <Table.Td><ActorEditor value={c.actor} onChange={(a) => patch(c.id, { actor: a })} /></Table.Td>
                    <Table.Td>
                      <ToolsOrderEditor value={c.expected_tools} onChange={(v) => patch(c.id, { expected_tools: v })} />
                    </Table.Td>
                    <Table.Td>{busyId === c.id ? <Loader size="xs" /> : verdictBadge(c)}</Table.Td>
                    <Table.Td>
                      <Group gap={2} wrap="nowrap">
                        <Tooltip label="Запустить"><ActionIcon variant="subtle" color="green" onClick={() => runOne(c.id)}><IconPlayerPlay size={16} /></ActionIcon></Tooltip>
                        <Tooltip label="Семантика (каталог)"><ActionIcon variant="subtle" onClick={() => showPreview(c)}><IconSearch size={16} /></ActionIcon></Tooltip>
                        <Tooltip label="Лог вызова LLM"><ActionIcon variant="subtle" color="grape" onClick={() => showLog(c)}><IconClipboardList size={16} /></ActionIcon></Tooltip>
                        <Tooltip label="Удалить"><ActionIcon variant="subtle" color="red" onClick={() => del(c.id)}><IconTrash size={16} /></ActionIcon></Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                  {preview[c.id] && (
                    <Table.Tr key={c.id + '-pv'}>
                      <Table.Td colSpan={6} bg="light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-6))">
                        <Group gap={4}>
                          <Text size="xs" c="dimmed">каталог (что уйдёт модели):</Text>
                          {preview[c.id].map((s) => (
                            <Badge key={s.name} size="sm" variant="outline"
                              color={c.expected_tools.includes(s.name) ? 'green' : 'gray'}>{s.name} {s.score}</Badge>
                          ))}
                          {preview[c.id].length === 0 && <Text size="xs" c="red">пусто</Text>}
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Fragment>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea.Autosize>

        {stats && (
          <>
            <Divider label="Сводка по аудиту" labelPosition="left" />
            <Group>
              <Badge size="lg" color={stats.pass_pct >= 95 ? 'green' : stats.pass_pct >= 80 ? 'yellow' : 'red'}>
                pass {stats.passed}/{stats.ran} ({stats.pass_pct}%)
              </Badge>
              <Text size="sm" c="dimmed">активных: {stats.active}</Text>
              {stats.trend.length > 1 && (
                <Text size="xs" c="dimmed">тренд: {stats.trend.map((t) => `${t.passed}/${t.total}`).join(' → ')}</Text>
              )}
            </Group>
            {stats.by_tool.by_tool.length > 0 && (
              <Stack gap={4}>
                <Text size="sm" fw={600}>Группировка промахов по тулу (что чинить):</Text>
                {stats.by_tool.by_tool.map((b) => (
                  <Text key={b.tool} size="sm">
                    <b>~{b.share}%</b> ({b.misses}) — <code>{b.tool}</code>
                    <Text span size="xs" c="dimmed"> · звали вместо: {Object.entries(b.called_instead).map(([k, v]) => `${k}:${v}`).join(', ') || '—'}</Text>
                  </Text>
                ))}
              </Stack>
            )}
          </>
        )}
      </Stack>

      <Modal opened={!!logFor} onClose={() => setLogFor(null)} size="lg" title="Лог вызова LLM">
        {logFor && (
          <Stack gap="xs">
            <Text size="sm" fw={600}>{logFor.q}</Text>
            <Text size="sm">модель: {logFor.data?.debug?.model_name || '—'} · латентность: {logFor.data?.debug?.latency_ms ?? '—'}мс · токены: {logFor.data?.debug?.tokens ?? '—'}</Text>
            <Text size="sm">вызвано (data): {(logFor.data?.called || []).join(', ') || 'ничего'}</Text>
            <Text size="sm" c="dimmed">tool_calls: {(logFor.data?.debug?.tool_calls || []).join(', ') || '—'}</Text>
            <Text size="sm" c="dimmed">показано модели: {(logFor.data?.debug?.tools_payload || []).join(', ') || '—'}</Text>
            {logFor.data?.debug?.tier0?.decision?.fired && (
              <Alert color="orange" p="xs">tier0 сработал → {logFor.data.debug.tier0.decision.tool}</Alert>
            )}
            {!logFor.data?.ts && <Text size="xs" c="dimmed">Кейс ещё не прогонялся — нажми ▶.</Text>}
          </Stack>
        )}
      </Modal>
    </Modal>
  );
}
