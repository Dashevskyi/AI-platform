/**
 * useWhisperLiveSTT — streaming speech-to-text via collabora/WhisperLive WebSocket.
 *
 * Connects to the backend proxy at /api/tenants/{tid}/voice/stt-stream,
 * captures 16 kHz mono PCM from the microphone via AudioContext,
 * streams it to WhisperLive and emits partial + final transcript callbacks.
 *
 * Protocol summary (collabora/WhisperLive):
 *   1. Connect WebSocket
 *   2. Send JSON config: { uid, language, task, model, use_vad }
 *   3. Send Float32Array chunks (16 kHz, mono) as binary frames
 *   4. Receive JSON: { uid, segments: [{ text, start, end, completed }], language }
 *      or           { uid, message: "SERVER_READY" | "WAIT" | "DISCONNECT" }
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export type WhisperLiveState = 'idle' | 'connecting' | 'ready' | 'streaming' | 'error';

export type WhisperLiveSegment = {
  text: string;
  start: number;
  end: number;
  completed: boolean;
};

export type UseWhisperLiveOptions = {
  /** Backend proxy URL, e.g. "/api/tenants/{tid}/voice/stt-stream?api_key=xxx" */
  proxyUrl: string;
  /** BCP-47 language code passed to WhisperLive. */
  language?: string;
  /** Called whenever WhisperLive sends an updated transcript (may repeat). */
  onPartial?: (text: string) => void;
  /**
   * Called when WhisperLive marks a phrase as fully completed (all segments
   * have completed=true AND we've seen a pause in new text).
   */
  onFinal?: (text: string) => void;
};

export type UseWhisperLiveResult = {
  state: WhisperLiveState;
  error: string | null;
  currentText: string;
  /** Open mic + WS, start streaming. */
  start: () => Promise<void>;
  /** Stop streaming, close WS and mic. */
  stop: () => void;
  /** Reset transcript text (call when starting a new phrase). */
  resetText: () => void;
};

const TARGET_SAMPLE_RATE = 16000;
const CHUNK_INTERVAL_MS = 250; // send a PCM chunk every 250ms

