import { useState, useMemo, useEffect } from 'react';
import { ActionIcon, Tooltip, Loader, Text } from '@mantine/core';
import { IconMicrophone, IconMicrophoneOff, IconPlayerStop } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMediaRecorder, getAiChatApi } from '../../packages/ai-chat-core';
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
 * MicButton — hold-to-record + auto-transcribe.
 *
 * Click toggles recording. The duration counter and a tiny level dot show
 * the user that the mic is live. Click again → stops → posts the blob to
 * `/voice/stt` → calls `onTranscribed(text)`.
 *
 * On error (denied permission, browser unsupported) a notification appears
 * and the button reverts to idle.
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

  const recorder = useMediaRecorder();
  const [transcribing, setTranscribing] = useState(false);

  // Surface a notification on recorder errors so the user knows it died.
  useEffect(() => {
    if (recorder.state === 'error' && recorder.error) {
      notifications.show({
        title: 'Микрофон',
        message: recorder.error,
        color: 'red',
      });
    }
  }, [recorder.state, recorder.error]);

  const isRecording = recorder.state === 'recording';
  const isBusy = transcribing || recorder.state === 'requesting' || recorder.state === 'stopping';

  const handleClick = async () => {
    if (isRecording) {
      const blob = await recorder.stop();
      if (!blob) return;
      setTranscribing(true);
      try {
        const { text } = await api.transcribeAudio(tenantId, blob);
        if (text.trim()) {
          onTranscribed(text.trim());
        } else {
          notifications.show({ title: 'Микрофон', message: 'Ничего не распознано', color: 'gray' });
        }
      } catch (e) {
        notifications.show({ title: 'STT ошибка', message: (e as Error).message || '', color: 'red' });
      } finally {
        setTranscribing(false);
      }
    } else {
      await recorder.start();
    }
  };

  const seconds = Math.floor(recorder.durationMs / 1000);
  const mins = Math.floor(seconds / 60);
  const ss = (seconds % 60).toString().padStart(2, '0');
  const timeLabel = `${mins}:${ss}`;

  // While recording — show stop icon + a level-pulsing red border via box-shadow.
  const recordingStyle = isRecording
    ? { boxShadow: `0 0 0 ${1 + Math.round(recorder.level * 8)}px rgba(255, 75, 75, 0.35)` }
    : undefined;

  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, marginBottom: 4 }}>
      <Tooltip
        label={isRecording ? `Остановить запись (${timeLabel})` :
               disabled ? 'Микрофон недоступен' : 'Записать голосовое сообщение'}
      >
        <ActionIcon
          variant={isRecording ? 'filled' : 'light'}
          color={isRecording ? 'red' : undefined}
          size="lg"
          onClick={handleClick}
          disabled={disabled || isBusy}
          style={{ alignSelf: 'flex-end', ...recordingStyle, transition: 'box-shadow 60ms linear' }}
          aria-label="Микрофон"
        >
          {isBusy ? <Loader size={14} /> :
           isRecording ? <IconPlayerStop size={18} /> :
           recorder.state === 'error' ? <IconMicrophoneOff size={18} /> :
           <IconMicrophone size={18} />}
        </ActionIcon>
      </Tooltip>
      {isRecording && (
        <Text size="xs" c="red" style={{ minWidth: 36, marginBottom: 6 }}>{timeLabel}</Text>
      )}
    </div>
  );
}
