/**
 * Editor for x_backend_config.tier0_template — the per-tool config that
 * lets a tool be eligible for Tier 0 deterministic routing.
 *
 * Lets admin set:
 *   - `template`           — Jinja-like string rendered against tool output
 *   - `required_entity`    — which entity kind must be in query
 *   - `keyword_regex`      — (only for keyword_extract) regex with capture group
 *   - `param_maps`         — list of attempts (each is path → value mapping)
 *   - `required_fields`    — fields that MUST be non-null in tool output
 *
 * Includes a live PREVIEW panel: paste sample JSON from the tool → see the
 * rendered template and which paths were/weren't found.
 */
import { useState, useMemo, useEffect } from 'react';
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Code,
  Collapse,
  Divider,
  Group,
  Menu,
  Modal,
  Select,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  Textarea,
  TextInput,
  ThemeIcon,
  Tooltip,
} from '@mantine/core';
import {
  IconGripVertical,
  IconPlus,
  IconTrash,
  IconFlask,
  IconChevronDown,
  IconChevronRight,
  IconCheck,
  IconX,
  IconTemplate,
  IconWand,
  IconSparkles,
  IconListCheck,
  IconBulb,
  IconAlertTriangle,
} from '@tabler/icons-react';
import { tier0Api, type Tier0ExplainResult, type Tier0TestLLMResult } from '../shared/api/endpoints';

// ─── Types ───────────────────────────────────────────────────────────────────

/** One column definition inside table_defs[fieldName].columns */
export type ColDef = {
  field: string;
  label: string;
  /** Value mapping: raw value → display string. E.g. {"up":"🟢","down":"🔴"} */
  values?: Record<string, string>;
  /** Shown when the field is null/empty/0. Default: "". */
  empty?: string;
  /** Truncate cell to this many chars (adds "…"). */
  max_len?: number;
  /** Strip HTML tags from the cell value before display. */
  strip_html?: boolean;
  /** Special formatting: "phones" (split 10-char chunks) or "money" (strip .0). */
  format?: 'phones' | 'money';
  /** Prepend to every non-empty cell. */
  prefix?: string;
  /** Append to every non-empty cell. */
  suffix?: string;
};

export type TableDefs = Record<string, { columns: ColDef[] }>;

export type Tier0Template = {
  template: string;
  required_entity?: string | null;
  /**
   * Required when required_entity === "keyword_extract".
   * A regex with one capture group — the captured text becomes $keyword_extract.
   * Example: "(?:свич|switch|коммутатор)\\s+(.+?)$"
   */
  keyword_regex?: string | null;
  /**
   * Saved state of the visual «Конструктор keyword_regex» so it reopens with the
   * same settings (the generated regex can't be reliably reverse-parsed).
   */
  keyword_builder_state?: BuilderState;
  /**
   * Saved inputs of the example-driven Wizard, so reopening it pre-fills them
   * instead of starting from scratch (add one more example and regenerate).
   */
  wizard_inputs?: {
    positive_examples?: string[];
    negative_examples?: string[];
    sample_output?: string | null;
    notes?: string | null;
  };
  /** Each attempt is a flat dict: parameter dotted-path → entity-ref OR literal. */
  param_maps?: Record<string, unknown>[];
  required_fields?: string[];
  /**
   * Column format definitions for the `{field:table}` spec.
   * Key = field name used in template (e.g. "ports").
   */
  table_defs?: TableDefs;
  /**
   * Only for keyword_extract. List of strings to strip (case-insensitive)
   * from the start of the captured keyword before using it as a param.
   * Example: ["на свиче", "по свичу"] — so "на свиче косарева 113" → "косарева 113".
   */
  strip_prefixes?: string[];
  /**
   * If any of these substrings is found anywhere in the user query (case-insensitive),
   * Tier 0 is skipped entirely and the query goes to LLM.
   * Example: ["з тарифом", "заборгували", "відключені"] — queries with conditions
   * that Tier 0 can't handle.
   */
  block_keywords?: string[];
  /**
   * Rendered when the tool succeeds but returns no matching record (empty array
   * / missing required fields). Placeholders: {keyword_extract}, {phone}, {ip},
   * {mac}, {id}, {email}, {date}, {query}. Empty → fall through to LLM on no-match.
   * Example: "Свич {keyword_extract} не найден в базе".
   */
  not_found_template?: string | null;
  /**
   * Value normalizers for the `{field:map}` spec. Key = field path or leaf name
   * ("state" or "items.0.state"); value = raw→display map.
   * Example: { "state": { "1": "Включен", "0": "Отключен" } }.
   */
  value_maps?: Record<string, Record<string, string>>;
};

type Tier0TemplateEditorProps = {
  value: Tier0Template | null;
  onChange: (next: Tier0Template | null) => void;
  tenantId?: string;
  toolName?: string;
  toolDescription?: string;
};

type ParamMapRow = { path: string; value: string };

function mapToRows(m: Record<string, unknown>): ParamMapRow[] {
  return Object.entries(m).map(([path, val]) => ({
    path,
    value: val == null ? '' : String(val),
  }));
}

function rowsToMap(rows: ParamMapRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const r of rows) {
    if (!r.path.trim()) continue;
    const v = r.value.trim();
    if (v === '') continue;
    if (v.startsWith('$')) {
      out[r.path.trim()] = v;
    } else if (v === 'true' || v === 'false') {
      out[r.path.trim()] = v === 'true';
    } else if (/^-?\d+$/.test(v)) {
      out[r.path.trim()] = parseInt(v, 10);
    } else if (/^-?\d*\.\d+$/.test(v)) {
      out[r.path.trim()] = parseFloat(v);
    } else {
      out[r.path.trim()] = v;
    }
  }
  return out;
}

function moveItem<T>(arr: T[], from: number, to: number): T[] {
  const next = [...arr];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// ─── Preview helpers (mirrors backend logic) ────────────────────────────────

/** Walk a dotted path through nested dicts/arrays. */
function getAtPath(obj: unknown, dotted: string): unknown {
  let cur = obj;
  for (const p of dotted.split('.')) {
    if (cur === null || cur === undefined) return undefined;
    if (Array.isArray(cur)) {
      const idx = parseInt(p, 10);
      cur = isNaN(idx) ? undefined : cur[idx];
    } else if (typeof cur === 'object') {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return undefined;
    }
  }
  return cur;
}

type RenderResult = {
  rendered: string;
  missing: string[];   // placeholder paths that resolved to null/undefined
};

/** Client-side mirror of backend _render_template. */
function renderTemplate(
  template: string,
  data: unknown,
  valueMaps?: Record<string, Record<string, string>>,
): RenderResult {
  const missing: string[] = [];
  const rendered = template.replace(/\{([^}]+)\}/g, (_match, rawInner: string) => {
    // Split off the format spec ("items.0.state:map" → path + "map").
    const inner = rawInner.trim();
    const ci = inner.indexOf(':');
    const path = (ci >= 0 ? inner.slice(0, ci) : inner).trim();
    const spec = ci >= 0 ? inner.slice(ci + 1).trim() : '';
    const val = getAtPath(data, path);
    if (val === null || val === undefined || val === '') {
      missing.push(path);
      return `⚠{${path}}`;
    }
    if (spec === 'map' && valueMaps) {
      const leaf = path.split('.').pop() || path;
      const m = valueMaps[path] || valueMaps[leaf];
      if (m) return m[String(val)] ?? String(val);
    }
    return String(val);
  });
  return { rendered, missing };
}

/** Recursively collect all leaf paths from a JSON value (max depth 4). */
function collectPaths(obj: unknown, prefix = '', depth = 0): string[] {
  if (depth > 4) return prefix ? [prefix] : [];
  if (obj === null || obj === undefined) return prefix ? [prefix] : [];
  if (Array.isArray(obj)) {
    const paths: string[] = [];
    obj.slice(0, 5).forEach((item, i) => {
      const child = prefix ? `${prefix}.${i}` : String(i);
      if (typeof item === 'object' && item !== null) {
        paths.push(...collectPaths(item, child, depth + 1));
      } else {
        paths.push(child);
      }
    });
    return paths;
  }
  if (typeof obj === 'object') {
    const paths: string[] = [];
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      const child = prefix ? `${prefix}.${k}` : k;
      if (typeof v === 'object' && v !== null) {
        paths.push(...collectPaths(v, child, depth + 1));
      } else {
        paths.push(child);
      }
    }
    return paths;
  }
  return prefix ? [prefix] : [];
}

// ─── Keyword Regex Builder ───────────────────────────────────────────────────

function escapeForRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const TRIGGER_OPTIONS = [
  'покажи', 'показати', 'show', 'що на', 'знайди', 'найди', 'пошукай', 'дай',
];

const ENDING_OPTIONS = [
  { char: 'а', example: 'свитча', desc: 'родит.' },
  { char: 'е', example: 'свиче', desc: 'предлож.' },
  { char: 'у', example: 'свитчу', desc: 'датель.' },
  { char: 'і', example: 'порті', desc: 'укр.' },
  { char: 'и', example: 'клиенты', desc: 'мн.ч.' },
];

/** Optional "по X" qualifier block — inserted between object words and capture group. */
type QualifierPresetDef = {
  id: string;
  label: string;
  example: string;
  /** Raw regex fragments; joined with | inside the qualifier group. */
  patterns: string[];
};

const QUALIFIER_PRESET_DEFS: QualifierPresetDef[] = [
  { id: 'address',  label: 'адресу',         example: 'по адресу Леніна…',    patterns: ['адресу?'] },
  { id: 'street',   label: 'вулиці / улице', example: 'по вулиці Садова…',    patterns: ['вулиці?', 'вул\\.?', 'улиц[еі]?', 'ул\\.?'] },
  { id: 'fio',      label: 'ФІО / ФИО',      example: 'по фіо Іванов…',       patterns: ['фіо', 'фио'] },
  { id: 'surname',  label: 'прізвищу',       example: 'по прізвищу Коваль…',  patterns: ['прізвищ[ую]'] },
  { id: 'name',     label: 'імені / имени',  example: 'по імені Андрій…',     patterns: ['імені?', 'имени?'] },
  { id: 'contract', label: 'договору',       example: 'по договору 12345…',   patterns: ['договор[уа]?'] },
  { id: 'phone_q',  label: 'телефону',       example: 'по телефону 0501234…', patterns: ['телефон[уо]?', 'номер[уо]?'] },
];

/** One object-word entry with its own case endings. */
type WordEntry = { word: string; endings: string[] };

type CategoryPresetDef = {
  id: string;
  label: string;
  patterns: string[];
  example: string;
};

const CATEGORY_PRESET_DEFS: CategoryPresetDef[] = [
  { id: 'ports', label: 'порти / порты / port', patterns: ['порт(?:ів|ов|[иіы])?', 'port[s]?'], example: 'порти свитча...' },
  { id: 'status_ports', label: 'статус портів / портов', patterns: ['статус\\s+порт(?:ів|ов|[иіаы])?'], example: 'статус портов...' },
  { id: 'clients', label: 'клієнт / клиент / абонент', patterns: ['клієнт[а-яіїєу]{0,4}', 'клиент[а-яиу]{0,3}', 'абонент[а-яіїєу]{0,4}'], example: 'клієнту Іванов...' },
  { id: 'address', label: 'адреса / по адресу', patterns: ['(?:по\\s+)?адрес(?:у|і|а)?'], example: 'по адресу Косарева...' },
  { id: 'phone_info', label: 'номер телефона / телефон', patterns: ['(?:номер[\\s]+)?телефон(?:у|а|и)?'], example: 'дай номер телефона Косарев...' },
];

type BuilderState = {
  objectWords: WordEntry[];
  useTriggers: boolean;
  triggers: string[];
  customTrigger: string;
  useCategories: boolean;
  selectedCategories: string[];
  customCategories: string[];
  customCategoryWord: string;
  useQualifiers: boolean;
  qualifierPrep: string;   // preposition(s) before qualifier nouns, e.g. "по" or "по|на" or "" (no preposition)
  selectedQualifiers: string[];
  customQualifiers: string[];
  customQualifierWord: string;
};

const DEFAULT_BUILDER_STATE: BuilderState = {
  objectWords: [],
  useTriggers: true,
  triggers: ['покажи', 'показати', 'show'],
  customTrigger: '',
  useCategories: false,
  selectedCategories: [],
  customCategories: [],
  customCategoryWord: '',
  useQualifiers: false,
  qualifierPrep: 'по',
  selectedQualifiers: [],
  customQualifiers: [],
  customQualifierWord: '',
};

const BUILDER_PRESETS: { label: string; state: Partial<BuilderState> }[] = [
  {
    label: '🖧 Порты свитча',
    state: {
      objectWords: [
        { word: 'свитч', endings: ['а', 'е', 'у'] },
        { word: 'свіч',  endings: ['а', 'е', 'у'] },
        { word: 'свич',  endings: ['а', 'е', 'у'] },
        { word: 'switch', endings: [] },
      ],
      useTriggers: true,
      triggers: ['покажи', 'показати', 'show'],
      useCategories: true,
      selectedCategories: ['ports', 'status_ports'],
    },
  },
  {
    label: '👤 Клиент по имени',
    state: {
      objectWords: [],
      useTriggers: true,
      triggers: ['покажи', 'знайди', 'пошукай'],
      useCategories: true,
      selectedCategories: ['clients'],
    },
  },
  {
    label: '📍 По адресу',
    state: {
      objectWords: [],
      useTriggers: true,
      triggers: ['покажи', 'знайди'],
      useCategories: true,
      selectedCategories: ['address'],
    },
  },
  {
    label: '⚡ С нуля',
    state: {
      objectWords: [],
      useTriggers: false,
      triggers: [],
      useCategories: false,
      selectedCategories: [],
    },
  },
];

/**
 * Best-effort reverse parse of a keyword_regex string back into BuilderState.
 * Works well for regexes generated by buildRegexFromBuilder; partial for hand-written ones.
 */