export function useWhisperLiveSTT(options: UseWhisperLiveOptions): UseWhisperLiveResult {
  const { proxyUrl, language = 'ru', onPartial, onFinal } = options;

  const [state, setState] = useState<WhisperLiveState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [currentText, setCurrentText] = useState('');

  const wsRef = useRef<WebSocket | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const accBufRef = useRef<Float32Array[]>([]);
  const stoppedRef = useRef(false);
  const sendIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Stable callback refs
  const onPartialRef = useRef(onPartial);
  const onFinalRef = useRef(onFinal);
  useEffect(() => { onPartialRef.current = onPartial; }, [onPartial]);
  useEffect(() => { onFinalRef.current = onFinal; }, [onFinal]);

  // Track last-completed phrase to detect new completions
  const lastCompletedTextRef = useRef('');

  const cleanup = useCallback(() => {
    stoppedRef.current = true;
    if (sendIntervalRef.current !== null) {
      clearInterval(sendIntervalRef.current);
      sendIntervalRef.current = null;
    }
    if (processorRef.current) {
      try { processorRef.current.disconnect(); } catch { /* ignore */ }
      processorRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (ctxRef.current && ctxRef.current.state !== 'closed') {
      ctxRef.current.close().catch(() => { /* ignore */ });
      ctxRef.current = null;
    }
    if (wsRef.current) {
      try { wsRef.current.close(); } catch { /* ignore */ }
      wsRef.current = null;
    }
    accBufRef.current = [];
  }, []);

  useEffect(() => () => { cleanup(); }, [cleanup]);

  /** Downsample a Float32Array from srcRate to 16000.
   *
   * Averages each source window (box low-pass) instead of picking the nearest
   * sample — naive decimation aliases high frequencies into the speech band
   * and audibly degrades recognition on 48k/44.1k contexts (Firefox always,
   * Chrome with some devices/bluetooth headsets). */
  function downsample(buf: Float32Array, srcRate: number): Float32Array {
    if (srcRate === TARGET_SAMPLE_RATE) return buf;
    const ratio = srcRate / TARGET_SAMPLE_RATE;
    const outLen = Math.floor(buf.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.min(buf.length, Math.max(start + 1, Math.floor((i + 1) * ratio)));
      let sum = 0;
      for (let j = start; j < end; j++) sum += buf[j];
      out[i] = sum / (end - start);
    }
    return out;
  }

  const handleWsMessage = useCallback((event: MessageEvent) => {
    if (typeof event.data !== 'string') return;
    let msg: Record<string, unknown>;
    try { msg = JSON.parse(event.data); } catch { return; }

    if (msg.message === 'SERVER_READY') {
      setState('streaming');
      return;
    }
    if (msg.message === 'WAIT') return;
    if (msg.message === 'DISCONNECT') {
      cleanup();
      setState('idle');
      return;
    }

    const segments = (msg.segments ?? []) as WhisperLiveSegment[];
    if (!segments.length) return;

    const fullText = segments.map((s) => s.text).join(' ').trim();
    if (!fullText) return;

    setCurrentText(fullText);
    onPartialRef.current?.(fullText);

    // Fire onFinal when all segments are completed and text changed since last final
    const allCompleted = segments.every((s) => s.completed);
    if (allCompleted && fullText !== lastCompletedTextRef.current) {
      lastCompletedTextRef.current = fullText;
      onFinalRef.current?.(fullText);
    }
  }, [cleanup]);

  const start = useCallback(async () => {
    if (state === 'connecting' || state === 'ready' || state === 'streaming') return;
    setError(null);
    setCurrentText('');
    lastCompletedTextRef.current = '';
    stoppedRef.current = false;
    setState('connecting');

    // 1. Open WebSocket
    const wsUrl = proxyUrl.startsWith('/')
      ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}${proxyUrl}`
      : proxyUrl;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.binaryType = 'arraybuffer';

    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error('WebSocket connection failed'));
      ws.onclose = (e) => { if (e.code !== 1000 && e.code < 4000) reject(new Error(`WS closed: ${e.code}`)); };
    }).catch((e) => {
      setError((e as Error).message);
      setState('error');
      cleanup();
      throw e;
    });

    // 2. Send config
    const uid = crypto.randomUUID();
    ws.send(JSON.stringify({
      uid,
      language,
      task: 'transcribe',
      model: 'large-v3',
      use_vad: true,
    }));

    ws.onmessage = handleWsMessage;
    ws.onclose = (e) => {
      if (!stoppedRef.current) {
        setError(`Connection closed (${e.code})`);
        setState('error');
      }
      cleanup();
    };
    ws.onerror = () => {
      if (!stoppedRef.current) {
        setError('WebSocket error');
        setState('error');
      }
      cleanup();
    };

    // 3. Open microphone + AudioContext at 16 kHz
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
    } catch (e) {
      setError((e as Error).message || 'Mic access denied');
      setState('error');
      cleanup();
      return;
    }
    streamRef.current = stream;

    // Try native 16 kHz first; some browsers don't support it — fall back.
    let ctx: AudioContext;
    try {
      ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      if (ctx.sampleRate !== TARGET_SAMPLE_RATE) {
        console.info('[WL-STT] AudioContext runs at %d Hz — downsampling to 16k', ctx.sampleRate);
      }
    } catch {
      ctx = new AudioContext();
    }
    ctxRef.current = ctx;

    const src = ctx.createMediaStreamSource(stream);
    // ScriptProcessorNode is deprecated but universally supported.
    // bufferSize=4096 @ 16kHz = 256ms per callback — fine for our use.
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    const proc = ctx.createScriptProcessor(4096, 1, 1);
    processorRef.current = proc;

    proc.onaudioprocess = (e) => {
      if (stoppedRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return;
      const raw = e.inputBuffer.getChannelData(0);
      const pcm = downsample(raw, ctx.sampleRate);
      accBufRef.current.push(pcm.slice());
    };
    src.connect(proc);
    proc.connect(ctx.destination); // required — silence output

    // 4. Flush accumulated PCM to WS every CHUNK_INTERVAL_MS
    sendIntervalRef.current = setInterval(() => {
      if (stoppedRef.current || wsRef.current?.readyState !== WebSocket.OPEN) return;
      const chunks = accBufRef.current.splice(0);
      if (!chunks.length) return;
      // Concatenate all chunks into one Float32Array and send
      const totalLen = chunks.reduce((s, c) => s + c.length, 0);
      const merged = new Float32Array(totalLen);
      let offset = 0;
      for (const c of chunks) { merged.set(c, offset); offset += c.length; }
      wsRef.current.send(merged.buffer);
    }, CHUNK_INTERVAL_MS);

    setState('ready');
  }, [state, proxyUrl, language, handleWsMessage, cleanup]);

  const stop = useCallback(() => {
    stoppedRef.current = true;
    cleanup();
    setState('idle');
    setCurrentText('');
    lastCompletedTextRef.current = '';
  }, [cleanup]);

  const resetText = useCallback(() => {
    setCurrentText('');
    lastCompletedTextRef.current = '';
  }, []);

  return { state, error, currentText, start, stop, resetText };
}
