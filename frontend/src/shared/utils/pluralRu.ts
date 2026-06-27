/** Russian plural: 1 X, 2–4 X, 5+ X (with 11–14 → many). */
export function pluralRu(count: number, one: string, few: string, many: string): string {
  const abs = Math.abs(count) % 100;
  const mod10 = abs % 10;
  if (abs > 10 && abs < 20) return many;
  if (mod10 > 1 && mod10 < 5) return few;
  if (mod10 === 1) return one;
  return many;
}

export function formatCountRu(
  count: number,
  one: string,
  few: string,
  many: string,
): string {
  return `${count} ${pluralRu(count, one, few, many)}`;
}