function parseBuilderStateFromRegex(regex: string): BuilderState {
  const state: BuilderState = {
    objectWords: [],
    useTriggers: false,
    triggers: [],
    customTrigger: '',
    useCategories: false,
    selectedCategories: [],
    customCategories: [],
    customCategoryWord: '',
    useQualifiers: false,
    qualifierPrep: 'по',
    selectedQualifiers: [],
    customQualifiers: [],
    customQualifierWord: '',
  };

  if (!regex || !regex.endsWith('(.+?)$')) return state;

  // Strip terminal capture group
  let rest = regex.slice(0, -'(.+?)$'.length);

  // Detect qualifier block at end. Three possible formats (in the stored regex string):
  //   no-prep:     (?:(?:NOUNS)\s+)?
  //   single-prep: (?:PREP\s+(?:NOUNS)\s+)?         e.g. по  or  на
  //   multi-prep:  (?:(?:P1|P2)\s+(?:NOUNS)\s+)?   e.g. (?:по|на)
  // In the stored string \s → 2-char sequence backslash+s.
  let qualInner: string | null = null;

  // Multi-prep: (?:(?:P1|P2)\s+(?:NOUNS)\s+)?
  const qualMultiMatch = /\(\?:\(\?:([^()]+)\)\\s\+\(\?:([^()]*)\)\\s\+\)\?$/.exec(rest);
  // Single-prep: (?:PREP\s+(?:NOUNS)\s+)?
  const qualSingleMatch = /\(\?:([а-яёА-ЯЁa-zA-Z]+)\\s\+\(\?:([^()]*)\)\\s\+\)\?$/.exec(rest);
  // No-prep: (?:(?:NOUNS)\s+)?
  const qualNoPrepMatch = /\(\?:\(\?:([^()]*)\)\\s\+\)\?$/.exec(rest);

  if (qualMultiMatch) {
    rest = rest.slice(0, qualMultiMatch.index);
    state.qualifierPrep = qualMultiMatch[1]; // e.g. "по|на"
    qualInner = qualMultiMatch[2];
  } else if (qualSingleMatch) {
    rest = rest.slice(0, qualSingleMatch.index);
    state.qualifierPrep = qualSingleMatch[1]; // e.g. "по" or "на"
    qualInner = qualSingleMatch[2];
  } else if (qualNoPrepMatch) {
    rest = rest.slice(0, qualNoPrepMatch.index);
    state.qualifierPrep = '';
    qualInner = qualNoPrepMatch[1];
  }

  if (qualInner !== null) {
    const inner = qualInner;
    for (const opt of QUALIFIER_PRESET_DEFS) {
      if (opt.patterns.some(p => inner.includes(p))) {
        state.useQualifiers = true;
        if (!state.selectedQualifiers.includes(opt.id)) state.selectedQualifiers.push(opt.id);
      }
    }
    // Custom qualifiers: single-word parts not matching any preset pattern
    const recognized = new Set(QUALIFIER_PRESET_DEFS.flatMap(o => o.patterns));
    inner.split('|').forEach(part => {
      if (part && !recognized.has(part) && !QUALIFIER_PRESET_DEFS.some(o => o.patterns.some(p => part === p))) {
        const word = part.replace(/\\/g, '').replace(/\?/g, '').trim();
        if (word && !state.customQualifiers.includes(word)) state.customQualifiers.push(word);
      }
    });
    if (state.customQualifiers.length > 0) state.useQualifiers = true;
  }

  // Extract object-words block: (?:word1[endings]?|word2)\s+ at end of rest
  const wordBlockMatch = /\(\?:([^()]+)\)\\s\+$/.exec(rest);
  if (wordBlockMatch) {
    rest = rest.slice(0, wordBlockMatch.index);
    const inner = wordBlockMatch[1];

    // Split by | not inside []
    const parts: string[] = [];
    let curr = '';
    let inBrack = 0;
    for (const ch of inner) {
      if (ch === '[') inBrack++;
      else if (ch === ']') inBrack--;
      else if (ch === '|' && inBrack === 0) { parts.push(curr); curr = ''; continue; }
      curr += ch;
    }
    if (curr) parts.push(curr);

    const entries: WordEntry[] = [];
    for (const part of parts) {
      const bracketIdx = part.indexOf('[');
      if (bracketIdx > 0) {
        const base = part.slice(0, bracketIdx).trim();
        if (base) {
          const closingIdx = part.indexOf(']', bracketIdx);
          const endings: string[] = [];
          if (closingIdx > bracketIdx) {
            const inside = part.slice(bracketIdx + 1, closingIdx);
            [...inside].forEach(c => {
              if (ENDING_OPTIONS.some(e => e.char === c)) endings.push(c);
            });
          }
          // ASCII-only word (English) → strip Cyrillic endings even if regex had them
          const isAscii = /^[\x00-\x7F]+$/.test(base);
          entries.push({ word: base, endings: isAscii ? [] : endings });
        }
      } else {
        const base = part.trim();
        if (base) entries.push({ word: base, endings: [] });
      }
    }
    state.objectWords = entries;
  }

  // Check for category patterns in remaining prefix
  for (const cat of CATEGORY_PRESET_DEFS) {
    if (cat.patterns.some(p => rest.includes(p))) {
      state.useCategories = true;
      if (!state.selectedCategories.includes(cat.id)) state.selectedCategories.push(cat.id);
    }
  }

  // Check for known trigger words
  for (const trig of TRIGGER_OPTIONS) {
    if (rest.includes(trig)) {
      if (!state.triggers.includes(trig)) state.triggers.push(trig);
      state.useTriggers = true;
    }
  }

  return state;
}

function buildRegexFromBuilder(state: BuilderState): string {
  const parts: string[] = [];

  // 1. Optional trigger words: (?:(?:покажи|show)\s+)?
  if (state.useTriggers && state.triggers.length > 0) {
    const pat = state.triggers
      .map(w => escapeForRegex(w).replace(/\\ /g, '\\s+'))
      .join('|');
    parts.push(`(?:(?:${pat})\\s+)?`);
  }

  // 2. Optional categories: (?:порт(?:ів|ов|...)?|port[s]?)?\s*
  if (state.useCategories) {
    const catPatterns = [
      ...state.selectedCategories.flatMap(
        id => CATEGORY_PRESET_DEFS.find(c => c.id === id)?.patterns ?? [],
      ),
      ...state.customCategories.map(w => escapeForRegex(w)),
    ];
    if (catPatterns.length > 0) {
      parts.push(`(?:${catPatterns.join('|')})?\\s*`);
    }
  }

  // 3. Object words with per-word endings: (?:свитч[аеу]?|свіч[аеу]?|switch)\s+
  if (state.objectWords.length > 0) {
    const wordPats = state.objectWords.map(({ word, endings }) => {
      const escaped = escapeForRegex(word);
      return endings.length > 0 ? escaped + `[${endings.join('')}]?` : escaped;
    });
    parts.push(`(?:${wordPats.join('|')})\\s+`);
  }

  // 4. Optional qualifier block: (?:PREP\s+(?:адресу?|вулиці?|...)\s+)?
  //    PREP is configurable: "по", "на", "по|на", or "" (no preposition).
  if (state.useQualifiers) {
    const qPatterns = [
      ...state.selectedQualifiers.flatMap(
        id => QUALIFIER_PRESET_DEFS.find(q => q.id === id)?.patterns ?? [],
      ),
      ...state.customQualifiers.map(w => escapeForRegex(w)),
    ];
    if (qPatterns.length > 0) {
      const prepRaw = state.qualifierPrep.trim();
      let prepBlock = '';
      if (prepRaw) {
        const prepParts = prepRaw.split(/[|,\/\s]+/).map(p => p.trim()).filter(Boolean).map(escapeForRegex);
        prepBlock = prepParts.length === 1
          ? `${prepParts[0]}\\s+`
          : `(?:${prepParts.join('|')})\\s+`;
      }
      parts.push(`(?:${prepBlock}(?:${qPatterns.join('|')})\\s+)?`);
    }
  }

  // 5. Capture group
  parts.push(`(.+?)$`);

  return parts.join('');
}

type KeywordRegexBuilderProps = {
  opened: boolean;
  onClose: () => void;
  onApply: (regex: string, builderState: BuilderState) => void;
  currentRegex?: string | null;
  initialState?: BuilderState;
  onOpenWizard?: () => void;
};

