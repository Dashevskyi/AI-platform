import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Code,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Stack,
  Text,
  Textarea,
} from '@mantine/core';
import { IconSparkles, IconClipboardCheck } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { shellApi } from '../../shared/api/endpoints';
import type { OntologyJson, OntologyPatch, Tool } from '../../shared/api/types';
import {
  applyOntologyPatches,
  countExamplesInPatches,
  examplesFromTools,
  focusSectionAfterPatches,
  normalizeExampleItems,
} from './ontologyImport';
import { fetchFailedAuditCases, type AuditCaseForSuggest } from './ontologyAudit';

type Props = {
  opened: boolean;
  onClose: () => void;
  tenantId: string;
  tools: Tool[];
  ontology: OntologyJson | null;
  onApply: (next: OntologyJson, focusSectionId?: string | null) => void;
  initialAuditCases?: AuditCaseForSuggest[] | null;
  initialTask?: string | null;
};

const TOOLS_EXAMPLES_TASK = 'Для каждого активного tool добавь 1–2 типовых примера запроса на русском.';

const TASK_PRESETS = [
  { label: 'Дополнить глоссарий и примеры', task: 'Дополнить глоссарий терминами предметной области и добавить примеры запросов для активных tools.' },
  { label: 'Примеры для всех tools', task: TOOLS_EXAMPLES_TASK, local: true as const },
  { label: 'Сжать онтологию', task: 'Предложи как сократить онтологию без потери смысла: объединить дубликаты, убрать лишнее.' },
];

