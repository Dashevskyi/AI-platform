import type { OntologyJson, OntologySection } from '../../shared/api/types';

export function restoreSection(
  ontology: OntologyJson | null,
  section: OntologySection,
  matchBy: 'id' | 'title' = 'id',
): OntologyJson {
  const sections = [...(ontology?.sections || [])];
  const key = matchBy === 'id' ? section.id : undefined;
  let idx = -1;
  if (key) {
    idx = sections.findIndex((s, i) => (s.id || String(i)) === key);
  }
  if (idx < 0 && section.title) {
    idx = sections.findIndex((s) => s.type === section.type && s.title === section.title);
  }
  const copy = JSON.parse(JSON.stringify(section)) as OntologySection;
  if (idx >= 0) sections[idx] = copy;
  else sections.push(copy);
  return { version: 1, sections };
}

export function extractOntologyFromVersionPayload(payload: Record<string, unknown> | null | undefined): OntologyJson | null {
  const raw = payload?.ontology_json;
  if (!raw || typeof raw !== 'object') return null;
  const oj = raw as OntologyJson;
  if (!Array.isArray(oj.sections)) return null;
  return oj;
}
