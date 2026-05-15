import type { CSSProperties, ReactNode } from 'react';
import { ActionIcon, Box, Code, Group, Table, Text, Tooltip } from '@mantine/core';
import { CodeHighlight } from '@mantine/code-highlight';
import { notifications } from '@mantine/notifications';
import { IconCopy, IconDownload } from '@tabler/icons-react';

type MarkdownContentProps = {
  content: string;
  color?: string;
  linkColor?: string;
};

type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'blockquote'; text: string }
  | { type: 'code'; code: string; language: string | null }
  | { type: 'ul'; items: string[] }
  | { type: 'ol'; items: string[] }
  | { type: 'table'; headers: string[]; rows: string[][] };

const INLINE_TOKEN_RE =
  /(\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|~~([^~]+)~~|\*([^*]+)\*|_([^_]+)_)/g;

function parseBlocks(content: string): Block[] {
  const normalized = content.replace(/\r\n/g, '\n').trim();
  if (!normalized) return [];

  const lines = normalized.split('\n');
  const blocks: Block[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    const codeFenceMatch = trimmed.match(/^```([\w-]+)?\s*$/);
    if (codeFenceMatch) {
      const language = codeFenceMatch[1] || null;
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push({ type: 'code', code: codeLines.join('\n'), language });
      continue;
    }

    const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      blocks.push({
        type: 'heading',
        level: headingMatch[1].length,
        text: headingMatch[2],
      });
      index += 1;
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index].trim())) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ''));
        index += 1;
      }
      blocks.push({ type: 'blockquote', text: quoteLines.join('\n') });
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*+]\s+/, '').trim());
        index += 1;
      }
      blocks.push({ type: 'ul', items });
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+\.\s+/, '').trim());
        index += 1;
      }
      blocks.push({ type: 'ol', items });
      continue;
    }

    if (index + 1 < lines.length) {
      const tableHeader = parseTableRow(line);
      const tableSeparator = lines[index + 1]?.trim() || '';
      if (tableHeader && isMarkdownTableSeparator(tableSeparator)) {
        const rows: string[][] = [];
        index += 2;
        while (index < lines.length) {
          const row = parseTableRow(lines[index]);
          if (!row) break;
          rows.push(normalizeTableRow(row, tableHeader.length));
          index += 1;
        }
        blocks.push({
          type: 'table',
          headers: normalizeTableRow(tableHeader, tableHeader.length),
          rows,
        });
        continue;
      }
    }

    const delimitedTable = parseDelimitedTable(lines, index);
    if (delimitedTable) {
      blocks.push({
        type: 'table',
        headers: delimitedTable.headers,
        rows: delimitedTable.rows,
      });
      index = delimitedTable.nextIndex;
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length) {
      const current = lines[index];
      const currentTrimmed = current.trim();
      if (!currentTrimmed) break;
      if (
        currentTrimmed.startsWith('```') ||
        /^#{1,6}\s+/.test(currentTrimmed) ||
        /^>\s?/.test(currentTrimmed) ||
        /^\s*[-*+]\s+/.test(current) ||
        /^\s*\d+\.\s+/.test(current)
      ) {
        break;
      }
      paragraphLines.push(currentTrimmed);
      index += 1;
    }
    blocks.push({ type: 'paragraph', text: paragraphLines.join('\n') });
  }

  return blocks;
}

function renderInline(text: string, keyPrefix: string, linkColor?: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let tokenIndex = 0;

  const pushPlainText = (chunk: string, chunkKey: string) => {
    const parts = chunk.split('\n');
    parts.forEach((part, index) => {
      if (part) nodes.push(part);
      if (index < parts.length - 1) {
        nodes.push(<br key={`${chunkKey}-br-${index}`} />);
      }
    });
  };

  INLINE_TOKEN_RE.lastIndex = 0;
  while ((match = INLINE_TOKEN_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      pushPlainText(text.slice(lastIndex, match.index), `${keyPrefix}-plain-${tokenIndex}`);
    }

    const tokenKey = `${keyPrefix}-token-${tokenIndex}`;
    if (match[2] && match[3]) {
      nodes.push(
        <a
          key={tokenKey}
          href={match[3]}
          target="_blank"
          rel="noreferrer"
          style={{ color: linkColor, textDecoration: 'underline' }}
        >
          {match[2]}
        </a>,
      );
    } else if (match[4]) {
      nodes.push(
        <Code key={tokenKey} fw={500}>
          {match[4]}
        </Code>,
      );
    } else if (match[5] || match[6]) {
      nodes.push(<strong key={tokenKey}>{match[5] || match[6]}</strong>);
    } else if (match[7]) {
      nodes.push(<del key={tokenKey}>{match[7]}</del>);
    } else if (match[8] || match[9]) {
      nodes.push(<em key={tokenKey}>{match[8] || match[9]}</em>);
    }

    lastIndex = INLINE_TOKEN_RE.lastIndex;
    tokenIndex += 1;
  }

  if (lastIndex < text.length) {
    pushPlainText(text.slice(lastIndex), `${keyPrefix}-plain-tail`);
  }

  return nodes;
}

