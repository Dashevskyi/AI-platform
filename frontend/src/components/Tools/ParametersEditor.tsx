/**
 * Typed, recursive JSON Schema editor for tool parameters.
 *
 * The editor manipulates a JSON Schema "properties" subtree (the body of
 * `function.parameters.properties` in OpenAI-style tool definitions). It
 * supports the types we actually use in the platform: string, integer,
 * number, boolean, array, object, plus oneOf for params that legitimately
 * accept multiple shapes (e.g. switch_id = integer ID OR IPv4 string).
 *
 * Each parameter is rendered as a Card with:
 *   - name / description / required toggle (required-list is owned by parent)
 *   - type dropdown
 *   - type-specific constraints (enum, pattern, min/max, items, properties, ...)
 *
 * The editor never mutates props — every change re-builds the parameters
 * subtree from scratch via the onChange callback. This keeps it pure and
 * compatible with React-Query-cached form state.
 */
import { useMemo, useState, useEffect } from 'react';
import {
  ActionIcon,
  Badge,
  Box,
  Button,
  Card,
  Group,
  NavLink,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
  Tooltip,
} from '@mantine/core';
import { IconPlus, IconTrash, IconCopy, IconInfoCircle, IconListDetails } from '@tabler/icons-react';

/** Compact label with a ⓘ tooltip icon — replaces verbose `description` props. */
function Hint({ label, tip, w = 240 }: { label: string; tip: string; w?: number }) {
  return (
    <Group gap={3} wrap="nowrap">
      <span>{label}</span>
      <Tooltip label={tip} multiline w={w} withArrow position="top">
        <IconInfoCircle
          size={13}
          style={{ color: 'var(--mantine-color-dimmed)', cursor: 'help', flexShrink: 0 }}
        />
      </Tooltip>
    </Group>
  );
}

// JSON Schema subset we actually allow in the UI.
export type JsonSchema = {
  type?: string | string[];
  description?: string;
  // string
  enum?: unknown[];
  pattern?: string;
  format?: string;
  minLength?: number;
  maxLength?: number;
  // numeric
  minimum?: number;
  maximum?: number;
  multipleOf?: number;
  // array
  items?: JsonSchema;
  minItems?: number;
  maxItems?: number;
  uniqueItems?: boolean;
  // object
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean | JsonSchema;
  // alternation
  oneOf?: JsonSchema[];
  // misc
  default?: unknown;
  [k: string]: unknown;
};

type ParamType = 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object' | 'oneOf';

const TYPE_OPTIONS: { value: ParamType; label: string; hint: string }[] = [
  { value: 'string',  label: 'string',  hint: 'Текст' },
  { value: 'integer', label: 'integer', hint: 'Целое число' },
  { value: 'number',  label: 'number',  hint: 'Число (с десятичной частью)' },
  { value: 'boolean', label: 'boolean', hint: 'true / false' },
  { value: 'array',   label: 'array',   hint: 'Список значений одного типа' },
  { value: 'object',  label: 'object',  hint: 'Вложенный объект со своими полями' },
  { value: 'oneOf',   label: 'oneOf',   hint: 'Несколько допустимых форм (например integer ИЛИ строка)' },
];

const STRING_FORMATS = [
  { value: '', label: '— нет —' },
  { value: 'date', label: 'date' },
  { value: 'date-time', label: 'date-time' },
  { value: 'time', label: 'time' },
  { value: 'email', label: 'email' },
  { value: 'uri', label: 'uri' },
  { value: 'uuid', label: 'uuid' },
  { value: 'ipv4', label: 'ipv4' },
  { value: 'ipv6', label: 'ipv6' },
  { value: 'hostname', label: 'hostname' },
];

/** Detect the effective type-key from a schema (handles oneOf). */
export function detectType(schema: JsonSchema | undefined): ParamType {
  if (!schema) return 'string';
  if (Array.isArray(schema.oneOf) && schema.oneOf.length > 0) return 'oneOf';
  const t = schema.type;
  if (typeof t === 'string' && ['string','integer','number','boolean','array','object'].includes(t)) {
    return t as ParamType;
  }
  return 'string';
}

/** Produce an empty schema for a freshly chosen type — preserves description. */
function emptySchemaForType(type: ParamType, prevDescription = ''): JsonSchema {
  switch (type) {
    case 'string':  return { type: 'string', description: prevDescription };
    case 'integer': return { type: 'integer', description: prevDescription };
    case 'number':  return { type: 'number',  description: prevDescription };
    case 'boolean': return { type: 'boolean', description: prevDescription };
    case 'array':   return { type: 'array', description: prevDescription, items: { type: 'string' } };
    case 'object':  return { type: 'object', description: prevDescription, properties: {}, required: [] };
    case 'oneOf':   return { description: prevDescription, oneOf: [{ type: 'integer' }, { type: 'string' }] };
  }
}

