import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Badge, Box, Button, Card, Group, Select, Stack, Text } from '@mantine/core';
import { IconDeviceFloppy } from '@tabler/icons-react';
import type { TablerIcon } from '@tabler/icons-react';
import { useRegisterSecondaryNav } from '../../../shared/ui/SecondaryNavContext';

export type SettingsNavItem = {
  id: string;
  label: string;
  description?: string;
  icon?: TablerIcon;
};

type SettingsSectionNavProps = {
  items: SettingsNavItem[];
  children: ReactNode;
  activeSection?: string | null;
  onSectionChange?: (id: string) => void;
  sectionDirty?: (id: string) => boolean;
};

export function SettingsSectionNav({
  items,
  children,
  activeSection,
  onSectionChange,
  sectionDirty,
}: SettingsSectionNavProps) {
  const [activeId, setActiveId] = useState(activeSection || items[0]?.id || '');

  useEffect(() => {
    if (activeSection) setActiveId(activeSection);
  }, [activeSection]);

  const scrollTo = useCallback((id: string) => {
    const el = document.getElementById(`shell-section-${id}`);
    if (!el) return;
    setActiveId(id);
    onSectionChange?.(id);
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [onSectionChange]);

  useEffect(() => {
    if (activeSection && document.getElementById(`shell-section-${activeSection}`)) {
      document.getElementById(`shell-section-${activeSection}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — initial deep-link only

  const sidebarNav = useMemo(
    () => ({
      title: 'Настройки оболочки',
      items: items.map((item) => {
        const Icon = item.icon;
        return {
          id: item.id,
          label: item.label,
          description: item.description,
          icon: Icon ? <Icon size={18} stroke={1.5} /> : null,
          active: activeId === item.id,
          dirty: sectionDirty?.(item.id),
          onClick: () => scrollTo(item.id),
        };
      }),
    }),
    [items, activeId, sectionDirty, scrollTo],
  );

  useRegisterSecondaryNav(sidebarNav);

  return (
    <Stack gap="md">
      <Box hiddenFrom="md">
        <Select
          label="Раздел"
          value={activeId}
          onChange={(v) => v && scrollTo(v)}
          data={items.map((item) => ({ value: item.id, label: item.label }))}
          allowDeselect={false}
        />
      </Box>
      {children}
    </Stack>
  );
}

type SettingsSectionCardProps = {
  id: string;
  title: string;
  description?: string;
  icon?: TablerIcon;
  children: ReactNode;
  dirty?: boolean;
  saving?: boolean;
  onSave?: () => void;
};

export function SettingsSectionCard({
  id,
  title,
  description,
  icon: Icon,
  children,
  dirty,
  saving,
  onSave,
}: SettingsSectionCardProps) {
  return (
    <Card
      id={`shell-section-${id}`}
      withBorder
      padding="lg"
      radius="md"
      style={{ scrollMarginTop: 'calc(var(--app-shell-header-height, 60px) + 80px)' }}
    >
      <Group justify="space-between" align="flex-start" mb={description ? 'xs' : 'md'} wrap="nowrap">
        <Group gap="sm" align="flex-start" style={{ flex: 1 }}>
          {Icon && <Icon size={20} stroke={1.5} style={{ opacity: 0.7, marginTop: 2 }} />}
          <div>
            <Group gap="xs">
              <Text fw={600}>{title}</Text>
              {dirty && <Badge size="xs" color="yellow" variant="light">есть изменения</Badge>}
            </Group>
            {description && (
              <Text size="sm" c="dimmed" mt={2}>
                {description}
              </Text>
            )}
          </div>
        </Group>
        {onSave && (
          <Button
            size="xs"
            variant={dirty ? 'filled' : 'light'}
            leftSection={<IconDeviceFloppy size={14} />}
            loading={saving}
            disabled={!dirty}
            onClick={onSave}
          >
            Сохранить
          </Button>
        )}
      </Group>
      {children}
    </Card>
  );
}
