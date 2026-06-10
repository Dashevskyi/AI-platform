import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Stack, Text, ActionIcon, Group, Tooltip, Loader, ScrollArea } from '@mantine/core';
import { IconMicrophone, IconMicrophoneOff, IconX, IconVolume, IconPlayerStop } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useQueryClient } from '@tanstack/react-query';
import { useVAD, useWhisperLiveSTT, getAiChatApi } from '../../packages/ai-chat-core';
import type { AuthMode } from '../../packages/ai-chat-core';
import { MarkdownContent } from '../../shared/ui/MarkdownContent';

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
function findSentenceSplit(buffer: string, minLen = 15): number {
  if (buffer.length < minLen) return -1;
  const fences = (buffer.match(/```/g) || []).length;
  if (fences % 2 === 1) return -1;
  // Don't split while inside a markdown table (lines starting with |).
  // A table "block" is any contiguous group of | lines — we detect if the
  // buffer tail contains an unclosed table (a | line without a blank line after it).
  const tailForTable = buffer.slice(Math.max(0, buffer.length - 200));
  if (/\|[^\n]+\|[^\n]*$/.test(tailForTable)) return -1;
  const re = /[.!?…](?=\s|$)|\n{2,}/g;
  let lastIdx = -1;
  let m: RegExpExecArray | null;
  while ((m = re.exec(buffer)) !== null) {
    if (m.index >= minLen - 1) lastIdx = m.index + m[0].length;
  }
  return lastIdx;
}

/**
 * Prepare text for TTS: strip markdown that Silero reads verbatim.
 *
 * - Table rows   (| col | val |) — replaced with "Данные в таблице" placeholder
 *   (emitted ONCE per table block to signal the user data exists).
 * - Separator rows (| --- |) — silently dropped.
 * - Bold/italic markers — stripped so "*bold*" → "bold".
 * - Code spans/blocks — replaced with "код".
 * - Markdown headers (#, ##) — strip the leading #'s.
 */
function prepareForTTS(raw: string): string {
  const lines = raw.split('\n');
  const out: string[] = [];
  let inTable = false;

  for (const line of lines) {
    const trimmed = line.trim();

    // Separator row: | --- | ---|  — always drop
    if (/^\|[\s|:-]+\|$/.test(trimmed)) {
      inTable = true;
      continue;
    }

    // Data row: | value | value |
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (!inTable) {
        // First table row encountered: emit a single announcement
        out.push('Данные в таблице.');
        inTable = true;
      }
      // Skip the actual cell content
      continue;
    }

    // Non-table line: reset table tracker
    inTable = false;

    // Strip markdown header hashes
    const noHeader = trimmed.replace(/^#{1,6}\s+/, '');

    // Strip bold/italic markers but keep the text
    const noEmphasis = noHeader.replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1').replace(/_([^_]+)_/g, '$1');

    // Replace inline code `...` with just the content
    const noInlineCode = noEmphasis.replace(/`([^`]+)`/g, '$1');

    // Drop fence markers ```
    if (/^```/.test(trimmed)) continue;

    if (noInlineCode.trim()) out.push(noInlineCode.trim());
  }

  return out.join('\n').trim();
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

  // Build WhisperLive proxy URL with auth token
  const wlProxyUrl = useMemo(() => {
    const base = `/api/tenants/${tenantId}/voice/stt-stream`;
    const params = new URLSearchParams();
    if (mode === 'admin' && authBearer) params.set('authorization', `Bearer ${authBearer}`);
    else if (apiKey) params.set('api_key', apiKey);
    const qs = params.toString();
    return qs ? `${base}?${qs}` : base;
  }, [tenantId, mode, apiKey, authBearer]);

  // Partial/final transcript ref for when VAD fires
  const wlFinalTextRef = useRef('');

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
  // WhisperLive debounce: after onFinal fires, wait this long for more speech
  // before submitting to LLM. Prevents false-triggers on brief mid-sentence pauses.
  const wlDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hold-phrase timers: play a filler phrase when LLM takes > N ms.
  // Cancelled immediately when the first real TTS chunk is enqueued.
  const holdTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  // Flips to true on the first real enqueueSpeech call; prevents hold phrases
  // from being enqueued after the LLM has already started responding.
  const firstTTSFiredRef = useRef(false);

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
    // Cancel any pending hold-phrase timers (user interrupted or closed).
    for (const t of holdTimersRef.current) clearTimeout(t);
    holdTimersRef.current = [];
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
        audio.onerror = (e) => {
          console.warn('[TTS] audio.onerror', audio.error?.code, audio.error?.message, e);
          resolve();
        };
        audio.play().then(() => {
          // play() resolved — audio started
        }).catch((err: Error) => {
          console.warn('[TTS] audio.play() rejected:', err.name, err.message);
          resolve();
        });
      });
      currentAudioRef.current = null;
      if (closedRef.current) break;
    }
    queueRunningRef.current = false;
    if (!llmStreamingRef.current && !closedRef.current) {
      setPhase('listening');
    }
  };

  const clearHoldTimers = () => {
    for (const t of holdTimersRef.current) clearTimeout(t);
    holdTimersRef.current = [];
  };

  const enqueueSpeech = async (rawSentence: string) => {
    const sentence = prepareForTTS(rawSentence);
    if (!sentence || closedRef.current) return;
    // First real TTS chunk: cancel any pending hold-phrase timers so they
    // don't speak on top of the actual response.
    if (!firstTTSFiredRef.current) {
      firstTTSFiredRef.current = true;
      clearHoldTimers();
    }
    try {
      const rawBlob = await api.synthesizeAudio(tenantId, sentence);
      // Overlay may have closed while the TTS request was in flight.
      if (closedRef.current) return;
      // Force MIME type — chunked streaming responses sometimes leave blob.type
      // empty, causing the browser to fail format sniffing and emit onerror.
      const blob = rawBlob.type ? rawBlob : new Blob([rawBlob], { type: 'audio/mpeg' });
      console.debug('[TTS] blob size=%d type=%s', blob.size, blob.type);
      if (blob.size === 0) {
        console.warn('[TTS] empty blob — TTS returned no audio');
        return;
      }
      const url = URL.createObjectURL(blob);
      urlsRef.current.push(url);
      const audio = new Audio(url);
      audio.preload = 'auto';
      audio.addEventListener('ended', () => {
        try { URL.revokeObjectURL(url); } catch { /* ignore */ }
        urlsRef.current = urlsRef.current.filter((u) => u !== url);
      });
      audioQueueRef.current.push(audio);
      void playQueueLoop();
    } catch (e) {
      console.warn('[TTS] chunk failed', e);
    }
  };

  /**
   * Core: send a transcribed phrase to LLM, stream response, TTS each sentence.
   * Called either by the WL debounce (fast path) or VAD fallback (slow path).
   */
  const submitLLM = async (userText: string) => {
    if (closedRef.current || !userText.trim()) return;
    // Cancel any pending WL debounce (idempotent guard)
    if (wlDebounceRef.current) { clearTimeout(wlDebounceRef.current); wlDebounceRef.current = null; }
    // Drop whatever was playing / streaming
    abortLLM();
    stopAudio();
    wlFinalTextRef.current = '';
    wlSTT.resetText();

    setTranscript(userText);
    setAssistantText('');
    setPhase('thinking');

    // Progressive hold phrases: fire if LLM is slow to produce the first TTS chunk.
    // Cancelled immediately when the first enqueueSpeech call happens.
    firstTTSFiredRef.current = false;
    clearHoldTimers();
    // Randomized hold phrases — vary on every invocation so the user doesn't
    // always hear the same filler. Arrays are: [1.6 s, 4.5 s, 8.5 s].
    const HOLD_DELAYS = [1600, 4500, 8500] as const;
    const HOLD_VARIANTS = [
      ['Одну секунду...', 'Секунду...', 'Подождите немного...', 'Сейчас посмотрю...', 'Минуточку...'],
      ['Обрабатываю запрос...', 'Анализирую...', 'Думаю...', 'Ищу информацию...', 'Собираю данные...'],
      ['Это займёт немного больше времени...', 'Почти готово...', 'Ещё секунду...', 'Запрос сложный, анализирую...'],
    ] as const;
    for (let _hi = 0; _hi < HOLD_DELAYS.length; _hi++) {
      const _vars = HOLD_VARIANTS[_hi];
      const _phrase = _vars[Math.floor(Math.random() * _vars.length)];
      holdTimersRef.current.push(
        setTimeout(() => {
          if (!firstTTSFiredRef.current && !closedRef.current) {
            void enqueueSpeech(_phrase);
          }
        }, HOLD_DELAYS[_hi]),
      );
    }

    let buffer = '';
    let splitOffset = 0;
    llmStreamingRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await api.sendMessageStream(
        tenantId,
        chatId,
        { content: userText, voice_mode: true },
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
            // Update the display with the server's cleaned-up final content, but
            // do NOT replace `buffer` — doing so would corrupt `splitOffset` which
            // was computed against the streamed chunks. TTS uses `buffer` in the
            // `final` event below to emit the unsent tail.
            setAssistantText(payload.content as string);
          } else if (eventType === 'final') {
            const remaining = buffer.slice(splitOffset).trim();
            if (remaining) void enqueueSpeech(remaining);
          } else if (eventType === 'error' && typeof payload.message === 'string') {
            notifications.show({ title: 'LLM', message: String(payload.message), color: 'red' });
          }
        },
        controller.signal,
      );
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

  // WhisperLive: drives the fast path.
  // onPartial → show live transcript, cancel any pending debounce (user still speaking).
  // onFinal   → start 350ms debounce; if no new partial arrives, submit to LLM.
  const wlSTT = useWhisperLiveSTT({
    proxyUrl: wlProxyUrl,
    language: 'ru',
    onPartial: (text) => {
      setTranscript(text);
      // New speech coming in — cancel premature submit
      if (wlDebounceRef.current) { clearTimeout(wlDebounceRef.current); wlDebounceRef.current = null; }
    },
    onFinal: (text) => {
      wlFinalTextRef.current = text;
      // Guard: don't trigger a new turn while LLM/TTS is still running
      if (llmStreamingRef.current || queueRunningRef.current) return;
      if (wlDebounceRef.current) clearTimeout(wlDebounceRef.current);
      wlDebounceRef.current = setTimeout(() => {
        wlDebounceRef.current = null;
        const t = wlFinalTextRef.current.trim();
        if (t) void submitLLM(t);
      }, 350);
    },
  });

  // VAD: slow-path fallback + barge-in detector.
  // onSegment fires at silenceMs — by then WL has usually already submitted.
  // We only do work here if WL debounce is still pending (fire it early) or
  // WL delivered nothing (fall back to batch STT).
  const vad = useVAD({
    silenceMs: 900,
    onSegment: async (blob) => {
      // Case 1: WL debounce is pending → fire immediately instead of waiting
      if (wlDebounceRef.current) {
        clearTimeout(wlDebounceRef.current);
        wlDebounceRef.current = null;
        const t = wlFinalTextRef.current.trim();
        if (t) { void submitLLM(t); return; }
      }
      // Case 2: submitLLM already running (WL fired faster) → ignore
      if (llmStreamingRef.current) return;
      // Case 3: WL has text but debounce already resolved → shouldn't happen, guard anyway
      const t = wlFinalTextRef.current.trim();
      if (t) { void submitLLM(t); return; }
      // Case 4: WL delivered nothing → batch STT fallback
      setPhase('transcribing');
      try {
        const { text } = await api.transcribeAudio(tenantId, blob);
        const ut = (text || '').trim();
        if (ut) void submitLLM(ut);
        else setPhase('listening');
      } catch (e) {
        setPhase('listening');
        notifications.show({ title: 'STT', message: (e as Error).message || '', color: 'red' });
      }
    },
    // INTERRUPT: user starts speaking → kill TTS + LLM immediately.
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
    void wlSTT.start();
    return () => {
      vad.stop();
      wlSTT.stop();
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
    wlSTT.stop();
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
      <Stack gap="lg" align="center" style={{ color: 'white', textAlign: 'center', width: 'min(92vw, 960px)', padding: '24px 32px' }}>
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

        <Text size="xs" c="dimmed">
          Просто говорите — распознавание в реальном времени.
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
          <Box style={{ width: '100%', textAlign: 'left' }}>
            <Text size="xs" c="dimmed">Вы сказали:</Text>
            <Text size="sm" c="white">{transcript}</Text>
          </Box>
        )}
        {assistantText && (
          <Box style={{ width: '100%' }}>
            <Text size="xs" c="dimmed" mb={4}>Ассистент:</Text>
            <ScrollArea.Autosize mah="58vh" type="hover" scrollbarSize={6}>
              <Box
                style={{
                  background: 'rgba(255,255,255,0.06)',
                  borderRadius: 8,
                  padding: '10px 16px',
                  textAlign: 'left',
                }}
              >
                <MarkdownContent content={assistantText} color="white" linkColor="rgba(255,255,255,0.8)" />
              </Box>
            </ScrollArea.Autosize>
          </Box>
        )}
      </Stack>
    </Box>
  );
}
