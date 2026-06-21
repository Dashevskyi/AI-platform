import { useState } from 'react';
import {
  Modal, Textarea, Button, Group, Stack, Text, Badge, Table, ScrollArea, Loader, Alert,
} from '@mantine/core';
import { toolAuditApi, type AuditResult } from '../../shared/api/endpoints';

const LEVEL_COLOR: Record<string, string> = { ok: 'green', warn: 'yellow', error: 'red', info: 'gray' };

interface Props {
  tenantId: string;
  assistantId: string;
  assistantName: string;
  opened: boolean;
  onClose: () => void;
}

/**
 * Routing audit (deterministic, no LLM): paste questions (one per line, optional
 * "question | expected_tool"), see what the pipeline would surface to the model
 * + the Tier-0 verdict, with flags for expected-not-surfaced / tier0-hijack /
 * low-rank. A pre-release sanity check for an assistant's tool configuration.
 */
export function AssistantAuditModal({ tenantId, assistantId, assistantName, opened, onClose }: Props) {
  const [text, setText] = useState(
    'покажи порти свіча косарева 26 | switch_ports_status\nнайди клиента 0676385100 | search_clients\nне працює інтернет | diagnose_service',
  );
  const [res, setRes] = useState<AuditResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setErr(null);
    try {
      const cases = text.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
        const [q, exp] = l.split('|').map((s) => s.trim());
        return { question: q, expect_tool: exp || null };
      });
      setRes(await toolAuditApi.preview(tenantId, assistantId, cases));
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'Ошибка');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} size="xl" title={`Аудит роутинга тулов — ${assistantName}`}>
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          По одному вопросу в строке. Опционально ожидаемый тул через «|».
          Показывает, что pipeline отдаст модели (каталог + score) и вердикт Tier-0. Без вызова LLM.
        </Text>
        <Textarea value={text} onChange={(e) => setText(e.currentTarget.value)} autosize minRows={4} maxRows={10} />
        <Group>
          <Button onClick={run} loading={loading}>Прогнать аудит</Button>
          {res && (
            <Group gap="xs">
              {(['ok', 'warn', 'error', 'info'] as const).map((lvl) =>
                res.summary[lvl] ? <Badge key={lvl} color={LEVEL_COLOR[lvl]}>{lvl}: {res.summary[lvl]}</Badge> : null)}
              <Badge variant="light" color={res.tier0_enabled ? 'blue' : 'gray'}>
                tier0 {res.tier0_enabled ? 'ON' : 'OFF'}
              </Badge>
            </Group>
          )}
        </Group>

        {err && <Alert color="red">{err}</Alert>}
        {loading && <Loader size="sm" />}

        {res && (
          <ScrollArea.Autosize mah={420}>
            <Table stickyHeader striped withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Вопрос</Table.Th>
                  <Table.Th>Вердикт</Table.Th>
                  <Table.Th>Каталог (top, score)</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {res.results.map((c, i) => (
                  <Table.Tr key={i}>
                    <Table.Td style={{ maxWidth: 230 }}>
                      <Text size="sm">{c.question}</Text>
                      {c.expect_tool && <Text size="xs" c="dimmed">ожид: {c.expect_tool}</Text>}
                    </Table.Td>
                    <Table.Td style={{ maxWidth: 240 }}>
                      <Badge color={LEVEL_COLOR[c.verdict.level]} variant="light">{c.verdict.level}</Badge>
                      <Text size="xs" mt={4}>{c.verdict.msg}</Text>
                      {c.tier0?.decision?.fired && c.tier0?.enabled && (
                        <Text size="xs" c="orange">tier0 → {c.tier0.decision.tool}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        {c.surfaced.slice(0, 5).map((s) => (
                          <Badge key={s.name} size="sm" variant="outline"
                            color={c.expect_tool && c.expect_tool.split('|').includes(s.name) ? 'green' : 'gray'}>
                            {s.name} {s.score}
                          </Badge>
                        ))}
                        {c.surfaced.length === 0 && <Text size="xs" c="red">пусто</Text>}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea.Autosize>
        )}
      </Stack>
    </Modal>
  );
}
