import { useEffect, useState, useCallback } from 'react';
import {
  Stack, Card, Text, Group, Button, TextInput, Textarea, Select, MultiSelect,
  Badge, ActionIcon, Switch, Loader, Alert, Divider, Code, Modal, TagsInput,
} from '@mantine/core';
import { IconPlus, IconTrash, IconRobot, IconDeviceFloppy, IconPencil, IconClipboardCheck } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { assistantsApi, toolsApi, modelsApi, type Assistant } from '../../shared/api/endpoints';
import { AssistantAuditModal } from '../../components/Tools/AssistantAuditModal';

// Tri-state select: "" = inherit (key removed from overrides), else override.
const INHERIT = '';
const LANG_OPTS = [
  { value: INHERIT, label: '— наследовать —' },
  { value: 'ru', label: 'Русский' },
  { value: 'uk', label: 'Українська' },
  { value: 'en', label: 'English' },
  { value: 'pl', label: 'Polski' },
];
const TRI = [
  { value: INHERIT, label: '— наследовать —' },
  { value: 'true', label: 'Включено' },
  { value: 'false', label: 'Выключено' },
];

interface Draft {
  name: string;
  description: string;
  is_default: boolean;
  is_active: boolean;
  overrides: Record<string, unknown>;
  allowed_tool_ids: string[] | null;
}

function toDraft(a: Assistant): Draft {
  return {
    name: a.name, description: a.description || '',
    is_default: a.is_default, is_active: a.is_active,
    overrides: { ...(a.overrides || {}) }, allowed_tool_ids: a.allowed_tool_ids,
  };
}