/** Sanitize CSV enum input: split, trim, drop empty, coerce numerics when type is numeric. */
function parseEnum(raw: string, asNumber: boolean): unknown[] | undefined {
  const tokens = raw.split(',').map((x) => x.trim()).filter(Boolean);
  if (tokens.length === 0) return undefined;
  if (!asNumber) return tokens;
  const nums = tokens.map((t) => Number(t)).filter((n) => Number.isFinite(n));
  return nums.length > 0 ? nums : undefined;
}

function serializeEnum(values: unknown[] | undefined): string {
  if (!Array.isArray(values)) return '';
  return values.map((v) => String(v)).join(', ');
}

// =====================================================================
// Per-type constraint editors
// =====================================================================

function StringConstraints({ schema, onChange }: { schema: JsonSchema; onChange: (s: JsonSchema) => void }) {
  return (
    <Stack gap="xs">
      {/* format + pattern + minLength + maxLength — one row, equal baseline, no descriptions */}
      <Group gap="xs" align="flex-end" wrap="wrap">
        <Select
          label={<Hint label="format" tip="Готовая семантика: date / email / uri / ipv4 / uuid / hostname. Модель сразу понимает тип значения без дополнительного описания." />}
          data={STRING_FORMATS}
          value={typeof schema.format === 'string' ? schema.format : ''}
          onChange={(v) => onChange({ ...schema, format: v || undefined })}
          clearable={false}
          w={145}
        />
        <TextInput
          label={<Hint label="pattern" tip="Regex-маска. Пример: ^\d+(-\d+|(,\d+)*)$ — целое, диапазон или CSV. Модель должна передать значение, совпадающее с этим паттерном." w={280} />}
          value={schema.pattern ?? ''}
          onChange={(e) => onChange({ ...schema, pattern: e.currentTarget.value || undefined })}
          style={{ flex: '1 1 150px', minWidth: 110 }}
        />
        <NumberInput
          label={<Hint label="minLength" tip="Минимальная длина строки (включительно). Пустое = без ограничения." />}
          value={schema.minLength ?? ''}
          onChange={(v) => onChange({ ...schema, minLength: typeof v === 'number' ? v : undefined })}
          min={0}
          w={90}
        />
        <NumberInput
          label={<Hint label="maxLength" tip="Максимальная длина строки (включительно). Пустое = без ограничения." />}
          value={schema.maxLength ?? ''}
          onChange={(v) => onChange({ ...schema, maxLength: typeof v === 'number' ? v : undefined })}
          min={0}
          w={90}
        />
      </Group>
      <TextInput
        label={<Hint label="enum" tip="Если задан — модель ОБЯЗАНА выбрать значение только из этого списка. Через запятую: active, blocked, paused" />}
        value={serializeEnum(schema.enum)}
        onChange={(e) => onChange({ ...schema, enum: parseEnum(e.currentTarget.value, false) })}
      />
    </Stack>
  );
}

function NumericConstraints({ schema, onChange, isInt }: { schema: JsonSchema; onChange: (s: JsonSchema) => void; isInt: boolean }) {
  return (
    <Stack gap="xs">
      <Group gap="xs">
        <NumberInput
          label="minimum"
          value={schema.minimum ?? ''}
          onChange={(v) => onChange({ ...schema, minimum: typeof v === 'number' ? v : undefined })}
          allowDecimal={!isInt}
          w={100}
        />
        <NumberInput
          label="maximum"
          value={schema.maximum ?? ''}
          onChange={(v) => onChange({ ...schema, maximum: typeof v === 'number' ? v : undefined })}
          allowDecimal={!isInt}
          w={100}
        />
        <NumberInput
          label="multipleOf"
          value={schema.multipleOf ?? ''}
          onChange={(v) => onChange({ ...schema, multipleOf: typeof v === 'number' ? v : undefined })}
          allowDecimal={!isInt}
          min={0}
          w={100}
        />
      </Group>
      <TextInput
        label={<Hint label="enum" tip="Если задан — модель ОБЯЗАНА выбрать значение только из этого списка. Через запятую: 1, 10, 100" />}
        value={serializeEnum(schema.enum)}
        onChange={(e) => onChange({ ...schema, enum: parseEnum(e.currentTarget.value, true) })}
      />
    </Stack>
  );
}

