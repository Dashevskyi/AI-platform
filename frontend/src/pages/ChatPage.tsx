import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Box,
  Group,
  Stack,
  Text,
  TextInput,
  Textarea,
  Button,
  ScrollArea,
  NavLink,
  ActionIcon,
  Loader,
  Center,
  Paper,
  Tooltip,
  Badge,
  Divider,
  Modal,
} from '@mantine/core';
import {
  IconSend,
  IconPlus,
  IconMessageCircle,
  IconArrowLeft,
  IconInfoCircle,
  IconPencil,
  IconPaperclip,
  IconFile,
  IconX,
  IconUpload,
} from '@tabler/icons-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { chatsApi } from '../shared/api/endpoints';
import type { Message, AttachmentBrief } from '../shared/api/types';

// If pasted text is longer than this, offer to attach as file instead
const PASTE_AS_FILE_THRESHOLD = 2000;

const FILE_TYPE_COLORS: Record<string, string> = {
  pdf: 'red',
  image: 'grape',
  audio: 'pink',
  docx: 'blue',
  xlsx: 'green',
  csv: 'teal',
  json: 'orange',
  html: 'cyan',
  xml: 'indigo',
  text: 'gray',
};

const STATUS_MAP: Record<string, { color: string; label: string }> = {
  pending: { color: 'yellow', label: 'Ожидание' },
  processing: { color: 'blue', label: 'Обработка' },
  done: { color: 'green', label: 'Готово' },
  error: { color: 'red', label: 'Ошибка' },
};

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

