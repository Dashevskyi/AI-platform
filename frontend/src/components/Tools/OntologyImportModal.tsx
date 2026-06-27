import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  MultiSelect,
  ScrollArea,
  Select,
  Stack,
  Tabs,
  Text,
  Textarea,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { dataSourcesApi, logsApi, tier0Api } from '../../shared/api/endpoints';
import type { LLMLog, OntologyJson, OntologySection, Tool } from '../../shared/api/types';
import {
  buildEntitiesSection,
  buildGlossarySection,
  entitiesFromSchema,
  examplesFromLogs,
  examplesFromTier0Hits,
  glossaryFromTools,
  mergeExamples,
  mergeGlossaryItems,
  parseGlossaryPaste,
} from './ontologyImport';

type Props = {
  opened: boolean;
  onClose: () => void;
  tenantId: string;
  tools: Tool[];
  ontology: OntologyJson | null;
  onApply: (next: OntologyJson) => void;
};

export function OntologyImportModal({ opened, onClose, tenantId, tools, ontology, onApply }: Props) {
  const [tab, setTab] = useState<string | null>('tools');
  const [csvText, setCsvText] = useState('');
  const [dataSources, setDataSources] = useState<{ value: string; label: string }[]>([]);
  const [selectedDs, setSelectedDs] = useState<string | null>(null);
  const [tableOptions, setTableOptions] = useState<{ value: string; label: string }[]>([]);
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [loadingSchema, setLoadingSchema] = useState(false);
  const [logRows, setLogRows] = useState<LLMLog[]>([]);
  const [selectedLogIds, setSelectedLogIds] = useState<string[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [tier0Hits, setTier0Hits] = useState<Array<{ user_query: string; tool: string | null }>>([]);
  const [selectedTier0, setSelectedTier0] = useState<number[]>([]);
  const [loadingTier0, setLoadingTier0] = useState(false);

  useEffect(() => {
    if (!opened) return;
    (async () => {
      try {
        const r = await dataSourcesApi.list(tenantId, 1, 100);
        setDataSources(r.items.filter((d) => d.is_active).map((d) => ({ value: d.id, label: d.name })));
      } catch { /* ignore */ }
    })();
  }, [opened, tenantId]);

  useEffect(() => {
    if (!selectedDs) {
      setTableOptions([]);
      setSelectedTables([]);
      return;
    }
    setLoadingSchema(true);
    dataSourcesApi.getSchema(tenantId, selectedDs)
      .then((schema) => {
        setTableOptions(schema.tables.map((t) => ({ value: t.full_name, label: t.full_name })));
      })
      .catch(() => notifications.show({ color: 'red', message: 'Не удалось загрузить схему' }))
      .finally(() => setLoadingSchema(false));
  }, [selectedDs, tenantId]);

  useEffect(() => {
    if (!opened || tab !== 'logs') return;
    setLoadingLogs(true);
    logsApi.list(tenantId, 1, 30, { status: 'success' })
      .then((r) => setLogRows(r.items.filter((l) => (l.tool_calls_count || 0) > 0 && l.request_preview)))
      .catch(() => notifications.show({ color: 'red', message: 'Не удалось загрузить логи' }))
      .finally(() => setLoadingLogs(false));
  }, [opened, tab, tenantId]);

  useEffect(() => {
    if (!opened || tab !== 'tier0') return;
    setLoadingTier0(true);
    tier0Api.getStats(tenantId, 14, 30)
      .then((r) => setTier0Hits(r.recent_hits || []))
      .catch(() => notifications.show({ color: 'red', message: 'Не удалось загрузить Tier 0' }))
      .finally(() => setLoadingTier0(false));
  }, [opened, tab, tenantId]);

  const glossaryPreview = useMemo(() => glossaryFromTools(tools), [tools]);

  const applyGlossary = (items: { term: string; definition: string }[], title: string) => {
    const existing = ontology?.sections.find((s) => s.type === 'glossary') as Extract<OntologySection, { type: 'glossary' }> | undefined;
    const merged = mergeGlossaryItems(existing?.items || [], items);
    const section = existing
      ? { ...existing, items: merged }
      : buildGlossarySection(title, merged);
    const sections = existing
      ? (ontology?.sections || []).map((s) => (s === existing ? section : s))
      : [...(ontology?.sections || []), section];
    onApply({ version: 1, sections });
    notifications.show({ color: 'green', message: `Глоссарий: ${merged.length} терминов` });
    onClose();
  };

  const applyEntities = async () => {
    if (!selectedDs || !selectedTables.length) return;
    try {
      const schema = await dataSourcesApi.getSchema(tenantId, selectedDs);
      const entities = entitiesFromSchema(schema, selectedTables);
      const section = buildEntitiesSection(`Сущности (${selectedTables.join(', ')})`, entities);
      onApply({ version: 1, sections: [...(ontology?.sections || []), section] });
      notifications.show({ color: 'green', message: `Добавлено сущностей: ${entities.length}` });
      onClose();
    } catch {
      notifications.show({ color: 'red', message: 'Ошибка импорта сущностей' });
    }
  };

  const applyExamples = (incoming: { query: string; expected_tool?: string; note?: string }[]) => {
    const existing = ontology?.sections.find((s) => s.type === 'examples') as Extract<OntologySection, { type: 'examples' }> | undefined;
    const merged = mergeExamples(existing?.items || [], incoming);
    const section = existing
      ? { ...existing, items: merged }
      : { id: `n${Date.now()}`, type: 'examples' as const, title: 'Примеры запросов', items: merged };
    const sections = existing
      ? (ontology?.sections || []).map((s) => (s === existing ? section : s))
      : [...(ontology?.sections || []), section];
    onApply({ version: 1, sections });
    notifications.show({ color: 'green', message: `Примеров: ${merged.length}` });
    onClose();
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Импорт в онтологию" size="lg">
      <Tabs value={tab} onChange={setTab}>
        <Tabs.List mb="md">
          <Tabs.Tab value="tools">Из tools</Tabs.Tab>
          <Tabs.Tab value="schema">Из схемы БД</Tabs.Tab>
          <Tabs.Tab value="csv">CSV / Excel</Tabs.Tab>
          <Tabs.Tab value="logs">Из логов</Tabs.Tab>
          <Tabs.Tab value="tier0">Из Tier 0</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="tools">
          <Stack gap="sm">
            <Text size="sm" c="dimmed">
              Имена и описания активных инструментов → термины глоссария. Существующие термины не перезаписываются.
            </Text>
            <Alert variant="light">Будет добавлено до {glossaryPreview.length} терминов</Alert>
            <Group justify="flex-end">
              <Button onClick={() => applyGlossary(glossaryPreview, 'Глоссарий (из tools)')}>Импортировать</Button>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="schema">
          <Stack gap="sm">
            <Select
              label="Источник данных"
              placeholder="Выберите"
              data={dataSources}
              value={selectedDs}
              onChange={setSelectedDs}
              searchable
            />
            {loadingSchema ? <Loader size="sm" /> : (
              <MultiSelect
                label="Таблицы"
                placeholder="Выберите таблицы"
                data={tableOptions}
                value={selectedTables}
                onChange={setSelectedTables}
                searchable
                disabled={!selectedDs}
              />
            )}
            <Group justify="flex-end">
              <Button disabled={!selectedTables.length} onClick={applyEntities}>Создать секцию сущностей</Button>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="csv">
          <Stack gap="sm">
            <Text size="sm" c="dimmed">Вставьте из Excel: термин и определение через таб, точку с запятой или запятую.</Text>
            <Textarea
              minRows={8}
              placeholder={'GPON;пассивная оптическая сеть\nVLAN;виртуальная сеть'}
              value={csvText}
              onChange={(e) => setCsvText(e.currentTarget.value)}
            />
            <Group justify="flex-end">
              <Button
                disabled={!csvText.trim()}
                onClick={() => applyGlossary(parseGlossaryPaste(csvText), 'Глоссарий (импорт)')}
              >
                Импортировать
              </Button>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="logs">
          <Stack gap="sm">
            {loadingLogs ? <Loader size="sm" /> : (
              <ScrollArea.Autosize mah={320}>
                <Stack gap="xs">
                  {logRows.length === 0 && <Text size="sm" c="dimmed">Нет успешных логов с вызовами tools</Text>}
                  {logRows.map((log) => (
                    <Checkbox
                      key={log.id}
                      checked={selectedLogIds.includes(log.id)}
                      onChange={(e) => {
                        setSelectedLogIds((ids) =>
                          e.currentTarget.checked ? [...ids, log.id] : ids.filter((id) => id !== log.id),
                        );
                      }}
                      label={
                        <Text size="sm" lineClamp={2}>
                          {log.request_preview}
                          <Text span size="xs" c="dimmed"> · {log.tool_calls_count} tools</Text>
                        </Text>
                      }
                    />
                  ))}
                </Stack>
              </ScrollArea.Autosize>
            )}
            <Group justify="flex-end">
              <Button
                disabled={!selectedLogIds.length}
                onClick={() => applyExamples(examplesFromLogs(logRows.filter((l) => selectedLogIds.includes(l.id))))}
              >
                Добавить примеры
              </Button>
            </Group>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="tier0">
          <Stack gap="sm">
            {loadingTier0 ? <Loader size="sm" /> : (
              <ScrollArea.Autosize mah={320}>
                <Stack gap="xs">
                  {tier0Hits.length === 0 && <Text size="sm" c="dimmed">Нет недавних срабатываний Tier 0</Text>}
                  {tier0Hits.map((hit, i) => (
                    <Checkbox
                      key={`${hit.user_query}-${i}`}
                      checked={selectedTier0.includes(i)}
                      onChange={(e) => {
                        setSelectedTier0((ids) =>
                          e.currentTarget.checked ? [...ids, i] : ids.filter((x) => x !== i),
                        );
                      }}
                      label={
                        <Text size="sm" lineClamp={2}>
                          {hit.user_query}
                          {hit.tool && <Text span size="xs" c="dimmed"> → {hit.tool}</Text>}
                        </Text>
                      }
                    />
                  ))}
                </Stack>
              </ScrollArea.Autosize>
            )}
            <Group justify="flex-end">
              <Button
                disabled={!selectedTier0.length}
                onClick={() => applyExamples(examplesFromTier0Hits(selectedTier0.map((i) => tier0Hits[i])))}
              >
                Добавить примеры
              </Button>
            </Group>
          </Stack>
        </Tabs.Panel>
      </Tabs>
    </Modal>
  );
}
