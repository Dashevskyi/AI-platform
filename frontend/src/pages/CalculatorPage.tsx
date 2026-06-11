import { useMemo, useState } from 'react';
import {
  Stack,
  Title,
  Text,
  Card,
  Grid,
  NumberInput,
  Select,
  Slider,
  Group,
  Table,
  Progress,
  Alert,
  Collapse,
  Button,
  Badge,
} from '@mantine/core';
import {
  IconCalculator,
  IconAlertTriangle,
  IconCircleCheck,
  IconChevronDown,
  IconChevronRight,
} from '@tabler/icons-react';

type StackKey = 'local' | 'el_flash' | 'el_std' | 'cloud';

const STACK_OPTIONS: { value: StackKey; label: string }[] = [
  { value: 'local', label: 'Локальный — Piper + Whisper + qwen (свои GPU)' },
  { value: 'el_flash', label: 'ElevenLabs Flash + локальные STT/LLM' },
  { value: 'el_std', label: 'ElevenLabs Standard + локальные STT/LLM' },
  { value: 'cloud', label: 'Всё облако (EL std + cloud STT/LLM)' },
];

const fmt = (n: number) => '$' + Math.round(n).toLocaleString('ru-RU');
const fmt2 = (n: number) => '$' + n.toFixed(3);
const ru = (n: number) => Math.round(n).toLocaleString('ru-RU');

