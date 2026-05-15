import { useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Center,
  Group,
  Loader,
  Modal,
  Pagination,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Textarea,
  Tooltip,
} from '@mantine/core';
import { IconPlus, IconRefresh, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { kbApi } from '../../shared/api/endpoints';
import type { KBDocument, KBDocumentCreate, KBDocumentUpdate } from '../../shared/api/types';

const SOURCE_TYPE_OPTIONS = [
  { value: 'manual', label: 'Ручной ввод' },
  { value: 'faq', label: 'FAQ' },
  { value: 'solution', label: 'Решение' },
  { value: 'procedure', label: 'Процедура' },
  { value: 'reference', label: 'Справка' },
];

const DOC_TYPE_OPTIONS = [
  { value: 'text', label: 'Текст' },
  { value: 'url', label: 'Ссылка (URL)' },
  { value: 'file', label: 'Файл' },
];

const EMBEDDING_STATUS_MAP: Record<string, { color: string; label: string }> = {
  pending: { color: 'yellow', label: 'Ожидание' },
  processing: { color: 'blue', label: 'Обработка' },
  done: { color: 'green', label: 'Готово' },
  error: { color: 'red', label: 'Ошибка' },
};

type KBTabProps = {
  tenantId: string;
};

export function KBTab({ tenantId }: KBTabProps) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [docType, setDocType] = useState<string>('text');
  const [sourceType, setSourceType] = useState<string>('manual');
  const [sourceUrl, setSourceUrl] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [docActive, setDocActive] = useState(true);
  const [filterDocType, setFilterDocType] = useState<string | null>(null);
  const [filterSourceType, setFilterSourceType] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'kb', page, filterDocType, filterSourceType],
    queryFn: () => kbApi.list(
      tenantId,
      page,
      20,
      filterDocType || undefined,
      filterSourceType || undefined,
    ),
  });

  const openCreate = () => {
    setEditId(null);
    setTitle('');
    setContent('');
    setDocType('text');
    setSourceType('manual');
    setSourceUrl('');
    setUploadFile(null);
    setDocActive(true);
    setModalOpen(true);
  };

  const openEdit = (doc: KBDocument) => {
    setEditId(doc.id);
    setTitle(doc.title);
    setContent(doc.content);
    setDocType(doc.doc_type);
    setSourceType(doc.source_type);
    setSourceUrl(doc.source_url || '');
    setUploadFile(null);
    setDocActive(doc.is_active);
    setModalOpen(true);
  };

  const invalidateKB = () => queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'kb'] });

  const createMutation = useMutation({
    mutationFn: (data: KBDocumentCreate) => kbApi.create(tenantId, data),
    onSuccess: () => {
      invalidateKB();
      setModalOpen(false);
      notifications.show({
        title: 'Создано',
        message: 'Документ создан и отправлен на индексацию',
        color: 'green',
      });
    },
    onError: (err: Error) => {
      notifications.show({
        title: 'Ошибка',
        message: err.message || 'Не удалось создать документ',
        color: 'red',
      });
    },
  });

  const uploadMutation = useMutation({
    mutationFn: ({ file, title, sourceType }: { file: File; title: string; sourceType: string }) =>
      kbApi.upload(tenantId, file, title, sourceType),
    onSuccess: () => {
      invalidateKB();
      setModalOpen(false);
      notifications.show({
        title: 'Загружено',
        message: 'Файл загружен и отправлен на индексацию',
        color: 'green',
      });
    },
    onError: (err: Error) => {
      notifications.show({
        title: 'Ошибка',
        message: err.message || 'Не удалось загрузить файл',
        color: 'red',
      });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ docId, data }: { docId: string; data: KBDocumentUpdate }) =>
      kbApi.update(tenantId, docId, data),
    onSuccess: () => {
      invalidateKB();
      setModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Документ обновлён', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось обновить документ', color: 'red' });
    },
  });

  const reembedMutation = useMutation({
    mutationFn: (docId: string) => kbApi.reembed(tenantId, docId),
    onSuccess: () => {
      invalidateKB();
      notifications.show({ title: 'Переиндексация', message: 'Документ переиндексирован', color: 'green' });
    },
  });

  const reembedAllMutation = useMutation({
    mutationFn: () => kbApi.reembedAll(tenantId),
    onSuccess: (res) => {
      invalidateKB();
      notifications.show({
        title: 'Переиндексация всех',
        message: `Готово: ${res.success} успешно, ${res.error} ошибок из ${res.total}`,
        color: res.error > 0 ? 'yellow' : 'green',
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (docId: string) => kbApi.delete(tenantId, docId),
    onSuccess: () => {
      invalidateKB();
      notifications.show({ title: 'Удалено', message: 'Документ удалён', color: 'green' });
    },
  });

  const handleSave = () => {
    if (editId) {
      updateMutation.mutate({
        docId: editId,
        data: { title, content, source_type: sourceType, is_active: docActive },
      });
    } else if (docType === 'file' && uploadFile) {
      uploadMutation.mutate({ file: uploadFile, title, sourceType });
    } else {
      createMutation.mutate({
        title,
        doc_type: docType,
        source_type: sourceType,
        source_url: docType === 'url' ? sourceUrl : undefined,
        content: docType === 'url' ? '' : content,
        is_active: docActive,
      });
    }
  };

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;
  const isSaving = createMutation.isPending || updateMutation.isPending || uploadMutation.isPending;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>База знаний (RAG)</Text>
        <Group gap="xs">
          <Button
            variant="light"
            size="sm"
            leftSection={<IconRefresh size={16} />}
            onClick={() => reembedAllMutation.mutate()}
            loading={reembedAllMutation.isPending}
          >
            Переиндексировать всё
          </Button>
          <Button leftSection={<IconPlus size={16} />} size="sm" onClick={openCreate}>
            Добавить
          </Button>
        </Group>
      </Group>

      <Text size="sm" c="dimmed">
        Релевантные фрагменты документов автоматически подбираются по смыслу запроса пользователя (семантический поиск).
      </Text>

      <Group gap="xs">
        <Select
          placeholder="Тип источника"
          data={[{ value: '', label: 'Все типы' }, ...DOC_TYPE_OPTIONS]}
          value={filterDocType || ''}
          onChange={(value) => {
            setFilterDocType(value || null);
            setPage(1);
          }}
          size="xs"
          w={160}
          clearable
        />
        <Select
          placeholder="Категория"
          data={[{ value: '', label: 'Все категории' }, ...SOURCE_TYPE_OPTIONS]}
          value={filterSourceType || ''}
          onChange={(value) => {
            setFilterSourceType(value || null);
            setPage(1);
          }}
          size="xs"
          w={160}
          clearable
        />
      </Group>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">Документов базы знаний нет.</Text>
      ) : (
        <>
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Заголовок</Table.Th>
                <Table.Th>Тип</Table.Th>
                <Table.Th>Категория</Table.Th>
                <Table.Th>Индексация</Table.Th>
                <Table.Th>Чанков</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((doc) => {
                const embStatus = EMBEDDING_STATUS_MAP[doc.embedding_status] || {
                  color: 'gray',
                  label: doc.embedding_status,
                };
                return (
                  <Table.Tr key={doc.id} style={{ cursor: 'pointer' }} onClick={() => openEdit(doc)}>
                    <Table.Td>
                      <Text size="sm" fw={500}>{doc.title}</Text>
                      {doc.source_url && (
                        <Text size="xs" c="dimmed" truncate="end" maw={250}>{doc.source_url}</Text>
                      )}
                      {doc.source_filename && (
                        <Text size="xs" c="dimmed">{doc.source_filename}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light" size="sm">
                        {DOC_TYPE_OPTIONS.find((option) => option.value === doc.doc_type)?.label || doc.doc_type}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="dot" size="sm">
                        {SOURCE_TYPE_OPTIONS.find((option) => option.value === doc.source_type)?.label || doc.source_type}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label={doc.embedding_error || ''} disabled={!doc.embedding_error}>
                        <Badge color={embStatus.color} size="sm">{embStatus.label}</Badge>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td>{doc.chunks_count}</Table.Td>
                    <Table.Td>
                      <Badge color={doc.is_active ? 'green' : 'gray'} size="sm">
                        {doc.is_active ? 'Активный' : 'Выкл'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        <Tooltip label="Переиндексировать">
                          <ActionIcon
                            variant="subtle"
                            color="blue"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation();
                              reembedMutation.mutate(doc.id);
                            }}
                          >
                            <IconRefresh size={14} />
                          </ActionIcon>
                        </Tooltip>
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            if (window.confirm(`Удалить "${doc.title}"?`)) {
                              deleteMutation.mutate(doc.id);
                            }
                          }}
                        >
                          <IconTrash size={14} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
          {totalPages > 1 && (
            <Center><Pagination total={totalPages} value={page} onChange={setPage} /></Center>
          )}
        </>
      )}

      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editId ? 'Редактировать документ' : 'Добавить в базу знаний'}
        size="lg"
      >
        <Stack gap="md">
          {!editId && (
            <Select
              label="Тип источника"
              data={DOC_TYPE_OPTIONS}
              value={docType}
              onChange={(value) => setDocType(value || 'text')}
            />
          )}

          <TextInput
            label="Заголовок"
            placeholder="Инструкция по настройке роутера"
            value={title}
            onChange={(e) => setTitle(e.currentTarget.value)}
            required
          />

          <Select
            label="Категория"
            description="Помогает LLM понять тип информации"
            data={SOURCE_TYPE_OPTIONS}
            value={sourceType}
            onChange={(value) => setSourceType(value || 'manual')}
          />

          {docType === 'url' && !editId && (
            <TextInput
              label="URL страницы"
              description="Содержимое страницы будет автоматически извлечено"
              placeholder="https://docs.example.com/article"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.currentTarget.value)}
              required
            />
          )}

          {docType === 'file' && !editId && (
            <div>
              <Text size="sm" fw={500} mb={4}>Файл</Text>
              <Text size="xs" c="dimmed" mb={8}>PDF, TXT, MD, CSV, HTML — до 10 МБ</Text>
              <input
                type="file"
                accept=".pdf,.txt,.md,.csv,.log,.json,.xml,.html"
                onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
              />
            </div>
          )}

          {(docType === 'text' || editId) && (
            <Textarea
              label="Содержание"
              description="Текст будет автоматически разбит на чанки и проиндексирован"
              placeholder="Для настройки роутера TP-Link выполните следующие шаги..."
              value={content}
              onChange={(e) => setContent(e.currentTarget.value)}
              autosize
              minRows={8}
              maxRows={20}
            />
          )}

          <Switch
            label="Активный"
            checked={docActive}
            onChange={(e) => setDocActive(e.currentTarget.checked)}
          />

          <Group justify="flex-end">
            <Button variant="default" onClick={() => setModalOpen(false)}>Отмена</Button>
            <Button onClick={handleSave} loading={isSaving}>
              {editId ? 'Обновить' : docType === 'file' ? 'Загрузить' : 'Создать'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