export function ChatPage() {
  const { id, chatId } = useParams<{ id: string; chatId?: string }>();
  const tenantId = id!;
  const activeChatId = chatId || null;
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [messageText, setMessageText] = useState('');
  const [showDebug, setShowDebug] = useState(false);
  const [editChatId, setEditChatId] = useState<string | null>(null);
  const [editChatTitle, setEditChatTitle] = useState('');
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load chat list
  const { data: chatsData, isLoading: chatsLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'list'],
    queryFn: () => chatsApi.list(tenantId, 1, 100),
  });

  // Load messages for active chat
  const { data: messagesData, isLoading: messagesLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', activeChatId, 'messages'],
    queryFn: () => chatsApi.listMessages(tenantId, activeChatId!, 1, 200),
    enabled: !!activeChatId,
  });

  // Load attachments for active chat
  const { data: attachments } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', activeChatId, 'attachments'],
    queryFn: () => chatsApi.listAttachments(tenantId, activeChatId!),
    enabled: !!activeChatId,
  });

  const messages = messagesData?.items || [];

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (viewportRef.current) {
      viewportRef.current.scrollTo({
        top: viewportRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, [messages.length]);

  // Create chat
  const createChatMutation = useMutation({
    mutationFn: () => chatsApi.create(tenantId, {}),
    onSuccess: (chat) => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', 'list'] });
      navigate(`/tenants/${tenantId}/chat/${chat.id}`);
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось создать чат', color: 'red' });
    },
  });

  // Rename chat
  const renameMutation = useMutation({
    mutationFn: () => chatsApi.update(tenantId, editChatId!, { title: editChatTitle }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', 'list'] });
      setEditChatId(null);
      notifications.show({ title: 'Готово', message: 'Название чата обновлено', color: 'green' });
    },
    onError: () => {
      notifications.show({ title: 'Ошибка', message: 'Не удалось переименовать чат', color: 'red' });
    },
  });

  // Send message (with or without files)
  const sendMutation = useMutation({
    mutationFn: ({ text, files }: { text: string; files: File[] }) => {
      const idempotencyKey = `${activeChatId}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      if (files.length > 0) {
        return chatsApi.sendMessageWithFiles(tenantId, activeChatId!, text, files, idempotencyKey);
      }
      return chatsApi.sendMessage(tenantId, activeChatId!, { content: text, idempotency_key: idempotencyKey });
    },
    onMutate: async ({ text }: { text: string; files: File[] }) => {
      const qk = ['tenants', tenantId, 'chats', activeChatId, 'messages'];
      await queryClient.cancelQueries({ queryKey: qk });
      const previous = queryClient.getQueryData(qk);
      queryClient.setQueryData(qk, (old: any) => {
        if (!old) return old;
        const optimisticMsg = {
          id: `temp-${Date.now()}`,
          tenant_id: tenantId,
          chat_id: activeChatId,
          role: 'user',
          content: text,
          prompt_tokens: null,
          completion_tokens: null,
          total_tokens: null,
          latency_ms: null,
          status: 'sent',
          created_at: new Date().toISOString(),
        };
        return { ...old, items: [...old.items, optimisticMsg], total_count: old.total_count + 1 };
      });
      return { previous };
    },
    onSuccess: () => {
      setMessageText('');
      setAttachedFiles([]);
      queryClient.invalidateQueries({
        queryKey: ['tenants', tenantId, 'chats', activeChatId, 'messages'],
      });
      queryClient.invalidateQueries({
        queryKey: ['tenants', tenantId, 'chats', activeChatId, 'attachments'],
      });
      queryClient.invalidateQueries({
        queryKey: ['tenants', tenantId, 'chats', 'list'],
      });
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(
          ['tenants', tenantId, 'chats', activeChatId, 'messages'],
          context.previous,
        );
      }
      notifications.show({
        title: 'Ошибка',
        message: 'Не удалось отправить сообщение',
        color: 'red',
      });
    },
  });

  const handleSend = useCallback(() => {
    if (!messageText.trim() || !activeChatId || sendMutation.isPending) return;
    const text = messageText.trim();
    const files = [...attachedFiles];
    setMessageText('');
    setAttachedFiles([]);
    sendMutation.mutate({ text, files });
  }, [messageText, activeChatId, sendMutation, attachedFiles]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  // File handling
  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const fileArray = Array.from(newFiles);
    setAttachedFiles((prev) => [...prev, ...fileArray]);
  }, []);

  const removeFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // Drag & drop handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (e.dataTransfer.files?.length) {
      addFiles(e.dataTransfer.files);
    }
  }, [addFiles]);

  // Paste handler: images from clipboard + large text as file
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    // Check for pasted files (images from clipboard)
    const pastedFiles: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) pastedFiles.push(file);
      }
    }
    if (pastedFiles.length > 0) {
      e.preventDefault();
      addFiles(pastedFiles);
      return;
    }

    // Check for large text paste
    const pastedText = e.clipboardData?.getData('text/plain') || '';
    if (pastedText.length > PASTE_AS_FILE_THRESHOLD) {
      e.preventDefault();
      const blob = new Blob([pastedText], { type: 'text/plain' });
      const file = new File([blob], `pasted_text_${Date.now()}.txt`, { type: 'text/plain' });
      addFiles([file]);
      notifications.show({
        title: 'Текст вставлен как файл',
        message: `Большой текст (${pastedText.length} символов) прикреплён как файл для анализа`,
        color: 'blue',
      });
    }
  }, [addFiles]);

  // Get last assistant message for debug info
  const lastAssistantMessage = [...messages].reverse().find((m) => m.role === 'assistant');

  return (
    <Box style={{ display: 'flex', height: 'calc(100vh - 120px)', gap: 0 }}>
      {/* Left sidebar - Chat list */}
      <Box
        style={{
          width: 280,
          borderRight: '1px solid var(--mantine-color-default-border)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Group p="sm" justify="space-between">
          <Group gap="xs">
            <ActionIcon variant="subtle" onClick={() => navigate(`/tenants/${tenantId}`)}>
              <IconArrowLeft size={18} />
            </ActionIcon>
            <Text fw={500} size="sm">
              Чаты
            </Text>
          </Group>
          <Tooltip label="Новый чат">
            <ActionIcon
              variant="light"
              onClick={() => createChatMutation.mutate()}
              loading={createChatMutation.isPending}
            >
              <IconPlus size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
        <Divider />
        <ScrollArea style={{ flex: 1 }} p="xs">
          {chatsLoading ? (
            <Center py="md">
              <Loader size="sm" />
            </Center>
          ) : !chatsData?.items.length ? (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Чатов пока нет
            </Text>
          ) : (
            chatsData.items.map((chat) => (
              <NavLink
                key={chat.id}
                label={chat.title || chat.description || 'Новый чат'}
                description={chat.description && chat.title ? chat.description : undefined}
                leftSection={<IconMessageCircle size={16} />}
                rightSection={
                  <Tooltip label="Переименовать">
                    <ActionIcon
                      variant="subtle"
                      size="xs"
                      onClick={(e) => {
                        e.stopPropagation();
                        e.preventDefault();
                        setEditChatId(chat.id);
                        setEditChatTitle(chat.title || chat.description || '');
                      }}
                    >
                      <IconPencil size={14} />
                    </ActionIcon>
                  </Tooltip>
                }
                active={chat.id === activeChatId}
                onClick={() => navigate(`/tenants/${tenantId}/chat/${chat.id}`)}
                variant="filled"
                mb={2}
              />
            ))
          )}
        </ScrollArea>
      </Box>

      {/* Main chat area */}
      <Box
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, position: 'relative' }}
        onDragOver={activeChatId ? handleDragOver : undefined}
        onDragLeave={activeChatId ? handleDragLeave : undefined}
        onDrop={activeChatId ? handleDrop : undefined}
      >
        {/* Drag overlay */}
        {isDragging && (
          <Box
            style={{
              position: 'absolute',
              inset: 0,
              zIndex: 100,
              background: 'rgba(34, 139, 230, 0.08)',
              border: '2px dashed var(--mantine-color-blue-5)',
              borderRadius: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              pointerEvents: 'none',
            }}
          >
            <Stack align="center" gap="xs">
              <IconUpload size={48} color="var(--mantine-color-blue-5)" />
              <Text size="lg" fw={500} c="blue">
                Перетащите файлы сюда
              </Text>
              <Text size="sm" c="dimmed">
                PDF, DOCX, XLSX, TXT, CSV, JSON, изображения, аудио
              </Text>
            </Stack>
          </Box>
        )}

        {!activeChatId ? (
          <Center style={{ flex: 1 }}>
            <Stack align="center" gap="md">
              <IconMessageCircle size={48} color="var(--mantine-color-dimmed)" />
              <Text c="dimmed">Выберите чат или создайте новый</Text>
              <Button
                variant="light"
                leftSection={<IconPlus size={16} />}
                onClick={() => createChatMutation.mutate()}
                loading={createChatMutation.isPending}
              >
                Новый чат
              </Button>
            </Stack>
          </Center>
        ) : (
          <>
            {/* Chat header */}
            <Group
              p="sm"
              justify="space-between"
              style={{ borderBottom: '1px solid var(--mantine-color-default-border)' }}
            >
              <Group gap="xs">
                <Text fw={500}>
                  {chatsData?.items.find((c) => c.id === activeChatId)?.title ||
                    chatsData?.items.find((c) => c.id === activeChatId)?.description ||
                    'Новый чат'}
                </Text>
                {attachments && attachments.length > 0 && (
                  <Tooltip label={`${attachments.length} файл(ов) приложено`}>
                    <Badge variant="light" size="sm" leftSection={<IconPaperclip size={10} />}>
                      {attachments.length}
                    </Badge>
                  </Tooltip>
                )}
              </Group>
              <Tooltip label="Отладочная информация">
                <ActionIcon
                  variant={showDebug ? 'filled' : 'subtle'}
                  onClick={() => setShowDebug(!showDebug)}
                >
                  <IconInfoCircle size={18} />
                </ActionIcon>
              </Tooltip>
            </Group>

            {/* Messages */}
            <ScrollArea style={{ flex: 1 }} viewportRef={viewportRef} p="md">
              {messagesLoading ? (
                <Center py="xl">
                  <Loader />
                </Center>
              ) : !messages.length ? (
                <Center py="xl">
                  <Stack align="center" gap="sm">
                    <Text c="dimmed">Сообщений пока нет. Начните разговор!</Text>
                    <Text size="xs" c="dimmed">
                      Вы можете прикрепить файлы через кнопку или перетащив их в окно чата
                    </Text>
                  </Stack>
                </Center>
              ) : (
                <Stack gap="md">
                  {messages.map((msg) => (
                    <MessageBubble key={msg.id} message={msg} />
                  ))}
                  {sendMutation.isPending && (
                    <Box style={{ display: 'flex', justifyContent: 'flex-start' }}>
                      <Paper
                        p="sm"
                        radius="md"
                        bg="var(--mantine-color-default-hover)"
                        maw="70%"
                        shadow="none"
                        style={{ border: 'none' }}
                      >
                        <Group gap="xs">
                          <Loader size="xs" />
                          <Text size="sm" c="dimmed">
                            {attachedFiles.length > 0 || sendMutation.variables?.files?.length
                              ? 'Обрабатываю файлы и думаю...'
                              : 'Думаю...'}
                          </Text>
                        </Group>
                      </Paper>
                    </Box>
                  )}
                  <div ref={messagesEndRef} />
                </Stack>
              )}
            </ScrollArea>

            {/* Attached files preview */}
            {attachedFiles.length > 0 && (
              <Box
                px="md"
                pt="xs"
                style={{ borderTop: '1px solid var(--mantine-color-default-border)' }}
              >
                <Group gap="xs" wrap="wrap">
                  {attachedFiles.map((file, idx) => (
                    <Paper key={idx} withBorder p="4px 8px" radius="sm">
                      <Group gap={4}>
                        <IconFile size={14} />
                        <Text size="xs" maw={150} truncate="end">
                          {file.name}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {formatFileSize(file.size)}
                        </Text>
                        <ActionIcon
                          size="xs"
                          variant="subtle"
                          color="red"
                          onClick={() => removeFile(idx)}
                        >
                          <IconX size={12} />
                        </ActionIcon>
                      </Group>
                    </Paper>
                  ))}
                </Group>
              </Box>
            )}

            {/* Input */}
            <Box
              p="md"
              style={{ borderTop: attachedFiles.length > 0 ? undefined : '1px solid var(--mantine-color-default-border)' }}
            >
              <Group gap="sm">
                <input
                  type="file"
                  multiple
                  ref={fileInputRef}
                  style={{ display: 'none' }}
                  accept=".pdf,.txt,.md,.csv,.log,.json,.xml,.html,.png,.jpg,.jpeg,.gif,.webp,.tiff,.bmp,.docx,.xlsx,.xls,.mp3,.wav,.ogg,.flac,.m4a,.aac,.webm"
                  onChange={(e) => {
                    if (e.target.files?.length) {
                      addFiles(e.target.files);
                      e.target.value = '';
                    }
                  }}
                />
                <Tooltip label="Прикрепить файлы">
                  <ActionIcon
                    variant="light"
                    size="lg"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={sendMutation.isPending}
                    style={{ alignSelf: 'flex-end', marginBottom: 4 }}
                  >
                    <IconPaperclip size={18} />
                  </ActionIcon>
                </Tooltip>
                <Textarea
                  placeholder={
                    attachedFiles.length > 0
                      ? `Сообщение (${attachedFiles.length} файл(ов) приложено)...`
                      : 'Введите сообщение... (Shift+Enter — новая строка)'
                  }
                  value={messageText}
                  onChange={(e) => setMessageText(e.currentTarget.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  style={{ flex: 1 }}
                  disabled={sendMutation.isPending}
                  autosize
                  minRows={1}
                  maxRows={8}
                />
                <Button
                  leftSection={<IconSend size={16} />}
                  onClick={handleSend}
                  loading={sendMutation.isPending}
                  disabled={!messageText.trim()}
                  style={{ alignSelf: 'flex-end', marginBottom: 4 }}
                >
                  Отправить
                </Button>
              </Group>
            </Box>
          </>
        )}
      </Box>

      {/* Rename chat modal */}
      <Modal
        opened={!!editChatId}
        onClose={() => setEditChatId(null)}
        title="Переименовать чат"
        size="sm"
      >
        <form onSubmit={(e) => { e.preventDefault(); renameMutation.mutate(); }}>
          <Stack gap="md">
            <TextInput
              label="Название чата"
              value={editChatTitle}
              onChange={(e) => setEditChatTitle(e.currentTarget.value)}
              required
              autoFocus
            />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setEditChatId(null)}>Отмена</Button>
              <Button type="submit" loading={renameMutation.isPending}>Сохранить</Button>
            </Group>
          </Stack>
        </form>
      </Modal>

      {/* Debug panel */}
      {showDebug && activeChatId && (
        <Box
          style={{
            width: 300,
            borderLeft: '1px solid var(--mantine-color-default-border)',
            overflowY: 'auto',
          }}
          p="md"
        >
          <Text fw={500} mb="md" size="sm">
            Панель отладки
          </Text>
          <Stack gap="sm">
            <Group>
              <Text size="xs" c="dimmed">ID чата:</Text>
              <Text size="xs" ff="monospace">{activeChatId}</Text>
            </Group>
            <Group>
              <Text size="xs" c="dimmed">ID тенанта:</Text>
              <Text size="xs" ff="monospace">{tenantId}</Text>
            </Group>
            <Group>
              <Text size="xs" c="dimmed">Сообщения:</Text>
              <Badge size="sm">{messages.length}</Badge>
            </Group>
            {lastAssistantMessage && (
              <>
                <Divider label="Последний ответ" labelPosition="center" />
                {lastAssistantMessage.total_tokens != null && (
                  <>
                    <Group>
                      <Text size="xs" c="dimmed">Токены промпта:</Text>
                      <Text size="xs">{lastAssistantMessage.prompt_tokens ?? '-'}</Text>
                    </Group>
                    <Group>
                      <Text size="xs" c="dimmed">Токены ответа:</Text>
                      <Text size="xs">{lastAssistantMessage.completion_tokens ?? '-'}</Text>
                    </Group>
                    <Group>
                      <Text size="xs" c="dimmed">Всего токенов:</Text>
                      <Text size="xs">{lastAssistantMessage.total_tokens ?? '-'}</Text>
                    </Group>
                    {lastAssistantMessage.latency_ms != null && (
                      <Group>
                        <Text size="xs" c="dimmed">Задержка:</Text>
                        <Text size="xs">{Math.round(lastAssistantMessage.latency_ms)}ms</Text>
                      </Group>
                    )}
                  </>
                )}
              </>
            )}

            {/* Attachments section */}
            {attachments && attachments.length > 0 && (
              <>
                <Divider label="Файлы" labelPosition="center" />
                <Stack gap={4}>
                  {attachments.map((att: AttachmentBrief) => {
                    const st = STATUS_MAP[att.processing_status] || { color: 'gray', label: att.processing_status };
                    return (
                      <Paper key={att.id} withBorder p="xs" radius="sm">
                        <Group gap={4} wrap="nowrap">
                          <IconFile size={14} />
                          <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                            <Text size="xs" fw={500} truncate="end">{att.filename}</Text>
                            <Group gap={4}>
                              <Badge
                                size="xs"
                                color={FILE_TYPE_COLORS[att.file_type] || 'gray'}
                                variant="light"
                              >
                                {att.file_type}
                              </Badge>
                              <Text size="xs" c="dimmed">{formatFileSize(att.file_size_bytes)}</Text>
                              <Badge size="xs" color={st.color} variant="dot">{st.label}</Badge>
                            </Group>
                            {att.summary && (
                              <Text size="xs" c="dimmed" lineClamp={2} mt={2}>{att.summary}</Text>
                            )}
                          </Stack>
                        </Group>
                      </Paper>
                    );
                  })}
                </Stack>
              </>
            )}
          </Stack>
        </Box>
      )}
    </Box>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';

  return (
    <Box
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
      }}
    >
      <Paper
        p="sm"
        radius="md"
        maw="70%"
        withBorder
        bg={
          isUser
            ? 'var(--mantine-color-blue-filled)'
            : isSystem
              ? 'var(--mantine-color-yellow-light)'
              : undefined
        }
        style={isUser ? { color: 'white' } : undefined}
      >
        <Stack gap={4}>
          {isSystem && (
            <Text size="xs" fw={700}>
              Система
            </Text>
          )}
          <Text
            size="sm"
            style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
            c={isUser ? 'white' : undefined}
          >
            {message.content}
          </Text>
          <Group justify="space-between" gap="xs">
            <Text size="xs" c={isUser ? 'rgba(255,255,255,0.7)' : 'dimmed'}>
              {new Date(message.created_at).toLocaleTimeString()}
            </Text>
            {message.total_tokens != null && message.role === 'assistant' && (
              <Text size="xs" c="dimmed">
                {message.total_tokens} токенов
              </Text>
            )}
          </Group>
        </Stack>
      </Paper>
    </Box>
  );
}
