import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Stack, Text, ActionIcon, Group, Tooltip, Loader } from '@mantine/core';
import { IconMicrophone, IconPlayerStop, IconX, IconVolume } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMediaRecorder, getAiChatApi } from '../../packages/ai-chat-core';
import type { AuthMode } from '../../packages/ai-chat-core';

type Phase = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error';

type Props = {
  tenantId: string;
  apiBase: string;
  mode: 'admin' | 'end-user';
  apiKey?: string;
  authBearer?: string;
  /** Called with the recognized user text — caller sends it through normal
   *  chat send pipeline and resolves the returned promise with assistant text
   *  so we can speak it back and resume listening. */
  onSend: (text: string) => Promise<string>;
  onClose: () => void;
};

/**
 * VoiceModeOverlay — full-screen "phone call" UX:
 *   1. listen → mic open while user holds it (or auto-VAD).
 *   2. transcribe → STT → user text.
 *   3. thinking → onSend() goes through normal chat pipeline.
 *   4. speak → TTS plays the answer aloud.
 *   5. back to (1).
 *
 * Implementation choice: PUSH-TO-TALK (not always-on VAD). It's predictable
 * across browsers and avoids the auto-cut-off problem when users pause mid-
 * sentence. A future iteration can swap in a VAD lib if needed.
 */
export function VoiceModeOverlay({
  tenantId, apiBase, mode, apiKey, authBearer, onSend, onClose,
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
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  // Map recorder state to phase
  useEffect(() => {
    if (recorder.state === 'recording') setPhase('listening');
    else if (recorder.state === 'error') setPhase('error');
  }, [recorder.state]);

  useEffect(() => {
    return () => {
      // Cleanup audio on unmount
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null; }
  };

  const handleMicTap = async () => {
    if (phase === 'speaking') {
      // Interrupt TTS, go back to listening immediately.
      stopAudio();
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
        setPhase('thinking');
        const answer = await onSend(clean);
        setAssistantText(answer);
        // Speak it back
        if (answer.trim()) {
          setPhase('speaking');
          const audioBlob = await api.synthesizeAudio(tenantId, answer);
          const url = URL.createObjectURL(audioBlob);
          urlRef.current = url;
          const audio = new Audio(url);
          audioRef.current = audio;
          audio.onended = () => {
            stopAudio();
            setPhase('idle');
            // Auto-restart listening so user can keep the conversation flowing.
            void recorder.start();
          };
          audio.onerror = () => {
            stopAudio();
            setPhase('idle');
          };
          await audio.play();
        } else {
          setPhase('idle');
        }
      } catch (e) {
        setPhase('error');
        notifications.show({ title: 'Voice', message: (e as Error).message || '', color: 'red' });
      }
      return;
    }
    // idle / error → start listening
    await recorder.start();
  };

  const closeAll = () => {
    if (recorder.state === 'recording') recorder.cancel();
    stopAudio();
    onClose();
  };

  const phaseLabel = {
    idle: 'Нажми, чтобы говорить',
    listening: 'Слушаю…',
    transcribing: 'Распознаю…',
    thinking: 'Думаю над ответом…',
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

        {/* Big mic button */}
        <Box style={{ position: 'relative' }}>
          <ActionIcon
            variant="filled"
            color={isListening ? 'red' : phase === 'speaking' ? 'blue' : 'gray'}
            size={120}
            radius={9999}
            onClick={handleMicTap}
            disabled={phase === 'transcribing' || phase === 'thinking'}
            style={{
              boxShadow: isListening
                ? `0 0 0 ${4 + Math.round(recorder.level * 24)}px rgba(255, 75, 75, 0.35)`
                : '0 8px 20px rgba(0,0,0,0.4)',
              transition: 'box-shadow 60ms linear',
            }}
            aria-label="Микрофон"
          >
            {phase === 'transcribing' || phase === 'thinking' ? <Loader size={42} color="white" /> :
             isListening ? <IconPlayerStop size={56} /> :
             <IconMicrophone size={56} />}
          </ActionIcon>
        </Box>

        {/* Last transcript + last answer for context */}
        {transcript && (
          <Box>
            <Text size="xs" c="dimmed">Вы сказали:</Text>
            <Text size="sm" c="white">{transcript}</Text>
          </Box>
        )}
        {assistantText && (
          <Box>
            <Text size="xs" c="dimmed">Ассистент:</Text>
            <Text size="sm" c="white" lineClamp={4}>{assistantText}</Text>
          </Box>
        )}
      </Stack>
    </Box>
  );
}
