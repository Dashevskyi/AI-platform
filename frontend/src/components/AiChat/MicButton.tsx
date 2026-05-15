import { useEffect, useMemo, useState } from 'react';
import { ActionIcon, Tooltip, Loader, Text, Group } from '@mantine/core';
import { IconMicrophone, IconMicrophoneOff } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useVAD, getAiChatApi } from '../../packages/ai-chat-core';
import type { AuthMode } from '../../packages/ai-chat-core';

type Props = {
  tenantId: string;
  apiBase: string;
  mode: 'admin' | 'end-user';
  apiKey?: string;
  authBearer?: string;
  disabled?: boolean;
  onTranscribed: (text: string) => void;
};

/**
 * MicButton — click toggles a continuous mic session. While listening, the
 * VAD splits speech into phrases automatically (pause ≥ silenceMs ends one),
 * each phrase is transcribed via /voice/stt and appended to the input. No
 * stop button — user clicks the mic again to close the session.
 *
 * This is push-to-talk-free: speak, pause, speak again — each phrase shows
 * up in the input as a separate burst. Reduces friction vs hold-and-release.
 */
export function MicButton({
  tenantId, apiBase, mode, apiKey, authBearer, disabled, onTranscribed,
}: Props) {
  const api = useMemo(() => {
    const auth: AuthMode | undefined =
      mode === 'admin' ? (authBearer ? { type: 'bearer', token: authBearer } : undefined)
                      : (apiKey ? { type: 'apiKey', apiKey } : undefined);
    return getAiChatApi({ variant: mode === 'admin' ? 'admin' : 'tenant', apiBase, auth });
  }, [mode, apiBase, apiKey, authBearer]);

  const [transcribingCount, setTranscribingCount] = useState(0);

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

  useEffect(() => {
    if (vad.state === 'error' && vad.error) {
      notifications.show({ title: 'Микрофон', message: vad.error, color: 'red' });
    }
  }, [vad.state, vad.error]);

  const isOpen = vad.state === 'listening' || vad.state === 'speaking';
  const isSpeaking = vad.state === 'speaking';

  const toggle = async () => {
    if (isOpen) vad.stop();
    else await vad.start();
  };

  const seconds = Math.floor(transcribingCount > 0 ? 0 : 0); // placeholder for future timer
  void seconds;

  return (
    <Group gap={4} style={{ alignItems: 'flex-end', marginBottom: 4 }}>
      <Tooltip
        label={
          isOpen
            ? 'Микрофон включён — говорите. Паузы автоматически разделяют фразы. Клик ещё раз — выключить.'
            : disabled ? 'Микрофон недоступен' : 'Голосовой ввод (VAD: пауза = конец фразы)'
        }
      >
        <ActionIcon
          variant={isOpen ? 'filled' : 'light'}
          color={isSpeaking ? 'red' : isOpen ? 'blue' : undefined}
          size="lg"
          onClick={toggle}
          disabled={disabled || vad.state === 'requesting'}
          style={{
            alignSelf: 'flex-end',
            boxShadow: isSpeaking
              ? `0 0 0 ${1 + Math.round(vad.level * 8)}px rgba(255, 75, 75, 0.35)`
              : undefined,
            transition: 'box-shadow 60ms linear',
          }}
          aria-label="Микрофон"
        >
          {vad.state === 'requesting' ? <Loader size={14} /> :
           vad.state === 'error' ? <IconMicrophoneOff size={18} /> :
           <IconMicrophone size={18} />}
        </ActionIcon>
      </Tooltip>
      {isOpen && transcribingCount > 0 && (
        <Text size="xs" c="dimmed" style={{ marginBottom: 6 }}>
          обработка…
        </Text>
      )}
      {isOpen && transcribingCount === 0 && (
        <Text size="xs" c={isSpeaking ? 'red' : 'dimmed'} style={{ marginBottom: 6 }}>
          {isSpeaking ? '🎙' : '...'}
        </Text>
      )}
    </Group>
  );
}
