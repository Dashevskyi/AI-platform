import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Stack, Text, ActionIcon, Group, Tooltip, Loader } from '@mantine/core';
import { IconMicrophone, IconMicrophoneOff, IconX, IconVolume, IconPlayerStop } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useQueryClient } from '@tanstack/react-query';
import { useVAD, getAiChatApi } from '../../packages/ai-chat-core';
import type { AuthMode } from '../../packages/ai-chat-core';

type Phase = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error';

type Props = {
  tenantId: string;
  chatId: string;
  apiBase: string;
  mode: 'admin' | 'end-user';
  apiKey?: string;
  authBearer?: string;
  onMessageSent?: () => void;
  onClose: () => void;
};

/**
 * Hands-free voice mode.
 *
 *   listen  → user speaks → VAD detects pause (silenceMs)
 *     ↓
 *   transcribe (POST /voice/stt)
 *     ↓
 *   thinking + speaking (in parallel):
 *     • LLM streams content_chunk events
 *     • sentence chunker emits sentences → TTS per sentence → audio queue
 *     • the audio queue plays sequentially
 *     • mic stays open the whole time
 *     ↓
 *   listen again (VAD never paused — same session continues)
 *
 * INTERRUPT: if VAD fires onSpeechStart while audio is playing or LLM is
 * still streaming, we cut the audio queue, abort the LLM stream, and start
 * collecting a new user phrase. No button press required.
 */
