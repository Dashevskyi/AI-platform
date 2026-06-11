import { useEffect, useMemo, useRef, useState } from 'react';
import { ActionIcon, Tooltip, Loader, Text, Group } from '@mantine/core';
import { IconMicrophone, IconMicrophoneOff } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useVAD, useWhisperLiveSTT, getAiChatApi } from '../../packages/ai-chat-core';
import type { AuthMode } from '../../packages/ai-chat-core';

type Props = {
  tenantId: string;
  apiBase: string;
  mode: 'admin' | 'end-user';
  apiKey?: string;
  authBearer?: string;
  disabled?: boolean;
  /** Final phrase append (VAD fallback path). */
  onTranscribed: (text: string) => void;
  /** Called when a mic session opens — parent snapshots current input as base. */
  onSessionStart?: () => void;
  /** Live full transcript (partials included) — parent renders base + this. */
  onLiveText?: (text: string) => void;
};

/**
 * MicButton — click toggles a mic session.
 *
 * Primary path: WhisperLive streaming STT — the transcript appears in the
 * input AS YOU SPEAK (partials), no waiting for a pause.
 *
 * Fallback path (WS unavailable): legacy VAD segmentation — phrase is
 * transcribed via /voice/stt after each pause and appended.
 */
export function MicButton({
  tenantId, apiBase, mode, apiKey, authBearer, disabled, onTranscribed,
  onSessionStart, onLiveText,
}: Props) {
  const api = useMemo(() => {
    const auth: AuthMode | undefined =
      mode === 'admin' ? (authBearer ? { type: 'bearer', token: authBearer } : undefined)
                      : (apiKey ? { type: 'apiKey', apiKey } : undefined);
    return getAiChatApi({ variant: mode === 'admin' ? 'admin' : 'tenant', apiBase, auth });
  }, [mode, apiBase, apiKey, authBearer]);

  // WhisperLive proxy URL (same auth scheme as VoiceModeOverlay)
  const wlProxyUrl = useMemo(() => {
    const base = `/api/tenants/${tenantId}/voice/stt-stream`;
    const params = new URLSearchParams();
    if (mode === 'admin' && authBearer) params.set('authorization', `Bearer ${authBearer}`);
    else if (apiKey) params.set('api_key', apiKey);
    const qs = params.toString();
    return qs ? `${base}?${qs}` : base;
  }, [tenantId, mode, apiKey, authBearer]);

  const [transcribingCount, setTranscribingCount] = useState(0);
  // 'wl' — streaming session, 'vad' — fallback session, null — closed
  const [session, setSession] = useState<'wl' | 'vad' | null>(null);
  const fellBackRef = useRef(false);

  // ── Fallback path: VAD + batch /stt ──────────────────────────────────────
  const transcribeSegment = async (blob: Blob) => {
    setTranscribingCount((c) => c + 1);
    try {
      const { text } = await api.transcribeAudio(tenantId, blob);
      if (text && text.trim()) onTranscribed(text.trim());
    } catch (e) {
      notifications.show({ title: 'STT', message: (e as Error).message || '', color: 'red' });
    } finally {
      setTranscribingCount((c) => Math.max(0, c - 1));
    }
  };

  const vad = useVAD({
    silenceMs: 1500,
    onSegment: (blob) => { void transcribeSegment(blob); },
  });

  // ── Primary path: WhisperLive streaming ─────────────────────────────────
  const wlSTT = useWhisperLiveSTT({
    proxyUrl: wlProxyUrl,
    language: 'ru',
    onPartial: (text) => { onLiveText?.(text); },
    onFinal: (text) => { onLiveText?.(text); },
  });

  // WS failed → fall back to VAD once, transparently.
  useEffect(() => {
    if (session === 'wl' && wlSTT.state === 'error' && !fellBackRef.current) {
      fellBackRef.current = true;
      notifications.show({
        title: 'Микрофон',
        message: 'Стриминг недоступен — перешёл в режим распознавания по фразам.',
        color: 'yellow',
      });
      setSession('vad');
      void vad.start();
    }
  }, [session, wlSTT.state]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (vad.state === 'error' && vad.error) {
      notifications.show({ title: 'Микрофон', message: vad.error, color: 'red' });
    }
  }, [vad.state, vad.error]);

  const wlOpen = session === 'wl' && (wlSTT.state === 'connecting' || wlSTT.state === 'ready' || wlSTT.state === 'streaming');
  const vadOpen = session === 'vad' && (vad.state === 'listening' || vad.state === 'speaking');
  const isOpen = wlOpen || vadOpen;
  const isSpeaking = vadOpen && vad.state === 'speaking';
  const isConnecting = session === 'wl' && wlSTT.state === 'connecting';

  const toggle = async () => {
    if (isOpen) {
      if (session === 'wl') wlSTT.stop();
      else vad.stop();
      setSession(null);
      return;
    }
    fellBackRef.current = false;
    onSessionStart?.();
    wlSTT.resetText();
    setSession('wl');
    try {
      await wlSTT.start();
    } catch {
      // hook reports via state==='error' → fallback effect handles it
    }
  };

  return (
    <Group gap={4} style={{ alignItems: 'flex-end', marginBottom: 4 }}>
      <Tooltip
        label={
          isOpen
            ? (session === 'wl'
                ? 'Стриминг включён — текст появляется по мере речи. Клик — выключить.'
                : 'Микрофон включён — говорите. Пауза = конец фразы. Клик — выключить.')
            : disabled ? 'Микрофон недоступен' : 'Голосовой ввод (реал-тайм распознавание)'
        }
      >
        <ActionIcon
          variant={isOpen ? 'filled' : 'light'}
          color={isSpeaking ? 'red' : isOpen ? 'green' : undefined}
          size="lg"
          onClick={toggle}
          disabled={disabled || isConnecting || vad.state === 'requesting'}
          style={{
            alignSelf: 'flex-end',
            boxShadow: isSpeaking
              ? `0 0 0 ${1 + Math.round(vad.level * 8)}px rgba(255, 75, 75, 0.35)`
              : wlOpen && wlSTT.state === 'streaming'
                ? '0 0 0 2px rgba(64, 192, 87, 0.35)'
                : undefined,
            transition: 'box-shadow 60ms linear',
          }}
          aria-label="Микрофон"
        >
          {isConnecting || vad.state === 'requesting' ? <Loader size={14} /> :
           vad.state === 'error' && session === 'vad' ? <IconMicrophoneOff size={18} /> :
           <IconMicrophone size={18} />}
        </ActionIcon>
      </Tooltip>
      {isOpen && session === 'wl' && wlSTT.state === 'streaming' && (
        <Text size="xs" c="green" style={{ marginBottom: 6 }}>● live</Text>
      )}
      {vadOpen && transcribingCount > 0 && (
        <Text size="xs" c="dimmed" style={{ marginBottom: 6 }}>обработка…</Text>
      )}
      {vadOpen && transcribingCount === 0 && (
        <Text size="xs" c={isSpeaking ? 'red' : 'green'} style={{ marginBottom: 6 }}>
          {isSpeaking ? '🎙' : '●'}
        </Text>
      )}
    </Group>
  );
}