function ArrayConstraints({ schema, onChange }: { schema: JsonSchema; onChange: (s: JsonSchema) => void }) {
  const items = schema.items || { type: 'string' };
  const itemType = detectType(items);
  return (
    <Stack gap="xs">
      <Group gap="xs">
        <NumberInput
          label="minItems"
          value={schema.minItems ?? ''}
          onChange={(v) => onChange({ ...schema, minItems: typeof v === 'number' ? v : undefined })}
          min={0}
          w={90}
        />
        <NumberInput
          label="maxItems"
          value={schema.maxItems ?? ''}
          onChange={(v) => onChange({ ...schema, maxItems: typeof v === 'number' ? v : undefined })}
          min={0}
          w={90}
        />
        <Switch
          label="uniqueItems"
          checked={!!schema.uniqueItems}
          onChange={(e) => onChange({ ...schema, uniqueItems: e.currentTarget.checked || undefined })}
          mt={28}
        />
      </Group>
      <Card withBorder padding="xs" bg="var(--mantine-color-gray-light)">
        <Group justify="space-between" mb={4}>
          <Text size="xs" fw={600}>items — тип элементов массива</Text>
          <Badge size="xs" variant="light" color="blue">{itemType}</Badge>
        </Group>
        <Select
          label="Тип элементов"
          data={TYPE_OPTIONS.map((o) => ({ value: o.value, label: `${o.label} — ${o.hint}` }))}
          value={itemType}
          onChange={(v) => {
            const newType = (v as ParamType) || 'string';
            onChange({ ...schema, items: emptySchemaForType(newType, items.description || '') });
          }}
          mb={6}
        />
        <SchemaConstraintsByType
          schema={items}
          type={itemType}
          onChange={(newItems) => onChange({ ...schema, items: newItems })}
        />
      </Card>
    </Stack>
  );
}

function ObjectConstraints({ schema, onChange }: { schema: JsonSchema; onChange: (s: JsonSchema) => void }) {
  const properties = schema.properties || {};
  const required = Array.isArray(schema.required) ? schema.required : [];
  return (
    <Card withBorder padding="xs" bg="var(--mantine-color-gray-light)">
      <Text size="xs" fw={600} mb={6}>properties — вложенные поля объекта</Text>
      <ParametersEditor
        parameters={properties}
        required={required}
        onChange={(newProps, newRequired) => onChange({ ...schema, properties: newProps, required: newRequired })}
      />
    </Card>
  );
}

function OneOfConstraints({ schema, onChange }: { schema: JsonSchema; onChange: (s: JsonSchema) => void }) {
  const variants = Array.isArray(schema.oneOf) ? schema.oneOf : [];
  const updateVariant = (idx: number, updater: (v: JsonSchema) => JsonSchema) => {
    const next = variants.map((v, i) => (i === idx ? updater(v) : v));
    onChange({ ...schema, oneOf: next });
  };
  const addVariant = () => onChange({ ...schema, oneOf: [...variants, { type: 'string' }] });
  const removeVariant = (idx: number) => onChange({ ...schema, oneOf: variants.filter((_, i) => i !== idx) });

  return (
    <Stack gap="xs">
      <Text size="xs" c="dimmed">
        oneOf: значение должно соответствовать ровно ОДНОМУ из вариантов ниже. Используй когда параметр
        принимает разные формы (например integer ИЛИ строка-IP).
      </Text>
      {variants.map((variant, idx) => {
        const vType = detectType(variant);
        return (
          <Card key={idx} withBorder padding="xs" bg="var(--mantine-color-gray-light)">
            <Group justify="space-between" mb={4}>
              <Group gap="xs">
                <Badge variant="light" color="blue">Вариант {idx + 1}</Badge>
                <Badge variant="light" color="gray" size="xs">{vType}</Badge>
              </Group>
              <Tooltip label="Удалить вариант">
                <ActionIcon variant="subtle" color="red" size="sm" onClick={() => removeVariant(idx)}>
                  <IconTrash size={14} />
                </ActionIcon>
              </Tooltip>
            </Group>
            <Select
              label="Тип варианта"
              data={TYPE_OPTIONS.filter((o) => o.value !== 'oneOf').map((o) => ({ value: o.value, label: `${o.label} — ${o.hint}` }))}
              value={vType}
              onChange={(v) => {
                const newType = (v as ParamType) || 'string';
                updateVariant(idx, () => emptySchemaForType(newType, variant.description || ''));
              }}
              mb={6}
            />
            <SchemaConstraintsByType
              schema={variant}
              type={vType}
              onChange={(s) => updateVariant(idx, () => s)}
            />
          </Card>
        );
      })}
      <Button leftSection={<IconPlus size={14} />} variant="light" size="xs" onClick={addVariant}>
        Добавить вариант
      </Button>
    </Stack>
  );
}

