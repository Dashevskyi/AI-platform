import { useMemo, useState } from 'react';
import {
  Modal,
  Stack,
  Group,
  Text,
  Select,
  Button,
  ScrollArea,
  Paper,
  Badge,
  TextInput,
  ActionIcon,
  Tooltip,
  Loader,
  Center,
  Alert,
  Divider,
} from '@mantine/core';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import {
  IconDeviceFloppy,
  IconTrash,
  IconWand,
  IconPlus,
  IconInfoCircle,
  IconBook2,
} from '@tabler/icons-react';
import { isAxiosError } from 'axios';
import { dataSourcesApi, schemaNotesApi, type SchemaNote } from '../../shared/api/endpoints';

const SQL_KINDS = new Set(['mysql', 'mariadb', 'postgresql']);

function errMessage(e: unknown): string {
  if (isAxiosError(e)) {
    const d = e.response?.data as { detail?: string } | undefined;
    return d?.detail || e.message;
  }
  return e instanceof Error ? e.message : 'Неизвестная ошибка';
}

function NoteRow({
  tenantId,
  dataSourceId,
  note,
}: {
  tenantId: string;
  dataSourceId: string;
  note: SchemaNote;
}) {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState(note.description || '');
  const [references, setReferences] = useState(note.references || '');
  const dirty = description !== (note.description || '') || references !== (note.references || '');

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'data-sources', dataSourceId, 'schema-notes'] });

  const saveMut = useMutation({
    mutationFn: () =>
      schemaNotesApi.upsert(tenantId, dataSourceId, {
        table_name: note.table_name,
        column_name: note.column_name,
        description: description.trim() || null,
        references: references.trim() || null,
      }),
    onSuccess: invalidate,
    onError: (e) => notifications.show({ title: 'Ошибка', message: errMessage(e), color: 'red' }),
  });
  const delMut = useMutation({
    mutationFn: () => schemaNotesApi.remove(tenantId, dataSourceId, note.id),
    onSuccess: invalidate,
    onError: (e) => notifications.show({ title: 'Ошибка', message: errMessage(e), color: 'red' }),
  });

  return (
    <Group gap={6} wrap="nowrap" align="flex-start">
      <Text size="sm" fw={500} w={150} style={{ flexShrink: 0, wordBreak: 'break-all' }}>
        {note.column_name || <Text span c="dimmed" fs="italic">вся таблица</Text>}
      </Text>
      <TextInput
        size="xs"
        style={{ flex: 1 }}
        placeholder="смысл колонки…"
        value={description}
        onChange={(e) => setDescription(e.currentTarget.value)}
      />
      <TextInput
        size="xs"
        w={170}
        placeholder="→ schema.table.col"
        value={references}
        onChange={(e) => setReferences(e.currentTarget.value)}
      />
      {note.source !== 'admin' && (
        <Badge size="xs" variant="light" color={note.source === 'agent' ? 'grape' : 'gray'}>
          {note.source}
        </Badge>
      )}
      <Tooltip label="Сохранить">
        <ActionIcon
          variant="light"
          color="blue"
          size="md"
          disabled={!dirty}
          loading={saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          <IconDeviceFloppy size={15} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label="Удалить">
        <ActionIcon variant="light" color="red" size="md" loading={delMut.isPending} onClick={() => delMut.mutate()}>
          <IconTrash size={15} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

function AddNoteRow({ tenantId, dataSourceId }: { tenantId: string; dataSourceId: string }) {
  const queryClient = useQueryClient();
  const [table, setTable] = useState('');
  const [column, setColumn] = useState('');
  const [description, setDescription] = useState('');

  const addMut = useMutation({
    mutationFn: () =>
      schemaNotesApi.upsert(tenantId, dataSourceId, {
        table_name: table.trim() || null,
        column_name: column.trim() || null,
        description: description.trim() || null,
      }),
    onSuccess: () => {
      setColumn('');
      setDescription('');
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'data-sources', dataSourceId, 'schema-notes'] });
    },
    onError: (e) => notifications.show({ title: 'Ошибка', message: errMessage(e), color: 'red' }),
  });

  return (
    <Group gap={6} wrap="nowrap" align="flex-end">
      <TextInput size="xs" label="Таблица" w={180} placeholder="schema.table" value={table} onChange={(e) => setTable(e.currentTarget.value)} />
      <TextInput size="xs" label="Колонка" w={140} placeholder="(пусто = таблица)" value={column} onChange={(e) => setColumn(e.currentTarget.value)} />
      <TextInput size="xs" label="Смысл" style={{ flex: 1 }} value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
      <Button
        size="xs"
        leftSection={<IconPlus size={14} />}
        loading={addMut.isPending}
        disabled={!table.trim() || !description.trim()}
        onClick={() => addMut.mutate()}
      >
        Добавить
      </Button>
    </Group>
  );
}