export function CalculatorPage() {
  // ── scenario ──
  const [lines, setLines] = useState(3);
  const [hours, setHours] = useState(8);
  const [days, setDays] = useState(30);
  const [occ, setOcc] = useState(50); // %
  const [spk, setSpk] = useState(40); // %
  const [stack, setStack] = useState<StackKey>('el_flash');
  const [tele, setTele] = useState(0.012);
  const [price, setPrice] = useState(2000);
  const [agent, setAgent] = useState(600);
  // ── cost-model constants (advanced) ──
  const [advanced, setAdvanced] = useState(false);
  const [cps, setCps] = useState(15); // chars/sec of speech
  const [elFlash, setElFlash] = useState(75); // $/1M chars
  const [elStd, setElStd] = useState(150);
  const [cstt, setCstt] = useState(0.005); // $/call-min
  const [cllm, setCllm] = useState(0.004);

  const r = useMemo(() => {
    const stacks: Record<StackKey, { ttsPrice: number; stt: number; llm: number }> = {
      local: { ttsPrice: 0, stt: 0, llm: 0 },
      el_flash: { ttsPrice: elFlash / 1e6, stt: 0, llm: 0 },
      el_std: { ttsPrice: elStd / 1e6, stt: 0, llm: 0 },
      cloud: { ttsPrice: elStd / 1e6, stt: cstt, llm: cllm },
    };
    const s = stacks[stack];
    const occF = occ / 100;
    const spkF = spk / 100;
    const callMin = lines * hours * days * 60 * occF;
    const ttsCharsPerMin = spkF * cps * 60;
    const ttsMin = ttsCharsPerMin * s.ttsPrice;
    const perMin = ttsMin + s.stt + s.llm + tele;
    const costMo = perMin * callMin;
    const margin = price - costMo;
    const mPct = price > 0 ? (margin / price) * 100 : 0;
    const speechMin = callMin * spkF;
    const ttsChars = speechMin * cps * 60;
    // occupancy at which margin hits 0 (only meaningful for cloud TTS)
    const baseMin = lines * hours * days * 60;
    const perMinFull = ttsCharsPerMin * s.ttsPrice + s.stt + s.llm + tele;
    const occZero = perMinFull * baseMin > 0 ? price / (perMinFull * baseMin) : Infinity;
    return {
      s, callMin, ttsMin, perMin, costMo, margin, mPct, speechMin, ttsChars, occZero,
      breakdown: [
        { k: 'TTS (озвучка бота)', v: ttsMin },
        { k: 'STT (распознавание абонента)', v: s.stt },
        { k: 'LLM (мозг бота)', v: s.llm },
        { k: 'Телефония (SIP)', v: tele },
      ],
    };
  }, [lines, hours, days, occ, spk, stack, tele, price, agent, cps, elFlash, elStd, cstt, cllm]);

  const agents = lines;
  const humanCost = agents * agent;
  const clientVsHuman = humanCost - price; // >0 → бот дешевле найма

  const marginColor = r.margin < 0 ? 'red' : r.mPct < 30 ? 'orange' : 'teal';

  return (
    <Stack gap="lg">
      <Group gap="sm" align="center">
        <IconCalculator size={26} />
        <div>
          <Title order={2}>Калькулятор голосового ассистента</Title>
          <Text size="sm" c="dimmed">
            Себестоимость минуты звонка, маржа при заданной цене и сравнение со стоимостью живых
            операторов. Все цифры — переменные, меняй под реальные тарифы.
          </Text>
        </div>
      </Group>

      <Grid>
        {/* ── INPUTS ── */}
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Card withBorder padding="lg">
            <Stack gap="sm">
              <Group grow>
                <NumberInput label="Линий" min={1} value={lines} onChange={(v) => setLines(Number(v) || 1)} />
                <NumberInput label="Часов/день" min={1} max={24} value={hours} onChange={(v) => setHours(Number(v) || 1)} />
                <NumberInput label="Дней/мес" min={1} max={31} value={days} onChange={(v) => setDays(Number(v) || 1)} />
              </Group>

              <div>
                <Text size="sm" fw={500}>Занятость линий: {occ}%</Text>
                <Slider min={5} max={100} step={5} value={occ} onChange={setOcc}
                  marks={[{ value: 30, label: '30' }, { value: 50, label: '50' }, { value: 100, label: '100' }]} mb="sm" />
              </div>
              <div>
                <Text size="sm" fw={500}>Бот говорит (% времени звонка): {spk}%</Text>
                <Slider min={10} max={80} step={5} value={spk} onChange={setSpk}
                  marks={[{ value: 25, label: '25' }, { value: 40, label: '40' }, { value: 60, label: '60' }]} mb="sm" />
              </div>

              <Select label="Стек (движки)" data={STACK_OPTIONS} value={stack}
                onChange={(v) => setStack((v as StackKey) || 'el_flash')} allowDeselect={false} />

              <NumberInput label="Телефония, $/мин (SIP inbound)" value={tele} step={0.001} decimalScale={3}
                onChange={(v) => setTele(Number(v) || 0)} />
              <NumberInput label="Цена клиенту, $/мес" value={price} step={50}
                onChange={(v) => setPrice(Number(v) || 0)} />
              <NumberInput label="Зарплата 1 оператора, $/мес" description="для сравнения с наймом людей"
                value={agent} step={50} onChange={(v) => setAgent(Number(v) || 0)} />

              <Button variant="subtle" color="gray" size="xs"
                leftSection={advanced ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                onClick={() => setAdvanced((a) => !a)}>
                Тонкая настройка модели стоимости
              </Button>
              <Collapse expanded={advanced}>
                <Stack gap="xs">
                  <NumberInput label="Символов речи в секунду" value={cps} onChange={(v) => setCps(Number(v) || 1)} />
                  <Group grow>
                    <NumberInput label="EL Flash, $/1М симв." value={elFlash} onChange={(v) => setElFlash(Number(v) || 0)} />
                    <NumberInput label="EL Standard, $/1М симв." value={elStd} onChange={(v) => setElStd(Number(v) || 0)} />
                  </Group>
                  <Group grow>
                    <NumberInput label="Cloud STT, $/мин" value={cstt} step={0.001} decimalScale={3} onChange={(v) => setCstt(Number(v) || 0)} />
                    <NumberInput label="Cloud LLM, $/мин" value={cllm} step={0.001} decimalScale={3} onChange={(v) => setCllm(Number(v) || 0)} />
                  </Group>
                </Stack>
              </Collapse>
            </Stack>
          </Card>
        </Grid.Col>

        {/* ── RESULTS ── */}
        <Grid.Col span={{ base: 12, md: 8 }}>
          <Card withBorder padding="lg">
            <Stack gap="md">
              <Group grow>
                <Card withBorder padding="md" bg="var(--mantine-color-default-hover)">
                  <Text size="xs" c="dimmed" tt="uppercase">Себестоимость / мес</Text>
                  <Text fz={30} fw={700}>{fmt(r.costMo)}</Text>
                  <Text size="xs" c="dimmed">{fmt2(r.perMin)} / мин · {ru(r.callMin)} мин/мес</Text>
                </Card>
                <Card withBorder padding="md" bg="var(--mantine-color-default-hover)">
                  <Text size="xs" c="dimmed" tt="uppercase">Маржа / мес</Text>
                  <Text fz={30} fw={700} c={marginColor}>{fmt(r.margin)}</Text>
                  <Text size="xs" c="dimmed">{r.mPct.toFixed(0)}% маржа</Text>
                </Card>
              </Group>

              <Progress value={Math.max(0, Math.min(100, r.mPct))} color={marginColor} size="lg" radius="sm" />

              {r.margin < 0 ? (
                <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} p="xs">
                  Убыток: цена ниже себестоимости.
                </Alert>
              ) : r.mPct < 30 ? (
                <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />} p="xs">
                  Тонкая маржа (&lt;30%). На тяжёлом клиенте рискованно — лимитируй минуты или возьми локальный голос.
                </Alert>
              ) : (
                <Alert color="teal" variant="light" icon={<IconCircleCheck size={16} />} p="xs">
                  Здоровая маржа.
                </Alert>
              )}

              <Table fz="sm" withTableBorder>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Статья</Table.Th>
                    <Table.Th ta="right">$/мин звонка</Table.Th>
                    <Table.Th ta="right">$/мес</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {r.breakdown.map((row) => (
                    <Table.Tr key={row.k}>
                      <Table.Td>{row.k}</Table.Td>
                      <Table.Td ta="right">
                        {row.v ? fmt2(row.v) : <Text span c="teal">$0 (своё)</Text>}
                      </Table.Td>
                      <Table.Td ta="right">{fmt(row.v * r.callMin)}</Table.Td>
                    </Table.Tr>
                  ))}
                  <Table.Tr>
                    <Table.Td fw={700}>Итого</Table.Td>
                    <Table.Td ta="right" fw={700}>{fmt2(r.perMin)}</Table.Td>
                    <Table.Td ta="right" fw={700}>{fmt(r.costMo)}</Table.Td>
                  </Table.Tr>
                </Table.Tbody>
              </Table>

              <Text size="xs" c="dimmed">
                Объём: {ru(r.callMin)} минут звонков/мес · из них речь бота ≈ {ru(r.speechMin)} мин ({ru(r.ttsChars)} символов TTS).
              </Text>

              <Card withBorder padding="sm" bg="var(--mantine-color-default-hover)">
                <Text size="sm" fw={600} mb={6}>Сравнение с людьми</Text>
                <Table fz="sm">
                  <Table.Tbody>
                    <Table.Tr>
                      <Table.Td>Заменяет операторов (параллельные звонки)</Table.Td>
                      <Table.Td ta="right"><Badge variant="light">{agents}</Badge></Table.Td>
                    </Table.Tr>
                    <Table.Tr>
                      <Table.Td>Стоила бы живая смена клиенту</Table.Td>
                      <Table.Td ta="right">{fmt(humanCost)}/мес</Table.Td>
                    </Table.Tr>
                    <Table.Tr>
                      <Table.Td>Клиент платит за бота</Table.Td>
                      <Table.Td ta="right">{fmt(price)}/мес</Table.Td>
                    </Table.Tr>
                    <Table.Tr>
                      <Table.Td>Выгода клиента vs наём</Table.Td>
                      <Table.Td ta="right" c={clientVsHuman >= 0 ? 'teal' : 'red'}>
                        {clientVsHuman >= 0 ? '−' : '+'}{fmt(Math.abs(clientVsHuman))}/мес
                      </Table.Td>
                    </Table.Tr>
                  </Table.Tbody>
                </Table>
                <Text size="xs" c="dimmed" mt={4}>
                  + бот работает 24/7, без больничных, мгновенно масштабируется.
                </Text>
              </Card>

              {r.s.ttsPrice > 0 ? (
                r.occZero <= 1 ? (
                  <Text size="xs" c="orange">
                    При этом тарифе маржа уходит в ноль на занятости ≈ {Math.round(r.occZero * 100)}% — выше этого
                    облачный TTS съедает прибыль. Либо лимит минут, либо локальный голос.
                  </Text>
                ) : (
                  <Text size="xs" c="dimmed">
                    Облачный TTS безопасен на любой занятости при этой цене (маржа не уходит в ноль).
                  </Text>
                )
              ) : (
                <Text size="xs" c="teal">
                  Локальный голос — фиксированный расход: маржа стабильна при любой нагрузке.
                </Text>
              )}
            </Stack>
          </Card>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