/** Render the appropriate constraints editor for a given type. */
function SchemaConstraintsByType({ schema, type, onChange }: {
  schema: JsonSchema; type: ParamType; onChange: (s: JsonSchema) => void;
}) {
  if (type === 'string') return <StringConstraints schema={schema} onChange={onChange} />;
  if (type === 'integer') return <NumericConstraints schema={schema} onChange={onChange} isInt />;
  if (type === 'number') return <NumericConstraints schema={schema} onChange={onChange} isInt={false} />;
  if (type === 'boolean') return null;
  if (type === 'array') return <ArrayConstraints schema={schema} onChange={onChange} />;
  if (type === 'object') return <ObjectConstraints schema={schema} onChange={onChange} />;
  if (type === 'oneOf') return <OneOfConstraints schema={schema} onChange={onChange} />;
  return null;
}

// =====================================================================
// Per-parameter card (name + type + description + constraints)
// =====================================================================

function ParameterCard({
  name, schema, isRequired,
  onRename, onChangeSchema, onToggleRequired, onRemove,
}: {
  name: string;
  schema: JsonSchema;
  isRequired: boolean;
  onRename: (newName: string) => void;
  onChangeSchema: (s: JsonSchema) => void;
  onToggleRequired: (r: boolean) => void;
  onRemove: () => void;
}) {
  const type = detectType(schema);
  const description = typeof schema.description === 'string' ? schema.description : '';

  // Local edit state for the name — committed on blur/Enter so focus isn't lost
  // on every keystroke (key={name} in the parent would cause remount otherwise).
  const [localName, setLocalName] = useState(name);
  useEffect(() => { setLocalName(name); }, [name]);

  const commitRename = () => {
    const trimmed = localName.trim();
    if (trimmed && trimmed !== name) onRename(trimmed);
    else setLocalName(name); // revert empty or unchanged
  };

  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" wrap="nowrap" mb="xs">
        <Group gap="xs" style={{ flex: 1 }}>
          <TextInput
            value={localName}
            onChange={(e) => setLocalName(e.currentTarget.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
              if (e.key === 'Escape') setLocalName(name);
            }}
            placeholder="snake_case"
            style={{ flex: 1, maxWidth: 280 }}
            ff="monospace"
            size="sm"
          />
          <Select
            value={type}
            data={TYPE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            onChange={(v) => onChangeSchema(emptySchemaForType((v as ParamType) || 'string', description))}
            w={130}
            size="sm"
            allowDeselect={false}
          />
          <Switch
            label="Обязательный"
            checked={isRequired}
            onChange={(e) => onToggleRequired(e.currentTarget.checked)}
            size="sm"
          />
        </Group>
        <Tooltip label="Удалить параметр">
          <ActionIcon variant="subtle" color="red" onClick={onRemove}>
            <IconTrash size={16} />
          </ActionIcon>
        </Tooltip>
      </Group>
      <Textarea
        label={<Hint label="Описание для LLM" tip="Что модель должна знать про этот параметр. Чем точнее — тем меньше галлюцинаций при заполнении." w={300} />}
        value={description}
        onChange={(e) => onChangeSchema({ ...schema, description: e.currentTarget.value || undefined })}
        minRows={2}
        autosize
        maxRows={6}
        mb="xs"
      />
      <SchemaConstraintsByType schema={schema} type={type} onChange={onChangeSchema} />
    </Card>
  );
}

// =====================================================================
// Top-level editor for a properties + required pair
// =====================================================================

