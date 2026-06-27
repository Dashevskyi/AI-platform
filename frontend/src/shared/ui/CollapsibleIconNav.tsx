import { Fragment, useState, type ReactNode } from 'react';
import { IconGripVertical } from '@tabler/icons-react';
import { ActionIcon, Badge, Box, NavLink, Stack, Text, Tooltip } from '@mantine/core';

export type CollapsibleNavItem = {
  id: string;
  label: string;
  description?: string;
  /** Icon node — Tabler icon or emoji */
  icon: ReactNode;
  active?: boolean;
  dirty?: boolean;
  onClick: () => void;
  rightSection?: ReactNode;
  /** Position in ordered list — for drag-and-drop reorder */
  orderIndex?: number;
  /** When set, render a type group header above this item (expanded rail only) */
  typeHeader?: string;
};

type CollapsibleIconNavProps = {
  items: CollapsibleNavItem[];
  title?: string;
  footer?: ReactNode;
  /** inline = sticky in content; navbar = lives in AppShell sidebar */
  variant?: 'inline' | 'navbar';
  onReorder?: (fromIndex: number, toIndex: number) => void;
};

const COLLAPSED_W = 52;
const EXPANDED_W = 252;
export const SIDEBAR_COLLAPSED_W = 68;
export const SIDEBAR_EXPANDED_W = 250;

export function CollapsibleIconNav({
  items,
  title,
  footer,
  variant = 'inline',
  onReorder,
}: CollapsibleIconNavProps) {
  const [expanded, setExpanded] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);

  const finishDrag = () => {
    setDragIndex(null);
    setDropIndex(null);
  };

  return (
    <Box
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
      style={{
        width: expanded ? EXPANDED_W : COLLAPSED_W,
        transition: 'width 180ms ease',
        flexShrink: 0,
        overflow: 'hidden',
        alignSelf: 'flex-start',
        ...(variant === 'inline'
          ? {
              position: 'sticky',
              top: 'calc(var(--app-shell-header-height, 60px) + 72px)',
              zIndex: 2,
            }
          : {
              borderRadius: 'var(--mantine-radius-md)',
              border: '1px solid var(--mantine-color-default-border)',
              background: 'var(--mantine-color-body)',
              padding: 4,
            }),
      }}
    >
      {title && expanded && (
        <Text size="xs" c="dimmed" tt="uppercase" fw={600} px={8} py={4} truncate>
          {title}
        </Text>
      )}
      <Stack gap={2}>
        {items.map((item, i) => {
          const idx = item.orderIndex ?? i;
          const isDropTarget = expanded && dragIndex !== null && dropIndex === idx && dragIndex !== idx;
          return (
            <Fragment key={item.id}>
              {item.typeHeader && expanded && (
                <Text
                  size="xs"
                  c="dimmed"
                  tt="uppercase"
                  fw={600}
                  px={8}
                  pt={i > 0 ? 6 : 2}
                  pb={2}
                >
                  {item.typeHeader}
                </Text>
              )}
              <Tooltip
                label={
                  <div>
                    <Text size="xs" fw={600}>{item.label}</Text>
                    {item.description && (
                      <Text size="xs" c="dimmed">{item.description}</Text>
                    )}
                  </div>
                }
                position="right"
                withArrow
                disabled={expanded}
                multiline
                w={220}
              >
                <Box
                  onDragOver={(e) => {
                    if (!expanded || !onReorder || dragIndex === null) return;
                    e.preventDefault();
                    setDropIndex(idx);
                  }}
                  onDragLeave={() => {
                    if (dropIndex === idx) setDropIndex(null);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragIndex !== null && onReorder && dragIndex !== idx) {
                      onReorder(dragIndex, idx);
                    }
                    finishDrag();
                  }}
                  style={{
                    borderRadius: 'var(--mantine-radius-sm)',
                    outline: isDropTarget ? '1px dashed var(--mantine-color-blue-5)' : undefined,
                  }}
                >
                  <NavLink
                    label={expanded ? item.label : undefined}
                    description={expanded ? item.description : undefined}
                    leftSection={(
                      <Box style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}>
                        {expanded && onReorder && (
                          <ActionIcon
                            size="xs"
                            variant="subtle"
                            color="gray"
                            draggable
                            style={{ cursor: 'grab' }}
                            onDragStart={(e) => {
                              e.stopPropagation();
                              setDragIndex(idx);
                            }}
                            onDragEnd={finishDrag}
                            aria-label="Перетащить секцию"
                          >
                            <IconGripVertical size={12} />
                          </ActionIcon>
                        )}
                        <Box style={{ width: expanded && onReorder ? 22 : 28, display: 'flex', justifyContent: 'center' }}>
                          {item.icon}
                        </Box>
                      </Box>
                    )}
                    rightSection={
                      expanded
                        ? (item.dirty
                          ? <Badge size="xs" color="yellow" variant="filled">•</Badge>
                          : item.rightSection)
                        : item.dirty
                          ? <Box w={6} h={6} style={{ borderRadius: '50%', background: 'var(--mantine-color-yellow-6)' }} />
                          : null
                    }
                    active={item.active}
                    onClick={item.onClick}
                    variant="subtle"
                    styles={{
                      root: { paddingLeft: expanded ? undefined : 8, paddingRight: expanded ? undefined : 8 },
                      label: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
                      description: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
                    }}
                  />
                </Box>
              </Tooltip>
            </Fragment>
          );
        })}
      </Stack>
      {footer && expanded && (
        <Box px={4} pt={6} pb={2}>
          {footer}
        </Box>
      )}
    </Box>
  );
}

/** Side-by-side layout: collapsible icon rail + content. */
export function LocalCollapsibleSectionNav({
  title,
  items,
  footer,
  children,
  onReorder,
}: {
  title: string;
  items: CollapsibleNavItem[];
  footer?: ReactNode;
  children: ReactNode;
  onReorder?: (fromIndex: number, toIndex: number) => void;
}) {
  return (
    <Box style={{ display: 'flex', gap: 'var(--mantine-spacing-md)', alignItems: 'flex-start', flexWrap: 'nowrap' }}>
      <Box visibleFrom="sm">
        <CollapsibleIconNav title={title} items={items} footer={footer} variant="inline" onReorder={onReorder} />
      </Box>
      <Box style={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Box>
  );
}
