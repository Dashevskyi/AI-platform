import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  ScrollArea,
  SegmentedControl,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';
import { IconBug, IconSparkles } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { shellApi } from '../../shared/api/endpoints';
import type { OntologyJson, OntologyPatch, ToolCallAuditItem } from '../../shared/api/types';
import {
  applyOntologyPatches,
  focusSectionAfterPatches,
  normalizeExampleItems,
} from './ontologyImport';
import {
  auditItemToSuggest,
  fetchToolCallAudit,
  type AuditCaseForSuggest,
} from './ontologyAudit';

const FAILURE_COLORS: Record<string, string> = {
  tool_error: 'red',
  no_tool_call: 'orange',
  wrong_tool: 'yellow',
  unexpected_tool: 'grape',
};

type Props = {
  opened: boolean;
  onClose: () => void;
  tenantId: string;
  ontology: OntologyJson | null;
  onApply: (next: OntologyJson, focusSectionId?: string | null) => void;
  onSuggestWithAudit?: (cases: AuditCaseForSuggest[], task: string) => void;
};

export function OntologyToolCallAuditModal({
  opened,
  onClose,
  tenantId,
  ontology,
  onApply,
  onSuggestWithAudit,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<ToolCallAuditItem[]>([]);
  const [summary, setSummary] = useState<{ total: number; by_failure_class: Record<string, number>; by_source: Record<string, number> } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sourceFilter, setSourceFilter] = useState<'all' | 'log' | 'audit_case'>('all');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetchToolCallAudit(tenantId, { days: 14, limit: 80 });
      setItems(r.items);
      setSummary(r.summary);
      setSelected(new Set(r.items.map((i) => i.id)));
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (e instanceof Error ? e.message : 'Не удалось загрузить аудит');
      notifications.show({ color: 'red', message: msg });
      setItems([]);
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    if (opened) load();
  }, [opened, load]);

  const filtered = useMemo(() => {
    if (sourceFilter === 'all') return items;
    return items.filter((i) => i.source === sourceFilter);
  }, [items, sourceFilter]);

  const visibleIds = useMemo(() => filtered.map((i) => i.id), [filtered]);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const someVisibleSelected = visibleIds.some((id) => selected.has(id)) && !allVisibleSelected;

  const toggle = (id: string) => {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  };

  const toggleAllVisible = (checked: boolean) => {
    setSelected((s) => {
      const n = new Set(s);
      visibleIds.forEach((id) => {
        if (checked) n.add(id);
        else n.delete(id);
      });
      return n;
    });
  };

  const selectedItems = items.filter((i) => selected.has(i.id));

  const applyAsExamples = () => {
    const examples = selectedItems
      .map((it) => ({
        query: it.query,
        expected_tool: it.expected_tool || undefined,
        note: `[${it.failure_label}] ${it.suggestion}`.slice(0, 240),
      }))
      .filter((ex) => ex.query.trim());
    if (!examples.length) {
      notifications.show({ color: 'yellow', message: 'Выберите строки для добавления' });
      return;
    }
    const patch: OntologyPatch = {
      id: `audit-ex-${Date.now()}`,
      op: 'merge_examples',
      section_type: 'examples',
      data: { items: examples },
      rationale: `Аудит ошибочных вызовов tools (${examples.length} шт.)`,
    };
    const next = applyOntologyPatches(ontology, [patch], [patch.id]);
    const focus = focusSectionAfterPatches(next, [patch]);
    onApply(next, focus);
    notifications.show({
      color: 'green',
      message: `Добавлено ${normalizeExampleItems(examples).length} примеров из аудита`,
    });
    onClose();
  };

  const runLlmSuggest = () => {
    if (!selectedItems.length) {
      notifications.show({ color: 'yellow', message: 'Выберите кейсы для LLM' });
      return;
    }
    const cases = selectedItems.map(auditItemToSuggest);
    const task =
      `Исправь онтологию по ${cases.length} ошибочным вызовам tools из логов и аудита: `
      + 'добавь/уточни примеры запросов, глоссарий и правила роутинга. Не удаляй существующие секции.';
    if (onSuggestWithAudit) {
      onSuggestWithAudit(cases, task);
      onClose();
      return;
    }
    notifications.show({ color: 'yellow', message: 'LLM-патчи недоступны из этого контекста' });
  };

  const applyRoutingFeedback = async (dryRun = true, asyncJob = false) => {
    setLoading(true);
    try {
      const r = await shellApi.ontologyRoutingFeedback(tenantId, {
        dry_run: dryRun,
        days: 14,
        limit: 40,
        async_job: asyncJob && !dryRun,
      });
      if (r.queued) {
        notifications.show({ color: 'blue', message: 'Routing-feedback поставлен в очередь jobs' });
        return;
      }
      notifications.show({
        color: 'green',
        message: dryRun
          ? `Dry-run: +${r.examples_added ?? 0} примеров для tools: ${(r.tools_updated || []).join(', ') || '—'}`
          : `Обновлено tools: ${(r.tools_updated || []).length}, примеров: ${r.examples_added ?? 0}`,
      });
    } catch (e: unknown) {
      notifications.show({
        color: 'red',
        message: e instanceof Error ? e.message : 'Ошибка routing-feedback',
      });
    } finally {
      setLoading(false);
    }
  };

  const scheduleRoutingFeedbackAll = async () => {
    setLoading(true);
    try {
      const r = await shellApi.scheduleRoutingFeedbackAll({ days: 14, limit: 40 });
      notifications.show({
        color: 'green',
        message: `В очередь поставлено ${r.queued} jobs routing-feedback`,
      });
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (e instanceof Error ? e.message : 'Ошибка планировщика');
      notifications.show({ color: 'red', message: msg });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title="Аудит ошибочных вызовов tools"
      size="xl"
      styles={{
        content: { maxWidth: 'min(1120px, 96vw)' },
        body: {
          display: 'flex',
          flexDirection: 'column',
          height: 'min(82vh, 860px)',
          overflow: 'hidden',
        },
      }}
    >
      <Box
        style={{
          display: 'flex',
          flexDirection: 'column',
          flex: 1,
          minHeight: 0,
          gap: 'var(--mantine-spacing-md)',
        }}
      >
        <Box style={{ flexShrink: 0 }}>
          <Text size="sm" c="dimmed" mb="md">
            Сигналы из production-логов и проваленных кейсов «Аудит ассистента». Используйте для примеров и LLM-патчей онтологии.
          </Text>

          <Group justify="space-between">
            <SegmentedControl
              size="xs"
              value={sourceFilter}
              onChange={(v) => setSourceFilter(v as typeof sourceFilter)}
              data={[
                { label: `Все (${items.length})`, value: 'all' },
                { label: `Логи (${items.filter((i) => i.source === 'log').length})`, value: 'log' },
                { label: `Аудит (${items.filter((i) => i.source === 'audit_case').length})`, value: 'audit_case' },
              ]}
            />
            <Button size="xs" variant="subtle" onClick={load} loading={loading}>
              Обновить
            </Button>
          </Group>

          {summary && summary.total > 0 && (
            <Group gap="xs" mt="md">
              {Object.entries(summary.by_failure_class).map(([k, n]) => (
                <Badge key={k} color={FAILURE_COLORS[k] || 'gray'} variant="light">
                  {k}: {n}
                </Badge>
              ))}
            </Group>
          )}

          {loading && <Loader size="sm" mt="md" />}

          {!loading && !items.length && (
            <Alert variant="light" color="blue" icon={<IconBug size={16} />} mt="md">
              За последние 14 дней не найдено ошибочных вызовов tools. Запустите «Аудит ассистента» или дождитесь трафика с tool_errors.
            </Alert>
          )}
        </Box>

        {filtered.length > 0 && (
          <Box style={{ flex: 1, minHeight: 0 }}>
            <ScrollArea h="100%" type="auto" offsetScrollbars>
              <Table stickyHeader striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={36}>
                    <Checkbox
                      checked={allVisibleSelected}
                      indeterminate={someVisibleSelected}
                      onChange={(e) => toggleAllVisible(e.currentTarget.checked)}
                      aria-label="Выбрать все строки"
                    />
                  </Table.Th>
                  <Table.Th>Запрос</Table.Th>
                  <Table.Th>Ожидался</Table.Th>
                  <Table.Th>Вызвано</Table.Th>
                  <Table.Th>Тип</Table.Th>
                  <Table.Th>Источник</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((it) => (
                  <Table.Tr key={it.id}>
                    <Table.Td>
                      <Checkbox checked={selected.has(it.id)} onChange={() => toggle(it.id)} />
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={it.suggestion} multiline maw={360}>
                        <Text size="sm" lineClamp={2}>{it.query}</Text>
                      </Tooltip>
                      {it.semantic_top[0] && (
                        <Text size="xs" c="dimmed">
                          semantic: {it.semantic_top[0].name} ({it.semantic_top[0].score})
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{it.expected_tool || '—'}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{it.called.length ? it.called.join(', ') : '—'}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Badge size="sm" color={FAILURE_COLORS[it.failure_class] || 'gray'} variant="light">
                        {it.failure_label}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge size="sm" variant="outline">
                        {it.source === 'log' ? 'лог' : it.assistant_name || 'аудит'}
                      </Badge>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
              </Table>
            </ScrollArea>
          </Box>
        )}

        {items.length > 0 && (
          <Group justify="flex-end" style={{ flexShrink: 0, paddingTop: 4 }}>
            <Button variant="subtle" size="xs" onClick={() => applyRoutingFeedback(true)} loading={loading}>
                Dry-run → tools
              </Button>
              <Button
                variant="light"
                color="teal"
                size="xs"
                onClick={() => applyRoutingFeedback(false, true)}
                loading={loading}
              >
                Применить → tools
              </Button>
              <Button variant="subtle" size="xs" onClick={scheduleRoutingFeedbackAll} loading={loading}>
                Запланировать для всех
              </Button>
              <Button
                variant="light"
                disabled={!selectedItems.length}
                onClick={applyAsExamples}
              >
                В примеры ({selectedItems.length})
              </Button>
              <Button
                leftSection={<IconSparkles size={16} />}
                disabled={!selectedItems.length}
                onClick={runLlmSuggest}
              >
                LLM-патчи
            </Button>
          </Group>
        )}
      </Box>
    </Modal>
  );
}
