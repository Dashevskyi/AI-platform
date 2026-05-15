import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Stack, Text, ActionIcon, Group, Tooltip, Loader } from '@mantine/core';
import { IconMicrophone, IconPlayerStop, IconX, IconVolume } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMediaRecorder, getAiChatApi } from '../../packages/ai-chat-core';
import type { AuthMode } from '../../packages/ai-chat-core';

type Phase = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error';

type Props = {
  tenantId: string;
  chatId: string;
  apiBase: string;
  mode: 'admin' | 'end-user';
  apiKey?: string;
  authBearer?: string;
  /** Called after each round-trip completes so the host chat can refetch
   *  its message list (the user/assistant turn is persisted server-side). */
  onMessageSent?: () => void;
  onClose: () => void;
};

/**
 * Sentence-based chunker.
 *
 * Returns the index in `buffer` AFTER which we can safely split off a TTS
 * chunk. We look for sentence-final punctuation that is followed by whitespace
 * or end-of-buffer. We require a minimum chunk length so short interjections
 * ("Ну,") aren't sent as their own audio request.
 *
 * Returns -1 if no good split point yet.
 */
function findSentenceSplit(buffer: string, minLen = 30): number {
  if (buffer.length < minLen) return -1;
  // Avoid splitting inside a code fence — once we see ``` we wait for the
  // closing fence before resuming sentence splitting.
  const fences = (buffer.match(/```/g) || []).length;
  if (fences % 2 === 1) return -1;

  // Look for [.!?…] followed by space/newline/end. Prefer the latest one.
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

  const recorder = useMediaRecorder();
  const [phase, setPhase] = useState<Phase>('idle');
  const [transcript, setTranscript] = useState<string>('');
  const [assistantText, setAssistantText] = useState<string>('');

  // Audio queue: FIFO of (objectURL, audio). Played strictly sequentially —
  // we kick off TTS synth in parallel but never play audio_N+1 before
  // audio_N finishes, so sentences come out in the right order.
  const audioQueueRef = useRef<HTMLAudioElement[]>([]);
  const urlsRef = useRef<string[]>([]);
  const queueRunningRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const stoppedRef = useRef(false);

  useEffect(() => {
    return () => { stopAll(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopAll = () => {
    stoppedRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    if (currentAudioRef.current) {
      try { currentAudioRef.current.pause(); } catch { /* ignore */ }
      currentAudioRef.current = null;
    }
    audioQueueRef.current = [];
    for (const u of urlsRef.current) {
      try { URL.revokeObjectURL(u); } catch { /* ignore */ }
    }
    urlsRef.current = [];
    queueRunningRef.current = false;
  };

  const playQueueLoop = async () => {
    if (queueRunningRef.current) return;
    queueRunningRef.current = true;
    setPhase('speaking');
    while (audioQueueRef.current.length > 0) {
      if (stoppedRef.current) break;
      const audio = audioQueueRef.current.shift()!;
      currentAudioRef.current = audio;
      await new Promise<void>((resolve) => {
        audio.onended = () => resolve();
        audio.onerror = () => resolve();
        audio.play().catch(() => resolve());
      });
      currentAudioRef.current = null;
    }
    queueRunningRef.current = false;
    if (!stoppedRef.current && phase !== 'listening') {
      setPhase('idle');
      // Auto-restart listening so the dialog flows naturally.
      void recorder.start();
    }
  };

  const enqueueSpeech = async (sentence: string) => {
    if (!sentence.trim() || stoppedRef.current) return;
    try {
      const blob = await api.synthesizeAudio(tenantId, sentence);
      if (stoppedRef.current) return;
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
      // Per-chunk TTS error doesn't kill the whole exchange — log + continue.
      // eslint-disable-next-line no-console
      console.warn('TTS chunk failed', e);
    }
  };

  // Map recorder errors to phase
  useEffect(() => {
    if (recorder.state === 'recording') setPhase('listening');
    else if (recorder.state === 'error') {
      setPhase('error');
      if (recorder.error) {
        notifications.show({ title: 'Микрофон', message: recorder.error, color: 'red' });
      }
    }
  }, [recorder.state, recorder.error]);

  const submitTranscript = async (text: string) => {
    if (!text.trim()) return;
    stoppedRef.current = false;
    setAssistantText('');
    setPhase('thinking');

    let buffer = '';
    let splitOffset = 0;
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await api.sendMessageStream(
        tenantId,
        chatId,
        { content: text },
        (eventType, payload) => {
          if (stoppedRef.current) return;
          if (eventType === 'content_chunk' && typeof payload.text === 'string') {
            buffer += payload.text as string;
            setAssistantText(buffer);
            // Slide a sentence-split window across the unflushed tail.
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
            // Flush whatever's left after the last split point.
            const remaining = buffer.slice(splitOffset).trim();
            if (remaining) void enqueueSpeech(remaining);
          } else if (eventType === 'error' && typeof payload.message === 'string') {
            notifications.show({
              title: 'LLM',
              message: String(payload.message),
              color: 'red',
            });
          }
        },
        controller.signal,
      );
      onMessageSent?.();
    } catch (e) {
      if (!stoppedRef.current) {
        notifications.show({ title: 'Voice', message: (e as Error).message || '', color: 'red' });
        setPhase('error');
      }
    }
  };

  const handleMicTap = async () => {
    if (phase === 'speaking') {
      // User wants to interrupt the assistant — kill audio + start fresh recording.
      stopAll();
      stoppedRef.current = false;
      await recorder.start();
      return;
    }
    if (phase === 'listening') {
      const blob = await recorder.stop();
      if (!blob) { setPhase('idle'); return; }
      setPhase('transcribing');
      try {
        const { text } = await api.transcribeAudio(tenantId, blob);
        const clean = (text || '').trim();
        setTranscript(clean);
        if (!clean) { setPhase('idle'); return; }
        await submitTranscript(clean);
      } catch (e) {
        setPhase('error');
        notifications.show({ title: 'STT', message: (e as Error).message || '', color: 'red' });
      }
      return;
    }
    await recorder.start();
  };

  const closeAll = () => {
    stopAll();
    if (recorder.state === 'recording') recorder.cancel();
    onClose();
  };

  const phaseLabel = {
    idle: 'Нажми, чтобы говорить',
    listening: 'Слушаю…',
    transcribing: 'Распознаю…',
    thinking: 'Думаю…',
    speaking: 'Отвечаю…',
    error: 'Ошибка',
  }[phase];

  const isListening = phase === 'listening';

  return (
    <Box
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        background: 'rgba(0, 0, 0, 0.65)',
        backdropFilter: 'blur(8px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) closeAll(); }}
    >
      <Stack gap="lg" align="center" style={{ color: 'white', textAlign: 'center', maxWidth: 480 }}>
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

        <Box style={{ position: 'relative' }}>
          <ActionIcon
            variant="filled"
            color={isListening ? 'red' : phase === 'speaking' ? 'blue' : 'gray'}
            size={120}
            radius={9999}
            onClick={handleMicTap}
            disabled={phase === 'transcribing'}
            style={{
              boxShadow: isListening
                ? `0 0 0 ${4 + Math.round(recorder.level * 24)}px rgba(255, 75, 75, 0.35)`
                : '0 8px 20px rgba(0,0,0,0.4)',
              transition: 'box-shadow 60ms linear',
            }}
            aria-label="Микрофон"
          >
            {phase === 'transcribing' ? <Loader size={42} color="white" /> :
             isListening ? <IconPlayerStop size={56} /> :
             <IconMicrophone size={56} />}
          </ActionIcon>
        </Box>

        {transcript && (
          <Box>
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
