import { useCallback, useEffect, useRef, useState } from 'react';

export type RecorderState = 'idle' | 'requesting' | 'recording' | 'stopping' | 'error';

export type UseMediaRecorderResult = {
  state: RecorderState;
  error: string | null;
  start: () => Promise<void>;
  stop: () => Promise<Blob | null>;
  cancel: () => void;
  durationMs: number;
  /** Approx 0..1 microphone level for the UI to draw a meter. */
  level: number;
};

/**
 * Browser MediaRecorder wrapper. Hides MIME-type negotiation (webm/opus on
 * Chrome/Firefox, mp4/aac on Safari) and adds a poor-man's level meter via
 * AnalyserNode RMS so the UI can show the user "yes, mic is picking you up".
 */
export function useMediaRecorder(): UseMediaRecorderResult {
  const [state, setState] = useState<RecorderState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [durationMs, setDurationMs] = useState(0);
  const [level, setLevel] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const startedAtRef = useRef<number>(0);
  const tickRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const stopResolverRef = useRef<((b: Blob | null) => void) | null>(null);

  const cleanup = useCallback(() => {
    if (tickRef.current !== null) {
      cancelAnimationFrame(tickRef.current);
      tickRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      audioCtxRef.current.close().catch(() => { /* ignore */ });
    }
    audioCtxRef.current = null;
    analyserRef.current = null;
    mediaRecorderRef.current = null;
    setLevel(0);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const pickMimeType = (): string => {
    if (typeof MediaRecorder === 'undefined') return '';
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
      'audio/aac',
    ];
    for (const m of candidates) {
      if ((MediaRecorder as { isTypeSupported?: (t: string) => boolean }).isTypeSupported?.(m)) {
        return m;
      }
    }
    return '';
  };

  const start = useCallback(async () => {
    if (state === 'recording' || state === 'requesting') return;
    setError(null);
    setDurationMs(0);
    setState('requesting');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Level meter via Web Audio API — independent from MediaRecorder.
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      src.connect(analyser);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;

      const buf = new Uint8Array(analyser.fftSize);
      const tick = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        setLevel(Math.min(1, rms * 4)); // scale 0..1 with some boost
        setDurationMs(Date.now() - startedAtRef.current);
        tickRef.current = requestAnimationFrame(tick);
      };

      chunksRef.current = [];
      const mime = pickMimeType();
      const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mediaRecorderRef.current = mr;
      mr.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || mime || 'audio/webm' });
        const resolver = stopResolverRef.current;
        stopResolverRef.current = null;
        cleanup();
        setState('idle');
        if (resolver) resolver(blob.size > 0 ? blob : null);
      };
      mr.onerror = (ev) => {
        const message = (ev as unknown as { error?: { message?: string } }).error?.message || 'MediaRecorder error';
        setError(message);
        setState('error');
        cleanup();
      };

      startedAtRef.current = Date.now();
      mr.start(250); // 250ms timeslice for periodic dataavailable
      tick();
      setState('recording');
    } catch (e) {
      const msg = (e as Error).message || 'Не удалось получить микрофон';
      setError(msg);
      setState('error');
      cleanup();
    }
  }, [state, cleanup]);

  const stop = useCallback(async (): Promise<Blob | null> => {
    if (state !== 'recording' && state !== 'stopping') return null;
    setState('stopping');
    return new Promise<Blob | null>((resolve) => {
      stopResolverRef.current = resolve;
      try {
        mediaRecorderRef.current?.stop();
      } catch {
        cleanup();
        setState('idle');
        resolve(null);
      }
    });
  }, [state, cleanup]);

  const cancel = useCallback(() => {
    try {
      mediaRecorderRef.current?.stop();
    } catch { /* ignore */ }
    stopResolverRef.current?.(null);
    stopResolverRef.current = null;
    chunksRef.current = [];
    cleanup();
    setState('idle');
  }, [cleanup]);

  return { state, error, start, stop, cancel, durationMs, level };
}
