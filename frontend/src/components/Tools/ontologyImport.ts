import type {
  DataSourceSchema,
  LLMLog,
  OntologyEntity,
  OntologyJson,
  OntologyPatch,
  OntologySection,
  Tool,
} from '../../shared/api/types';

const uid = () => `n${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;

export type OntologyTemplateId = 'blank' | 'isp' | 'billing' | 'noc';

export const ONTOLOGY_TEMPLATES: Record<Exclude<OntologyTemplateId, 'blank'>, { label: string; description: string; sections: OntologySection[] }> = {
  isp: {
    label: 'ISP / сеть',
    description: 'GPON, VLAN, абонентское оборудование',
    sections: [
      {
        id: uid(),
        type: 'glossary',
        title: 'Глоссарий ISP',
        items: [
          { term: 'GPON', definition: 'Технология пассивной оптической сети до абонента' },
          { term: 'OLT', definition: 'Оптический линейный терминал на стороне провайдера' },
          { term: 'ONU/ONT', definition: 'Абонентское оптическое устройство' },
          { term: 'VLAN', definition: 'Виртуальная локальная сеть, сегмент L2' },
          { term: 'MAC-таблица', definition: 'Таблица соответствия MAC-адресов портам коммутатора' },
        ],
      },
      {
        id: uid(),
        type: 'examples',
        title: 'Примеры запросов',
        items: [
          { query: 'Покажи MAC-таблицу на свиче', expected_tool: '', note: 'Замените expected_tool на ваш tool' },
          { query: 'Какой статус порта у абонента', expected_tool: '', note: '' },
        ],
      },
    ],
  },
  billing: {
    label: 'Биллинг',
    description: 'Абоненты, договоры, платежи',
    sections: [
      {
        id: uid(),
        type: 'glossary',
        title: 'Глоссарий биллинга',
        items: [
          { term: 'Абонент', definition: 'Клиент с активным или архивным договором' },
          { term: 'Договор', definition: 'Юридическое основание оказания услуг' },
          { term: 'Баланс', definition: 'Текущее сальдо лицевого счёта' },
          { term: 'Тариф', definition: 'Набор услуг и цена за период' },
        ],
      },
      {
        id: uid(),
        type: 'examples',
        title: 'Примеры запросов',
        items: [
          { query: 'Какой баланс у абонента', expected_tool: '', note: '' },
          { query: 'Когда последний платёж', expected_tool: '', note: '' },
        ],
      },
    ],
  },
  noc: {
    label: 'NOC / поддержка',
    description: 'Инциденты, мониторинг, SLA',
    sections: [
      {
        id: uid(),
        type: 'glossary',
        title: 'Глоссарий NOC',
        items: [
          { term: 'Инцидент', definition: 'Зафиксированная проблема в инфраструктуре или у абонента' },
          { term: 'SLA', definition: 'Целевое время реакции и восстановления' },
          { term: 'Деградация', definition: 'Ухудшение качества без полного отказа' },
        ],
      },
      {
        id: uid(),
        type: 'examples',
        title: 'Примеры запросов',
        items: [
          { query: 'Есть ли авария на узле', expected_tool: '', note: '' },
        ],
      },
    ],
  },
};

export function templateSections(id: OntologyTemplateId): OntologySection[] {
  if (id === 'blank') return [];
  return JSON.parse(JSON.stringify(ONTOLOGY_TEMPLATES[id].sections)) as OntologySection[];
}

export function glossaryFromTools(tools: Tool[]): { term: string; definition: string }[] {
  return tools
    .filter((t) => t.is_active !== false)
    .map((t) => {
      const fn = (t.config_json as { function?: { description?: string } })?.function;
      const desc = (fn?.description || t.description || '').trim();
      return {
        term: t.name,
        definition: desc || `Инструмент платформы «${t.name}»`,
      };
    })
    .sort((a, b) => a.term.localeCompare(b.term, 'ru'));
}

export function mergeGlossaryItems(
  existing: { term: string; definition: string }[],
  incoming: { term: string; definition: string }[],
): { term: string; definition: string }[] {
  const map = new Map<string, { term: string; definition: string }>();
  existing.forEach((i) => {
    if (i.term?.trim()) map.set(i.term.trim().toLowerCase(), i);
  });
  incoming.forEach((i) => {
    if (!i.term?.trim()) return;
    const key = i.term.trim().toLowerCase();
    if (!map.has(key)) map.set(key, { term: i.term.trim(), definition: i.definition?.trim() || '' });
  });
  return [...map.values()];
}

export function parseGlossaryPaste(text: string): { term: string; definition: string }[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const items: { term: string; definition: string }[] = [];
  for (const line of lines) {
    const parts = line.split(/[\t;,|]/).map((p) => p.trim()).filter(Boolean);
    if (parts.length >= 2) {
      items.push({ term: parts[0], definition: parts.slice(1).join(' — ') });
    } else if (parts.length === 1) {
      items.push({ term: parts[0], definition: '' });
    }
  }
  return items;
}

export function entitiesFromSchema(schema: DataSourceSchema, tableFullNames?: string[]): OntologyEntity[] {
  const tables = tableFullNames?.length
    ? schema.tables.filter((t) => tableFullNames.includes(t.full_name))
    : schema.tables;
  return tables.map((table) => {
    const cols = schema.columns.filter((c) => c.table === table.full_name || c.table === table.name);
    return {
      name: table.name,
      fields: cols.map((c) => ({
        name: c.column,
        type: c.type,
        description: c.nullable ? `${c.type}, nullable` : c.type,
      })),
    };
  });
}

export function buildEntitiesSection(title: string, entities: OntologyEntity[]): OntologySection {
  return { id: uid(), type: 'entities', title, entities };
}

export function buildGlossarySection(title: string, items: { term: string; definition: string }[]): OntologySection {
  return { id: uid(), type: 'glossary', title, items };
}

export function examplesFromTier0Hits(
  hits: Array<{ user_query: string; tool: string | null }>,
): { query: string; expected_tool?: string; note?: string }[] {
  return hits
    .filter((h) => h.user_query?.trim())
    .map((h) => ({
      query: h.user_query.trim(),
      expected_tool: h.tool || '',
      note: h.tool ? 'Импорт из Tier 0' : '',
    }));
}

export function examplesFromLogs(
  logs: LLMLog[],
): { query: string; expected_tool?: string; note?: string }[] {
  return logs
    .filter((l) => l.request_preview?.trim())
    .map((l) => ({
      query: l.request_preview!.trim(),
      expected_tool: '',
      note: l.tool_calls_count ? `Лог ${l.id.slice(0, 8)}, ${l.tool_calls_count} tool call(s)` : `Лог ${l.id.slice(0, 8)}`,
    }));
}

export function mergeExamples(
  existing: { query: string; expected_tool?: string; note?: string }[],
  incoming: { query: string; expected_tool?: string; note?: string }[],
): typeof existing {
  const seen = new Set(existing.map((e) => e.query.trim().toLowerCase()));
  const next = [...existing];
  incoming.forEach((item) => {
    const key = item.query.trim().toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      next.push(item);
    }
  });
  return next;
}

export type ExampleItem = { query: string; expected_tool?: string; note?: string };

/** LLM часто шлёт question/tool вместо query/expected_tool — приводим к схеме редактора. */
export function normalizeExampleItem(raw: Record<string, unknown>): ExampleItem | null {
  const query = String(
    raw.query ?? raw.question ?? raw.user_query ?? raw.request ?? raw.text ?? '',
  ).trim();
  if (!query) return null;
  return {
    query,
    expected_tool: String(raw.expected_tool ?? raw.tool ?? raw.tool_name ?? raw.name ?? '').trim(),
    note: String(raw.note ?? raw.comment ?? '').trim(),
  };
}

export function normalizeExampleItems(items: unknown): ExampleItem[] {
  if (!Array.isArray(items)) return [];
  return items
    .filter((it): it is Record<string, unknown> => typeof it === 'object' && it !== null && !Array.isArray(it))
    .map(normalizeExampleItem)
    .filter((x): x is ExampleItem => x !== null);
}

export function examplesFromTools(tools: Tool[]): ExampleItem[] {
  return tools
    .filter((t) => t.is_active !== false)
    .map((t) => {
      const fn = (t.config_json as { function?: { description?: string } })?.function;
      const desc = (fn?.description || t.description || '').trim();
      const firstSentence = desc.split(/[.!?\n]/)[0]?.trim() || '';
      const query = firstSentence
        ? (firstSentence.endsWith('?') ? firstSentence : `${firstSentence}?`)
        : `Вызови ${t.name}`;
      return {
        query,
        expected_tool: t.name,
        note: 'Сгенерировано из описания tool',
      };
    });
}

export function sectionKeyForFocus(sec: OntologySection, index: number): string {
  return sec.id || String(index);
}

export function focusSectionAfterPatches(next: OntologyJson, patches: OntologyPatch[]): string | null {
  const wantsExamples = patches.some(
    (p) => p.op === 'merge_examples' || (p.op === 'add_section' && (p.data as { type?: string })?.type === 'examples'),
  );
  if (wantsExamples) {
    const idx = next.sections.findIndex((s) => s.type === 'examples');
    if (idx >= 0) return sectionKeyForFocus(next.sections[idx], idx);
  }
  const wantsGlossary = patches.some(
    (p) => p.op === 'merge_glossary' || (p.op === 'add_section' && (p.data as { type?: string })?.type === 'glossary'),
  );
  if (wantsGlossary) {
    const idx = next.sections.findIndex((s) => s.type === 'glossary');
    if (idx >= 0) return sectionKeyForFocus(next.sections[idx], idx);
  }
  if (next.sections.length) {
    const i = next.sections.length - 1;
    return sectionKeyForFocus(next.sections[i], i);
  }
  return null;
}

export function countExamplesInPatches(patches: OntologyPatch[], patchIds: string[]): number {
  const idSet = new Set(patchIds);
  let n = 0;
  for (const p of patches) {
    if (!idSet.has(p.id)) continue;
    if (p.op === 'merge_examples') {
      n += normalizeExampleItems((p.data as { items?: unknown })?.items).length;
    } else if (p.op === 'add_section' && (p.data as { type?: string })?.type === 'examples') {
      n += normalizeExampleItems((p.data as { items?: unknown })?.items).length;
    }
  }
  return n;
}

export function duplicateSection(sec: OntologySection): OntologySection {
  const copy = JSON.parse(JSON.stringify(sec)) as OntologySection;
  copy.id = uid();
  copy.title = `${copy.title || sec.type} (копия)`;
  return copy;
}

export function upsertSection(ontology: OntologyJson | null, section: OntologySection): OntologyJson {
  const sections = [...(ontology?.sections || [])];
  const idx = sections.findIndex((s) => s.type === section.type && s.title === section.title);
  if (idx >= 0) sections[idx] = section;
  else sections.push(section);
  return { version: 1, sections };
}

export function appendSections(ontology: OntologyJson | null, newSections: OntologySection[]): OntologyJson {
  return { version: 1, sections: [...(ontology?.sections || []), ...newSections] };
}

export type { OntologyPatch } from '../../shared/api/types';

const patchUid = () => `n${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;

function findSection(sections: OntologySection[], sectionId?: string | null, sectionType?: string | null): OntologySection | undefined {
  if (sectionId) {
    return sections.find((s, i) => (s.id || String(i)) === sectionId);
  }
  if (sectionType) {
    return sections.find((s) => s.type === sectionType);
  }
  return undefined;
}

function mergeExampleItemsIntoSection(
  sec: Extract<OntologySection, { type: 'examples' }>,
  incoming: ExampleItem[],
): number {
  const seen = new Set(sec.items.map((i) => i.query.trim().toLowerCase()));
  let added = 0;
  for (const it of incoming) {
    const key = it.query.trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    sec.items.push({
      query: it.query,
      expected_tool: it.expected_tool || '',
      note: it.note || '',
    });
    added += 1;
  }
  return added;
}

export function applyOntologyPatches(
  ontology: OntologyJson | null,
  patches: OntologyPatch[],
  patchIds: string[],
): OntologyJson {
  const idSet = new Set(patchIds);
  const toApply = patches.filter((p) => idSet.has(p.id));
  let sections: OntologySection[] = JSON.parse(JSON.stringify(ontology?.sections || []));

  for (const patch of toApply) {
    const data = patch.data || {};
    if (patch.op === 'merge_glossary') {
      let sec = findSection(sections, patch.section_id, 'glossary') as Extract<OntologySection, { type: 'glossary' }> | undefined;
      if (!sec) {
        sec = { id: patchUid(), type: 'glossary', title: 'Глоссарий', items: [] };
        sections.push(sec);
      }
      const existing = new Set(sec.items.map((i) => i.term.trim().toLowerCase()));
      for (const it of (data.items as { term?: string; definition?: string }[]) || []) {
        const term = (it.term || '').trim();
        if (!term || existing.has(term.toLowerCase())) continue;
        existing.add(term.toLowerCase());
        sec.items.push({ term, definition: (it.definition || '').trim() });
      }
    } else if (patch.op === 'merge_examples') {
      const incoming = normalizeExampleItems((data as { items?: unknown }).items);
      let sec = findSection(sections, patch.section_id, 'examples') as Extract<OntologySection, { type: 'examples' }> | undefined;
      if (!sec) {
        sec = { id: patchUid(), type: 'examples', title: 'Примеры запросов', items: [] };
        sections.push(sec);
      }
      mergeExampleItemsIntoSection(sec, incoming);
    } else if (patch.op === 'add_section') {
      const raw = JSON.parse(JSON.stringify(data)) as OntologySection & { items?: unknown[] };
      if (!raw.type) continue;
      if (!raw.id) raw.id = patchUid();
      if (raw.type === 'examples') {
        const incoming = normalizeExampleItems(raw.items);
        let sec = findSection(sections, patch.section_id, 'examples') as Extract<OntologySection, { type: 'examples' }> | undefined;
        if (!sec) {
          sec = {
            id: raw.id,
            type: 'examples',
            title: raw.title || 'Примеры запросов',
            items: [],
          };
          sections.push(sec);
        }
        mergeExampleItemsIntoSection(sec, incoming);
      } else if (raw.type === 'glossary') {
        let sec = findSection(sections, patch.section_id, 'glossary') as Extract<OntologySection, { type: 'glossary' }> | undefined;
        if (!sec) {
          sections.push({ ...raw, items: raw.items || [] } as Extract<OntologySection, { type: 'glossary' }>);
        } else {
          const items = (raw.items as { term?: string; definition?: string }[]) || [];
          const existing = new Set(sec.items.map((i) => i.term.trim().toLowerCase()));
          for (const it of items) {
            const term = (it.term || '').trim();
            if (!term || existing.has(term.toLowerCase())) continue;
            existing.add(term.toLowerCase());
            sec.items.push({ term, definition: (it.definition || '').trim() });
          }
        }
      } else {
        sections.push(raw);
      }
    } else if (patch.op === 'append_freeform') {
      let sec = findSection(sections, patch.section_id, 'freeform') as Extract<OntologySection, { type: 'freeform' }> | undefined;
      if (!sec) {
        sec = { id: patchUid(), type: 'freeform', title: 'Заметки', text: '' };
        sections.push(sec);
      }
      const chunk = String(data.text || '').trim();
      if (chunk) sec.text = sec.text?.trim() ? `${sec.text.trim()}\n\n${chunk}` : chunk;
    }
  }

  return { version: 1, sections };
}