function KeywordRegexBuilder({ opened, onClose, onApply, currentRegex, initialState, onOpenWizard }: KeywordRegexBuilderProps) {
  const [state, setState] = useState<BuilderState>(DEFAULT_BUILDER_STATE);
  const [newWord, setNewWord] = useState('');
  const [testInput, setTestInput] = useState('');
  // True when there's a regex we couldn't load into the builder (foreign shape).
  const [unrepresentable, setUnrepresentable] = useState(false);

  // When modal opens, restore the builder's view of the current regex:
  //   1. saved builder state → use verbatim (exact authoring intent);
  //   2. else best-effort parse, but ONLY adopt it if it round-trips back to the
  //      SAME regex (so we never silently rewrite a regex the builder can't
  //      faithfully represent — e.g. wizard/hand-written ones);
  //   3. else start blank and flag the regex as foreign.
  useEffect(() => {
    if (!opened) return;
    const rx = (currentRegex ?? '').trim();
    if (initialState) {
      setState(initialState);
      setUnrepresentable(false);
      return;
    }
    if (!rx) {
      setState(DEFAULT_BUILDER_STATE);
      setUnrepresentable(false);
      return;
    }
    const parsed = parseBuilderStateFromRegex(rx);
    if (buildRegexFromBuilder(parsed) === rx) {
      setState(parsed);          // builder can reproduce it exactly → safe to load
      setUnrepresentable(false);
    } else {
      setState(DEFAULT_BUILDER_STATE);
      setUnrepresentable(true);  // foreign regex → keep it, warn, don't rewrite
    }
  }, [opened]); // eslint-disable-line react-hooks/exhaustive-deps

  const generatedRegex = useMemo(() => buildRegexFromBuilder(state), [state]);

  const testResult = useMemo(() => {
    if (!testInput.trim()) return null;
    try {
      const m = new RegExp(generatedRegex, 'i').exec(testInput);
      if (m) return { ok: true, captured: m[1] ?? '' };
      return { ok: false, captured: null };
    } catch {
      return { ok: false, captured: null };
    }
  }, [testInput, generatedRegex]);

  // Test against current (saved) regex too
  const currentTestResult = useMemo(() => {
    if (!testInput.trim() || !currentRegex) return null;
    try {
      const m = new RegExp(currentRegex, 'i').exec(testInput);
      if (m) return { ok: true, captured: m[1] ?? '' };
      return { ok: false, captured: null };
    } catch {
      return null;
    }
  }, [testInput, currentRegex]);

  function applyPreset(preset: typeof BUILDER_PRESETS[0]) {
    setState(s => ({ ...s, ...preset.state }));
  }

  function toggleWordEnding(idx: number, char: string) {
    setState(s => ({
      ...s,
      objectWords: s.objectWords.map((e, i) =>
        i !== idx ? e : {
          ...e,
          endings: e.endings.includes(char)
            ? e.endings.filter(c => c !== char)
            : [...e.endings, char],
        }
      ),
    }));
  }

  function toggleTrigger(w: string) {
    setState(s => ({
      ...s,
      triggers: s.triggers.includes(w)
        ? s.triggers.filter(t => t !== w)
        : [...s.triggers, w],
    }));
  }

  function toggleCategory(id: string) {
    setState(s => ({
      ...s,
      selectedCategories: s.selectedCategories.includes(id)
        ? s.selectedCategories.filter(c => c !== id)
        : [...s.selectedCategories, id],
    }));
  }

  function addObjectWord() {
    const w = newWord.trim().toLowerCase();
    if (!w) return;
    // Use functional update to always read latest state (avoids stale closure after useEffect)
    setState(s => {
      if (s.objectWords.some(e => e.word === w)) return s; // duplicate
      const isAscii = /^[\x00-\x7F]+$/.test(w);
      const endings = isAscii ? [] : ['а', 'е', 'у'];
      return { ...s, objectWords: [...s.objectWords, { word: w, endings }] };
    });
    setNewWord('');
  }

  function removeObjectWord(idx: number) {
    setState(s => ({ ...s, objectWords: s.objectWords.filter((_, i) => i !== idx) }));
  }

  function addCustomTrigger() {
    const w = state.customTrigger.trim().toLowerCase();
    if (!w || state.triggers.includes(w)) return;
    setState(s => ({ ...s, triggers: [...s.triggers, w], customTrigger: '' }));
  }

  function addCustomCategory() {
    const w = state.customCategoryWord.trim().toLowerCase();
    if (!w || state.customCategories.includes(w)) return;
    setState(s => ({ ...s, customCategories: [...s.customCategories, w], customCategoryWord: '' }));
  }

  function removeCustomCategory(w: string) {
    setState(s => ({ ...s, customCategories: s.customCategories.filter(c => c !== w) }));
  }

  function toggleQualifier(id: string) {
    setState(s => ({
      ...s,
      selectedQualifiers: s.selectedQualifiers.includes(id)
        ? s.selectedQualifiers.filter(q => q !== id)
        : [...s.selectedQualifiers, id],
    }));
  }

  function addCustomQualifier() {
    const w = state.customQualifierWord.trim().toLowerCase();
    if (!w || state.customQualifiers.includes(w)) return;
    setState(s => ({ ...s, customQualifiers: [...s.customQualifiers, w], customQualifierWord: '' }));
  }

  function removeCustomQualifier(w: string) {
    setState(s => ({ ...s, customQualifiers: s.customQualifiers.filter(q => q !== w) }));
  }

  const isValid = generatedRegex !== '(.+?)$';

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={
        <Group gap="xs">
          <IconWand size={16} />
          <Text fw={600}>Конструктор keyword_regex</Text>
          <Text size="xs" c="dimmed">— без написания регулярных выражений</Text>
        </Group>
      }
      size="xl"
    >
      <Stack gap="md">

        {unrepresentable && (
          <Alert color="yellow" variant="light" p="xs" icon={<IconAlertTriangle size={15} />}>
            <Text size="xs" mb={onOpenWizard ? 6 : 0}>
              Текущий <Code>keyword_regex</Code> создан не Конструктором (Визардом или вручную) и не выражается
              его блоками — настройки здесь не отображаются, а «Применить» <b>заменит</b> текущий regex.
              Чтобы доработать его <b>без написания regex</b> — используй Визард: добавь примеры запросов
              (что ловить / что не ловить) и он сам обновит правило.
            </Text>
            {onOpenWizard && (
              <Button size="compact-xs" variant="light" color="grape"
                      leftSection={<IconSparkles size={13} />}
                      onClick={() => { onClose(); onOpenWizard(); }}>
                Доработать через Визард
              </Button>
            )}
          </Alert>
        )}

        {/* Presets row */}
        <div>
          <Text size="xs" c="dimmed" fw={500} mb={6}>
            Быстрый старт — выбери шаблон или настрой с нуля:
          </Text>
          <Group gap="xs">
            {BUILDER_PRESETS.map(p => (
              <Button key={p.label} size="xs" variant="light" color="grape" onClick={() => applyPreset(p)}>
                {p.label}
              </Button>
            ))}
          </Group>
        </div>

        <Divider />

        {/* Test area — MOST prominent */}
        <div>
          <Text size="sm" fw={700} mb={2}>🧪 Тест</Text>
          <Text size="xs" c="dimmed" mb={6}>
            Введи реальный запрос пользователя — сразу увидишь, что regex захватит как ключевое слово.
            Захваченный текст будет передан в tool как <Code style={{ fontSize: 11 }}>$keyword_extract</Code>.
          </Text>
          <TextInput
            placeholder="покажи порты свача Летчиков 10"
            value={testInput}
            onChange={e => setTestInput(e.currentTarget.value)}
            size="sm"
          />
          {testInput.trim() && (
            <Stack gap={4} mt={6}>
              {/* New regex result */}
              <Box
                p="xs"
                style={{
                  borderRadius: 6,
                  background: testResult?.ok
                    ? 'var(--mantine-color-green-light)'
                    : 'var(--mantine-color-red-light)',
                  border: `1px solid ${testResult?.ok ? 'var(--mantine-color-green-4)' : 'var(--mantine-color-red-4)'}`,
                }}
              >
                {testResult?.ok ? (
                  <Text size="sm">
                    ✅ <b>Новый regex:</b> захвачено → <Code>{testResult.captured}</Code>
                  </Text>
                ) : (
                  <Text size="sm">❌ <b>Новый regex:</b> не совпадает</Text>
                )}
              </Box>
              {/* Current saved regex result */}
              {currentRegex && (
                <Box
                  p="xs"
                  style={{
                    borderRadius: 6,
                    background: 'var(--mantine-color-default-hover)',
                    border: '1px solid var(--mantine-color-default-border)',
                  }}
                >
                  {currentTestResult?.ok ? (
                    <Text size="xs" c="dimmed">
                      📌 Текущий (сохранён): захвачено → <Code style={{ fontSize: 11 }}>{currentTestResult.captured}</Code>
                    </Text>
                  ) : (
                    <Text size="xs" c="dimmed">📌 Текущий (сохранён): не совпадает</Text>
                  )}
                </Box>
              )}
            </Stack>
          )}
        </div>

        <Divider label="Настройки" labelPosition="center" />

        {/* Settings Tabs */}
        <Tabs defaultValue="object" styles={{ tab: { whiteSpace: 'nowrap', minWidth: 'max-content' } }}>
          <Tabs.List>
            <Tabs.Tab value="object">
              🔑 Объект
              {state.objectWords.length > 0 && (
                <Badge size="xs" ml={5} color="blue" variant="filled">{state.objectWords.length}</Badge>
              )}
            </Tabs.Tab>
            <Tabs.Tab value="triggers">
              ▶ Действия
              {state.useTriggers && state.triggers.length > 0 && (
                <Badge size="xs" ml={5} color="violet" variant="filled">{state.triggers.length}</Badge>
              )}
            </Tabs.Tab>
            <Tabs.Tab value="categories">
              📋 Категории
              {state.useCategories && (state.selectedCategories.length + state.customCategories.length) > 0 && (
                <Badge size="xs" ml={5} color="teal" variant="filled">{state.selectedCategories.length + state.customCategories.length}</Badge>
              )}
            </Tabs.Tab>
            <Tabs.Tab value="qualifiers">
              🔍 Квалификаторы
              {state.useQualifiers && (state.selectedQualifiers.length + state.customQualifiers.length) > 0 && (
                <Badge size="xs" ml={5} color="orange" variant="filled">{state.selectedQualifiers.length + state.customQualifiers.length}</Badge>
              )}
            </Tabs.Tab>
          </Tabs.List>

          {/* ── Tab: Object words ─────────────────────────────────────────── */}
          <Tabs.Panel value="object" pt="sm">
            <Stack gap="sm">
              <Text size="xs" c="dimmed">
                Слова, на которые реагирует regex — название сущности в запросе («свитч», «клиент», «абонент»).
                Пиши основу без окончания — для «свитча» вводи «свитч», окончания выбери кнопками.
                Можно добавить несколько вариантов написания одного слова.
              </Text>
              <Stack gap={4}>
                {state.objectWords.map((entry, idx) => (
                  <Group key={idx} gap={4} wrap="nowrap" align="center">
                    <Badge
                      size="lg"
                      variant="filled"
                      color="blue"
                      ff="monospace"
                      style={{ flexShrink: 0, minWidth: 80 }}
                    >
                      {entry.word}
                    </Badge>
                    <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>окончания:</Text>
                    {ENDING_OPTIONS.map(({ char, example }) => {
                      const active = entry.endings.includes(char);
                      return (
                        <Badge
                          key={char}
                          size="sm"
                          variant={active ? 'filled' : 'outline'}
                          color={active ? 'blue' : 'gray'}
                          style={{ cursor: 'pointer', userSelect: 'none', minWidth: 30, padding: '0 5px' }}
                          onClick={() => toggleWordEnding(idx, char)}
                          title={`…${char} (${example})`}
                        >
                          …{char}
                        </Badge>
                      );
                    })}
                    <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
                      {entry.endings.length === 0 ? '(без окончаний)' : ''}
                    </Text>
                    <ActionIcon size={16} variant="transparent" color="red" onClick={() => removeObjectWord(idx)}>
                      <IconX size={10} />
                    </ActionIcon>
                  </Group>
                ))}
                {state.objectWords.length === 0 && (
                  <Text size="xs" c="dimmed" fs="italic">нет слов — добавь ниже или выбери пресет</Text>
                )}
              </Stack>
              <Group gap="xs">
                <TextInput
                  size="xs"
                  ff="monospace"
                  placeholder="свитч"
                  value={newWord}
                  onChange={e => setNewWord(e.currentTarget.value)}
                  onKeyDown={e => e.key === 'Enter' && addObjectWord()}
                  style={{ width: 150 }}
                />
                <Button size="xs" variant="light" onClick={addObjectWord} leftSection={<IconPlus size={10} />}>
                  Добавить
                </Button>
                <Text size="xs" c="dimmed">— Enter тоже работает</Text>
              </Group>
            </Stack>
          </Tabs.Panel>

          {/* ── Tab: Trigger words ────────────────────────────────────────── */}
          <Tabs.Panel value="triggers" pt="sm">
            <Stack gap="sm">
              <Group gap="sm">
                <Switch
                  size="sm"
                  checked={state.useTriggers}
                  onChange={e => { const v = e.currentTarget.checked; setState(s => ({ ...s, useTriggers: v })); }}
                />
                <Text size="sm" fw={500}>Включить слова-действия</Text>
              </Group>
              <Text size="xs" c="dimmed">
                Глаголы или команды в начале запроса: «покажи», «знайди», «дай», «show».
                Если включено — regex требует одного из этих слов перед объектом.
                Не входят в <Code style={{ fontSize: 11 }}>$keyword_extract</Code>.
              </Text>
              {state.useTriggers && (
                <Stack gap="xs">
                  <Group gap={6} wrap="wrap">
                    {TRIGGER_OPTIONS.map(w => {
                      const active = state.triggers.includes(w);
                      return (
                        <Badge
                          key={w}
                          size="md"
                          variant={active ? 'filled' : 'outline'}
                          color="violet"
                          style={{ cursor: 'pointer', userSelect: 'none' }}
                          onClick={() => toggleTrigger(w)}
                        >
                          {w}
                        </Badge>
                      );
                    })}
                    {/* Custom triggers (added by user) */}
                    {state.triggers.filter(t => !TRIGGER_OPTIONS.includes(t)).map(w => (
                      <Badge
                        key={w}
                        size="md"
                        variant="filled"
                        color="violet"
                        rightSection={
                          <ActionIcon size={10} variant="transparent" color="white"
                            onClick={e => { e.stopPropagation(); setState(s => ({ ...s, triggers: s.triggers.filter(t => t !== w) })); }}>
                            <IconX size={8} />
                          </ActionIcon>
                        }
                      >
                        {w}
                      </Badge>
                    ))}
                  </Group>
                  <Group gap={4}>
                    <TextInput
                      size="xs"
                      placeholder="своё слово (напр. «дайте»)"
                      value={state.customTrigger}
                      style={{ width: 200 }}
                      onChange={e => { const v = e.currentTarget.value; setState(s => ({ ...s, customTrigger: v })); }}
                      onKeyDown={e => e.key === 'Enter' && addCustomTrigger()}
                    />
                    <Button size="xs" variant="light" color="violet" onClick={addCustomTrigger} leftSection={<IconPlus size={10} />}>
                      Добавить
                    </Button>
                  </Group>
                </Stack>
              )}
            </Stack>
          </Tabs.Panel>

          {/* ── Tab: Category words ───────────────────────────────────────── */}
          <Tabs.Panel value="categories" pt="sm">
            <Stack gap="sm">
              <Group gap="sm">
                <Switch
                  size="sm"
                  checked={state.useCategories}
                  onChange={e => { const v = e.currentTarget.checked; setState(s => ({ ...s, useCategories: v })); }}
                />
                <Text size="sm" fw={500}>Включить слова-категории</Text>
              </Group>
              <Text size="xs" c="dimmed">
                Уточняют тип данных в запросе: «порти», «клієнт», «номер телефона».
                Стоят между словом-действием и ключевым словом объекта.
                Не входят в <Code style={{ fontSize: 11 }}>$keyword_extract</Code>.
              </Text>
              {state.useCategories && (
                <Stack gap="xs">
                  <Stack gap={4}>
                    {CATEGORY_PRESET_DEFS.map(cat => {
                      const active = state.selectedCategories.includes(cat.id);
                      return (
                        <Box
                          key={cat.id}
                          p="xs"
                          onClick={() => toggleCategory(cat.id)}
                          style={{
                            border: `1px solid ${active ? 'var(--mantine-color-teal-5)' : 'var(--mantine-color-default-border)'}`,
                            borderRadius: 4,
                            cursor: 'pointer',
                            background: active ? 'var(--mantine-color-teal-light)' : 'var(--mantine-color-body)',
                            userSelect: 'none',
                          }}
                        >
                          <Group gap="xs">
                            <Box
                              w={14} h={14}
                              style={{
                                borderRadius: 3,
                                border: `2px solid ${active ? 'var(--mantine-color-teal-5)' : 'var(--mantine-color-default-border)'}`,
                                background: active ? 'var(--mantine-color-teal-5)' : undefined,
                                flexShrink: 0,
                              }}
                            />
                            <div>
                              <Text size="xs" fw={500}>{cat.label}</Text>
                              <Text size="xs" c="dimmed">например: <em>{cat.example}</em></Text>
                            </div>
                          </Group>
                        </Box>
                      );
                    })}
                  </Stack>
                  {/* Custom categories */}
                  {state.customCategories.length > 0 && (
                    <Group gap={4} wrap="wrap">
                      {state.customCategories.map(w => (
                        <Badge
                          key={w}
                          size="sm"
                          variant="filled"
                          color="teal"
                          rightSection={
                            <ActionIcon size={10} variant="transparent" color="white"
                              onClick={e => { e.stopPropagation(); removeCustomCategory(w); }}>
                              <IconX size={8} />
                            </ActionIcon>
                          }
                        >
                          {w}
                        </Badge>
                      ))}
                    </Group>
                  )}
                  <Group gap={4}>
                    <TextInput
                      size="xs"
                      placeholder="своя категория (напр. «статус»)"
                      value={state.customCategoryWord}
                      style={{ width: 220 }}
                      onChange={e => { const v = e.currentTarget.value; setState(s => ({ ...s, customCategoryWord: v })); }}
                      onKeyDown={e => e.key === 'Enter' && addCustomCategory()}
                    />
                    <Button size="xs" variant="light" color="teal" onClick={addCustomCategory} leftSection={<IconPlus size={10} />}>
                      Добавить
                    </Button>
                  </Group>
                </Stack>
              )}
            </Stack>
          </Tabs.Panel>

          {/* ── Tab: Qualifier words ──────────────────────────────────────── */}
          <Tabs.Panel value="qualifiers" pt="sm">
            <Stack gap="sm">
              <Group gap="sm">
                <Switch
                  size="sm"
                  checked={state.useQualifiers}
                  onChange={e => { const v = e.currentTarget.checked; setState(s => ({ ...s, useQualifiers: v })); }}
                />
                <Text size="sm" fw={500}>Включить уточняющие квалификаторы</Text>
              </Group>
              <Text size="xs" c="dimmed">
                Фразы вида «по адресу», «на вулиці», «за договором» — стоящие между объектом и значением поиска.
                Не входят в <Code style={{ fontSize: 11 }}>$keyword_extract</Code>.
                Предлог задаётся ниже — можно указать несколько через&nbsp;|.
              </Text>
              {state.useQualifiers && (
                <Stack gap={6}>
                  <Group gap="xs" align="flex-end">
                    <TextInput
                      size="xs"
                      label={<Text size="xs" fw={500}>Предлог (приставка)</Text>}
                      description="Один или через | — например: по|на|за. Пусто — без предлога."
                      placeholder="по"
                      value={state.qualifierPrep}
                      style={{ width: 200 }}
                      ff="monospace"
                      onChange={e => { const v = e.currentTarget.value; setState(s => ({ ...s, qualifierPrep: v })); }}
                    />
                    {state.qualifierPrep.trim() && (
                      <Text size="xs" c="dimmed" mb={4}>
                        → <Code style={{ fontSize: 11 }}>{
                          state.qualifierPrep.trim().split(/[|,\/\s]+/).filter(Boolean).length > 1
                            ? `(?:${state.qualifierPrep.trim().split(/[|,\/\s]+/).filter(Boolean).join('|')})`
                            : state.qualifierPrep.trim()
                        }\s+NOUN\s+</Code>
                      </Text>
                    )}
                  </Group>
                  <Stack gap={4}>
                    {QUALIFIER_PRESET_DEFS.map(opt => {
                      const active = state.selectedQualifiers.includes(opt.id);
                      const prep = state.qualifierPrep.trim() || '';
                      return (
                        <Box
                          key={opt.id}
                          p="xs"
                          onClick={() => toggleQualifier(opt.id)}
                          style={{
                            border: `1px solid ${active ? 'var(--mantine-color-orange-5)' : 'var(--mantine-color-default-border)'}`,
                            borderRadius: 4,
                            cursor: 'pointer',
                            background: active ? 'var(--mantine-color-orange-light)' : 'var(--mantine-color-body)',
                            userSelect: 'none',
                          }}
                        >
                          <Group gap="xs">
                            <Box
                              w={14} h={14}
                              style={{
                                borderRadius: 3,
                                border: `2px solid ${active ? 'var(--mantine-color-orange-5)' : 'var(--mantine-color-default-border)'}`,
                                background: active ? 'var(--mantine-color-orange-5)' : undefined,
                                flexShrink: 0,
                              }}
                            />
                            <div>
                              <Text size="xs" fw={500}>{prep ? `${prep.split(/[|,\/\s]+/)[0]} ` : ''}{opt.label}</Text>
                              <Text size="xs" c="dimmed">например: <em>{opt.example}</em></Text>
                            </div>
                          </Group>
                        </Box>
                      );
                    })}
                  </Stack>
                  {state.customQualifiers.length > 0 && (
                    <Group gap={4} wrap="wrap">
                      {state.customQualifiers.map(w => (
                        <Badge
                          key={w}
                          size="sm"
                          variant="filled"
                          color="orange"
                          rightSection={
                            <ActionIcon size={10} variant="transparent" color="white"
                              onClick={e => { e.stopPropagation(); removeCustomQualifier(w); }}>
                              <IconX size={8} />
                            </ActionIcon>
                          }
                        >
                          {state.qualifierPrep.trim() ? `${state.qualifierPrep.trim().split(/[|,\/\s]+/)[0]} ` : ''}{w}
                        </Badge>
                      ))}
                    </Group>
                  )}
                  <Group gap={4}>
                    <TextInput
                      size="xs"
                      placeholder="своё слово (напр. «договору»)"
                      value={state.customQualifierWord}
                      style={{ width: 220 }}
                      onChange={e => { const v = e.currentTarget.value; setState(s => ({ ...s, customQualifierWord: v })); }}
                      onKeyDown={e => e.key === 'Enter' && addCustomQualifier()}
                    />
                    <Button size="xs" variant="light" color="orange" onClick={addCustomQualifier} leftSection={<IconPlus size={10} />}>
                      Добавить
                    </Button>
                  </Group>
                </Stack>
              )}
            </Stack>
          </Tabs.Panel>

        </Tabs>

        <Divider />

        {/* Generated regex */}
        <div>
          <Text size="xs" fw={500} mb={2}>Сгенерированный regex</Text>
          <Text size="xs" c="dimmed" mb={4}>
            Итоговое выражение на основе настроек выше. Проверь его в поле «Тест» перед сохранением.
            При необходимости можно скопировать и отредактировать вручную в поле keyword_regex.
          </Text>
          <Code
            block
            style={{
              fontSize: 11,
              wordBreak: 'break-all',
              opacity: isValid ? 1 : 0.4,
            }}
          >
            {isValid ? generatedRegex : '(добавь ключевые слова или категории)'}
          </Code>
        </div>

        {/* Actions */}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Отмена</Button>
          <Button
            color="blue"
            leftSection={<IconCheck size={14} />}
            onClick={() => { onApply(generatedRegex, state); onClose(); }}
            disabled={!isValid}
          >
            Применить regex
          </Button>
        </Group>

      </Stack>
    </Modal>
  );
}

// ─── Entity options ──────────────────────────────────────────────────────────