export function OntologySuggestModal({
  opened,
  onClose,
  tenantId,
  tools,
  ontology,
  onApply,
  initialAuditCases,
  initialTask,
}: Props) {
  const [task, setTask] = useState(TASK_PRESETS[0].task);
  const [loading, setLoading] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditInfo, setAuditInfo] = useState<{ assistantName: string; count: number } | null>(null);
  const [auditCases, setAuditCases] = useState<AuditCaseForSuggest[] | null>(null);
  const [summary, setSummary] = useState('');
  const [patches, setPatches] = useState<OntologyPatch[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const bootstrappedRef = useRef(false);

  useEffect(() => {
    if (!opened) {
      bootstrappedRef.current = false;
      return;
    }
    if (bootstrappedRef.current || !initialAuditCases?.length) return;
    bootstrappedRef.current = true;
    setAuditCases(initialAuditCases);
    setAuditInfo({ assistantName: 'аудит tools', count: initialAuditCases.length });
    if (initialTask) setTask(initialTask);
    void runSuggest(initialAuditCases, initialTask || task);
  }, [opened, initialAuditCases, initialTask]); // eslint-disable-line react-hooks/exhaustive-deps

  const reset = () => {
    setSummary('');
    setPatches([]);
    setSelected(new Set());
  };

  const runSuggest = async (cases?: AuditCaseForSuggest[] | null, customTask?: string) => {
    setLoading(true);
    reset();
    try {
      const r = await shellApi.ontologySuggest(tenantId, {
        task: customTask || task,
        ontology_json: ontology,
        audit_cases: cases ?? auditCases,
      });
      setSummary(r.summary || '');
      setPatches(r.patches || []);
      setSelected(new Set((r.patches || []).map((p) => p.id)));
      if (!r.patches?.length) {
        notifications.show({ color: 'yellow', message: 'Модель не предложила патчей' });
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Ошибка LLM';
      notifications.show({ color: 'red', message: msg });
    } finally {
      setLoading(false);
    }
  };

  const runAuditSuggest = async () => {
    setAuditLoading(true);
    try {
      const { assistantName, cases } = await fetchFailedAuditCases(tenantId);
      setAuditCases(cases);
      setAuditInfo({ assistantName, count: cases.length });
      const auditTask =
        `Исправь онтологию по проваленным кейсам аудита ассистента «${assistantName}» (${cases.length} шт.): `
        + 'добавь/уточни примеры запросов, глоссарий и логику роутинга. Не удаляй существующие секции.';
      setTask(auditTask);
      await runSuggest(cases, auditTask);
    } catch (e: unknown) {
      notifications.show({
        color: 'red',
        message: e instanceof Error ? e.message : 'Не удалось загрузить аудит',
      });
    } finally {
      setAuditLoading(false);
    }
  };

  const applySelected = () => {
    const ids = [...selected];
    if (!ids.length) return;
    const applied = patches.filter((p) => ids.includes(p.id));
    const next = applyOntologyPatches(ontology, patches, ids);
    const focus = focusSectionAfterPatches(next, applied);
    const exCount = countExamplesInPatches(patches, ids);
    onApply(next, focus);
    notifications.show({
      color: 'green',
      message: exCount
        ? `Добавлено примеров: ${exCount}. Открыта секция «Примеры».`
        : `Применено патчей: ${ids.length}`,
    });
    reset();
    onClose();
  };

  const applyLocalToolsExamples = () => {
    const items = examplesFromTools(tools);
    if (!items.length) {
      notifications.show({ color: 'yellow', message: 'Нет активных tools для генерации примеров' });
      return;
    }
    const patch: OntologyPatch = {
      id: `local-${Date.now()}`,
      op: 'merge_examples',
      section_type: 'examples',
      data: { items },
      rationale: 'Локальная генерация из описаний tools (без LLM)',
    };
    const next = applyOntologyPatches(ontology, [patch], [patch.id]);
    const focus = focusSectionAfterPatches(next, [patch]);
    onApply(next, focus);
    notifications.show({
      color: 'green',
      message: `Добавлено ${items.length} примеров. Смотрите секцию «Примеры запросов».`,
    });
    onClose();
  };

  const opLabel: Record<string, string> = {
    merge_glossary: 'Глоссарий',
    merge_examples: 'Примеры',
    add_section: 'Новая секция',
    append_freeform: 'Freeform',
  };

  return (
    <Modal
      opened={opened}
      onClose={() => { reset(); onClose(); }}
      title="Помощник онтологии (LLM)"
      size="xl"
      styles={{ content: { maxWidth: 'min(1120px, 96vw)' } }}
    >
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Модель предложит структурированные патчи — применяйте по одному или выборочно.
        </Text>
        <Group gap="xs">
          {TASK_PRESETS.map((p) => (
            <Button
              key={p.label}
              size="xs"
              variant="light"
              onClick={() => {
                setTask(p.task);
                if ('local' in p && p.local) applyLocalToolsExamples();
              }}
            >
              {p.label}
            </Button>
          ))}
        </Group>
        {task === TOOLS_EXAMPLES_TASK && (
          <Text size="xs" c="dimmed">
            «Примеры для всех tools» сразу добавляет строки в секцию «Примеры» из описаний tools. LLM ниже — опционально для более живых формулировок.
          </Text>
        )}
        <Textarea
          label="Задача для модели"
          minRows={3}
          value={task}
          onChange={(e) => setTask(e.currentTarget.value)}
        />
        <Group>
          <Button leftSection={<IconSparkles size={16} />} loading={loading && !auditLoading} onClick={() => runSuggest()}>
            Получить предложения
          </Button>
          <Button
            leftSection={<IconClipboardCheck size={16} />}
            variant="light"
            color="orange"
            loading={auditLoading}
            onClick={runAuditSuggest}
          >
            Исправить по аудиту
          </Button>
        </Group>
        {auditInfo && (
          <Text size="xs" c="dimmed">
            Аудит: {auditInfo.assistantName} · провалено кейсов: {auditInfo.count}
          </Text>
        )}

        {(loading || auditLoading) && <Loader size="sm" />}

        {summary && (
          <Alert variant="light" color="blue">{summary}</Alert>
        )}

        {patches.length > 0 && (
          <ScrollArea.Autosize mah={360}>
            <Stack gap="sm">
              {patches.map((p) => (
                <Alert key={p.id} variant="light" color="gray" py="sm">
                  <Group justify="space-between" mb="xs" wrap="nowrap">
                    <Group gap="xs">
                      <Checkbox
                        checked={selected.has(p.id)}
                        onChange={(e) => {
                          setSelected((s) => {
                            const n = new Set(s);
                            if (e.currentTarget.checked) n.add(p.id);
                            else n.delete(p.id);
                            return n;
                          });
                        }}
                      />
                      <Badge variant="light">{opLabel[p.op] || p.op}</Badge>
                      {p.op === 'merge_examples' && (
                        <Badge size="xs" color="teal">
                          {normalizeExampleItems((p.data as { items?: unknown })?.items).length} прим.
                        </Badge>
                      )}
                      {p.op === 'add_section' && (p.data as { type?: string })?.type === 'examples' && (
                        <Badge size="xs" color="teal">
                          {normalizeExampleItems((p.data as { items?: unknown })?.items).length} прим.
                        </Badge>
                      )}
                      {p.section_type && <Badge size="xs" color="grape">{p.section_type}</Badge>}
                    </Group>
                  </Group>
                  <Text size="sm" mb="xs">{p.rationale}</Text>
                  <Code block style={{ whiteSpace: 'pre-wrap', fontSize: 11, maxHeight: 120, overflow: 'auto' }}>
                    {JSON.stringify(p.data, null, 2)}
                  </Code>
                </Alert>
              ))}
            </Stack>
          </ScrollArea.Autosize>
        )}

        {patches.length > 0 && (
          <Group justify="space-between">
            <Button variant="subtle" size="xs" onClick={() => setSelected(new Set(patches.map((p) => p.id)))}>Выбрать все</Button>
            <Button disabled={!selected.size} onClick={applySelected}>
              Применить выбранные ({selected.size})
            </Button>
          </Group>
        )}
      </Stack>
    </Modal>
  );
}
