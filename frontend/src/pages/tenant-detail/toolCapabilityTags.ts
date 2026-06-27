export type ToolPreset = {
  label: string;
  description: string;
  tags: string[];
  all?: boolean;
  none?: boolean;
};

export const TOOL_PERMISSION_PRESETS: ToolPreset[] = [
  {
    label: 'Все tools',
    description: 'Полный доступ без ограничений.',
    tags: [],
    all: true,
  },
  {
    label: 'Без tools',
    description: 'Полный запрет использования tools.',
    tags: [],
    none: true,
  },
  {
    label: 'Сеть',
    description: 'Проверка доступности и диагностика сети.',
    tags: ['network', 'diagnostics'],
  },
  {
    label: 'Поиск данных',
    description: 'Поиск по БД и API-источникам tenant-а.',
    tags: ['data_search', 'db_search', 'api_search', 'records'],
  },
  {
    label: 'Биллинг',
    description: 'Платежи и начисления.',
    tags: ['billing', 'payments'],
  },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function readToolCapabilityTags(config: Record<string, unknown> | null | undefined): string[] {
  if (!isRecord(config)) return [];
  const runtime = isRecord(config.x_backend_config) ? config.x_backend_config : {};
  const tags = Array.isArray(runtime.capability_tags) ? runtime.capability_tags : [];
  return tags.map((tag) => String(tag).trim()).filter(Boolean);
}

export function applyToolCapabilityTags(
  runtime: Record<string, unknown>,
  tags: string[],
): Record<string, unknown> {
  const next = { ...runtime };
  const normalized = Array.from(new Set(tags.map((tag) => tag.trim()).filter(Boolean)));
  if (normalized.length > 0) {
    next.capability_tags = normalized;
  } else if ('capability_tags' in next) {
    delete next.capability_tags;
  }
  return next;
}