function AssistantEditor({
  tenantId, assistant, toolOptions, modelOptions, onSaved, onDeleted,
}: {
  tenantId: string;
  assistant: Assistant;
  toolOptions: { value: string; label: string }[];
  modelOptions: { value: string; label: string }[];
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const [d, setD] = useState<Draft>(toDraft(assistant));
  const [saving, setSaving] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  useEffect(() => { setD(toDraft(assistant)); }, [assistant]);

  const ovStr = (key: string) => (d.overrides[key] as string | undefined) ?? INHERIT;
  const setOvStr = (key: string, val: string) => setD((p) => {
    const o = { ...p.overrides };
    if (val === INHERIT || val === '') delete o[key]; else o[key] = val;
    return { ...p, overrides: o };
  });
  const ovBool = (key: string) => {
    const v = d.overrides[key];
    return v === undefined ? INHERIT : v ? 'true' : 'false';
  };
  const setOvBool = (key: string, val: string) => setD((p) => {
    const o = { ...p.overrides };
    if (val === INHERIT) delete o[key]; else o[key] = val === 'true';
    return { ...p, overrides: o };
  });
  const ovArr = (key: string): string[] => {
    const v = d.overrides[key];
    return Array.isArray(v) ? (v as string[]) : [];
  };
  const setOvArr = (key: string, val: string[]) => setD((p) => {
    const o = { ...p.overrides };
    const clean = val.map((s) => s.trim()).filter(Boolean);
    if (clean.length === 0) delete o[key]; else o[key] = clean;
    return { ...p, overrides: o };
  });

  async function save() {
    if (!d.name.trim()) { notifications.show({ message: 'Имя обязательно', color: 'red' }); return; }
    setSaving(true);
    try {
      await assistantsApi.update(tenantId, assistant.id, {
        name: d.name.trim(),
        description: d.description || null,
        is_default: d.is_default,
        is_active: d.is_active,
        overrides: d.overrides,
        allowed_tool_ids: d.allowed_tool_ids,
      });
      notifications.show({ message: 'Сохранено', color: 'green' });
      onSaved();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      notifications.show({ title: 'Ошибка', message: typeof detail === 'string' ? detail : (e as Error).message, color: 'red' });
    } finally { setSaving(false); }
  }

  async function remove() {
    if (!confirm(`Удалить ассистента «${assistant.name}»?`)) return;
    try {
      await assistantsApi.remove(tenantId, assistant.id);
      notifications.show({ message: 'Удалён', color: 'green' });
      onDeleted();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      notifications.show({ title: 'Ошибка', message: typeof detail === 'string' ? detail : (e as Error).message, color: 'red' });
    }
  }

  const overrideCount = Object.keys(d.overrides).length;

  return (
    <Stack gap="sm">
      <Group grow>
        <TextInput label="Имя" value={d.name} onChange={(e) => setD((p) => ({ ...p, name: e.currentTarget.value }))} />
        <TextInput label="Описание" value={d.description} onChange={(e) => setD((p) => ({ ...p, description: e.currentTarget.value }))} />
      </Group>
      <Group gap="lg">
        <Switch label="По умолчанию" checked={d.is_default} onChange={(e) => setD((p) => ({ ...p, is_default: e.currentTarget.checked }))} disabled={assistant.is_default} />
        <Switch label="Активен" checked={d.is_active} onChange={(e) => setD((p) => ({ ...p, is_active: e.currentTarget.checked }))} disabled={assistant.is_default} />
      </Group>

      <Divider label="Переопределения (пусто = наследовать от тенанта)" labelPosition="left" />

      <Textarea label="Системный промт" autosize minRows={2} maxRows={10}
        placeholder="(наследовать общий промт тенанта)"
        value={ovStr('system_prompt')} onChange={(e) => setOvStr('system_prompt', e.currentTarget.value)} />
      <Textarea label="Онтология (ontology_prompt)" autosize minRows={2} maxRows={8}
        placeholder="(наследовать)"
        value={ovStr('ontology_prompt')} onChange={(e) => setOvStr('ontology_prompt', e.currentTarget.value)} />

      <Group grow>
        <Select label="Язык ответа" data={LANG_OPTS} value={ovStr('response_language')} onChange={(v) => setOvStr('response_language', v || INHERIT)} />
        <Select label="Tier 0" data={TRI} value={ovBool('tier0_enabled')} onChange={(v) => setOvBool('tier0_enabled', v || INHERIT)} />
        <Select label="Авто-лимит tools" data={TRI} value={ovBool('tool_limit_auto')} onChange={(v) => setOvBool('tool_limit_auto', v || INHERIT)} />
      </Group>

      <Select
        label="Модель LLM"
        description="Своя модель для этого ассистента; пусто = модель тенанта"
        data={[{ value: INHERIT, label: '— модель тенанта —' }, ...modelOptions]}
        value={ovStr('model_id')} onChange={(v) => setOvStr('model_id', v || INHERIT)}
        searchable clearable
      />

      <MultiSelect
        label="Доступные инструменты (пусто = все инструменты тенанта)"
        description="Выбери из списка; сужает набор tools для ассистента, пересекается с правами API-ключа"
        data={toolOptions} searchable clearable
        value={d.allowed_tool_ids ?? []}
        onChange={(v) => setD((p) => ({ ...p, allowed_tool_ids: v.length ? v : null }))}
      />

      <TagsInput
        label="Скрывать поля (PII-денлист)"
        description="Имена полей, которые вырезаются из вывода ЛЮБОГО инструмента до показа модели (напр. phone, balance, dogovor_num). По имени ключа; пусто = ничего не скрывать."
        placeholder="введите имя поля и Enter"
        value={ovArr('redact_fields')}
        onChange={(v) => setOvArr('redact_fields', v)}
        clearable
      />

      <TagsInput
        label="Разрешённые поля actor (whitelist)"
        description="Какие поля идентичности (actor) этот ассистент принимает: external_id, phone, role, geo, display_name. Остальное канал прислать может, но платформа их отбросит. Пусто = принимать все."
        placeholder="external_id, phone… (Enter)"
        value={ovArr('actor_fields')}
        onChange={(v) => setOvArr('actor_fields', v)}
        clearable
      />

      <Text size="xs" c="dimmed">
        Активных оверрайдов: {overrideCount}{overrideCount ? <> — <Code>{Object.keys(d.overrides).join(', ')}</Code></> : ''}.
        Остальное (эмбеддинги, KB, память) — общее на уровне тенанта.
      </Text>

      <Group justify="space-between" mt="sm">
        {!assistant.is_default
          ? <Button variant="subtle" color="red" leftSection={<IconTrash size={14} />} onClick={remove}>Удалить</Button>
          : <span />}
        <Group gap="xs">
          <Button variant="default" leftSection={<IconClipboardCheck size={14} />} onClick={() => setAuditOpen(true)}>
            Аудит роутинга
          </Button>
          <Button leftSection={<IconDeviceFloppy size={14} />} loading={saving} onClick={save}>Сохранить</Button>
        </Group>
      </Group>
      <AssistantAuditModal
        tenantId={tenantId} assistantId={assistant.id} assistantName={assistant.name}
        toolOptions={toolOptions}
        opened={auditOpen} onClose={() => setAuditOpen(false)}
      />
    </Stack>
  );
}

export function AssistantsTab({ tenantId }: { tenantId: string }) {
  const [list, setList] = useState<Assistant[] | null>(null);
  const [toolOptions, setToolOptions] = useState<{ value: string; label: string }[]>([]);
  const [modelOptions, setModelOptions] = useState<{ value: string; label: string }[]>([]);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Assistant | null>(null);

  const reload = useCallback(async () => {
    const all = await assistantsApi.list(tenantId).catch(() => []);
    // Hide system throwaway clones (eval/audit isolation) — names start with "__".
    const rows = all.filter((a) => !a.name.startsWith('__'));
    setList(rows);
    // keep the open modal's data fresh
    setEditing((cur) => (cur ? rows.find((r) => r.id === cur.id) ?? null : cur));
  }, [tenantId]);

  useEffect(() => {
    reload();
    // Load all tenant tools (page_size is capped at 100 server-side; page
    // through if a tenant has more so the picker is complete).
    (async () => {
      try {
        const opts: { value: string; label: string }[] = [];
        for (let page = 1; page <= 20; page++) {
          const p = await toolsApi.list(tenantId, page, 100);
          for (const t of p.items || []) opts.push({ value: t.id, label: t.name });
          if (!p.items || p.items.length < 100) break;
        }
        setToolOptions(opts);
      } catch { setToolOptions([]); }
    })();
    modelsApi.brief()
      .then((ms) => setModelOptions((ms || []).map((m) => ({ value: m.id, label: m.name }))))
      .catch(() => setModelOptions([]));
  }, [tenantId, reload]);

  async function createNew() {
    setCreating(true);
    try {
      const a = await assistantsApi.create(tenantId, { name: 'Новый ассистент', overrides: {} });
      await reload();
      setEditing(a); // open the editor for the freshly created one
    } catch (e: unknown) {
      notifications.show({ title: 'Ошибка', message: (e as Error).message, color: 'red' });
    } finally { setCreating(false); }
  }

  if (list === null) return <Group justify="center" p="xl"><Loader /></Group>;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <div>
          <Text fw={600}>Ассистенты</Text>
          <Text size="sm" c="dimmed">
            Персоны под одним тенантом (голос / чат / email). Каждый переопределяет промт, язык,
            Tier 0, набор инструментов — общие KB, память и модель берутся с уровня тенанта.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} loading={creating} onClick={createNew}>Добавить</Button>
      </Group>

      {list.length === 0 && <Alert color="gray">Нет ассистентов. Должен быть хотя бы один по умолчанию.</Alert>}

      <Stack gap="xs">
        {list.map((a) => {
          const oc = Object.keys(a.overrides || {}).length;
          return (
            <Card key={a.id} withBorder padding="sm"
              style={{ cursor: 'pointer' }} onClick={() => setEditing(a)}>
              <Group justify="space-between" wrap="nowrap">
                <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                  <IconRobot size={18} />
                  <div style={{ minWidth: 0 }}>
                    <Group gap={6}>
                      <Text fw={600} truncate>{a.name}</Text>
                      {a.is_default && <Badge size="sm" color="blue">по умолчанию</Badge>}
                      {!a.is_active && <Badge size="sm" color="gray">выключен</Badge>}
                    </Group>
                    <Text size="xs" c="dimmed" truncate fs={a.description ? undefined : 'italic'}>
                      {a.description || 'без описания'}
                    </Text>
                  </div>
                </Group>
                <Group gap="xs" wrap="nowrap">
                  <Badge size="sm" variant="light">{oc} оверрайд(ов)</Badge>
                  <ActionIcon variant="subtle" onClick={(e) => { e.stopPropagation(); setEditing(a); }}>
                    <IconPencil size={16} />
                  </ActionIcon>
                </Group>
              </Group>
            </Card>
          );
        })}
      </Stack>

      <Modal
        opened={!!editing}
        onClose={() => setEditing(null)}
        size="lg"
        title={<Group gap="xs"><IconRobot size={18} /><Text fw={600}>{editing?.name}</Text></Group>}
      >
        {editing && (
          <AssistantEditor
            tenantId={tenantId} assistant={editing}
            toolOptions={toolOptions} modelOptions={modelOptions}
            onSaved={reload}
            onDeleted={() => { setEditing(null); reload(); }}
          />
        )}
      </Modal>
    </Stack>
  );
}
