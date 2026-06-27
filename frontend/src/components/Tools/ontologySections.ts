import type { OntologySection } from '../../shared/api/types';

export const ONTOLOGY_SECTION_TYPE_ORDER = [
  'glossary',
  'entities',
  'relations',
  'logic',
  'examples',
  'freeform',
] as const;

export function sortOntologySectionsByType(sections: OntologySection[]): OntologySection[] {
  const rank = (type: string) => {
    const i = ONTOLOGY_SECTION_TYPE_ORDER.indexOf(type as typeof ONTOLOGY_SECTION_TYPE_ORDER[number]);
    return i >= 0 ? i : ONTOLOGY_SECTION_TYPE_ORDER.length;
  };
  return [...sections].sort((a, b) => rank(a.type) - rank(b.type));
}

export function moveOntologySection(sections: OntologySection[], from: number, to: number): OntologySection[] {
  if (from === to || from < 0 || to < 0 || from >= sections.length || to >= sections.length) {
    return sections;
  }
  const next = [...sections];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}