const ENTITY_OPTIONS = [
  { value: '', label: '— не требуется —' },
  { value: 'phone', label: 'phone — телефон (+380…)' },
  { value: 'mac', label: 'mac — MAC-адрес' },
  { value: 'ip', label: 'ip — IP-адрес' },
  { value: 'id', label: 'id — числовой ID (#123, №456)' },
  { value: 'email', label: 'email — электронная почта' },
  { value: 'date', label: 'date — дата (DD.MM.YYYY, ISO, словесная)' },
  { value: 'keyword_extract', label: 'keyword_extract — текст после ключевого слова (regex)' },
];

// ─── Presets ─────────────────────────────────────────────────────────────────

type Preset = {
  label: string;
  description: string;
  value: Tier0Template;
};

const PRESETS: Preset[] = [
  {
    label: '📞 Поиск по телефону',
    description: 'Срабатывает когда в запросе есть телефон +380. Ищет по filters.phone, затем filters.sms_phone.',
    value: {
      template:
        '**{items.0.name}** (договор №{items.0.dogovor_num})\n' +
        '- Телефон: {items.0.phone}  |  SMS: {items.0.sms_phone}\n' +
        '- Адрес: {items.0.street} {items.0.house}, кв. {items.0.apart}\n' +
        '- Баланс: {items.0.amount} грн (кредит: {items.0.kredit})',
      required_entity: 'phone',
      keyword_regex: null,
      param_maps: [
        { 'filters.phone': '$phone|re_sub:^\\+38=>', limit: 1 },
        { 'filters.sms_phone': '$phone|re_sub:^\\+38=>', limit: 1 },
      ],
      required_fields: ['items.0.name', 'items.0.amount'],
    },
  },
  {
    label: '🔍 Поиск клиента по ФИО / адресу',
    description: 'Срабатывает на «клієнт Іванов Іван», «абонент по адресу Косарева 26» и т.п. Использует свободный текстовый поиск.',
    value: {
      template:
        '**{items.0.name}** (договор №{items.0.dogovor_num})\n' +
        '- Телефон: {items.0.phone}  |  SMS: {items.0.sms_phone}\n' +
        '- Адрес: {items.0.street} {items.0.house}, кв. {items.0.apart}\n' +
        '- Баланс: {items.0.amount} грн (кредит: {items.0.kredit})',
      required_entity: 'keyword_extract',
      keyword_regex:
        '(?:клієнт[а-яіїєу]{0,4}|клиент[а-яиу]{0,3}|абонент[а-яіїєу]{0,4}|мешканц[яіе]{0,2}' +
        '|(?:знайд[ии]|найд[ии]|пошукай|покаж[иі])\\s+(?:клієнт[а-яіїє]{0,4}|клиент[а-яиу]{0,3}|абонент[а-яіїє]{0,4}))' +
        '\\s+(?:по\\s+(?:адресу?|фіо|фио|прізвищ[ую])\\s+)?(.+?)$',
      param_maps: [{ query: '$keyword_extract', limit: 1 }],
      required_fields: ['items.0.name'],
    },
  },
  {
    label: '🖧 Поиск свича по названию / адресу',
    description: 'Срабатывает на «свич Косарева 26», «switch center-01». Ищет по полю Name (contains).',
    value: {
      template:
        '**{items.0.Name}** (IP: {items.0.ip})\n' +
        '- Тип: {items.0.dev_tip} | Район: {items.0.district}\n' +
        '- Статус: {items.0.state} | RTT: {items.0.last_rtt_ms}мс',
      required_entity: 'keyword_extract',
      keyword_regex:
        '(?:свич[а-яіїє]{0,2}|свіч[а-яіїє]{0,2}|switch|коммутатор[а-яіїє]{0,3}|sw)\\s+(.+?)$',
      param_maps: [{ 'filters.Name': '$keyword_extract', limit: 1 }],
      required_fields: ['items.0.Name', 'items.0.ip'],
    },
  },
  {
    label: '🌐 Поиск по IP-адресу',
    description: 'Срабатывает когда в запросе есть IPv4. Передаёт IP как параметр — подходит для поиска оборудования.',
    value: {
      template:
        '**{items.0.Name}** (IP: {items.0.ip})\n' +
        '- Тип: {items.0.dev_tip} | Статус: {items.0.state}\n' +
        '- RTT: {items.0.last_rtt_ms}мс',
      required_entity: 'ip',
      keyword_regex: null,
      param_maps: [{ 'filters.ip': '$ip', limit: 1 }],
      required_fields: ['items.0.Name'],
    },
  },
  {
    label: '📧 Поиск по email',
    description: 'Срабатывает когда в запросе есть email-адрес.',
    value: {
      template:
        '**{items.0.name}** (договор №{items.0.dogovor_num})\n' +
        '- Email: {items.0.email}\n' +
        '- Баланс: {items.0.amount} грн',
      required_entity: 'email',
      keyword_regex: null,
      param_maps: [{ 'filters.email': '$email', limit: 1 }],
      required_fields: ['items.0.name'],
    },
  },
  {
    label: '🔧 Свой вариант (с нуля)',
    description: 'Пустая заготовка — заполни все поля самостоятельно.',
    value: {
      template: '',
      required_entity: null,
      keyword_regex: null,
      param_maps: [{}],
      required_fields: [],
    },
  },
];

// ─── Live preview panel ──────────────────────────────────────────────────────

function PreviewPanel({ template, requiredFields, valueMaps }: {
  template: string; requiredFields: string[]; valueMaps?: Record<string, Record<string, string>>;
}) {
  const [sampleJson, setSampleJson] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);

  const parsed = useMemo(() => {
    if (!sampleJson.trim()) return null;
    try {
      const v = JSON.parse(sampleJson);
      setJsonError(null);
      return v;
    } catch (e) {
      setJsonError((e as Error).message);
      return null;
    }
  }, [sampleJson]);

  const paths = useMemo(() => parsed !== null ? collectPaths(parsed) : [], [parsed]);
  const renderResult = useMemo(
    () => parsed !== null && template ? renderTemplate(template, parsed, valueMaps) : null,
    [parsed, template, valueMaps],
  );

  const reqFieldResults = useMemo(() => {
    if (!parsed || !requiredFields.length) return [];
    return requiredFields.map((f) => {
      const val = getAtPath(parsed, f);
      return { field: f, ok: val !== null && val !== undefined && val !== '' };
    });
  }, [parsed, requiredFields]);

  return (
    <Card withBorder padding="xs" bg="var(--mantine-color-blue-light)">
      <Group gap="xs" mb={6}>
        <ThemeIcon size="sm" variant="light" color="blue">
          <IconFlask size={13} />
        </ThemeIcon>
        <Text size="sm" fw={600}>Preview — вставь пример JSON от tool'а</Text>
      </Group>

      <Textarea
        placeholder={'{\n  "items": [\n    { "name": "Іван", "amount": 150 }\n  ]\n}'}
        value={sampleJson}
        onChange={(e) => setSampleJson(e.currentTarget.value)}
        autosize
        minRows={3}
        maxRows={8}
        ff="monospace"
        size="xs"
        error={jsonError}
        mb={6}
      />

      {parsed !== null && (
        <Stack gap={6}>
          {/* Available paths */}
          {paths.length > 0 && (
            <Box>
              <Text size="xs" c="dimmed" mb={2}>Доступные пути в JSON:</Text>
              <Group gap={4} wrap="wrap">
                {paths.map((p) => (
                  <Code
                    key={p}
                    style={{ fontSize: 11, cursor: 'default' }}
                    title={`Используй {${p}} в template`}
                  >
                    {`{${p}}`}
                  </Code>
                ))}
              </Group>
            </Box>
          )}

          {/* Required fields check */}
          {reqFieldResults.length > 0 && (
            <Group gap={6}>
              <Text size="xs" c="dimmed">required_fields:</Text>
              {reqFieldResults.map(({ field, ok }) => (
                <Badge
                  key={field}
                  size="xs"
                  color={ok ? 'green' : 'red'}
                  variant="light"
                  leftSection={ok ? <IconCheck size={10} /> : <IconX size={10} />}
                >
                  {field}
                </Badge>
              ))}
            </Group>
          )}

          {/* Rendered result */}
          {renderResult && (
            <Box>
              <Text size="xs" c="dimmed" mb={2}>Результат рендера:</Text>
              <Box
                p="xs"
                style={{
                  background: 'var(--mantine-color-body)',
                  border: '1px solid var(--mantine-color-default-border)',
                  borderRadius: 4,
                  whiteSpace: 'pre-wrap',
                  fontFamily: 'inherit',
                  fontSize: 13,
                }}
              >
                {renderResult.rendered || <Text size="xs" c="dimmed">— пустой template —</Text>}
              </Box>
              {renderResult.missing.length > 0 && (
                <Alert color="orange" p="xs" mt={4}>
                  <Text size="xs">
                    Пути не найдены в JSON:{' '}
                    {renderResult.missing.map((m) => (
                      <Code key={m} style={{ fontSize: 11 }}>{m}</Code>
                    ))}
                    {' '}→ Tier 0 упадёт в fallback.
                  </Text>
                </Alert>
              )}
            </Box>
          )}
        </Stack>
      )}
    </Card>
  );
}

// ─── Table defs editor ───────────────────────────────────────────────────────

/** Extract all {field:table} and {field:table:...} field names from a template string. */
function detectTableFields(template: string): string[] {
  const re = /\{([^}:]+):table(?::[^}]*)?\}/g;
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = re.exec(template)) !== null) seen.add(m[1].trim());
  return [...seen];
}

type ValueMapEditorProps = {
  values: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
};

function ValueMapEditor({ values, onChange }: ValueMapEditorProps) {
  // Local pairs state — NOT derived from props on every render.
  // This prevents rows from disappearing while the user is typing
  // (empty key gets filtered out of the parent object, causing the row to vanish).
  const [pairs, setPairs] = useState<[string, string][]>(
    () => Object.entries(values),
  );

  function commit(next: [string, string][]) {
    setPairs(next);
    // Push only pairs with a non-empty key to the parent
    onChange(Object.fromEntries(next.filter(([k]) => k.trim() !== '')));
  }

  function set(i: number, key: string, val: string) {
    const next = [...pairs] as [string, string][];
    next[i] = [key, val];
    commit(next);
  }

  function remove(i: number) {
    commit(pairs.filter((_, j) => j !== i) as [string, string][]);
  }

  function addRow() {
    // Add row only to local state — don't call onChange yet (key is empty)
    setPairs((p) => [...p, ['', '']]);
  }

  return (
    <Stack gap={2}>
      {pairs.map(([k, v], i) => (
        <Group key={i} gap={4} wrap="nowrap">
          <TextInput
            size="xs" ff="monospace" placeholder="up"
            value={k}
            style={{ width: 80 }}
            onChange={(e) => set(i, e.currentTarget.value, v)}
          />
          <Text size="xs" c="dimmed">→</Text>
          <TextInput
            size="xs" placeholder="🟢"
            value={v}
            style={{ flex: 1 }}
            onChange={(e) => set(i, k, e.currentTarget.value)}
          />
          <ActionIcon size="xs" variant="subtle" color="red" onClick={() => remove(i)}>
            <IconTrash size={10} />
          </ActionIcon>
        </Group>
      ))}
      <Button
        size="xs" variant="subtle" leftSection={<IconPlus size={10} />}
        onClick={addRow}
      >
        Добавить значение
      </Button>
    </Stack>
  );
}

type ColDefCardProps = {
  col: ColDef;
  index: number;
  total: number;
  onChange: (next: ColDef) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
};