export function ParametersEditor({
  parameters, required, onChange,
}: {
  parameters: Record<string, JsonSchema>;
  required: string[];
  onChange: (newProperties: Record<string, JsonSchema>, newRequired: string[]) => void;
}) {
  const names = useMemo(() => Object.keys(parameters || {}), [parameters]);
  const [selectedName, setSelectedName] = useState<string | null>(names[0] ?? null);

  useEffect(() => {
    if (!names.length) {
      setSelectedName(null);
      return;
    }
    if (!selectedName || !names.includes(selectedName)) {
      setSelectedName(names[0]);
    }
  }, [names, selectedName]);

  const updateOne = (oldName: string, newName: string, newSchema: JsonSchema) => {
    const next: Record<string, JsonSchema> = {};
    const finalName = (() => {
      const trimmed = (newName || '').trim();
      if (!trimmed) return oldName;
      if (trimmed === oldName) return trimmed;
      let candidate = trimmed;
      let i = 2;
      while (candidate !== oldName && candidate in parameters) {
        candidate = `${trimmed}_${i++}`;
      }
      return candidate;
    })();
    for (const n of names) {
      if (n === oldName) next[finalName] = newSchema;
      else next[n] = parameters[n];
    }
    const newReq = required
      .map((r) => (r === oldName ? finalName : r))
      .filter((r, idx, arr) => arr.indexOf(r) === idx);
    if (oldName === selectedName && finalName !== oldName) setSelectedName(finalName);
    onChange(next, newReq);
  };

  const toggleRequired = (name: string, makeRequired: boolean) => {
    const next = makeRequired
      ? Array.from(new Set([...required, name]))
      : required.filter((r) => r !== name);
    onChange(parameters, next);
  };

  const removeParam = (name: string) => {
    const next: Record<string, JsonSchema> = {};
    for (const n of names) if (n !== name) next[n] = parameters[n];
    const nextNames = names.filter((n) => n !== name);
    if (selectedName === name) setSelectedName(nextNames[0] ?? null);
    onChange(next, required.filter((r) => r !== name));
  };

  const addParam = () => {
    let candidate = 'new_param';
    let i = 1;
    while (candidate in parameters) candidate = `new_param_${++i}`;
    onChange({ ...parameters, [candidate]: { type: 'string', description: '' } }, required);
    setSelectedName(candidate);
  };

  const duplicateParam = (name: string) => {
    let candidate = `${name}_copy`;
    let i = 2;
    while (candidate in parameters) candidate = `${name}_copy_${i++}`;
    const next: Record<string, JsonSchema> = {};
    for (const n of names) {
      next[n] = parameters[n];
      if (n === name) next[candidate] = JSON.parse(JSON.stringify(parameters[name]));
    }
    onChange(next, required);
    setSelectedName(candidate);
  };

  const selectedSchema = selectedName ? parameters[selectedName] : undefined;
  const typeHint = (name: string) => {
    const t = detectType(parameters[name]);
    const opt = TYPE_OPTIONS.find((o) => o.value === t);
    return opt?.hint || t;
  };

  if (names.length === 0) {
    return (
      <Stack gap="md" align="center" py="lg">
        <IconListDetails size={36} stroke={1.2} style={{ opacity: 0.35 }} />
        <Text size="sm" c="dimmed" ta="center">
          У инструмента пока нет параметров. Добавьте первый — имя, тип и описание для LLM.
        </Text>
        <Button leftSection={<IconPlus size={14} />} variant="light" size="sm" onClick={addParam}>
          Добавить параметр
        </Button>
      </Stack>
    );
  }

  return (
    <Group align="flex-start" gap="md" wrap="nowrap">
      <Box w={220} style={{ flexShrink: 0 }}>
        <Text size="xs" c="dimmed" tt="uppercase" fw={600} mb={6}>
          Параметры ({names.length})
        </Text>
        <Stack gap={4}>
          {names.map((name) => (
            <NavLink
              key={name}
              active={selectedName === name}
              onClick={() => setSelectedName(name)}
              label={name}
              description={typeHint(name)}
              leftSection={
                <Badge size="xs" variant="light" color={required.includes(name) ? 'red' : 'gray'}>
                  {detectType(parameters[name])}
                </Badge>
              }
              rightSection={
                required.includes(name) ? <Badge size="xs" color="red" variant="outline">*</Badge> : null
              }
            />
          ))}
        </Stack>
        <Button leftSection={<IconPlus size={14} />} variant="light" size="xs" fullWidth mt="sm" onClick={addParam}>
          Добавить
        </Button>
      </Box>

      <Box style={{ flex: 1, minWidth: 0 }}>
        {selectedName && selectedSchema && (
          <Stack gap="xs">
            <Group justify="flex-end" gap="xs">
              <Tooltip label="Скопировать параметр">
                <Button size="xs" variant="subtle" leftSection={<IconCopy size={14} />} onClick={() => duplicateParam(selectedName)}>
                  Дублировать
                </Button>
              </Tooltip>
            </Group>
            <ParameterCard
              name={selectedName}
              schema={selectedSchema}
              isRequired={required.includes(selectedName)}
              onRename={(newName) => updateOne(selectedName, newName, selectedSchema)}
              onChangeSchema={(s) => updateOne(selectedName, selectedName, s)}
              onToggleRequired={(r) => toggleRequired(selectedName, r)}
              onRemove={() => removeParam(selectedName)}
            />
          </Stack>
        )}
      </Box>
    </Group>
  );
}