export function SchemaNotesModal({
  tenantId,
  opened,
  onClose,
}: {
  tenantId: string;
  opened: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [dataSourceId, setDataSourceId] = useState<string | null>(null);

  const { data: sourcesData } = useQuery({
    queryKey: ['tenants', tenantId, 'data-sources', 'for-notes'],
    queryFn: () => dataSourcesApi.list(tenantId, 1, 100),
    enabled: opened,
  });
  const sqlSources = useMemo(
    () => (sourcesData?.items || []).filter((s) => SQL_KINDS.has(s.kind.toLowerCase())),
    [sourcesData],
  );

  const { data: notesData, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'data-sources', dataSourceId, 'schema-notes'],
    queryFn: () => schemaNotesApi.list(tenantId, dataSourceId!),
    enabled: opened && !!dataSourceId,
  });

  const seedMut = useMutation({
    mutationFn: () => schemaNotesApi.seed(tenantId, dataSourceId!),
    onSuccess: (res) => {
      notifications.show({
        title: 'Справочник пополнен из инструментов',
        message: `Колонок: ${res.columns_seeded}, связей: ${res.relations_seeded}. Всего заметок: ${res.total}.`,
        color: 'green',
      });
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'data-sources', dataSourceId, 'schema-notes'] });
    },
    onError: (e) => notifications.show({ title: 'Ошибка', message: errMessage(e), color: 'red' }),
  });

  // Group notes by table for display.
  const grouped = useMemo(() => {
    const notes = notesData?.notes || [];
    const sourceLevel = notes.filter((n) => !n.table_name);
    const byTable = new Map<string, SchemaNote[]>();
    for (const n of notes) {
      if (n.table_name) {
        if (!byTable.has(n.table_name)) byTable.set(n.table_name, []);
        byTable.get(n.table_name)!.push(n);
      }
    }
    const tables = Array.from(byTable.entries()).sort(([a], [b]) => a.localeCompare(b));
    for (const [, rows] of tables) {
      rows.sort((a, b) => (a.column_name || '').localeCompare(b.column_name || ''));
    }
    return { sourceLevel, tables };
  }, [notesData]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      size="80%"
      title={
        <Group gap={8}>
          <IconBook2 size={20} />
          <Text fw={600}>Справочник схемы (смысловой слой)</Text>
        </Group>
      }
      styles={{ body: { display: 'flex', flexDirection: 'column', height: '72vh' } }}
    >
      <Stack gap="sm" style={{ flex: 1, minHeight: 0 }}>
        <Alert color="blue" icon={<IconInfoCircle size={16} />} py={6}>
          Интроспекция даёт структуру (имена/типы), справочник — смысл: что значат таблицы и колонки,
          какие между ними связи. Агент-конструктор читает его перед интроспекцией и пополняет сам.
        </Alert>

        <Group justify="space-between" align="flex-end">
          <Select
            label="Источник данных (SQL)"
            placeholder="Выберите источник"
            w={360}
            data={sqlSources.map((s) => ({ value: s.id, label: `${s.name} (${s.kind})` }))}
            value={dataSourceId}
            onChange={setDataSourceId}
            nothingFoundMessage="Нет SQL-источников"
          />
          {dataSourceId && (
            <Group gap="xs">
              {notesData && (
                <Text size="xs" c="dimmed">
                  Заметок: {notesData.count}
                </Text>
              )}
              <Tooltip label="Заполнить смысл из уже созданных инструментов (описания колонок + связи)">
                <Button
                  size="xs"
                  variant="light"
                  color="teal"
                  leftSection={<IconWand size={14} />}
                  loading={seedMut.isPending}
                  onClick={() => seedMut.mutate()}
                >
                  Сидировать из инструментов
                </Button>
              </Tooltip>
            </Group>
          )}
        </Group>

        {!dataSourceId ? (
          <Center style={{ flex: 1 }}>
            <Text c="dimmed" size="sm">
              Выберите SQL-источник, чтобы посмотреть и отредактировать справочник.
            </Text>
          </Center>
        ) : isLoading ? (
          <Center style={{ flex: 1 }}>
            <Loader />
          </Center>
        ) : (
          <ScrollArea style={{ flex: 1 }}>
            <Stack gap="md" pr="sm">
              {grouped.sourceLevel.length > 0 && (
                <Paper withBorder p="sm" radius="md">
                  <Text size="sm" fw={600} mb={4}>
                    Об источнике
                  </Text>
                  {grouped.sourceLevel.map((n) => (
                    <NoteRow key={n.id} tenantId={tenantId} dataSourceId={dataSourceId} note={n} />
                  ))}
                </Paper>
              )}
              {grouped.tables.length === 0 && grouped.sourceLevel.length === 0 && (
                <Text c="dimmed" ta="center" py="lg" size="sm">
                  Справочник пуст. Нажмите «Сидировать из инструментов» или добавьте заметки вручную.
                </Text>
              )}
              {grouped.tables.map(([table, rows]) => (
                <Paper key={table} withBorder p="sm" radius="md">
                  <Group gap={6} mb={6}>
                    <Badge variant="light" color="blue">
                      {table}
                    </Badge>
                    <Text size="xs" c="dimmed">
                      {rows.filter((r) => r.column_name).length} колонок
                    </Text>
                  </Group>
                  <Stack gap={6}>
                    {rows.map((n) => (
                      <NoteRow key={n.id} tenantId={tenantId} dataSourceId={dataSourceId} note={n} />
                    ))}
                  </Stack>
                </Paper>
              ))}
            </Stack>
          </ScrollArea>
        )}

        {dataSourceId && (
          <>
            <Divider label="Добавить заметку" labelPosition="center" />
            <AddNoteRow tenantId={tenantId} dataSourceId={dataSourceId} />
          </>
        )}
      </Stack>
    </Modal>
  );
}
