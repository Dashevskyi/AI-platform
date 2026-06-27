import { useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { IconHistory, IconDeviceFloppy } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { shellApi } from '../../shared/api/endpoints';
import type { OntologyJson, OntologySection } from '../../shared/api/types';
import { extractOntologyFromVersionPayload, restoreSection } from './ontologyVersions';

type Props = {
  opened: boolean;
  onClose: () => void;
  tenantId: string;
  ontology: OntologyJson | null;
  onApply: (next: OntologyJson) => void;
};

type VersionRow = {
  id: string;
  changed_at: string;
  changed_by: string | null;
  comment: string | null;
  section_count: number;
};

export function OntologySectionHistoryModal({ opened, onClose, tenantId, ontology, onApply }: Props) {
  const [versions, setVersions] = useState<VersionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [snapshotSections, setSnapshotSections] = useState<OntologySection[]>([]);
  const [snapshotComment, setSnapshotComment] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!opened) return;
    setLoading(true);
    shellApi.ontologyListVersions(tenantId, 1, 30)
      .then((r) => setVersions(r.items))
      .catch(() => notifications.show({ color: 'red', message: 'Не удалось загрузить версии' }))
      .finally(() => setLoading(false));
  }, [opened, tenantId]);

  const loadVersion = async (versionId: string) => {
    setSelectedVersionId(versionId);
    setSnapshotSections([]);
    try {
      const detail = await shellApi.getVersion(tenantId, versionId);
      const oj = extractOntologyFromVersionPayload(detail.new_payload);
      setSnapshotSections(oj?.sections || []);
    } catch {
      notifications.show({ color: 'red', message: 'Не удалось загрузить версию' });
    }
  };

  const saveSnapshot = async () => {
    if (!ontology?.sections?.length) {
      notifications.show({ color: 'yellow', message: 'Нечего сохранять' });
      return;
    }
    setSaving(true);
    try {
      await shellApi.ontologySnapshot(tenantId, {
        ontology_json: ontology,
        comment: snapshotComment.trim() || undefined,
      });
      notifications.show({ color: 'green', message: 'Снимок сохранён' });
      setSnapshotComment('');
      const r = await shellApi.ontologyListVersions(tenantId, 1, 30);
      setVersions(r.items);
    } catch {
      notifications.show({ color: 'red', message: 'Ошибка сохранения снимка' });
    } finally {
      setSaving(false);
    }
  };

  const restoreOne = (section: OntologySection) => {
    const next = restoreSection(ontology, section);
    onApply(next);
    notifications.show({ color: 'green', message: `Секция «${section.title}» восстановлена из версии` });
  };

  return (
    <Modal opened={opened} onClose={onClose} title="История секций онтологии" size="lg">
      <Stack gap="md">
        <Alert variant="light" color="blue" icon={<IconHistory size={16} />}>
          Снимки создаются при сохранении настроек обolочки или вручную. Можно восстановить отдельную секцию из прошлой версии.
        </Alert>

        <Group align="flex-end">
          <TextInput
            style={{ flex: 1 }}
            label="Комментарий к снимку"
            placeholder="Перед правками промптов…"
            value={snapshotComment}
            onChange={(e) => setSnapshotComment(e.currentTarget.value)}
          />
          <Button
            leftSection={<IconDeviceFloppy size={16} />}
            loading={saving}
            onClick={saveSnapshot}
          >
            Сохранить снимок
          </Button>
        </Group>

        {loading ? <Loader size="sm" /> : (
          <ScrollArea.Autosize mah={160}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Дата</Table.Th>
                  <Table.Th>Автор</Table.Th>
                  <Table.Th>Секций</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {versions.length === 0 && (
                  <Table.Tr><Table.Td colSpan={4}><Text size="sm" c="dimmed">Нет версий с изменениями онтологии</Text></Table.Td></Table.Tr>
                )}
                {versions.map((v) => (
                  <Table.Tr key={v.id} bg={selectedVersionId === v.id ? 'var(--mantine-color-blue-0)' : undefined}>
                    <Table.Td>
                      <Text size="sm">{new Date(v.changed_at).toLocaleString()}</Text>
                      {v.comment && <Text size="xs" c="dimmed" lineClamp={1}>{v.comment}</Text>}
                    </Table.Td>
                    <Table.Td><Text size="sm">{v.changed_by || '—'}</Text></Table.Td>
                    <Table.Td><Badge size="sm">{v.section_count}</Badge></Table.Td>
                    <Table.Td>
                      <Button size="xs" variant="light" onClick={() => loadVersion(v.id)}>Открыть</Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea.Autosize>
        )}

        {snapshotSections.length > 0 && (
          <Stack gap="xs">
            <Text size="sm" fw={600}>Секции в выбранной версии</Text>
            <ScrollArea.Autosize mah={220}>
              <Stack gap={6}>
                {snapshotSections.map((sec, i) => (
                  <Group key={sec.id || i} justify="space-between" wrap="nowrap">
                    <div>
                      <Text size="sm" fw={500}>{sec.title || sec.type}</Text>
                      <Text size="xs" c="dimmed">{sec.type}</Text>
                    </div>
                    <Button size="xs" variant="subtle" onClick={() => restoreOne(sec)}>
                      Восстановить
                    </Button>
                  </Group>
                ))}
              </Stack>
            </ScrollArea.Autosize>
          </Stack>
        )}
      </Stack>
    </Modal>
  );
}
