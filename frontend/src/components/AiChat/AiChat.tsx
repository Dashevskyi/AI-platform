import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  Box,
  Group,
  Stack,
  Text,
  TextInput,
  Textarea,
  Button,
  ScrollArea,
  ActionIcon,
  Loader,
  Center,
  Paper,
  Popover,
  Tooltip,
  Badge,
  Divider,
  Modal,
  Table,
} from '@mantine/core';
import {
  IconSend,
  IconPlus,
  IconMessageCircle,
  IconInfoCircle,
  IconCode,
  IconHeadphones,
  IconPencil,
  IconPaperclip,
  IconFile,
  IconX,
  IconUpload,
  IconChartDots,
  IconBolt,
  IconCheck,
  IconAlertCircle,
  IconTool,
  IconBook,
  IconBrain,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import type { Message, AttachmentBrief } from '../../shared/api/types';
import { MarkdownContent } from '../../shared/ui/MarkdownContent';
import {
  useAiChatList,
  useAiChatMessages,
  useAiChatAttachments,
  useAiChatSend,
  getAiChatApi,
} from '../../packages/ai-chat-core';
import { ArtifactsPanel } from './ArtifactsPanel';
import { MicButton } from './MicButton';
import { SpeakButton } from './SpeakButton';
import { VoiceModeOverlay } from './VoiceModeOverlay';
import type { AuthMode } from '../../packages/ai-chat-core';

export type AiChatMode = 'admin' | 'end-user';

export type AiChatFeatures = {
  /** Show step-by-step trail of pipeline events while streaming. Default: admin=true, end-user=false. */
  showTrail?: boolean;
  /** Show "Размышления" (reasoning) popover on assistant messages. Default: admin=true, end-user=false. */
  showReasoning?: boolean;
  /** Show stats popover (tokens, model, latency). Default: admin=true, end-user=false. */
  showStats?: boolean;
  /** Show file-attach button and drag-and-drop zone. Default: true. */
  showFiles?: boolean;
  /** Render assistant tool-result tables inside the chat. Default: admin=true, end-user=false. */
  showToolResults?: boolean;
  /** Show "новый чат"/edit-title controls (chat-management UI). Default: admin=true, end-user=false. */
  showChatControls?: boolean;
};

export type AiChatProps = {
  /** Tenant id (UUID) — required. */
  tenantId: string;
  /** Active chat id. Component will load messages and send to this chat. */
  chatId: string | null;
  /** API base URL. Defaults to same origin (''). */
  apiBase?: string;
  /** Operating mode. 'admin' = all features visible (uses /api/admin/...,
   *  Bearer JWT from localStorage). 'end-user' = sanitized UI for embedded
   *  clients (uses /api/tenants/..., requires apiKey prop). */
  mode?: AiChatMode;
  /** Tenant API key. Required when mode='end-user'; ignored in 'admin' mode. */
  apiKey?: string;
  /** Per-feature overrides. Each defaults based on mode. */
  features?: AiChatFeatures;
  /** Optional callback fired after each successful message round trip. */
  onMessageSent?: () => void;
  /** Called after a new chat is created via the "New chat" button.
   *  Caller is expected to navigate / re-route to the new chat. */
  onChatCreated?: (chatId: string) => void;
};

function defaultFeatures(mode: AiChatMode, overrides: AiChatFeatures = {}): Required<AiChatFeatures> {
  const base: Required<AiChatFeatures> =
    mode === 'admin'
      ? {
          showTrail: true,
          showReasoning: true,
          showStats: true,
          showFiles: true,
          showToolResults: true,
          showChatControls: true,
        }
      : {
          showTrail: false,
          showReasoning: false,
          showStats: false,
          showFiles: true,
          showToolResults: false,
          showChatControls: false,
        };
  return { ...base, ...overrides };
}

// If pasted text is longer than this, offer to attach as file instead
const PASTE_AS_FILE_THRESHOLD = 2000;

type StreamEvent = {
  type: string;
  payload: Record<string, unknown>;
  ts: number;
};

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

function getFileTypeFromName(filename: string): string {
  const ext = filename.toLowerCase().split('.').pop() || '';
  const map: Record<string, string> = {
    pdf: 'pdf', png: 'image', jpg: 'image', jpeg: 'image', gif: 'image',
    webp: 'image', bmp: 'image', tiff: 'image',
    mp3: 'audio', wav: 'audio', ogg: 'audio', flac: 'audio', m4a: 'audio',
    aac: 'audio', webm: 'audio', wma: 'audio',
    docx: 'docx', xlsx: 'xlsx', xls: 'xlsx',
    csv: 'csv', json: 'json', html: 'html', htm: 'html',
    xml: 'xml', txt: 'text', md: 'text', log: 'text',
  };
  return map[ext] || 'text';
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatDurationMs(ms: number | null | undefined): string | null {
  if (ms == null || Number.isNaN(ms)) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function AiChat({
  tenantId,
  chatId,
  apiBase = '',
  mode = 'admin',
  apiKey,
  features: featuresOverride,
  onMessageSent,
  onChatCreated,
}: AiChatProps) {
  const features = defaultFeatures(mode, featuresOverride);
  const activeChatId = chatId || null;

  const [messageText, setMessageText] = useState('');
  const [showDebug, setShowDebug] = useState(false);
  const [showArtifacts, setShowArtifacts] = useState(false);
  const [voiceModeOpen, setVoiceModeOpen] = useState(false);
  const [editChatId, setEditChatId] = useState<string | null>(null);
  const [editChatTitle, setEditChatTitle] = useState('');
  // Drafts the user attached to the upcoming message. Files are uploaded to the
  // server immediately (POST .../attachments/draft) and processed in background,
  // so by the time the user hits "Send" the summary is usually already there.
  type DraftItem = {
    localId: string;            // stable client-side key
    draftId: string | null;     // server id (null while uploading)
    filename: string;
    file_type: string;
    size: number;
    status: 'uploading' | 'pending' | 'processing' | 'done' | 'error';
    summary?: string | null;
    error?: string | null;
  };
  const [drafts, setDrafts] = useState<DraftItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [optimisticUserMsg, setOptimisticUserMsg] = useState<Message | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const messageInputRef = useRef<HTMLTextAreaElement>(null);

  const connection = useMemo(() => {
    if (mode === 'admin') {
      const token = typeof localStorage !== 'undefined' ? localStorage.getItem('auth_token') : null;
      return {
        mode: 'admin' as const,
        apiBase,
        auth: token ? ({ type: 'bearer' as const, token }) : undefined,
      };
    }
    return {
      mode: 'end-user' as const,
      apiBase,
      apiKey,
    };
  }, [mode, apiKey, apiBase]);

  // Dedicated API client for draft attachment lifecycle (upload + poll + delete).
  const draftApi = useMemo(() => {
    const auth: AuthMode | undefined =
      mode === 'admin'
        ? (connection.auth as AuthMode | undefined)
        : (apiKey ? { type: 'apiKey', apiKey } : undefined);
    return getAiChatApi({
      variant: mode === 'admin' ? 'admin' : 'tenant',
      apiBase,
      auth,
    });
  }, [mode, apiBase, apiKey, connection]);

  const { chats, create: createChatAction, rename: renameChatAction } = useAiChatList(tenantId, connection);
  const chatsData = useMemo(() => ({ items: chats }), [chats]);

  const { messages: serverMessages, isLoading: messagesLoading } = useAiChatMessages(
    tenantId,
    activeChatId,
    connection,
  );
  const { attachments } = useAiChatAttachments(tenantId, activeChatId, connection);

  const sendApi = useAiChatSend({
    tenantId,
    chatId: activeChatId,
    streaming: true,
    ...connection,
    onError: (err) => {
      notifications.show({
        title: 'Ошибка',
        message: err.message || 'Не удалось отправить сообщение',
        color: 'red',
      });
      setOptimisticUserMsg(null);
    },
  });
  const { send, streaming, streamingContent, streamEvents } = sendApi;
  const sendIsPending = sendApi.isLoading && !streaming;

  // Merge optimistic user message into the rendered list while the send is in flight.
  const messages: Message[] = useMemo(() => {
    if (!optimisticUserMsg) return serverMessages;
    if (serverMessages.some((m) => m.id === optimisticUserMsg.id)) return serverMessages;
    return [...serverMessages, optimisticUserMsg];
  }, [serverMessages, optimisticUserMsg]);
  // Drop the optimistic message once the server returns the real one.
  useEffect(() => {
    if (!optimisticUserMsg) return;
    if (!sendApi.isLoading) {
      // request finished — let invalidation refetch real messages then drop the placeholder
      const t = setTimeout(() => setOptimisticUserMsg(null), 50);
      return () => clearTimeout(t);
    }
  }, [optimisticUserMsg, sendApi.isLoading]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (viewportRef.current) {
      viewportRef.current.scrollTo({
        top: viewportRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }
  }, [messages.length]);

  // Auto-scroll while streaming (events/content grow inside the bubble) —
  // only nudge if user is already near the bottom, so we don't fight scroll-up.
  useEffect(() => {
    if (!streaming) return;
    const v = viewportRef.current;
    if (!v) return;
    const distanceFromBottom = v.scrollHeight - v.scrollTop - v.clientHeight;
    if (distanceFromBottom < 200) {
      v.scrollTo({ top: v.scrollHeight, behavior: 'smooth' });
    }
  }, [streaming, streamEvents.length, streamingContent]);

  // Create / rename chat — wrap core actions with notifications
  const createChatMutation = {
    isPending: false,
    mutate: async () => {
      try {
        const chat = await createChatAction();
        onChatCreated?.(chat.id);
      } catch {
        notifications.show({ title: 'Ошибка', message: 'Не удалось создать чат', color: 'red' });
      }
    },
  };
  const renameMutation = {
    isPending: false,
    mutate: async () => {
      if (!editChatId) return;
      try {
        await renameChatAction(editChatId, { title: editChatTitle });
        setEditChatId(null);
        notifications.show({ title: 'Готово', message: 'Название чата обновлено', color: 'green' });
      } catch {
        notifications.show({ title: 'Ошибка', message: 'Не удалось переименовать чат', color: 'red' });
      }
    },
  };

  const handleSendMessage = useCallback(async (text: string, attachmentIds: string[]) => {
    if (!activeChatId) return;
    setOptimisticUserMsg({
      id: `temp-${Date.now()}`,
      tenant_id: tenantId,
      chat_id: activeChatId,
      role: 'user',
      content: text,
      metadata_json: null,
      prompt_tokens: null,
      completion_tokens: null,
      total_tokens: null,
      latency_ms: null,
      time_to_first_token_ms: null,
      provider_type: null,
      model_name: null,
      correlation_id: null,
      tool_calls_count: null,
      finish_reason: null,
      status: 'sent',
      created_at: new Date().toISOString(),
    });
    try {
      await send({ content: text, attachmentIds });
      setMessageText('');
      setDrafts([]);
      requestAnimationFrame(() => {
        messageInputRef.current?.focus();
      });
      onMessageSent?.();
    } catch {
      // onError callback already handled it; just clear optimistic
      setOptimisticUserMsg(null);
    }
  }, [activeChatId, tenantId, send, onMessageSent]);

  // Are any drafts still being uploaded or processed? Block send if so.
  const draftsBusy = drafts.some((d) => d.status === 'uploading' || d.status === 'pending' || d.status === 'processing');
  const draftsAttachable = drafts.filter((d) => d.draftId && d.status !== 'error').map((d) => d.draftId as string);

  // Backwards-compatible mutation handle for the rest of the component
  const sendMutation = {
    isPending: sendIsPending,
    variables: { hasAttachments: drafts.length > 0 } as { hasAttachments: boolean },
    mutate: ({ text, attachmentIds }: { text: string; attachmentIds: string[] }) => {
      void handleSendMessage(text, attachmentIds);
    },
  };

  const handleSend = useCallback(() => {
    if (!messageText.trim() || !activeChatId) return;
    if (sendApi.isLoading) return;
    if (draftsBusy) return;
    const text = messageText.trim();
    const ids = [...draftsAttachable];
    setMessageText('');
    setDrafts([]);
    void handleSendMessage(text, ids);
  }, [messageText, activeChatId, sendApi.isLoading, draftsBusy, draftsAttachable, handleSendMessage]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  // File handling: each dropped file → POST .../attachments/draft, processing
  // runs in background, we poll for status until done/error.
  const addFiles = useCallback((newFiles: FileList | File[]) => {
    if (!activeChatId) return;
    const fileArray = Array.from(newFiles);
    for (const f of fileArray) {
      const localId = `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      setDrafts((prev) => [...prev, {
        localId,
        draftId: null,
        filename: f.name,
        file_type: getFileTypeFromName(f.name),
        size: f.size,
        status: 'uploading',
      }]);
      // Fire-and-forget upload; result lands via state update.
      void (async () => {
        try {
          const att = await draftApi.uploadDraftAttachment(tenantId, activeChatId, f);
          setDrafts((prev) => prev.map((d) =>
            d.localId === localId
              ? {
                  ...d,
                  draftId: att.id,
                  file_type: att.file_type || d.file_type,
                  status: (att.processing_status as DraftItem['status']) || 'pending',
                  summary: att.summary || null,
                }
              : d
          ));
        } catch (err) {
          setDrafts((prev) => prev.map((d) =>
            d.localId === localId
              ? { ...d, status: 'error', error: (err as Error).message }
              : d
          ));
          notifications.show({
            title: 'Не удалось загрузить файл',
            message: f.name,
            color: 'red',
          });
        }
      })();
    }
  }, [activeChatId, tenantId, draftApi]);

  const removeFile = useCallback((localId: string) => {
    const d = drafts.find((x) => x.localId === localId);
    if (!d) return;
    setDrafts((prev) => prev.filter((x) => x.localId !== localId));
    // Best-effort server-side cleanup. If it's still uploading, the draftId may
    // not exist yet — that's OK, the lazy GC will reap it.
    if (d.draftId && activeChatId) {
      void draftApi.deleteDraftAttachment(tenantId, activeChatId, d.draftId).catch(() => {});
    }
  }, [drafts, activeChatId, tenantId, draftApi]);

  // Poll status of in-flight drafts at 1s intervals while any of them are
  // pending or processing on the server.
  useEffect(() => {
    if (!activeChatId) return;
    const pollables = drafts.filter((d) => d.draftId && (d.status === 'pending' || d.status === 'processing'));
    if (pollables.length === 0) return;
    let cancelled = false;
    const timer = setInterval(async () => {
      for (const d of pollables) {
        if (!d.draftId || cancelled) continue;
        try {
          const att = await draftApi.getDraftAttachment(tenantId, activeChatId, d.draftId);
          if (cancelled) return;
          setDrafts((prev) => prev.map((x) =>
            x.localId === d.localId
              ? {
                  ...x,
                  status: (att.processing_status as DraftItem['status']) || x.status,
                  summary: att.summary || x.summary || null,
                }
              : x
          ));
        } catch {
          // Network hiccup — keep polling, status stays as-is.
        }
      }
    }, 1000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [drafts, activeChatId, tenantId, draftApi]);

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
  const hasLastAssistantStats = !!lastAssistantMessage && (
    lastAssistantMessage.prompt_tokens != null ||
    lastAssistantMessage.completion_tokens != null ||
    lastAssistantMessage.total_tokens != null ||
    lastAssistantMessage.latency_ms != null ||
    lastAssistantMessage.tool_calls_count != null ||
    !!lastAssistantMessage.provider_type ||
    !!lastAssistantMessage.model_name ||
    !!lastAssistantMessage.finish_reason
  );

  return (
    <Box style={{ display: 'flex', height: 'calc(100vh - 120px)', gap: 0 }}>
      {/* Main chat area */}
      <Box
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, position: 'relative' }}
        onDragOver={activeChatId && features.showFiles ? handleDragOver : undefined}
        onDragLeave={activeChatId && features.showFiles ? handleDragLeave : undefined}
        onDrop={activeChatId && features.showFiles ? handleDrop : undefined}
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
            {features.showChatControls && (
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
                  <Tooltip label="Переименовать чат">
                    <ActionIcon
                      variant="subtle"
                      size="xs"
                      onClick={() => {
                        const chat = chatsData?.items.find((c) => c.id === activeChatId);
                        if (chat) {
                          setEditChatId(chat.id);
                          setEditChatTitle(chat.title || chat.description || '');
                        }
                      }}
                    >
                      <IconPencil size={14} />
                    </ActionIcon>
                  </Tooltip>
                  {features.showFiles && attachments && attachments.length > 0 && (
                    <Tooltip label={`${attachments.length} файл(ов) приложено`}>
                      <Badge variant="light" size="sm" leftSection={<IconPaperclip size={10} />}>
                        {attachments.length}
                      </Badge>
                    </Tooltip>
                  )}
                </Group>
                <Group gap={4}>
                  <Tooltip label="Голосовой режим">
                    <ActionIcon
                      variant={voiceModeOpen ? 'filled' : 'subtle'}
                      onClick={() => setVoiceModeOpen(true)}
                      disabled={!activeChatId}
                    >
                      <IconHeadphones size={18} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Артефакты чата">
                    <ActionIcon
                      variant={showArtifacts ? 'filled' : 'subtle'}
                      onClick={() => setShowArtifacts(!showArtifacts)}
                    >
                      <IconCode size={18} />
                    </ActionIcon>
                  </Tooltip>
                  {features.showStats && (
                    <Tooltip label="Отладочная информация">
                      <ActionIcon
                        variant={showDebug ? 'filled' : 'subtle'}
                        onClick={() => setShowDebug(!showDebug)}
                      >
                        <IconInfoCircle size={18} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Group>
              </Group>
            )}

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
                    <MessageBubble
                      key={msg.id}
                      message={msg}
                      showReasoning={features.showReasoning}
                      showStats={features.showStats}
                      tenantId={tenantId}
                      apiBase={apiBase}
                      mode={mode}
                      apiKey={apiKey}
                      authBearer={
                        mode === 'admin' && typeof localStorage !== 'undefined'
                          ? (localStorage.getItem('auth_token') || undefined)
                          : undefined
                      }
                    />
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
                          <ThinkingTimer hasFiles={drafts.length > 0 || sendMutation.variables.hasAttachments} />
                        </Group>
                      </Paper>
                    </Box>
                  )}
                  {streaming && (
                    <Box style={{ display: 'flex', justifyContent: 'flex-start' }}>
                      <Paper
                        p="sm"
                        radius="md"
                        bg="var(--mantine-color-default-hover)"
                        maw="80%"
                        shadow="none"
                        style={{ border: 'none', minWidth: 280 }}
                      >
                        <StreamProgress
                          events={streamEvents}
                          content={streamingContent}
                          showTrail={features.showTrail}
                          showReasoning={features.showReasoning}
                        />
                      </Paper>
                    </Box>
                  )}
                  <div ref={messagesEndRef} />
                </Stack>
              )}
            </ScrollArea>

            {/* Draft attachments preview */}
            {drafts.length > 0 && (
              <Box
                px="md"
                pt="xs"
                style={{ borderTop: '1px solid var(--mantine-color-default-border)' }}
              >
                <Group gap="xs" wrap="wrap">
                  {drafts.map((d) => {
                    const ft = d.file_type || 'text';
                    const st = STATUS_MAP[d.status] || { color: 'gray', label: d.status };
                    return (
                      <Paper key={d.localId} withBorder p="4px 8px" radius="sm">
                        <Group gap={4} wrap="nowrap">
                          <Badge size="xs" color={FILE_TYPE_COLORS[ft] || 'gray'} variant="light">
                            {ft}
                          </Badge>
                          <Text size="xs" maw={150} truncate="end">
                            {d.filename}
                          </Text>
                          <Text size="xs" c="dimmed">
                            {formatFileSize(d.size)}
                          </Text>
                          {(d.status === 'uploading' || d.status === 'pending' || d.status === 'processing') ? (
                            <Group gap={4} wrap="nowrap">
                              <Loader size={10} />
                              <Text size="xs" c={st.color}>{st.label}</Text>
                            </Group>
                          ) : (
                            <Badge size="xs" color={st.color} variant="dot">{st.label}</Badge>
                          )}
                          <ActionIcon
                            size="xs"
                            variant="subtle"
                            color="red"
                            onClick={() => removeFile(d.localId)}
                          >
                            <IconX size={12} />
                          </ActionIcon>
                        </Group>
                      </Paper>
                    );
                  })}
                </Group>
              </Box>
            )}

            {/* Input */}
            <Box
              p="md"
              style={{ borderTop: drafts.length > 0 ? undefined : '1px solid var(--mantine-color-default-border)' }}
            >
              <Group gap="sm">
                {features.showFiles && (
                  <>
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
                        disabled={sendMutation.isPending || !activeChatId}
                        style={{ alignSelf: 'flex-end', marginBottom: 4 }}
                      >
                        <IconPaperclip size={18} />
                      </ActionIcon>
                    </Tooltip>
                    <MicButton
                      tenantId={tenantId}
                      apiBase={apiBase}
                      mode={mode}
                      apiKey={apiKey}
                      authBearer={
                        mode === 'admin' && typeof localStorage !== 'undefined'
                          ? (localStorage.getItem('auth_token') || undefined)
                          : undefined
                      }
                      disabled={sendMutation.isPending || !activeChatId}
                      onTranscribed={(text) => {
                        setMessageText((prev) => prev ? (prev.trim() + ' ' + text) : text);
                        requestAnimationFrame(() => messageInputRef.current?.focus());
                      }}
                    />
                  </>
                )}
                <Textarea
                  ref={messageInputRef}
                  placeholder={
                    drafts.length > 0
                      ? `Сообщение (${drafts.length} файл(ов) приложено)...`
                      : 'Введите сообщение... (Shift+Enter — новая строка)'
                  }
                  value={messageText}
                  onChange={(e) => setMessageText(e.currentTarget.value)}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  style={{ flex: 1 }}
                  autosize
                  minRows={1}
                  maxRows={8}
                />
                <Tooltip label={draftsBusy ? 'Дождитесь обработки файлов' : ''} disabled={!draftsBusy}>
                  <Button
                    leftSection={<IconSend size={16} />}
                    onClick={handleSend}
                    loading={sendMutation.isPending}
                    disabled={!messageText.trim() || draftsBusy}
                    style={{ alignSelf: 'flex-end', marginBottom: 4 }}
                  >
                    Отправить
                  </Button>
                </Tooltip>
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

      {/* Voice mode overlay — streams TTS sentence-by-sentence */}
      {voiceModeOpen && activeChatId && (
        <VoiceModeOverlay
          tenantId={tenantId}
          chatId={activeChatId}
          apiBase={apiBase}
          mode={mode}
          apiKey={apiKey}
          authBearer={
            mode === 'admin' && typeof localStorage !== 'undefined'
              ? (localStorage.getItem('auth_token') || undefined)
              : undefined
          }
          onMessageSent={() => { onMessageSent?.(); }}
          onClose={() => setVoiceModeOpen(false)}
        />
      )}

      {/* Artifacts panel */}
      {showArtifacts && activeChatId && (
        <Box
          style={{
            width: 380,
            borderLeft: '1px solid var(--mantine-color-default-border)',
            overflow: 'hidden',
          }}
        >
          <ArtifactsPanel
            tenantId={tenantId}
            chatId={activeChatId}
            mode={mode}
            apiBase={apiBase}
            apiKey={apiKey}
            authBearer={
              mode === 'admin' && typeof localStorage !== 'undefined'
                ? (localStorage.getItem('auth_token') || undefined)
                : undefined
            }
          />
        </Box>
      )}

      {/* Debug panel */}
      {features.showStats && showDebug && activeChatId && (
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
                {hasLastAssistantStats && (
                  <>
                    {lastAssistantMessage.prompt_tokens != null && (
                      <Group>
                        <Text size="xs" c="dimmed">Токены промпта:</Text>
                        <Text size="xs">{lastAssistantMessage.prompt_tokens}</Text>
                      </Group>
                    )}
                    {lastAssistantMessage.completion_tokens != null && (
                      <Group>
                        <Text size="xs" c="dimmed">Токены ответа:</Text>
                        <Text size="xs">{lastAssistantMessage.completion_tokens}</Text>
                      </Group>
                    )}
                    {lastAssistantMessage.total_tokens != null && (
                      <Group>
                        <Text size="xs" c="dimmed">Всего токенов:</Text>
                        <Text size="xs">{lastAssistantMessage.total_tokens}</Text>
                      </Group>
                    )}
                    {lastAssistantMessage.latency_ms != null && (
                      <Group>
                        <Text size="xs" c="dimmed">Задержка:</Text>
                        <Text size="xs">{formatDurationMs(lastAssistantMessage.latency_ms) ?? '-'}</Text>
                      </Group>
                    )}
                    {lastAssistantMessage.tool_calls_count != null && (
                      <Group>
                        <Text size="xs" c="dimmed">Tool calls:</Text>
                        <Text size="xs">{lastAssistantMessage.tool_calls_count}</Text>
                      </Group>
                    )}
                    {lastAssistantMessage.provider_type && (
                      <Group>
                        <Text size="xs" c="dimmed">Провайдер:</Text>
                        <Text size="xs">{lastAssistantMessage.provider_type}</Text>
                      </Group>
                    )}
                    {lastAssistantMessage.model_name && (
                      <Group>
                        <Text size="xs" c="dimmed">Модель:</Text>
                        <Text size="xs">{lastAssistantMessage.model_name}</Text>
                      </Group>
                    )}
                    {lastAssistantMessage.finish_reason && (
                      <Group>
                        <Text size="xs" c="dimmed">Finish:</Text>
                        <Text size="xs">{lastAssistantMessage.finish_reason}</Text>
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

function ThinkingTimer({ hasFiles }: { hasFiles: boolean }) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  const time = mins > 0 ? `${mins}:${secs.toString().padStart(2, '0')}` : `${secs}с`;

  return (
    <Text size="sm" c="dimmed">
      {hasFiles ? 'Обрабатываю файлы и думаю' : 'Думаю'}... {time}
    </Text>
  );
}

function MessageBubble({
  message,
  showReasoning = true,
  showStats = true,
  tenantId,
  apiBase,
  mode,
  apiKey,
  authBearer,
}: {
  message: Message;
  tenantId?: string;
  apiBase?: string;
  mode?: 'admin' | 'end-user';
  apiKey?: string;
  authBearer?: string;
  showReasoning?: boolean;
  showStats?: boolean;
}) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';
  const isAssistant = message.role === 'assistant';
  const latencyLabel = formatDurationMs(message.latency_ms);
  const timeToFirstTokenLabel = formatDurationMs(message.time_to_first_token_ms);
  const reasoning = (() => {
    const meta = message.metadata_json;
    if (meta && typeof meta === 'object' && typeof (meta as Record<string, unknown>).reasoning === 'string') {
      const txt = (meta as Record<string, unknown>).reasoning as string;
      return txt.trim() ? txt : null;
    }
    return null;
  })();
  const eventsTrail = (() => {
    const meta = message.metadata_json;
    if (meta && typeof meta === 'object' && Array.isArray((meta as Record<string, unknown>).events)) {
      return (meta as Record<string, unknown>).events as Array<{ type: string; payload: Record<string, unknown> }>;
    }
    return null;
  })();
  const hasTrail = !!(eventsTrail && eventsTrail.length > 0);
  const isEmptyContent = isAssistant && !message.content?.trim();
  const statsRows = isAssistant
    ? [
        message.provider_type ? { label: 'Провайдер', value: message.provider_type } : null,
        message.model_name ? { label: 'Модель', value: message.model_name } : null,
        latencyLabel ? { label: 'Задержка', value: latencyLabel } : null,
        timeToFirstTokenLabel ? { label: 'TTFT', value: timeToFirstTokenLabel } : null,
        message.tool_calls_count != null ? { label: 'Tool calls', value: String(message.tool_calls_count) } : null,
        message.prompt_tokens != null ? { label: 'Промпт', value: String(message.prompt_tokens) } : null,
        message.completion_tokens != null ? { label: 'Ответ', value: String(message.completion_tokens) } : null,
        message.total_tokens != null ? { label: 'Всего', value: String(message.total_tokens) } : null,
        message.finish_reason ? { label: 'Finish', value: message.finish_reason } : null,
      ].filter(Boolean) as { label: string; value: string }[]
    : [];
  const hasStats = statsRows.length > 0;

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
          {isEmptyContent ? (
            <Text size="sm" c="dimmed" fs="italic">
              Модель не сформировала текстовый ответ
              {reasoning ? ' (см. размышления)' : ''}
              {hasTrail ? ' (см. ход обработки)' : ''}.
            </Text>
          ) : (
            <MarkdownContent
              content={message.content}
              color={isUser ? 'white' : undefined}
              linkColor={isUser ? 'rgba(255,255,255,0.92)' : undefined}
            />
          )}
          <Group justify="space-between" gap="xs">
            <Text size="xs" c={isUser ? 'rgba(255,255,255,0.7)' : 'dimmed'}>
              {new Date(message.created_at).toLocaleTimeString()}
            </Text>
            {isAssistant && (
              <Group gap={6} wrap="nowrap">
                {tenantId && apiBase != null && mode && (
                  <SpeakButton
                    tenantId={tenantId}
                    apiBase={apiBase}
                    mode={mode}
                    apiKey={apiKey}
                    authBearer={authBearer}
                    text={message.content || ''}
                  />
                )}
                {showStats && (message.total_tokens != null || message.tool_calls_count != null) && (
                  <Text size="xs" c="dimmed">
                    {message.total_tokens != null
                      ? `${message.total_tokens} токенов`
                      : `${message.tool_calls_count} tool`}
                  </Text>
                )}
                {showReasoning && (reasoning || hasTrail) && (
                  <Popover width={460} position="top-end" withArrow shadow="md">
                    <Popover.Target>
                      <Tooltip label={reasoning && hasTrail ? 'Размышления и ход обработки' : reasoning ? 'Размышления модели' : 'Ход обработки'}>
                        <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Показать размышления и trail">
                          <IconBrain size={14} />
                        </ActionIcon>
                      </Tooltip>
                    </Popover.Target>
                    <Popover.Dropdown p="xs">
                      <ScrollArea.Autosize mah={420}>
                        <Stack gap={8}>
                          {hasTrail && (
                            <>
                              <Text size="xs" fw={600}>Ход обработки</Text>
                              <Stack gap={2} pl={2}>
                                {eventsTrail!.map((ev, i) => {
                                  const Icon = eventIcon(ev.type);
                                  const isError = ev.type === 'error';
                                  if (ev.type === 'reasoning') return null;
                                  return (
                                    <Group key={i} gap={6} wrap="nowrap">
                                      <Icon size={12} color={isError ? 'var(--mantine-color-red-6)' : 'var(--mantine-color-dimmed)'} />
                                      <Text size="xs" c={isError ? 'red' : 'dimmed'} style={{ whiteSpace: 'pre-wrap' }}>
                                        {eventLabel({ type: ev.type, payload: ev.payload, ts: 0 })}
                                      </Text>
                                    </Group>
                                  );
                                })}
                              </Stack>
                            </>
                          )}
                          {reasoning && (
                            <>
                              {hasTrail && <Divider my={4} />}
                              <Group gap="xs" justify="space-between">
                                <Text size="xs" fw={600}>Размышления модели</Text>
                                <Text size="xs" c="dimmed">{reasoning.length} симв.</Text>
                              </Group>
                              <Text size="xs" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>
                                {reasoning}
                              </Text>
                            </>
                          )}
                        </Stack>
                      </ScrollArea.Autosize>
                    </Popover.Dropdown>
                  </Popover>
                )}
                {showStats && hasStats && (
                  <Popover width={280} position="top-end" withArrow shadow="md">
                    <Popover.Target>
                      <Tooltip label="Статистика ответа">
                        <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Показать статистику">
                          <IconChartDots size={14} />
                        </ActionIcon>
                      </Tooltip>
                    </Popover.Target>
                    <Popover.Dropdown p="xs">
                      <Stack gap={6}>
                        <Text size="xs" fw={600}>
                          Статистика ответа
                        </Text>
                        <Table withTableBorder={false} withColumnBorders={false}>
                          <Table.Tbody>
                            {statsRows.map((row) => (
                              <Table.Tr key={row.label}>
                                <Table.Td px={0} py={3}>
                                  <Text size="xs" c="dimmed">
                                    {row.label}
                                  </Text>
                                </Table.Td>
                                <Table.Td px={0} py={3}>
                                  <Text size="xs" ta="right">
                                    {row.value}
                                  </Text>
                                </Table.Td>
                              </Table.Tr>
                            ))}
                          </Table.Tbody>
                        </Table>
                      </Stack>
                    </Popover.Dropdown>
                  </Popover>
                )}
              </Group>
            )}
          </Group>
        </Stack>
      </Paper>
    </Box>
  );
}


function eventIcon(type: string) {
  if (type === 'kb_search_start' || type === 'kb_search_done') return IconBook;
  if (type === 'tool_call_start' || type === 'tool_call_done') return IconTool;
  if (type === 'provider_call_start' || type === 'provider_call_done') return IconBrain;
  if (type === 'error' || type === 'throttle_rejected') return IconAlertCircle;
  if (type === 'done' || type === 'final') return IconCheck;
  return IconBolt;
}

function eventLabel(ev: StreamEvent): string {
  const p = ev.payload || {};
  switch (ev.type) {
    case 'pipeline_start':
      return 'Старт обработки';
    case 'kb_search_start':
      return 'Поиск в базе знаний...';
    case 'kb_search_done':
      return `База знаний: найдено ${p.chunks_count ?? 0}`;
    case 'provider_call_start':
      return `Запрос к LLM (${p.model ?? 'модель'}, раунд ${p.round ?? 0})...`;
    case 'provider_call_done': {
      const ms = typeof p.latency_ms === 'number' ? `${p.latency_ms}мс` : '';
      const tools = p.has_tool_calls ? ' → вызывает tools' : '';
      return `Ответ LLM (${ms})${tools}`;
    }
    case 'tool_call_start':
      return `Вызов tool: ${p.name ?? '?'}`;
    case 'tool_call_done': {
      const ms = typeof p.latency_ms === 'number' ? `${p.latency_ms.toLocaleString('ru-RU')} мс` : '';
      const ok = p.ok ? '✓' : '✗';
      const sizes: string[] = [];
      if (typeof p.output_tokens === 'number' && p.output_tokens > 0) {
        sizes.push(`~${p.output_tokens.toLocaleString('ru-RU')} ток.`);
      } else if (typeof p.output_chars === 'number' && p.output_chars > 0) {
        sizes.push(`${p.output_chars.toLocaleString('ru-RU')} симв.`);
      }
      const sizeStr = sizes.length ? `, ${sizes.join(', ')}` : '';
      return `${ok} ${p.name ?? '?'} (${ms}${sizeStr})`;
    }
    case 'done':
      return 'Готово';
    case 'final':
      return 'Сохранено';
    case 'error':
      return `Ошибка: ${p.message ?? ''}`;
    case 'throttle_rejected': {
      const retry = typeof p.retry_after === 'number' ? ` (повтор через ${p.retry_after}с)` : '';
      return `Лимит запросов превышен${retry}: ${p.message ?? ''}`;
    }
    case 'merge_pending':
      return `Ожидание объединения с другими сообщениями (${p.window_ms}мс)...`;
    case 'merge_start':
      return `Объединено ${p.merged_count} сообщений → один LLM-запрос`;
    default:
      return ev.type;
  }
}

function StreamProgress({
  events,
  content,
  showTrail = true,
  showReasoning = true,
}: {
  events: StreamEvent[];
  content: string;
  showTrail?: boolean;
  showReasoning?: boolean;
}) {
  const [reasoningOpen, setReasoningOpen] = useState(true);
  // Hide noisy events; reasoning + content_chunk are rendered separately
  const visible = !showTrail
    ? []
    : events.filter(
        (e) =>
          e.type !== 'stream_open' &&
          e.type !== 'pipeline_start' &&
          e.type !== 'reasoning' &&
          e.type !== 'reasoning_chunk' &&
          e.type !== 'content_chunk',
      );
  const reasoningPieces: string[] = [];
  for (const e of events) {
    if (e.type === 'reasoning_chunk' && typeof e.payload?.text === 'string') {
      reasoningPieces.push(String(e.payload.text));
    }
  }
  // Final whole-text reasoning event (some providers fire this at end of round)
  const finalReasoning = events
    .filter((e) => e.type === 'reasoning' && typeof e.payload?.text === 'string')
    .map((e) => String(e.payload.text));
  const reasoning = (reasoningPieces.length > 0 ? reasoningPieces.join('') : finalReasoning.join('\n\n')).trim();
  return (
    <Stack gap={4}>
      <Group gap="xs">
        <Loader size="xs" />
        <ThinkingTimer hasFiles={false} />
      </Group>
      {visible.length > 0 && (
        <Stack gap={2} pl={4}>
          {visible.map((ev, i) => {
            const Icon = eventIcon(ev.type);
            const isError = ev.type === 'error';
            return (
              <Group key={i} gap={6} wrap="nowrap">
                <Icon size={12} color={isError ? 'var(--mantine-color-red-6)' : 'var(--mantine-color-dimmed)'} />
                <Text size="xs" c={isError ? 'red' : 'dimmed'} style={{ whiteSpace: 'pre-wrap' }}>
                  {eventLabel(ev)}
                </Text>
              </Group>
            );
          })}
        </Stack>
      )}
      {showReasoning && reasoning && (
        <Box>
          <Group gap={4} wrap="nowrap" style={{ cursor: 'pointer' }} onClick={() => setReasoningOpen((v) => !v)}>
            <IconBrain size={12} color="var(--mantine-color-dimmed)" />
            <Text size="xs" c="dimmed">
              {reasoningOpen ? 'Скрыть размышления' : `Размышления (${reasoning.length} симв.)`}
            </Text>
          </Group>
          {reasoningOpen && (
            <Paper p="xs" mt={4} radius="sm" bg="var(--mantine-color-default-hover)" style={{ border: 'none' }}>
              <Text size="xs" c="dimmed" style={{ whiteSpace: 'pre-wrap' }}>{reasoning}</Text>
            </Paper>
          )}
        </Box>
      )}
      {content && (
        <Box mt={6}>
          <MarkdownContent content={content} />
        </Box>
      )}
    </Stack>
  );
}