function ColDefCard({ col, index, total, onChange, onRemove, onMove }: ColDefCardProps) {
  const [open, setOpen] = useState(false);
  const hasValues = col.values && Object.keys(col.values).length > 0;

  return (
    <Card withBorder padding="xs" style={{ background: 'var(--mantine-color-default-hover)' }}>
      <Group gap="xs" wrap="nowrap" align="center">
        {/* Reorder */}
        <Stack gap={1}>
          <ActionIcon size="xs" variant="subtle" disabled={index === 0} onClick={() => onMove(-1)}>
            <Text style={{ fontSize: 10 }} lh={1}>▲</Text>
          </ActionIcon>
          <ActionIcon size="xs" variant="subtle" disabled={index === total - 1} onClick={() => onMove(1)}>
            <Text style={{ fontSize: 10 }} lh={1}>▼</Text>
          </ActionIcon>
        </Stack>

        {/* Field + Label (always visible) */}
        <TextInput
          size="xs" ff="monospace" placeholder="field"
          label={<Text size="xs" c="dimmed">field</Text>}
          value={col.field}
          onChange={(e) => onChange({ ...col, field: e.currentTarget.value })}
          style={{ width: 120 }}
        />
        <TextInput
          size="xs" placeholder="Заголовок"
          label={<Text size="xs" c="dimmed">label</Text>}
          value={col.label}
          onChange={(e) => onChange({ ...col, label: e.currentTarget.value })}
          style={{ width: 110 }}
        />

        {/* Quick empty field */}
        <TextInput
          size="xs" placeholder="—"
          label={<Text size="xs" c="dimmed">если пусто</Text>}
          value={col.empty ?? ''}
          onChange={(e) => onChange({ ...col, empty: e.currentTarget.value })}
          style={{ width: 70 }}
        />

        {/* Expand toggle */}
        <ActionIcon
          size="sm" variant={open || hasValues ? 'light' : 'subtle'}
          color={hasValues ? 'blue' : 'gray'}
          title="Маппинги и опции"
          onClick={() => setOpen((v) => !v)}
          style={{ marginTop: 'auto' }}
        >
          {open ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
        </ActionIcon>

        <div style={{ flex: 1 }} />

        <ActionIcon size="sm" variant="subtle" color="red" onClick={onRemove} style={{ marginTop: 'auto' }}>
          <IconTrash size={12} />
        </ActionIcon>
      </Group>

      <Collapse expanded={open}>
        <Stack gap="xs" mt="xs" pl={28}>
          {/* Value map */}
          <div>
            <Text size="xs" fw={500} mb={4}>
              values — маппинг значений{' '}
              <Text component="span" c="dimmed" size="xs">(напр. up→🟢, down→🔴)</Text>
            </Text>
            <ValueMapEditor
              values={col.values ?? {}}
              onChange={(v) => onChange({ ...col, values: v })}
            />
          </div>

          {/* Options row */}
          <Group gap="sm" wrap="wrap">
            <Select
              size="xs"
              label={<Text size="xs" c="dimmed">format</Text>}
              placeholder="—"
              style={{ width: 100 }}
              data={[
                { value: '', label: '—' },
                { value: 'phones', label: 'phones' },
                { value: 'money', label: 'money' },
              ]}
              value={col.format ?? ''}
              onChange={(v) => onChange({ ...col, format: (v as ColDef['format']) || undefined })}
            />
            <TextInput
              size="xs" label={<Text size="xs" c="dimmed">max_len</Text>}
              placeholder="38" style={{ width: 70 }}
              value={col.max_len != null ? String(col.max_len) : ''}
              onChange={(e) => {
                const n = parseInt(e.currentTarget.value, 10);
                onChange({ ...col, max_len: isNaN(n) ? undefined : n });
              }}
            />
            <TextInput
              size="xs" label={<Text size="xs" c="dimmed">prefix</Text>}
              placeholder="" style={{ width: 70 }}
              value={col.prefix ?? ''}
              onChange={(e) => onChange({ ...col, prefix: e.currentTarget.value || undefined })}
            />
            <TextInput
              size="xs" label={<Text size="xs" c="dimmed">suffix</Text>}
              placeholder=" Mbps" style={{ width: 70 }}
              value={col.suffix ?? ''}
              onChange={(e) => onChange({ ...col, suffix: e.currentTarget.value || undefined })}
            />
            <Switch
              size="sm"
              label={<Text size="xs">strip_html</Text>}
              checked={!!col.strip_html}
              onChange={(e) => onChange({ ...col, strip_html: e.currentTarget.checked || undefined })}
              style={{ marginTop: 'auto', paddingBottom: 2 }}
            />
          </Group>
        </Stack>
      </Collapse>
    </Card>
  );
}

type TableDefsEditorProps = {
  template: string;
  tableDefs: TableDefs;
  onChange: (next: TableDefs) => void;
};

function TableDefsSection({ template, tableDefs, onChange }: TableDefsEditorProps) {
  const detectedFields = detectTableFields(template);

  if (detectedFields.length === 0) return null;

  function getEntry(field: string) {
    return tableDefs[field] ?? { columns: [] };
  }

  function setEntry(field: string, entry: { columns: ColDef[] }) {
    onChange({ ...tableDefs, [field]: entry });
  }

  function addCol(field: string) {
    const entry = getEntry(field);
    setEntry(field, {
      columns: [...entry.columns, { field: '', label: '' }],
    });
  }

  function updateCol(field: string, i: number, col: ColDef) {
    const entry = getEntry(field);
    const cols = [...entry.columns];
    cols[i] = col;
    setEntry(field, { columns: cols });
  }

  function removeCol(field: string, i: number) {
    const entry = getEntry(field);
    setEntry(field, { columns: entry.columns.filter((_, j) => j !== i) });
  }

  function moveCol(field: string, i: number, dir: -1 | 1) {
    const entry = getEntry(field);
    const cols = [...entry.columns];
    const j = i + dir;
    if (j < 0 || j >= cols.length) return;
    [cols[i], cols[j]] = [cols[j], cols[i]];
    setEntry(field, { columns: cols });
  }

  return (
    <div>
      <Group justify="space-between" mb={4}>
        <div>
          <Text size="sm" fw={500}>📊 Table defs — форматирование таблиц</Text>
          <Text size="xs" c="dimmed">
            Для каждого <Code>{'{field:table}'}</Code> в template: задай колонки, заголовки, маппинги значений.
          </Text>
        </div>
      </Group>

      <Stack gap="sm">
        {detectedFields.map((field) => {
          const entry = getEntry(field);
          return (
            <Card key={field} withBorder padding="sm">
              <Group justify="space-between" mb="xs">
                <Group gap="xs">
                  <Badge color="grape" variant="light" ff="monospace">{`{${field}:table}`}</Badge>
                  <Text size="xs" c="dimmed">{entry.columns.length} колонок</Text>
                </Group>
                <Button
                  size="xs" variant="light" color="grape"
                  leftSection={<IconPlus size={11} />}
                  onClick={() => addCol(field)}
                >
                  Добавить колонку
                </Button>
              </Group>

              {entry.columns.length === 0 ? (
                <Alert color="grape" variant="light" p="xs">
                  <Text size="xs">
                    Нет колонок → таблица авто-определит поля из первой строки данных.
                    Добавь колонки чтобы контролировать порядок, заголовки и форматирование.
                  </Text>
                </Alert>
              ) : (
                <Stack gap={4}>
                  {entry.columns.map((col, i) => (
                    <ColDefCard
                      key={i}
                      col={col}
                      index={i}
                      total={entry.columns.length}
                      onChange={(next) => updateCol(field, i, next)}
                      onRemove={() => removeCol(field, i)}
                      onMove={(dir) => moveCol(field, i, dir)}
                    />
                  ))}
                </Stack>
              )}
            </Card>
          );
        })}
      </Stack>
    </div>
  );
}

// ─── Value maps editor: {field:map} normalizers ─────────────────────────────
function ValueMapsSection({
  value,
  onChange,
}: {
  value: Record<string, Record<string, string>> | undefined;
  onChange: (next: Record<string, Record<string, string>> | undefined) => void;
}) {
  const entries = Object.entries(value || {});

  // Serialize one field's map to "raw=display" lines and back.
  const mapToText = (m: Record<string, string>) =>
    Object.entries(m).map(([k, v]) => `${k}=${v}`).join('\n');
  const textToMap = (t: string): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const line of t.split('\n')) {
      const i = line.indexOf('=');
      if (i < 0) continue;
      const k = line.slice(0, i).trim();
      if (k) out[k] = line.slice(i + 1).trim();
    }
    return out;
  };

  const setField = (oldName: string, newName: string, m: Record<string, string>) => {
    const next: Record<string, Record<string, string>> = {};
    for (const [f, mm] of Object.entries(value || {})) {
      if (f === oldName) continue;
      next[f] = mm;
    }
    if (newName.trim()) next[newName.trim()] = m;
    onChange(Object.keys(next).length ? next : undefined);
  };
  const removeField = (name: string) => {
    const next = { ...(value || {}) };
    delete next[name];
    onChange(Object.keys(next).length ? next : undefined);
  };
  const addField = () => {
    const base = 'field';
    let name = base;
    let n = 1;
    while ((value || {})[name]) name = `${base}${++n}`;
    onChange({ ...(value || {}), [name]: {} });
  };

  return (
    <div>
      <Group justify="space-between" mb={4}>
        <div>
          <Text size="sm" fw={500}>Нормализация значений <Code>{`{поле:map}`}</Code></Text>
          <Text size="xs" c="dimmed">1/0 → Включен/Отключен и т.п. Применяется только к плейсхолдерам со спецом <Code>:map</Code>.</Text>
        </div>
        <Button size="compact-xs" variant="light" leftSection={<IconPlus size={12} />} onClick={addField}>
          Поле
        </Button>
      </Group>
      {entries.length === 0 ? (
        <Text size="xs" c="dimmed">Нет карт значений. «Поле» → добавить (имя = последний сегмент пути, напр. <Code>state</Code>).</Text>
      ) : (
        <Stack gap="xs">
          {entries.map(([field, m]) => (
            <Card key={field} withBorder padding="xs">
              <Group gap="xs" mb={4} align="center" wrap="nowrap">
                <TextInput
                  size="xs"
                  label="Поле (имя или путь)"
                  value={field}
                  onChange={(e) => setField(field, e.currentTarget.value, m)}
                  placeholder="state"
                  style={{ flex: 1 }}
                />
                <ActionIcon variant="subtle" color="red" size="sm" mt={18} onClick={() => removeField(field)}>
                  <IconTrash size={14} />
                </ActionIcon>
              </Group>
              <Textarea
                size="xs"
                label="Карта: одна пара на строку, формат raw=display"
                placeholder={'1=Включен\n0=Отключен'}
                autosize minRows={2} maxRows={8}
                ff="monospace"
                value={mapToText(m)}
                onChange={(e) => setField(field, field, textToMap(e.currentTarget.value))}
              />
            </Card>
          ))}
        </Stack>
      )}
    </div>
  );
}

// ─── Main editor ─────────────────────────────────────────────────────────────