function parseTableRow(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return null;
  const rawParts = trimmed.split("|");
  const parts = rawParts
    .slice(trimmed.startsWith("|") ? 1 : 0, trimmed.endsWith("|") ? rawParts.length - 1 : rawParts.length)
    .map((part) => part.trim());
  if (parts.length < 2 || parts.every((part) => part === "")) return null;
  return parts;
}

function parseDelimitedRow(line: string, delimiter: ";" | "\t"): string[] | null {
  if (!line.includes(delimiter)) return null;
  const cells = line.split(delimiter).map((part) => part.trim());
  if (cells.length < 2 || cells.every((cell) => cell === "")) return null;
  return cells;
}

function parseDelimitedTable(
  lines: string[],
  startIndex: number,
): { headers: string[]; rows: string[][]; nextIndex: number } | null {
  const delimiters: Array<";" | "\t"> = [";", "\t"];

  for (const delimiter of delimiters) {
    const header = parseDelimitedRow(lines[startIndex].trim(), delimiter);
    if (!header) continue;

    const rows: string[][] = [];
    let index = startIndex + 1;
    while (index < lines.length) {
      const trimmed = lines[index].trim();
      if (!trimmed) break;
      const row = parseDelimitedRow(trimmed, delimiter);
      if (!row || row.length !== header.length) break;
      rows.push(row);
      index += 1;
    }

    if (rows.length >= 1) {
      return {
        headers: header,
        rows,
        nextIndex: index,
      };
    }
  }

  return null;
}