function findSentenceSplit(buffer: string, minLen = 30): number {
  if (buffer.length < minLen) return -1;
  const fences = (buffer.match(/```/g) || []).length;
  if (fences % 2 === 1) return -1;
  const re = /[.!?…](?=\s|$)|\n{2,}/g;
  let lastIdx = -1;
  let m: RegExpExecArray | null;
  while ((m = re.exec(buffer)) !== null) {
    if (m.index >= minLen - 1) lastIdx = m.index + m[0].length;
  }
  return lastIdx;
}


export function VoiceModeOverlay({
  tenantId, chatId, apiBase, mode, apiKey, authBearer, onMessageSent, onClose,
}: Props) {
  const api = useMemo(() => {
    const auth: AuthMode | undefined =
      mode === 'admin' ? (authBearer ? { type: 'bearer', token: authBearer } : undefined)
                      : (apiKey ? { type: 'apiKey', apiKey } : undefined);
    return getAiChatApi({ variant: mode === 'admin' ? 'admin' : 'tenant', apiBase, auth });
  }, [mode, apiBase, apiKey, authBearer]);

  const queryClient = useQueryClient();

  /**
   * Voice mode bypasses useAiChatSend and calls sendMessageStream directly,
   * so it never triggers the hook's invalidateAfterSend. The user/assistant
   * turns ARE persisted server-side, but the host AiChat's react-query
   * caches keep stale data and the messages never show up in the chat
   * timeline. Match the same invalidation set used by useAiChatSend.
   */
  const invalidateChatCaches = () => {
    queryClient.invalidateQueries({ queryKey: ['ai-chat-core', 'messages', tenantId, chatId, mode] });
    queryClient.invalidateQueries({ queryKey: ['ai-chat-core', 'attachments', tenantId, chatId, mode] });
    queryClient.invalidateQueries({ queryKey: ['ai-chat-core', 'list', tenantId, mode] });
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', chatId, 'messages'] });
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', chatId, 'attachments'] });
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', 'list'] });
  };

  const [phase, setPhase] = useState<Phase>('idle');
  const [transcript, setTranscript] = useState<string>('');
  const [assistantText, setAssistantText] = useState<string>('');

  // Refs we mutate from VAD callbacks without re-rendering.
  const audioQueueRef = useRef<HTMLAudioElement[]>([]);
  const urlsRef = useRef<string[]>([]);
  const queueRunningRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const llmStreamingRef = useRef(false);
  // Hard kill-switch — set on closeAll(). Every async callback (TTS resolve,
  // queue loop, LLM stream callback) bails when this flips. Without it,
  // TTS requests in flight when the user closes the overlay still resolve
  // and push audio into the queue, which keeps playing after dismissal.
  const closedRef = useRef(false);

  const stopAudio = () => {
    audioQueueRef.current = [];
    if (currentAudioRef.current) {
      try { currentAudioRef.current.pause(); } catch { /* ignore */ }
      try { currentAudioRef.current.src = ''; } catch { /* ignore */ }
      currentAudioRef.current = null;
    }
    for (const u of urlsRef.current) {
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    }
    urlsRef.current = [];
    queueRunningRef.current = false;
  };

  const abortLLM = () => {
    try { abortRef.current?.abort(); } catch { /* ignore */ }
    abortRef.current = null;
    llmStreamingRef.current = false;
  };

  const playQueueLoop = async () => {
    if (queueRunningRef.current || closedRef.current) return;
    queueRunningRef.current = true;
    setPhase('speaking');
    while (audioQueueRef.current.length > 0) {
      if (closedRef.current) { audioQueueRef.current = []; break; }
      const audio = audioQueueRef.current.shift()!;
      currentAudioRef.current = audio;
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve();
        audio.onerror = () => resolve();
        audio.play().catch(() => resolve());
      });
      currentAudioRef.current = null;
      if (closedRef.current) break;
    }
    queueRunningRef.current = false;
    if (!llmStreamingRef.current && !closedRef.current) {
      setPhase('listening');
    }
  };

  const enqueueSpeech = async (sentence: string) => {
    if (!sentence.trim() || closedRef.current) return;
    try {
      const blob = await api.synthesizeAudio(tenantId, sentence);
      // Overlay may have closed while the TTS request was in flight.
      if (closedRef.current) return;
      const url = URL.createObjectURL(blob);
      urlsRef.current.push(url);
      const audio = new Audio(url);
      audio.addEventListener('ended', () => {
        try { URL.revokeObjectURL(url); } catch { /* ignore */ }
        urlsRef.current = urlsRef.current.filter((u) => u !== url);
      });
      audioQueueRef.current.push(audio);
      void playQueueLoop();
    } catch (e) {
      console.warn('TTS chunk failed', e);
    }
  };

  /** User speech segment: STT → submit → stream LLM → per-sentence TTS. */
  const handleSegment = async (blob: Blob) => {
    // If we are currently speaking or thinking — drop those, this is a new turn.
    abortLLM();
    stopAudio();

    setPhase('transcribing');
    let userText = '';
    try {
      const { text } = await api.transcribeAudio(tenantId, blob);
      userText = (text || '').trim();
      setTranscript(userText);
    } catch (e) {
      setPhase('listening');
      notifications.show({ title: 'STT', message: (e as Error).message || '', color: 'red' });
      return;
    }
    if (!userText) { setPhase('listening'); return; }

    setAssistantText('');
    setPhase('thinking');
    let buffer = '';
    let splitOffset = 0;
    llmStreamingRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await api.sendMessageStream(
        tenantId,
        chatId,
        { content: userText },
        (eventType, payload) => {
          if (controller.signal.aborted || closedRef.current) return;
          if (eventType === 'content_chunk' && typeof payload.text === 'string') {
            buffer += payload.text as string;
            setAssistantText(buffer);
            const tail = buffer.slice(splitOffset);
            const cut = findSentenceSplit(tail);
            if (cut > 0) {
              const sentence = tail.slice(0, cut).trim();
              splitOffset += cut;
              if (sentence) void enqueueSpeech(sentence);
            }
          } else if (eventType === 'done' && typeof payload.content === 'string') {
            buffer = payload.content as string;
            setAssistantText(buffer);
          } else if (eventType === 'final') {
            const remaining = buffer.slice(splitOffset).trim();
            if (remaining) void enqueueSpeech(remaining);
          } else if (eventType === 'error' && typeof payload.message === 'string') {
            notifications.show({ title: 'LLM', message: String(payload.message), color: 'red' });
          }
        },
        controller.signal,
      );
      // Round-trip persisted server-side; refresh local caches so the
      // user/assistant turns show up in the underlying chat timeline.
      if (!closedRef.current) {
        invalidateChatCaches();
        onMessageSent?.();
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        notifications.show({ title: 'Voice', message: (e as Error).message || '', color: 'red' });
      }
    } finally {
      llmStreamingRef.current = false;
      if (audioQueueRef.current.length === 0 && !currentAudioRef.current) {
        setPhase('listening');
      }
    }
  };

  // VAD: silenceMs=1500 is comfortable for most languages; tune later.
  const vad = useVAD({
    silenceMs: 1500,
    onSegment: (blob) => { void handleSegment(blob); },
    // INTERRUPT: as soon as the user starts speaking again, kill whatever
    // the assistant is doing — TTS queue + in-flight LLM stream.
    onSpeechStart: () => {
      if (currentAudioRef.current || audioQueueRef.current.length > 0 || llmStreamingRef.current) {
        abortLLM();
        stopAudio();
        setPhase('listening');
      }
    },
  });

  // Start listening as soon as the overlay mounts.
  useEffect(() => {
    void vad.start();
    return () => {
      vad.stop();
      abortLLM();
      stopAudio();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Mirror VAD state to UI phase when nothing more interesting is happening.
  useEffect(() => {
    if (vad.state === 'error' && vad.error) {
      notifications.show({ title: 'Микрофон', message: vad.error, color: 'red' });
      setPhase('error');
    } else if (vad.state === 'listening' && phase === 'idle') {
      setPhase('listening');
    }
  }, [vad.state, vad.error, phase]);

  const closeAll = () => {
    // Flip the kill-switch FIRST so any in-flight TTS/LLM callbacks bail
    // before they push more audio into the queue.
    closedRef.current = true;
    abortLLM();
    stopAudio();
    vad.stop();
    onClose();
  };

  const phaseLabel = ({
    idle: 'Готов',
    listening: vad.state === 'speaking' ? '🎙 Слушаю…' : 'Жду речи…',
    transcribing: 'Распознаю…',
    thinking: 'Думаю…',
    speaking: 'Отвечаю…',
    error: 'Ошибка',
  } as Record<Phase, string>)[phase];

  const isUserSpeaking = vad.state === 'speaking';

  return (
    <Box
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'rgba(0, 0, 0, 0.65)', backdropFilter: 'blur(8px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) closeAll(); }}
    >
      <Stack gap="lg" align="center" style={{ color: 'white', textAlign: 'center', maxWidth: 520, padding: 24 }}>
        <Box style={{ position: 'absolute', top: 16, right: 16 }}>
          <Tooltip label="Закрыть голосовой режим">
            <ActionIcon variant="subtle" color="gray" onClick={closeAll}>
              <IconX size={20} color="white" />
            </ActionIcon>
          </Tooltip>
        </Box>

        <Group gap="xs">
          {phase === 'speaking' && <IconVolume size={18} />}
          {phase === 'thinking' && <Loader size="sm" color="gray" />}
          <Text size="lg" fw={500}>{phaseLabel}</Text>
        </Group>

        {/* Big indicator orb. No buttons — hands-free. */}
        <Box
          style={{
            width: 120, height: 120, borderRadius: 9999,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: isUserSpeaking ? 'var(--mantine-color-red-7)'
                     : phase === 'speaking' ? 'var(--mantine-color-blue-7)'
                     : 'var(--mantine-color-gray-7)',
            boxShadow: isUserSpeaking
              ? `0 0 0 ${6 + Math.round(vad.level * 30)}px rgba(255, 75, 75, 0.30)`
              : phase === 'speaking'
              ? '0 0 0 12px rgba(0, 122, 255, 0.20)'
              : '0 8px 20px rgba(0,0,0,0.4)',
            transition: 'box-shadow 60ms linear, background 200ms ease',
          }}
        >
          {phase === 'transcribing' ? <Loader size={42} color="white" /> :
           phase === 'speaking' ? <IconVolume size={56} color="white" /> :
           vad.state === 'error' ? <IconMicrophoneOff size={56} color="white" /> :
           <IconMicrophone size={56} color="white" />}
        </Box>

        <Text size="xs" c="dimmed" style={{ maxWidth: 360 }}>
          Просто говорите. Пауза 1.5 с автоматически отправляет фразу.
          Чтобы прервать ассистента — начните говорить.
        </Text>

        {/* Tiny "force-stop everything and listen now" button as escape hatch. */}
        {(phase === 'speaking' || phase === 'thinking') && (
          <Tooltip label="Прервать и слушать">
            <ActionIcon
              variant="light" color="red" size="md"
              onClick={() => { abortLLM(); stopAudio(); setPhase('listening'); }}
              style={{ position: 'absolute', bottom: 24, right: 24 }}
            >
              <IconPlayerStop size={16} />
            </ActionIcon>
          </Tooltip>
        )}

        {transcript && (
          <Box style={{ maxWidth: 480 }}>
            <Text size="xs" c="dimmed">Вы сказали:</Text>
            <Text size="sm" c="white">{transcript}</Text>
          </Box>
        )}
        {assistantText && (
          <Box style={{ maxWidth: 480 }}>
            <Text size="xs" c="dimmed">Ассистент:</Text>
            <Text size="sm" c="white" lineClamp={4} style={{ whiteSpace: 'pre-wrap' }}>{assistantText}</Text>
          </Box>
        )}
      </Stack>
    </Box>
  );
}
