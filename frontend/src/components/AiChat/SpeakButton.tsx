import { useMemo, useRef, useState } from 'react';
import { ActionIcon, Tooltip, Loader } from '@mantine/core';
import { IconVolume, IconVolumeOff, IconPlayerStop } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { getAiChatApi } from '../../packages/ai-chat-core';
import type { AuthMode } from '../../packages/ai-chat-core';

type Props = {
  tenantId: string;
  apiBase: string;
  mode: 'admin' | 'end-user';
  apiKey?: string;
  authBearer?: string;
  text: string;
};

/** Per-message "🔊 Озвучить" button. Fetches mp3 from /voice/tts and plays it.
 *  Click again while playing → stops. Cleanup revokes the object URL. */
export function SpeakButton({ tenantId, apiBase, mode, apiKey, authBearer, text }: Props) {
  const api = useMemo(() => {
    const auth: AuthMode | undefined =
      mode === 'admin' ? (authBearer ? { type: 'bearer', token: authBearer } : undefined)
                      : (apiKey ? { type: 'apiKey', apiKey } : undefined);
    return getAiChatApi({ variant: mode === 'admin' ? 'admin' : 'tenant', apiBase, auth });
  }, [mode, apiBase, apiKey, authBearer]);

  const [loading, setLoading] = useState(false);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  const stop = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setPlaying(false);
  };

  const handleClick = async () => {
    if (playing) { stop(); return; }
    if (!text || !text.trim()) return;
    setLoading(true);
    try {
      const blob = await api.synthesizeAudio(tenantId, text);
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => { setPlaying(false); if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null; } };
      audio.onerror = () => { setPlaying(false); notifications.show({ title: 'Ошибка воспроизведения', message: '', color: 'red' }); };
      await audio.play();
      setPlaying(true);
    } catch (e) {
      notifications.show({ title: 'TTS', message: (e as Error).message || 'не удалось', color: 'red' });
    } finally {
      setLoading(false);
    }
  };

  if (!text || !text.trim()) return null;

  return (
    <Tooltip label={playing ? 'Остановить озвучку' : 'Озвучить'}>
      <ActionIcon
        variant="subtle"
        color={playing ? 'red' : 'gray'}
        size="sm"
        onClick={handleClick}
        disabled={loading}
        aria-label="Озвучить ответ"
      >
        {loading ? <Loader size={12} /> :
         playing ? <IconPlayerStop size={14} /> :
         <IconVolume size={14} />}
      </ActionIcon>
    </Tooltip>
  );
}

export { IconVolumeOff }; // re-export for convenience