function isMarkdownTableSeparator(line: string): boolean {
  const cells = parseTableRow(line);
  if (!cells || cells.length < 2) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function normalizeTableRow(row: string[], targetLength: number): string[] {
  if (row.length === targetLength) return row;
  if (row.length > targetLength) return row.slice(0, targetLength);
  return [...row, ...Array.from({ length: targetLength - row.length }, () => "")];
}

function escapeCsvCell(value: string): string {
  const normalized = value.replace(/\r?\n/g, " ").trim();
  if (/[",;]/.test(normalized)) {
    return `"${normalized.replace(/"/g, '""')}"`;
  }
  return normalized;
}

function tableToCsv(headers: string[], rows: string[][]): string {
  const lines = [
    headers.map(escapeCsvCell).join(";"),
    ...rows.map((row) => row.map(escapeCsvCell).join(";")),
  ];
  return lines.join("\n");
}

function downloadCsvFile(headers: string[], rows: string[][]) {
  const csv = tableToCsv(headers, rows);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  link.href = url;
  link.download = `chat-table-${timestamp}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function getHeadingSize(level: number): 'xl' | 'lg' | 'md' | 'sm' {
  if (level === 1) return 'xl';
  if (level === 2) return 'lg';
  if (level === 3) return 'md';
  return 'sm';
}

export function MarkdownContent({ content, color, linkColor }: MarkdownContentProps) {
  const blocks = parseBlocks(content);
  const resolvedLinkColor = linkColor || color || 'var(--mantine-color-blue-6)';
  const blockStyle: CSSProperties = {
    wordBreak: 'break-word',
  };
  const isInverted = color === 'white' || color?.includes('255');
  const codeBlockBackground = isInverted
    ? 'rgba(255, 255, 255, 0.16)'
    : 'var(--mantine-color-gray-0)';
  const codeBlockBorder = isInverted
    ? '1px solid rgba(255, 255, 255, 0.2)'
    : '1px solid var(--mantine-color-gray-3)';
  const handleCopyCsv = async (headers: string[], rows: string[][]) => {
    try {
      await navigator.clipboard.writeText(tableToCsv(headers, rows));
      notifications.show({
        color: 'green',
        message: 'CSV скопирован в буфер обмена',
      });
    } catch {
      notifications.show({
        color: 'red',
        message: 'Не удалось скопировать CSV',
      });
    }
  };

  return (
    <Box
      style={{
        ...blockStyle,
        fontSize: 'var(--mantine-font-size-sm)',
        lineHeight: 1.55,
      }}
    >
      {blocks.map((block, index) => {
        const key = `block-${index}`;

        if (block.type === 'heading') {
          return (
            <Text key={key} fw={700} size={getHeadingSize(block.level)} c={color} mt={index === 0 ? 0 : 'xs'}>
              {renderInline(block.text, key, resolvedLinkColor)}
            </Text>
          );
        }

        if (block.type === 'blockquote') {
          return (
            <Box
              key={key}
              mt={index === 0 ? 0 : 'xs'}
              pl="sm"
              style={{
                borderLeft: '3px solid var(--mantine-color-gray-4)',
                opacity: 0.9,
              }}
            >
              <Text size="sm" c={color}>
                {renderInline(block.text, key, resolvedLinkColor)}
              </Text>
            </Box>
          );
        }

        if (block.type === 'code') {
          return (
            <Box
              key={key}
              mt={index === 0 ? 0 : 'xs'}
            >
              <CodeHighlight
                code={block.code}
                language={block.language || 'txt'}
                radius="md"
                copyLabel="Скопировать код"
                copiedLabel="Скопировано"
                styles={{
                  codeHighlight: {
                    overflow: 'hidden',
                    background: codeBlockBackground,
                    border: codeBlockBorder,
                  },
                  pre: {
                    background: codeBlockBackground,
                  },
                  controls: {
                    background: 'transparent',
                  },
                  code: {
                    fontSize: '13px',
                    lineHeight: 1.5,
                  },
                }}
              />
            </Box>
          );
        }

        if (block.type === 'ul' || block.type === 'ol') {
          const ListTag = block.type === 'ul' ? 'ul' : 'ol';
          return (
            <Box
              key={key}
              component={ListTag}
              mt={index === 0 ? 0 : 'xs'}
              pl="lg"
              style={{ marginBottom: 0 }}
            >
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-item-${itemIndex}`}>
                  <Text size="sm" c={color} component="span">
                    {renderInline(item, `${key}-item-${itemIndex}`, resolvedLinkColor)}
                  </Text>
                </li>
              ))}
            </Box>
          );
        }

        if (block.type === 'table') {
          return (
            <Box
              key={key}
              mt={index === 0 ? 0 : 'xs'}
              style={{ overflowX: 'auto' }}
            >
              <Group justify="flex-end" mb={6}>
                <Tooltip label="Скачать CSV">
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    size="sm"
                    aria-label="Скачать таблицу как CSV"
                    onClick={() => downloadCsvFile(block.headers, block.rows)}
                  >
                    <IconDownload size={14} />
                  </ActionIcon>
                </Tooltip>
                <Tooltip label="Скопировать как CSV">
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    size="sm"
                    aria-label="Скопировать таблицу как CSV"
                    onClick={() => void handleCopyCsv(block.headers, block.rows)}
                  >
                    <IconCopy size={14} />
                  </ActionIcon>
                </Tooltip>
              </Group>
              <Table
                striped
                highlightOnHover
                withTableBorder
                withColumnBorders
                horizontalSpacing="sm"
                verticalSpacing="xs"
                style={{ minWidth: '100%' }}
              >
                <Table.Thead>
                  <Table.Tr>
                    {block.headers.map((header, headerIndex) => (
                      <Table.Th key={`${key}-head-${headerIndex}`}>
                        <Text size="sm" fw={600} c={color}>
                          {renderInline(header, `${key}-head-${headerIndex}`, resolvedLinkColor)}
                        </Text>
                      </Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {block.rows.map((row, rowIndex) => (
                    <Table.Tr key={`${key}-row-${rowIndex}`}>
                      {row.map((cell, cellIndex) => (
                        <Table.Td key={`${key}-row-${rowIndex}-cell-${cellIndex}`}>
                          <Text size="sm" c={color}>
                            {renderInline(cell, `${key}-row-${rowIndex}-cell-${cellIndex}`, resolvedLinkColor)}
                          </Text>
                        </Table.Td>
                      ))}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Box>
          );
        }

        return (
          <Text key={key} size="sm" c={color} mt={index === 0 ? 0 : 'xs'}>
            {renderInline(block.text, key, resolvedLinkColor)}
          </Text>
        );
      })}
    </Box>
  );
}