// ─── Test bench: query → Tier 0 explain trace + full LLM run ─────────────────
function Tier0TestBench({ tenantId, toolName, currentConfig, embedded }: {
  tenantId: string; toolName?: string; currentConfig?: Tier0Template | null; embedded?: boolean;
}) {
  const [open, setOpen] = useState(!!embedded);
  const [query, setQuery] = useState('');
  const [explain, setExplain] = useState<Tier0ExplainResult | null>(null);
  const [llm, setLlm] = useState<Tier0TestLLMResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [llmLoading, setLlmLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const sevColor: Record<string, string> = { error: 'red', warning: 'orange', info: 'blue' };

  async function runExplain() {
    if (!query.trim()) return;
    setLoading(true); setErr(null); setLlm(null);
    try {
      // Test the LIVE editor config (unsaved) via override, so changes are
      // checkable immediately — no need to save the tool first.
      const override = currentConfig ? (currentConfig as unknown as Record<string, unknown>) : null;
      setExplain(await tier0Api.explain(tenantId, query.trim(), toolName, true, override));
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setErr(typeof detail === 'string' ? detail : e instanceof Error ? e.message : String(e));
    } finally { setLoading(false); }
  }

  async function runLlm() {
    if (!query.trim()) return;
    setLlmLoading(true); setErr(null);
    try {
      setLlm(await tier0Api.testLlm(tenantId, query.trim()));
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setErr(typeof detail === 'string' ? detail : e instanceof Error ? e.message : String(e));
    } finally { setLlmLoading(false); }
  }

  const d = explain?.decision;
  const entityChips = explain
    ? Object.entries(explain.entities).filter(([, v]) => Array.isArray(v) && v.length > 0)
    : [];

  const body = (
        <Stack gap="sm" mt={embedded ? 0 : 'sm'}>
          {embedded && currentConfig && (
            <Group justify="flex-end">
              <Badge size="xs" variant="light" color="grape">текущая настройка (без сохранения)</Badge>
            </Group>
          )}
          <Group align="flex-end" gap="xs" wrap="nowrap">
            <Textarea
              label="Тестовый запрос"
              placeholder="покажи свич косарева 113"
              autosize minRows={1} maxRows={4}
              style={{ flex: 1 }}
              value={query}
              onChange={(e) => setQuery(e.currentTarget.value)}
            />
            <Button leftSection={<IconFlask size={14} />} loading={loading}
                    disabled={!query.trim()} onClick={runExplain}>
              Проверить
            </Button>
          </Group>

          {err && <Alert color="red" variant="light" p="xs"><Text size="sm">{err}</Text></Alert>}

          {explain && (
            <Stack gap="sm">
              {!explain.tenant_tier0_enabled && (
                <Alert color="yellow" variant="light" p="xs">
                  <Text size="xs">Tier 0 выключен у тенанта (Настройки оболочки → Tier 0). Трейс показывает,
                    что произошло бы, если включить.</Text>
                </Alert>
              )}

              {/* Decision banner */}
              <Alert
                color={d?.fired ? 'green' : 'orange'}
                variant="light" p="xs"
                icon={d?.fired ? <IconCheck size={16} /> : <IconX size={16} />}
              >
                <Text size="sm" fw={600}>
                  {d?.fired ? `Tier 0 сработал → ${d?.tool}` : 'Tier 0 не сработал'}
                </Text>
                <Text size="xs" style={{ whiteSpace: 'pre-wrap' }}>{d?.reason}</Text>
              </Alert>

              {/* Recommendations */}
              {explain.recommendations.length > 0 && (
                <Stack gap={4}>
                  {explain.recommendations.map((r, i) => (
                    <Alert key={i} color={sevColor[r.severity] || 'blue'} variant="light" p="xs"
                           icon={<IconBulb size={15} />}>
                      <Text size="xs">{r.text}</Text>
                    </Alert>
                  ))}
                </Stack>
              )}

              {/* Entities */}
              <Group gap="xs">
                <Text size="xs" fw={600} c="dimmed">Сущности:</Text>
                {entityChips.length === 0
                  ? <Text size="xs" c="dimmed">— (тел./ip/mac/id/email/дата не найдены)</Text>
                  : entityChips.map(([k, v]) => (
                      <Badge key={k} size="xs" variant="light" color="teal">{k}: {(v as string[]).join(', ')}</Badge>
                    ))}
              </Group>

              {/* Steps */}
              {explain.steps.length > 0 && (
                <Stack gap={2}>
                  <Text size="xs" fw={600} c="dimmed">Шаги решения:</Text>
                  {explain.steps.map((s, i) => (
                    <Group key={i} gap={6} wrap="nowrap" align="flex-start">
                      {s.status === 'ok'
                        ? <IconCheck size={14} color="var(--mantine-color-green-6)" style={{ marginTop: 2 }} />
                        : s.status === 'fail'
                          ? <IconX size={14} color="var(--mantine-color-red-6)" style={{ marginTop: 2 }} />
                          : <Text span size="xs" c="dimmed">•</Text>}
                      <Text size="xs"><b>{s.label}:</b> {s.detail}</Text>
                    </Group>
                  ))}
                </Stack>
              )}

              {/* Ranking */}
              {explain.ranking.length > 0 && (
                <div>
                  <Text size="xs" fw={600} c="dimmed" mb={2}>Семантическое ранжирование (top-8):</Text>
                  <Table fz="xs" withTableBorder verticalSpacing={2}>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Инструмент</Table.Th>
                        <Table.Th style={{ width: 70 }}>score</Table.Th>
                        <Table.Th style={{ width: 60 }}>boost</Table.Th>
                        <Table.Th style={{ width: 60 }}>Tier 0</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {explain.ranking.map((r) => (
                        <Table.Tr key={r.name}
                                  style={r.name === toolName ? { background: 'var(--mantine-color-blue-light)' } : undefined}>
                          <Table.Td>{r.name}{r.name === toolName && ' ⭐'}</Table.Td>
                          <Table.Td>{r.total_score.toFixed(3)}</Table.Td>
                          <Table.Td>{r.entity_boost ? `+${r.entity_boost.toFixed(2)}` : '—'}</Table.Td>
                          <Table.Td>{r.has_tier0
                            ? <Badge size="xs" color="grape" variant="light">{r.required_entity || 'Y'}</Badge>
                            : <Text span size="xs" c="dimmed">—</Text>}</Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </div>
              )}

              {/* Competing regex matches */}
              {explain.regex_matches.length > 0 && (
                <div>
                  <Text size="xs" fw={600} c="dimmed" mb={2}>
                    Конкурирующие совпадения regex ({explain.regex_matches.length}):
                  </Text>
                  <Stack gap={2}>
                    {explain.regex_matches.map((r) => (
                      <Group key={r.name} gap={6} wrap="nowrap">
                        <Badge size="xs" variant="light"
                               color={r.name === d?.tool ? 'green' : 'gray'}>{r.name}</Badge>
                        <Text size="xs" c="dimmed">→ {r.extracted}</Text>
                        {!r.in_topk && <Badge size="xs" color="orange" variant="outline">не в top-8</Badge>}
                        {r.blocked_by && <Badge size="xs" color="red" variant="outline">block: {r.blocked_by}</Badge>}
                      </Group>
                    ))}
                  </Stack>
                </div>
              )}

              {/* Assembled tool arguments */}
              {d?.arguments && (
                <Text size="xs" c="dimmed">
                  Аргументы вызова: <Code fz="xs">{JSON.stringify(d.arguments)}</Code>
                </Text>
              )}

              {/* Raw tool output JSON — what template paths must match */}
              {d?.tool_output && (
                <div>
                  <Text size="xs" fw={600} c="dimmed" mb={2}>
                    JSON-вывод инструмента <Text span c="dimmed">(сверяй пути шаблона с этой структурой)</Text>:
                  </Text>
                  <Box style={{ background: 'var(--mantine-color-dark-8)', borderRadius: 6, padding: '8px 10px',
                               fontFamily: 'monospace', fontSize: 11, whiteSpace: 'pre-wrap',
                               maxHeight: 280, overflowY: 'auto' }}>
                    {(() => { try { return JSON.stringify(JSON.parse(d.tool_output), null, 2); }
                              catch { return d.tool_output; } })()}
                  </Box>
                </div>
              )}

              {/* Rendered output (if tool ran) */}
              {d?.rendered && (
                <div>
                  <Text size="xs" fw={600} c="dimmed" mb={2}>Ответ Tier 0 (отрендерено):</Text>
                  <Box style={{ background: 'var(--mantine-color-dark-8)', borderRadius: 6, padding: '8px 10px',
                               fontSize: 12, whiteSpace: 'pre-wrap', maxHeight: 200, overflowY: 'auto' }}>
                    {d.rendered}
                  </Box>
                </div>
              )}

              <Divider label="Полный пайплайн" labelPosition="center" />
              <Group justify="space-between" align="center">
                <Text size="xs" c="dimmed">Прогнать тот же запрос через всю цепочку (Tier 0 → LLM), не засоряя чаты.</Text>
                <Button size="xs" variant="light" color="blue" leftSection={<IconWand size={13} />}
                        loading={llmLoading} onClick={runLlm}>
                  Прогнать через LLM
                </Button>
              </Group>

              {llm && (
                <Alert color={llm.served_by === 'tier0' ? 'grape' : 'blue'} variant="light" p="xs">
                  <Group gap="xs" mb={4}>
                    <Badge size="sm" color={llm.served_by === 'tier0' ? 'grape' : 'blue'}>
                      {llm.served_by === 'tier0' ? 'Ответил Tier 0' : 'Ответил LLM'}
                    </Badge>
                    {llm.model_name && <Badge size="sm" variant="light" color="gray">{llm.model_name}</Badge>}
                    {!!llm.tool_calls_count && <Badge size="sm" variant="light" color="cyan">tools: {llm.tool_calls_count}</Badge>}
                    {llm.total_tokens != null && <Text size="xs" c="dimmed">{llm.total_tokens} ток.</Text>}
                    {llm.latency_ms != null && <Text size="xs" c="dimmed">{Math.round(llm.latency_ms)} мс</Text>}
                  </Group>
                  <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{llm.content}</Text>
                </Alert>
              )}
            </Stack>
          )}
        </Stack>
  );

  if (embedded) return body;

  return (
    <Card withBorder padding="sm">
      <Group
        justify="space-between" align="center"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen((o) => !o)}
      >
        <Group gap="xs">
          {open ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          <IconFlask size={16} />
          <Text fw={600} size="sm">Тест и диагностика</Text>
          <Text size="xs" c="dimmed">— проверить запрос в Tier 0 и в LLM, увидеть причину</Text>
        </Group>
        {currentConfig && <Badge size="xs" variant="light" color="grape">текущая настройка (без сохранения)</Badge>}
      </Group>
      <Collapse expanded={open}>{body}</Collapse>
    </Card>
  );
}

export function Tier0TemplateEditor({ value, onChange, tenantId, toolName, toolDescription }: Tier0TemplateEditorProps) {
  const enabled = value !== null;
  const tpl = value ?? { template: '', required_entity: null, keyword_regex: null, param_maps: [], required_fields: [] };
  const [showPreview, setShowPreview] = useState(false);
  const [dragAttemptIndex, setDragAttemptIndex] = useState<number | null>(null);

  // ── LLM Assist modal ──────────────────────────────────────────────────────
  const [assistOpen, setAssistOpen] = useState(false);
  const [assistMessage, setAssistMessage] = useState('');
  const [assistLoading, setAssistLoading] = useState(false);
  const [assistResult, setAssistResult] = useState<{ explanation: string; suggestion: Record<string, unknown> } | null>(null);
  const [assistError, setAssistError] = useState<string | null>(null);

  async function runAssist() {
    if (!tenantId || !assistMessage.trim()) return;
    setAssistLoading(true);
    setAssistResult(null);
    setAssistError(null);
    try {
      const token = localStorage.getItem('auth_token') || '';
      const resp = await fetch(`/api/admin/tenants/${tenantId}/tier0/assist`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          user_message: assistMessage.trim(),
          tool_name: toolName || '',
          tool_description: toolDescription || '',
          current_tier0: value,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        // err.detail may be a Pydantic validation array — normalise to string
        const detail = err.detail;
        const msg = typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((d: Record<string, unknown>) => `${Array.isArray(d.loc) ? d.loc.slice(-1)[0] : ''}: ${d.msg ?? d}`).join('; ')
            : `HTTP ${resp.status}`;
        throw new Error(msg);
      }
      const data = await resp.json();
      // Normalise explanation — LLM might return array instead of string
      const rawExp = data.explanation;
      const explanation: string = typeof rawExp === 'string'
        ? rawExp
        : Array.isArray(rawExp)
          ? rawExp.map((e: unknown) => (typeof e === 'string' ? e : JSON.stringify(e))).join('\n')
          : rawExp != null ? String(rawExp) : '';
      setAssistResult({ explanation, suggestion: data.suggestion || {} });
    } catch (e: unknown) {
      setAssistError(e instanceof Error ? e.message : String(e));
    } finally {
      setAssistLoading(false);
    }
  }

  function applyAssistSuggestion() {
    if (!assistResult) return;
    // LLM-authored regex doesn't come from the visual builder — drop any saved
    // builder state so the constructor won't show settings that no longer match.
    onChange({ ...tpl, keyword_builder_state: undefined, ...(assistResult.suggestion as Partial<Tier0Template>) });
    setAssistOpen(false);
    setAssistMessage('');
    setAssistResult(null);
  }

  // ── Wizard modal (multi-example generation + validation) ───────────────────
  type ValRow = {
    query: string; matched: boolean; extracted: string | null;
    blocked: boolean; reason: string; expected: 'match' | 'skip'; ok: boolean;
  };
  type WizardResult = {
    explanation: string;
    suggestion: Record<string, unknown>;
    validation: { results: ValRow[]; passed: number; total: number; all_ok: boolean };
  };
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizPos, setWizPos] = useState('');
  const [wizNeg, setWizNeg] = useState('');
  const [wizSample, setWizSample] = useState('');
  const [wizNotes, setWizNotes] = useState('');
  const [wizLoading, setWizLoading] = useState(false);
  const [wizResult, setWizResult] = useState<WizardResult | null>(null);
  const [wizError, setWizError] = useState<string | null>(null);

  // Pre-fill the wizard with previously saved inputs when it opens, so the admin
  // tweaks an existing set of examples instead of re-typing them every time.
  useEffect(() => {
    if (!wizardOpen) return;
    const wi = value?.wizard_inputs;
    if (!wi) return;
    setWizPos((prev) => prev || (wi.positive_examples || []).join('\n'));
    setWizNeg((prev) => prev || (wi.negative_examples || []).join('\n'));
    setWizSample((prev) => prev || (wi.sample_output || ''));
    setWizNotes((prev) => prev || (wi.notes || ''));
  }, [wizardOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const splitLines = (s: string) => s.split('\n').map((l) => l.trim()).filter(Boolean);

  async function runWizard(refine: boolean) {
    if (!tenantId) return;
    const positives = splitLines(wizPos);
    if (positives.length === 0) { setWizError('Добавьте хотя бы один пример-запрос.'); return; }
    setWizLoading(true);
    setWizError(null);
    setWizCheck(null);
    try {
      const token = localStorage.getItem('auth_token') || '';
      const resp = await fetch(`/api/admin/tenants/${tenantId}/tier0/wizard`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          tool_name: toolName || '',
          tool_description: toolDescription || '',
          positive_examples: positives,
          negative_examples: splitLines(wizNeg),
          sample_output: wizSample.trim() || null,
          notes: wizNotes.trim() || null,
          // On refine, hand back the previous suggestion so the backend can
          // diff its failures and tell the LLM what to fix.
          current_tier0: refine && wizResult ? wizResult.suggestion : null,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const detail = err.detail;
        const msg = typeof detail === 'string' ? detail
          : Array.isArray(detail)
            ? detail.map((d: Record<string, unknown>) => `${Array.isArray(d.loc) ? d.loc.slice(-1)[0] : ''}: ${d.msg ?? d}`).join('; ')
            : `HTTP ${resp.status}`;
        throw new Error(msg);
      }
      const data = await resp.json();
      const rawExp = data.explanation;
      const explanation: string = typeof rawExp === 'string' ? rawExp
        : Array.isArray(rawExp) ? rawExp.map((e: unknown) => (typeof e === 'string' ? e : JSON.stringify(e))).join('\n')
        : rawExp != null ? String(rawExp) : '';
      setWizResult({
        explanation,
        suggestion: data.suggestion || {},
        validation: data.validation || { results: [], passed: 0, total: 0, all_ok: false },
      });
    } catch (e: unknown) {
      setWizError(e instanceof Error ? e.message : String(e));
    } finally {
      setWizLoading(false);
    }
  }

  function applyWizardSuggestion() {
    if (!wizResult) return;
    // Wizard regex isn't from the visual builder — drop saved builder state so
    // the constructor won't reopen with settings that no longer match.
    // Persist the example inputs so a later tweak starts from them, not blank.
    const wizard_inputs = {
      positive_examples: splitLines(wizPos),
      negative_examples: splitLines(wizNeg),
      sample_output: wizSample.trim() || null,
      notes: wizNotes.trim() || null,
    };
    onChange({
      ...tpl,
      keyword_builder_state: undefined,
      ...(wizResult.suggestion as Partial<Tier0Template>),
      wizard_inputs,
    });
    setWizardOpen(false);
    setWizResult(null);
  }

  // Quick Tier 0 check of the generated suggestion WITHOUT applying/saving.
  const [wizCheck, setWizCheck] = useState<Tier0ExplainResult | null>(null);
  const [wizCheckLoading, setWizCheckLoading] = useState(false);
  async function checkWizardInTier0() {
    if (!wizResult || !tenantId) return;
    const firstQuery = splitLines(wizPos)[0];
    if (!firstQuery) return;
    setWizCheckLoading(true);
    try {
      setWizCheck(await tier0Api.explain(
        tenantId, firstQuery, toolName, true,
        wizResult.suggestion as Record<string, unknown>,
      ));
    } catch {
      setWizCheck(null);
    } finally { setWizCheckLoading(false); }
  }

  const attempts: ParamMapRow[][] = useMemo(
    () => (tpl.param_maps || []).map(mapToRows),
    [tpl.param_maps],
  );

  function emit(next: Partial<Tier0Template>) {
    onChange({ ...tpl, ...next });
  }

  function updateAttempt(i: number, rows: ParamMapRow[]) {
    const nextMaps = [...(tpl.param_maps || [])];
    nextMaps[i] = rowsToMap(rows);
    emit({ param_maps: nextMaps });
  }

  function addAttempt() {
    emit({ param_maps: [...(tpl.param_maps || []), {}] });
  }

  function removeAttempt(i: number) {
    emit({ param_maps: (tpl.param_maps || []).filter((_, j) => j !== i) });
  }

  function reorderAttempts(from: number, to: number) {
    emit({ param_maps: moveItem(tpl.param_maps || [], from, to) });
  }

  const isKeywordExtract = tpl.required_entity === 'keyword_extract';
  const [builderOpen, setBuilderOpen] = useState(false);

  // ── Disabled state ────────────────────────────────────────────────────────
  if (!enabled) {
    return (
      <Card withBorder padding="sm">
        <Group justify="space-between">
          <div>
            <Text size="sm" fw={600}>
              ⚡ Tier 0 template
              <Text component="span" size="xs" c="dimmed" ml={6}>
                (deterministic shortcut, без LLM)
              </Text>
            </Text>
            <Text size="xs" c="dimmed">
              Если включено, pipeline может вызывать этот tool напрямую и рендерить ответ
              через template — за 100-700ms без LLM.
            </Text>
          </div>
          <Button
            size="xs"
            variant="light"
            color="yellow"
            leftSection={<IconPlus size={12} />}
            onClick={() =>
              onChange({
                template: '**{items.0.name}**\n- Поле: {items.0.field}',
                required_entity: 'phone',
                keyword_regex: null,
                param_maps: [{ 'filters.phone': '$phone|re_sub:^\\+38=>' }],
                required_fields: ['items.0.name'],
              })
            }
          >
            Включить Tier 0
          </Button>
        </Group>
      </Card>
    );
  }

  // ── Enabled state ─────────────────────────────────────────────────────────
  return (
    <>
    <Card withBorder padding="sm">
      <Stack gap="sm">

        {/* Header */}
        <Group justify="space-between" align="center">
          <Group gap="xs">
            <Badge color="yellow" variant="filled" leftSection="⚡">Tier 0 template</Badge>
            <Text size="xs" c="dimmed">x_backend_config.tier0_template</Text>
          </Group>
          <Group gap="xs">
            {/* AI Assist button */}
            {tenantId && (
              <Button
                size="xs"
                variant="light"
                color="violet"
                leftSection={<IconWand size={13} />}
                onClick={() => { setAssistOpen(true); setAssistResult(null); setAssistError(null); }}
              >
                🤖 Помощь
              </Button>
            )}
            {tenantId && (
              <Button
                size="xs"
                variant="light"
                color="grape"
                leftSection={<IconSparkles size={13} />}
                onClick={() => { setWizardOpen(true); setWizError(null); }}
              >
                Визард
              </Button>
            )}
            {/* Preset picker */}
            <Menu shadow="md" width={320} position="bottom-end">
              <Menu.Target>
                <Button
                  size="xs"
                  variant="light"
                  color="blue"
                  leftSection={<IconTemplate size={13} />}
                >
                  Пресеты
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>Выбери пресет — заполнит все поля</Menu.Label>
                {PRESETS.map((p) => (
                  <Menu.Item
                    key={p.label}
                    onClick={() => onChange(p.value)}
                    style={{ whiteSpace: 'normal' }}
                  >
                    <Text size="sm" fw={600}>{p.label}</Text>
                    <Text size="xs" c="dimmed" style={{ whiteSpace: 'normal' }}>
                      {p.description}
                    </Text>
                  </Menu.Item>
                ))}
              </Menu.Dropdown>
            </Menu>
            <Switch
              label="Активно"
              checked
              onChange={(e) => { if (!e.currentTarget.checked) onChange(null); }}
            />
          </Group>
        </Group>

        {/* ── Main content Tabs ─────────────────────────────────────────── */}
        <Tabs defaultValue="template" styles={{ tab: { whiteSpace: 'nowrap', minWidth: 'max-content' } }}>
          <Tabs.List>
            <Tabs.Tab value="template">📝 Шаблон</Tabs.Tab>
            <Tabs.Tab value="trigger">🎯 Сработка</Tabs.Tab>
            <Tabs.Tab value="params">
              🗺️ Параметры
              {attempts.length > 0 && (
                <Badge size="xs" ml={5} color="blue" variant="filled">{attempts.length}</Badge>
              )}
            </Tabs.Tab>
            <Tabs.Tab value="filters">
              🚫 Фильтры
              {(tpl.block_keywords?.length ?? 0) > 0 && (
                <Badge size="xs" ml={5} color="red" variant="filled">{tpl.block_keywords!.length}</Badge>
              )}
            </Tabs.Tab>
            {tenantId && <Tabs.Tab value="test">🧪 Тест и диагностика</Tabs.Tab>}
            <Tabs.Tab value="help">📖 Справочник</Tabs.Tab>
          </Tabs.List>

          {/* ── Tab: Шаблон ─────────────────────────────────────────────────── */}
          <Tabs.Panel value="template" pt="sm">
            <Stack gap="sm">
              <Alert color="blue" variant="light" p="xs" icon={<IconBulb size={15} />}>
                <Text size="xs" fw={600} mb={4}>Путь к полю зависит от структуры ответа инструмента:</Text>
                <Stack gap={4}>
                  <div>
                    <Text size="xs">
                      • Плоский объект <Code>{`{"name": "X", "balance": 10}`}</Code> → пиши <Code>{`{name}`}</Code>, <Code>{`{balance}`}</Code>
                    </Text>
                  </div>
                  <div>
                    <Text size="xs">
                      • Массив записей <Code>{`{"items": [{"name": "X"}]}`}</Code> → индекс в массив: <Code>{`{items.0.name}`}</Code> (НЕ <Code>{`{name}`}</Code> и НЕ <Code>{`{items.name}`}</Code>)
                    </Text>
                  </div>
                  <div>
                    <Text size="xs">
                      • Чистый массив <Code>{`[{"name": "X"}]`}</Code> → <Code>{`{0.name}`}</Code>
                    </Text>
                  </div>
                  <Text size="xs" c="dimmed">
                    Не уверен в структуре — открой вкладку «🧪 Тест», проверь запрос: видно JSON-вывод инструмента и подсказку по путям. Индекс <Code>0</Code> = первая запись.
                  </Text>
                  <Text size="xs">
                    • Спецы значений: <Code>{`{x:money}`}</Code>, <Code>{`{x:int}`}</Code>, <Code>{`{x:phones}`}</Code>, <Code>{`{x:map}`}</Code> (нормализация 1→Включен — настраивается ниже в «Нормализация значений»).
                  </Text>
                </Stack>
              </Alert>
              <Textarea
                label={
                  <Group gap={4} wrap="nowrap">
                    <Text size="sm" fw={500}>Template</Text>
                    <Text size="xs" c="dimmed">
                      — синтаксис <Code>{`{items.0.name}`}</Code> · dotted path · индексы массивов как числа
                    </Text>
                  </Group>
                }
                placeholder={
                  '**{items.0.name}** (договор №{items.0.dogovor_num})\n- Баланс: {items.0.amount} грн'
                }
                autosize
                minRows={3}
                maxRows={10}
                ff="monospace"
                value={tpl.template}
                onChange={(e) => emit({ template: e.currentTarget.value })}
              />

              <Textarea
                label={
                  <Group gap={6} wrap="nowrap" align="center">
                    <Text size="sm" fw={500}>Шаблон «не найдено»</Text>
                    <Text size="xs" c="dimmed">
                      — когда инструмент вернул пусто. Плейсхолдеры: <Code>{`{keyword_extract}`}</Code>, <Code>{`{phone}`}</Code>, <Code>{`{query}`}</Code>…
                    </Text>
                  </Group>
                }
                description="Пусто — на пустом результате уходим в LLM (как сейчас). Заполнено — Tier 0 ответит этим текстом."
                placeholder="Свич {keyword_extract} не найден в базе"
                autosize
                minRows={1}
                maxRows={4}
                value={tpl.not_found_template ?? ''}
                onChange={(e) => emit({ not_found_template: e.currentTarget.value || null })}
              />

              <Group gap="xs" style={{ cursor: 'pointer' }} onClick={() => setShowPreview((v) => !v)}>
                {showPreview ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
                <Text size="xs" c="blue" fw={500}>
                  {showPreview ? 'Скрыть preview' : '🧪 Проверить template на примере JSON'}
                </Text>
              </Group>
              <Collapse expanded={showPreview}>
                <PreviewPanel template={tpl.template} requiredFields={tpl.required_fields || []} valueMaps={tpl.value_maps} />
              </Collapse>

              <ValueMapsSection
                value={tpl.value_maps}
                onChange={(next) => emit({ value_maps: next })}
              />

              <TableDefsSection
                template={tpl.template}
                tableDefs={tpl.table_defs ?? {}}
                onChange={(next) => emit({ table_defs: next })}
              />
            </Stack>
          </Tabs.Panel>

          {/* ── Tab: Сработка ───────────────────────────────────────────────── */}
          <Tabs.Panel value="trigger" pt="sm">
            <Stack gap="sm">

              <KeywordRegexBuilder
                opened={builderOpen}
                onClose={() => setBuilderOpen(false)}
                onApply={(regex, builderState) => emit({ keyword_regex: regex || null, keyword_builder_state: builderState })}
                currentRegex={tpl.keyword_regex}
                initialState={tpl.keyword_builder_state}
                onOpenWizard={tenantId ? () => setWizardOpen(true) : undefined}
              />

              <Group align="flex-end" gap="sm" wrap="wrap">
                <Select
                  label={
                    <Tooltip
                      label="Tier 0 срабатывает только если в запросе пользователя найдена entity этого типа. keyword_extract — захватывает текст по regex (имя, адрес, название свитча и т.п.)."
                      multiline w={320} withArrow
                    >
                      <Text size="sm" fw={500}>Required entity ⓘ</Text>
                    </Tooltip>
                  }
                  data={ENTITY_OPTIONS}
                  value={tpl.required_entity || ''}
                  onChange={(v) => emit({ required_entity: v || null, keyword_regex: v === 'keyword_extract' ? (tpl.keyword_regex || '') : null })}
                  style={{ flex: '0 0 260px' }}
                />

                {isKeywordExtract && (
                  <div style={{ flex: 1, minWidth: 240 }}>
                    <Group gap={4} mb={4} align="center">
                      <Tooltip
                        label={'Regex с одной capture group. Весь текст запроса проверяется против этого pattern (case-insensitive). Первая группа становится $keyword_extract.\n\nПример для свитча:\n(?:свич|switch|коммутатор)\\s+(.+?)$\n\nПример для клиента:\n(?:клиент[а-я]*|абонент[а-я]*)\\s+(.+?)$'}
                        multiline w={360} withArrow
                      >
                        <Text size="sm" fw={500}>keyword_regex ⓘ</Text>
                      </Tooltip>
                      <Tooltip label="Конструктор regex" withArrow>
                        <ActionIcon
                          size="sm"
                          variant="light"
                          color="grape"
                          onClick={() => setBuilderOpen(true)}
                          style={{ marginLeft: 4 }}
                        >
                          <IconWand size={13} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                    <TextInput
                      placeholder={'(?:свич|switch)\\s+(.+?)$'}
                      ff="monospace"
                      value={tpl.keyword_regex || ''}
                      onChange={(e) => emit({ keyword_regex: e.currentTarget.value || null })}
                    />
                  </div>
                )}

                <TextInput
                  label={
                    <Tooltip
                      label={'Через запятую. Если хоть одно поле в результате tool == null / "" → Tier 0 не срабатывает, fallback to LLM.\n\nПример: items.0.name, items.0.amount'}
                      multiline w={320} withArrow
                    >
                      <Text size="sm" fw={500}>Required fields ⓘ</Text>
                    </Tooltip>
                  }
                  placeholder="items.0.name, items.0.amount"
                  ff="monospace"
                  value={(tpl.required_fields || []).join(', ')}
                  onChange={(e) =>
                    emit({
                      required_fields: e.currentTarget.value
                        .split(',')
                        .map((s) => s.trim())
                        .filter(Boolean),
                    })
                  }
                  style={{ flex: '0 0 260px' }}
                />
              </Group>

              {isKeywordExtract && (
                <Alert color="blue" variant="light" p="xs">
                  <Text size="xs">
                    При совпадении regex захваченный текст доступен как <Code>$keyword_extract</Code> в param maps.
                    Используй в param_maps: ключ → <Code>$keyword_extract</Code>.
                    <br />
                    Пример: запрос «<em>свич Косарева 26</em>» при regex <Code>{'(?:свич|switch)\\s+(.+?)$'}</Code> даст
                    {' '}<Code>$keyword_extract = "Косарева 26"</Code>.
                  </Text>
                </Alert>
              )}

              {isKeywordExtract && (
                <div>
                  <Group justify="space-between" mb={4}>
                    <Tooltip
                      label={'Список фраз, которые срезаются с начала захваченного keyword (case-insensitive) перед передачей в tool.\n\nНапример: добавь "на свиче" — и запрос "покажи клиентов на свиче косарева 113" даст keyword_extract = "косарева 113", а не "на свиче косарева 113".'}
                      multiline w={360} withArrow
                    >
                      <Text size="sm" fw={500}>✂️ Стрип-префиксы keyword_extract ⓘ</Text>
                    </Tooltip>
                    <Button
                      size="xs" variant="light" color="orange"
                      leftSection={<IconPlus size={12} />}
                      onClick={() => emit({ strip_prefixes: [...(tpl.strip_prefixes || []), ''] })}
                    >
                      Добавить
                    </Button>
                  </Group>
                  {(!tpl.strip_prefixes || tpl.strip_prefixes.length === 0) ? (
                    <Text size="xs" c="dimmed">Не настроено — keyword передаётся как есть</Text>
                  ) : (
                    <Stack gap={4}>
                      {(tpl.strip_prefixes || []).map((sp, idx) => (
                        <Group key={idx} gap={4} wrap="nowrap">
                          <TextInput
                            value={sp}
                            placeholder="на свиче"
                            style={{ flex: 1 }}
                            styles={{ input: { fontFamily: 'monospace', fontSize: '12px' } }}
                            onChange={(e) => {
                              const next = [...(tpl.strip_prefixes || [])];
                              next[idx] = e.currentTarget.value;
                              emit({ strip_prefixes: next });
                            }}
                          />
                          <ActionIcon
                            variant="subtle" color="red" size="sm"
                            onClick={() => emit({ strip_prefixes: (tpl.strip_prefixes || []).filter((_, i) => i !== idx) })}
                          >
                            <IconTrash size={13} />
                          </ActionIcon>
                        </Group>
                      ))}
                    </Stack>
                  )}
                </div>
              )}

            </Stack>
          </Tabs.Panel>

          {/* ── Tab: Параметры ──────────────────────────────────────────────── */}
          <Tabs.Panel value="params" pt="sm">
            <div>
              <Group justify="space-between" mb={4}>
                <div>
                  <Text size="sm" fw={500}>Param maps — аргументы вызова tool'а</Text>
                  <Text size="xs" c="dimmed">
                    Каждый attempt задаёт набор параметров для вызова. Tier 0 пробует по порядку,
                    останавливается на первом успешном (все required_fields не пустые).
                    Перетаскивай <IconGripVertical size={10} style={{ verticalAlign: 'middle' }} /> для смены порядка.
                  </Text>
                </div>
                <Button
                  size="xs"
                  variant="light"
                  leftSection={<IconPlus size={12} />}
                  onClick={addAttempt}
                >
                  Добавить attempt
                </Button>
              </Group>

              {attempts.length === 0 ? (
                <Alert color="yellow" variant="light">
                  Нужен хотя бы один attempt.{' '}
                  Пример: ключ <Code>filters.phone</Code> → значение <Code>$phone|re_sub:^\+38=&gt;</Code>
                </Alert>
              ) : (
                <Stack gap={6}>
                  {attempts.map((rows, ai) => (
                    <Card
                      key={`attempt-${ai}`}
                      withBorder
                      padding="xs"
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => {
                        if (dragAttemptIndex === null || dragAttemptIndex === ai) return;
                        reorderAttempts(dragAttemptIndex, ai);
                        setDragAttemptIndex(null);
                      }}
                    >
                      <Group gap="xs" mb={6} align="center">
                        <ActionIcon
                          variant="subtle"
                          draggable
                          onDragStart={() => setDragAttemptIndex(ai)}
                          onDragEnd={() => setDragAttemptIndex(null)}
                          style={{ cursor: 'grab' }}
                          title="Перетащить attempt"
                        >
                          <IconGripVertical size={14} />
                        </ActionIcon>
                        <Badge size="sm" color="blue" variant="light">Attempt #{ai + 1}</Badge>
                        <div style={{ flex: 1 }} />
                        <ActionIcon
                          size="sm" variant="subtle" color="red"
                          onClick={() => removeAttempt(ai)}
                          title="Удалить attempt"
                        >
                          <IconTrash size={12} />
                        </ActionIcon>
                      </Group>
                      <Table withTableBorder withColumnBorders verticalSpacing={2}>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th>
                              <Tooltip label="Dotted path параметра tool'а. Пример: filters.phone, query, limit" withArrow>
                                <span>Параметр tool'а ⓘ</span>
                              </Tooltip>
                            </Table.Th>
                            <Table.Th>
                              <Tooltip
                                label={
                                  '$phone, $mac, $ip, $id, $email, $date — первая извлечённая entity.\n' +
                                  '$keyword_extract — текст захваченный keyword_regex.\n' +
                                  'После | — pipeline: upper, lower, re_sub:PAT=>REPL, extract:hex\n' +
                                  'Литерал — статическое значение: 1, true, "active"'
                                }
                                multiline w={340} withArrow
                              >
                                <span>Значение ($entity или литерал) ⓘ</span>
                              </Tooltip>
                            </Table.Th>
                            <Table.Th w={36} />
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {rows.map((row, ri) => (
                            <Table.Tr key={`a${ai}-r${ri}`}>
                              <Table.Td>
                                <TextInput
                                  size="xs"
                                  ff="monospace"
                                  placeholder="filters.query"
                                  value={row.path}
                                  onChange={(e) => {
                                    const next = [...rows];
                                    next[ri] = { ...row, path: e.currentTarget.value };
                                    updateAttempt(ai, next);
                                  }}
                                />
                              </Table.Td>
                              <Table.Td>
                                <TextInput
                                  size="xs"
                                  ff="monospace"
                                  placeholder={isKeywordExtract ? '$keyword_extract' : '$phone|re_sub:^\\+38=>'}
                                  value={row.value}
                                  onChange={(e) => {
                                    const next = [...rows];
                                    next[ri] = { ...row, value: e.currentTarget.value };
                                    updateAttempt(ai, next);
                                  }}
                                />
                              </Table.Td>
                              <Table.Td>
                                <ActionIcon
                                  variant="subtle" color="red" size="sm"
                                  onClick={() => {
                                    updateAttempt(ai, rows.filter((_, j) => j !== ri));
                                  }}
                                >
                                  <IconTrash size={12} />
                                </ActionIcon>
                              </Table.Td>
                            </Table.Tr>
                          ))}
                          <Table.Tr>
                            <Table.Td colSpan={3}>
                              <Button
                                size="xs" variant="subtle" leftSection={<IconPlus size={10} />}
                                onClick={() => updateAttempt(ai, [...rows, { path: '', value: '' }])}
                              >
                                Добавить параметр
                              </Button>
                            </Table.Td>
                          </Table.Tr>
                        </Table.Tbody>
                      </Table>
                    </Card>
                  ))}
                </Stack>
              )}
            </div>
          </Tabs.Panel>

          {/* ── Tab: Фильтры ────────────────────────────────────────────────── */}
          <Tabs.Panel value="filters" pt="sm">
            <div>
              <Group justify="space-between" mb={4}>
                <Tooltip
                  label={'Если любое из этих слов/фраз встречается в запросе пользователя — Tier 0 не срабатывает, запрос идёт в LLM.\n\nПример: добавь "з тарифом" — и "покажи клієнтів з тарифом 50 грн" не будет перехвачен Tier 0.\n\nПроверка: substring, case-insensitive.'}
                  multiline w={380} withArrow
                >
                  <Text size="sm" fw={500}>🚫 Блокирующие слова (→ LLM) ⓘ</Text>
                </Tooltip>
                <Button
                  size="xs" variant="light" color="red"
                  leftSection={<IconPlus size={12} />}
                  onClick={() => emit({ block_keywords: [...(tpl.block_keywords || []), ''] })}
                >
                  Добавить
                </Button>
              </Group>
              <Text size="xs" c="dimmed" mb={8}>
                Запрос содержит одно из этих слов → Tier 0 пропускает, запрос идёт в LLM.
                Используй для запросов с условиями, которые Tier 0 не умеет обрабатывать.
              </Text>
              {(!tpl.block_keywords || tpl.block_keywords.length === 0) ? (
                <Text size="xs" c="dimmed">Не настроено — Tier 0 срабатывает на все совпадения regex</Text>
              ) : (
                <Stack gap={4}>
                  {(tpl.block_keywords || []).map((bk, idx) => (
                    <Group key={idx} gap={4} wrap="nowrap">
                      <TextInput
                        value={bk}
                        placeholder="з тарифом"
                        style={{ flex: 1 }}
                        styles={{ input: { fontFamily: 'monospace', fontSize: '12px' } }}
                        onChange={(e) => {
                          const next = [...(tpl.block_keywords || [])];
                          next[idx] = e.currentTarget.value;
                          emit({ block_keywords: next });
                        }}
                      />
                      <ActionIcon
                        variant="subtle" color="red" size="sm"
                        onClick={() => emit({ block_keywords: (tpl.block_keywords || []).filter((_, i) => i !== idx) })}
                      >
                        <IconTrash size={13} />
                      </ActionIcon>
                    </Group>
                  ))}
                </Stack>
              )}
            </div>
          </Tabs.Panel>

          {/* ── Tab: Тест и диагностика ─────────────────────────────────────── */}
          {tenantId && (
            <Tabs.Panel value="test" pt="sm">
              <Tier0TestBench tenantId={tenantId} toolName={toolName} currentConfig={value} embedded />
            </Tabs.Panel>
          )}

          {/* ── Tab: Справочник ─────────────────────────────────────────────── */}
          <Tabs.Panel value="help" pt="sm">
            <Stack gap="sm">
              <Alert variant="light" color="gray" p="xs">
                <Stack gap={6}>

                  <Text size="xs" fw={700} c="orange">✂️ Стрип-префиксы — предлог прямо перед значением</Text>
                  <Text size="xs">
                    Используй когда предлог/контекстное слово идёт <em>напрямую</em> перед искомым значением, без существительного-посредника.
                  </Text>
                  <Box pl={8}>
                    <Text size="xs" c="dimmed">Запрос: <Code>покажи клієнтів <b>на</b> Мелешкіна 29</Code></Text>
                    <Text size="xs" c="dimmed">Regex захватил: <Code>на Мелешкіна 29</Code></Text>
                    <Text size="xs" c="green">Добавь стрип-префикс <Code>на </Code> → в тул уйдёт: <Code>Мелешкіна 29</Code> ✓</Text>
                    <Text size="xs" c="dimmed" mt={2}>Ещё примеры: <Code>по </Code>, <Code>на свиче </Code>, <Code>за адресом </Code></Text>
                  </Box>

                  <Divider my={2} />

                  <Text size="xs" fw={700} c="blue">🔷 Квалификаторы (в Конструкторе) — ПРЕДЛОГ + СУЩЕСТВИТЕЛЬНОЕ + значение</Text>
                  <Text size="xs">
                    Используй когда между предлогом и значением стоит слово-существительное (адресу, вулиці, договору…).
                    Оно убирается прямо в regex — не попадает в <Code>$keyword_extract</Code>.
                  </Text>
                  <Box pl={8}>
                    <Text size="xs" c="dimmed">Запрос: <Code>покажи клієнтів <b>по вулиці</b> Садова</Code></Text>
                    <Text size="xs" c="green">Regex: <Code>{'(?:по\\s+(?:вулиці?)\\s+)?'}</Code> → захват: <Code>Садова</Code> ✓</Text>
                    <Text size="xs" c="dimmed" mt={2}>Ещё примеры: <Code>по адресу X</Code>, <Code>по договору X</Code>, <Code>по ФИО X</Code></Text>
                    <Text size="xs" c="dimmed">Несколько предлогов: поле «Приставка» → <Code>по|на</Code></Text>
                  </Box>

                  <Divider my={2} />

                  <Text size="xs" fw={700} c="red">🚫 Блокирующие слова — запрос слишком специфичный → в LLM</Text>
                  <Box pl={8}>
                    <Text size="xs" c="dimmed">Запрос: <Code>покажи клієнтів <b>з тарифом</b> 50 грн</Code> — нужен LLM</Text>
                    <Text size="xs" c="green">Добавь блокирующее слово <Code>з тарифом</Code> → Tier 0 пропускает ✓</Text>
                    <Text size="xs" c="dimmed" mt={2}>Ещё примеры: <Code>за останній місяць</Code>, <Code>без договору</Code>, <Code>що підключені</Code></Text>
                  </Box>

                  <Divider my={2} />

                  <Text size="xs" c="dimmed">
                    💡 <b>Порядок обработки:</b> block_keywords → keyword_regex → strip_prefixes → в tool
                  </Text>

                </Stack>
              </Alert>

              <Alert variant="light" color="gray" p="xs">
                <Text size="xs">
                  <strong>Быстрая шпаргалка:</strong>
                  <br />
                  <strong>Template:</strong>{' '}
                  <Code>{'{items.0.name}'}</Code> — первый элемент массива <Code>items</Code>, поле <Code>name</Code>.
                  Root-массив: <Code>{'{0.name}'}</Code>.
                  <br />
                  <strong>Entity refs:</strong>{' '}
                  <Code>$phone</Code> <Code>$mac</Code> <Code>$ip</Code> <Code>$id</Code>{' '}
                  <Code>$email</Code> <Code>$date</Code> <Code>$keyword_extract</Code> — первая найденная entity.
                  <br />
                  <strong>Pipeline:</strong>{' '}
                  <Code>$phone|re_sub:^\+38=&gt;</Code> (убрать +38) ·{' '}
                  <Code>$mac|upper</Code> ·{' '}
                  <Code>$mac|template:xxxx.xxxx.xxxx</Code> ·{' '}
                  <Code>$id|int</Code>
                  <br />
                  <strong>keyword_extract regex:</strong>{' '}
                  <Code>{'(?:свич|switch)\\s+(.+?)$'}</Code> → «свич Косарева 26» → <Code>Косарева 26</Code>
                  <br />
                  <strong>date формат:</strong> нормализуется в ISO <Code>YYYY-MM-DD</Code>.
                  Распознаёт DD.MM.YYYY, ISO, «17 мая 2024».
                </Text>
              </Alert>
            </Stack>
          </Tabs.Panel>

        </Tabs>

      </Stack>
    </Card>

    {/* ── LLM Assist Modal ─────────────────────────────────────────────────── */}
    <Modal
      opened={assistOpen}
      onClose={() => setAssistOpen(false)}
      title={
        <Group gap="xs">
          <Text fw={600}>🤖 Помощь в настройке Tier 0</Text>
          {toolName && <Badge color="blue" variant="light" size="sm">{toolName}</Badge>}
        </Group>
      }
      size="lg"
      styles={{ body: { padding: '16px' } }}
    >
      <Stack gap="sm">
        <Alert variant="light" color="violet" p="xs">
          <Text size="xs">
            Опишите, что должен делать инструмент и какие запросы нужно перехватывать через Tier 0.
            LLM предложит конфигурацию — вы сможете применить её одним кликом.
          </Text>
        </Alert>

        <Textarea
          label="Ваш запрос"
          placeholder={'Например:\n"Хочу перехватывать запросы типа \"покажи клієнтів на Мелешкіна 29\", \"знайди абонентів по вулиці Садова\". Инструмент search_clients ищет клиентов по адресу."'}
          autosize
          minRows={4}
          maxRows={10}
          value={assistMessage}
          onChange={(e) => { const v = e.currentTarget.value; setAssistMessage(v); }}
          disabled={assistLoading}
        />

        <Group justify="flex-end">
          <Button
            variant="filled"
            color="violet"
            leftSection={<IconWand size={14} />}
            loading={assistLoading}
            disabled={!assistMessage.trim()}
            onClick={runAssist}
          >
            Спросить LLM
          </Button>
        </Group>

        {assistError && (
          <Alert color="red" variant="light">
            <Text size="sm">{assistError}</Text>
          </Alert>
        )}

        {assistResult && (
          <Stack gap="sm">
            <Divider label="Ответ LLM" labelPosition="center" />

            {assistResult.explanation && (
              <Alert variant="light" color="green" p="xs">
                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{assistResult.explanation}</Text>
              </Alert>
            )}

            <div>
              <Text size="xs" fw={600} c="dimmed" mb={4}>Предлагаемая конфигурация:</Text>
              <Box
                style={{
                  background: 'var(--mantine-color-dark-8)',
                  borderRadius: 6,
                  padding: '10px 12px',
                  fontFamily: 'monospace',
                  fontSize: '12px',
                  whiteSpace: 'pre-wrap',
                  overflowX: 'auto',
                  maxHeight: 300,
                  overflowY: 'auto',
                }}
              >
                {JSON.stringify(assistResult.suggestion, null, 2)}
              </Box>
            </div>

            <Group justify="flex-end">
              <Button variant="subtle" color="gray" size="xs" onClick={() => setAssistResult(null)}>
                Отменить
              </Button>
              <Button
                variant="filled"
                color="green"
                size="sm"
                leftSection={<IconCheck size={14} />}
                onClick={applyAssistSuggestion}
              >
                Применить
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Modal>

    {/* ── Tier 0 Wizard ───────────────────────────────────────────────── */}
    <Modal
      opened={wizardOpen}
      onClose={() => setWizardOpen(false)}
      title={
        <Group gap="xs">
          <IconSparkles size={18} />
          <Text fw={600}>Визард настройки Tier 0</Text>
          {toolName && <Badge color="grape" variant="light" size="sm">{toolName}</Badge>}
        </Group>
      }
      size="xl"
      styles={{ body: { padding: '16px' } }}
    >
      <Stack gap="sm">
        <Alert variant="light" color="grape" p="xs">
          <Text size="xs">
            Дайте несколько реальных примеров запросов — визард сгенерирует полную конфигурацию
            и сразу прогонит regex/сущность по примерам, показав, что совпало, а что нет.
            Поля универсальные (телефон, email, IP, MAC, номер, дата, произвольный текст) — не привязаны к домену.
            {value?.wizard_inputs && (
              <Text span fw={600}> Примеры подставлены из прошлого раза — допишите недостающие и сгенерируйте заново.</Text>
            )}
          </Text>
        </Alert>

        <Textarea
          label="Примеры запросов — должны срабатывать (по одному на строку)"
          placeholder={'find customer John Smith\nlookup user Jane Doe\nстатус заказа 10254'}
          autosize minRows={3} maxRows={8}
          value={wizPos}
          onChange={(e) => setWizPos(e.currentTarget.value)}
          disabled={wizLoading}
        />
        <Textarea
          label="Контр-примеры — НЕ должны срабатывать (по одному на строку, необязательно)"
          placeholder={'список всех клиентов\nкак сбросить пароль'}
          autosize minRows={2} maxRows={6}
          value={wizNeg}
          onChange={(e) => setWizNeg(e.currentTarget.value)}
          disabled={wizLoading}
        />
        <Textarea
          label="Пример JSON-ответа инструмента (необязательно — для точного шаблона рендера)"
          placeholder={'{"name": "Иван", "email": "ivan@example.com", "status": "active"}'}
          autosize minRows={2} maxRows={8}
          value={wizSample}
          onChange={(e) => setWizSample(e.currentTarget.value)}
          disabled={wizLoading}
          styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
        />
        <Textarea
          label="Доп. пояснения (необязательно)"
          placeholder="Инструмент ищет запись по введённому значению и возвращает карточку."
          autosize minRows={1} maxRows={4}
          value={wizNotes}
          onChange={(e) => setWizNotes(e.currentTarget.value)}
          disabled={wizLoading}
        />

        <Group justify="flex-end">
          <Button
            variant="filled" color="grape"
            leftSection={<IconSparkles size={14} />}
            loading={wizLoading}
            disabled={!wizPos.trim()}
            onClick={() => runWizard(false)}
          >
            Сгенерировать
          </Button>
        </Group>

        {wizError && (
          <Alert color="red" variant="light"><Text size="sm">{wizError}</Text></Alert>
        )}

        {wizResult && (
          <Stack gap="sm">
            <Divider label="Результат" labelPosition="center" />

            {wizResult.explanation && (
              <Alert variant="light" color="green" p="xs">
                <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>{wizResult.explanation}</Text>
              </Alert>
            )}

            {/* Validation summary */}
            <Group gap="xs" align="center">
              <IconListCheck size={16} />
              <Text size="sm" fw={600}>Проверка по примерам:</Text>
              <Badge
                color={wizResult.validation.all_ok ? 'green' : 'orange'}
                variant="filled"
              >
                {wizResult.validation.passed} / {wizResult.validation.total} OK
              </Badge>
            </Group>

            {wizResult.validation.results.length > 0 && (
              <Table withTableBorder withColumnBorders verticalSpacing={4} fz="xs">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th style={{ width: 36 }} />
                    <Table.Th>Запрос</Table.Th>
                    <Table.Th style={{ width: 90 }}>Ожидаем</Table.Th>
                    <Table.Th>Извлечено / причина</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {wizResult.validation.results.map((r, i) => (
                    <Table.Tr key={i}>
                      <Table.Td>
                        {r.ok
                          ? <IconCheck size={15} color="var(--mantine-color-green-6)" />
                          : <IconX size={15} color="var(--mantine-color-red-6)" />}
                      </Table.Td>
                      <Table.Td><Text size="xs">{r.query}</Text></Table.Td>
                      <Table.Td>
                        <Badge size="xs" variant="light" color={r.expected === 'match' ? 'blue' : 'gray'}>
                          {r.expected === 'match' ? 'совпасть' : 'пропуск'}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        {r.extracted
                          ? <Code fz="xs">{r.extracted}</Code>
                          : <Text size="xs" c="dimmed">{r.reason}</Text>}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}

            <div>
              <Text size="xs" fw={600} c="dimmed" mb={4}>Конфигурация:</Text>
              <Box
                style={{
                  background: 'var(--mantine-color-dark-8)', borderRadius: 6,
                  padding: '10px 12px', fontFamily: 'monospace', fontSize: 12,
                  whiteSpace: 'pre-wrap', overflowX: 'auto', maxHeight: 260, overflowY: 'auto',
                }}
              >
                {JSON.stringify(wizResult.suggestion, null, 2)}
              </Box>
            </div>

            {/* Quick Tier 0 check of the generated config (no save needed) */}
            <Group gap="xs" align="center">
              <Button
                variant="light" color="grape" size="xs"
                leftSection={<IconFlask size={13} />}
                loading={wizCheckLoading}
                onClick={checkWizardInTier0}
              >
                ⚡ Проверить в Tier 0
              </Button>
              <Text size="xs" c="dimmed">прогон по первому примеру, без сохранения</Text>
            </Group>
            {wizCheck && (
              <Alert
                color={wizCheck.decision.fired ? 'green' : 'orange'} variant="light" p="xs"
                icon={wizCheck.decision.fired ? <IconCheck size={15} /> : <IconX size={15} />}
              >
                <Text size="sm" fw={600}>
                  {wizCheck.decision.fired
                    ? `Сработает → ${wizCheck.decision.tool}`
                    : 'Не сработает'}
                </Text>
                <Text size="xs" style={{ whiteSpace: 'pre-wrap' }}>{wizCheck.decision.reason}</Text>
                {wizCheck.decision.arguments && (
                  <Text size="xs" c="dimmed" mt={2}>
                    Аргументы: <Code fz="xs">{JSON.stringify(wizCheck.decision.arguments)}</Code>
                  </Text>
                )}
                {wizCheck.recommendations.slice(0, 2).map((r, i) => (
                  <Text key={i} size="xs" c={r.severity === 'error' ? 'red' : 'dimmed'} mt={2}>↳ {r.text}</Text>
                ))}
              </Alert>
            )}

            <Group justify="space-between">
              <Button
                variant="light" color="orange" size="xs"
                leftSection={<IconWand size={13} />}
                loading={wizLoading}
                disabled={wizResult.validation.all_ok}
                onClick={() => runWizard(true)}
              >
                Доработать с учётом провалов
              </Button>
              <Group gap="xs">
                <Button variant="subtle" color="gray" size="xs" onClick={() => { setWizResult(null); setWizCheck(null); }}>
                  Сбросить
                </Button>
                <Button
                  variant="filled" color="green" size="sm"
                  leftSection={<IconCheck size={14} />}
                  onClick={applyWizardSuggestion}
                >
                  Применить
                </Button>
              </Group>
            </Group>
          </Stack>
        )}
      </Stack>
    </Modal>
    </>
  );
}
