import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Voice Activity Detection on top of MediaRecorder.
 *
 * The mic stays open continuously while `active` is true. An AnalyserNode
 * samples RMS level ~every 50 ms. A small state machine emits a Blob
 * segment each time the user finishes a phrase:
 *
 *    SILENT → (level > start_threshold for >= activationMs) → SPEECH
 *    SPEECH → (level < end_threshold for >= silenceMs)       → emit segment, SILENT
 *
 * Why a custom RMS state-machine instead of silero-vad-web:
 *   • zero extra deps and no WASM round-trip
 *   • the level we already measure for the UI meter is enough at chat
 *     latencies — wrong cuts are rare and Whisper tolerates 200ms of
 *     trailing silence in either direction.
 *
 * Concurrency model: MediaRecorder runs with a fixed timeslice so we get
 * frequent ondataavailable chunks; we keep them in a rolling array with
 * timestamps and slice out the right window on segment emit.
 */
export type VadState = 'idle' | 'requesting' | 'listening' | 'speaking' | 'error';

export type UseVADOptions = {
  /** Level (0..1) above which we declare 'speech started' on a rising edge. */
  startLevel?: number;
  /** Level (0..1) below which we declare 'speech ended' on a falling edge. */
  endLevel?: number;
  /** Sustain time at startLevel before declaring speech. Filters short noises. */
  activationMs?: number;
  /** Sustain time below endLevel before emitting a segment. The "pause threshold". */
  silenceMs?: number;
  /** Bytes-per-second a segment must reach to be emitted (filters out clicks). */
  minSegmentMs?: number;
  /** Called with the final audio blob when a speech segment is recognised. */
  onSegment?: (blob: Blob) => void;
  /** Called when we transition to/from speech — useful for UI ducking. */
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
};

export type UseVADResult = {
  state: VadState;
  error: string | null;
  start: () => Promise<void>;
  stop: () => void;
  /** Last measured 0..1 mic level — drive a meter. */
  level: number;
};

const DEFAULTS = {
  startLevel: 0.06,
  endLevel: 0.04,
  activationMs: 120,
  silenceMs: 1500,
  minSegmentMs: 300,
};

export function useVAD(options: UseVADOptions = {}): UseVADResult {
  const opts = { ...DEFAULTS, ...options };
  const [state, setState] = useState<VadState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);

  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const rafRef = useRef<number | null>(null);

  // Each chunk lives in the ring with the wall-clock time it was added.
  // On segment emit we slice by speech-start ts.
  type Chunk = { ts: number; data: Blob };
  const chunksRef = useRef<Chunk[]>([]);
  // The very FIRST chunk MediaRecorder emits carries the container init
  // (EBML headers for WebM, ftyp/moov for MP4). Subsequent chunks are
  // media-only. We MUST prepend it to every segment blob — Whisper rejects
  // a header-less blob with HTTP 500.
  const initChunkRef = useRef<Blob | null>(null);

  // VAD state.
  const speechActiveRef = useRef(false);
  const speechStartTsRef = useRef<number>(0);
  const lastAboveRef = useRef<number>(0);    // last time we observed level > end
  const lastBelowRef = useRef<number>(0);    // last time we observed level < start (for activation)
  const stoppedRef = useRef(false);
  const mimeRef = useRef<string>('audio/webm');

  // Stable callbacks across renders.
  const onSegmentRef = useRef(opts.onSegment);
  const onSpeechStartRef = useRef(opts.onSpeechStart);
  const onSpeechEndRef = useRef(opts.onSpeechEnd);
  useEffect(() => { onSegmentRef.current = options.onSegment; }, [options.onSegment]);
  useEffect(() => { onSpeechStartRef.current = options.onSpeechStart; }, [options.onSpeechStart]);
  useEffect(() => { onSpeechEndRef.current = options.onSpeechEnd; }, [options.onSpeechEnd]);

  const pickMime = (): string => {
    if (typeof MediaRecorder === 'undefined') return '';
    const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    for (const m of candidates) {
      if ((MediaRecorder as { isTypeSupported?: (t: string) => boolean }).isTypeSupported?.(m)) return m;
    }
    return '';
  };

  const cleanup = useCallback(() => {
    stoppedRef.current = true;
    if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    // Detach the dataavailable handler BEFORE calling .stop(). MediaRecorder
    // fires a final ondataavailable asynchronously when stopped — if that
    // landed in the next session's ring it would be mistaken for the init
    // chunk (it's not — it's the tail of the OLD session, no headers) and
    // Whisper would 500 on the first segment of the new session.
    const oldRec = recorderRef.current;
    if (oldRec) {
      try { oldRec.ondataavailable = null as unknown as (e: BlobEvent) => void; } catch { /* ignore */ }
      try { oldRec.stop(); } catch { /* ignore */ }
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (ctxRef.current && ctxRef.current.state !== 'closed') {
      ctxRef.current.close().catch(() => { /* ignore */ });
    }
    recorderRef.current = null;
    analyserRef.current = null;
    ctxRef.current = null;
    chunksRef.current = [];
    initChunkRef.current = null;
    speechActiveRef.current = false;
    setLevel(0);
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const emitSegment = useCallback((endTs: number) => {
    const startTs = speechStartTsRef.current;
    if (startTs <= 0) return;
    const durationMs = endTs - startTs;
    if (durationMs < opts.minSegmentMs) {
      // Too short — likely a false trigger. Drop it.
      speechActiveRef.current = false;
      speechStartTsRef.current = 0;
      return;
    }
    // Pull every chunk whose ts >= speechStart, plus the immediately preceding
    // one (so we don't clip the first 250 ms after MediaRecorder's timeslice).
    const ring = chunksRef.current;
    let firstIdx = ring.findIndex((c) => c.ts >= startTs);
    if (firstIdx === -1) firstIdx = ring.length - 1;
    if (firstIdx > 0) firstIdx -= 1;
    const slice = ring.slice(firstIdx).map((c) => c.data);
    // Trim the ring so it doesn't grow forever.
    chunksRef.current = ring.slice(-2);
    speechActiveRef.current = false;
    speechStartTsRef.current = 0;
    onSpeechEndRef.current?.();
    if (slice.length === 0) return;
    if (!initChunkRef.current) {
      // Init chunk hasn't arrived yet — emitting a header-less blob would
      // just guarantee a 500 from Whisper. Discard the phrase; the user
      // hears nothing about it and the next segment in this session will
      // have init available.
      return;
    }
    const parts: BlobPart[] = slice[0] !== initChunkRef.current
      ? [initChunkRef.current, ...slice]
      : slice;
    const blob = new Blob(parts, { type: mimeRef.current });
    if (blob.size > 0) onSegmentRef.current?.(blob);
  }, [opts.minSegmentMs]);

  const tick = useCallback(() => {
    if (stoppedRef.current || !analyserRef.current) return;
    const analyser = analyserRef.current;
    const buf = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / buf.length);
    const lvl = Math.min(1, rms * 4);
    setLevel(lvl);

    const now = performance.now();
    if (lvl >= opts.endLevel) lastAboveRef.current = now;
    if (lvl < opts.startLevel) lastBelowRef.current = now;

    if (!speechActiveRef.current) {
      // Looking for speech-start: level above startLevel sustained activationMs.
      if (lvl >= opts.startLevel) {
        if (lastBelowRef.current === 0) lastBelowRef.current = now; // first reading
        // We need a continuous run above startLevel for activationMs.
        // Trick: track lastBelowRef = last time we were *below* start; if it's
        // older than activationMs we have sustained speech.
        if (now - lastBelowRef.current >= opts.activationMs) {
          speechActiveRef.current = true;
          speechStartTsRef.current = now - opts.activationMs;
          setState('speaking');
          onSpeechStartRef.current?.();
        }
      }
    } else {
      // Looking for speech-end: level below endLevel sustained silenceMs.
      if (now - lastAboveRef.current >= opts.silenceMs) {
        emitSegment(now);
        setState('listening');
      }
    }
    rafRef.current = requestAnimationFrame(tick);
  }, [opts.startLevel, opts.endLevel, opts.activationMs, opts.silenceMs, emitSegment]);

  const start = useCallback(async () => {
    if (state === 'listening' || state === 'speaking' || state === 'requesting') return;
    setError(null);
    setState('requesting');
    stoppedRef.current = false;
    speechActiveRef.current = false;
    speechStartTsRef.current = 0;
    chunksRef.current = [];
    lastAboveRef.current = 0;
    lastBelowRef.current = 0;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      src.connect(analyser);
      ctxRef.current = ctx;
      analyserRef.current = analyser;

      const mime = pickMime();
      mimeRef.current = mime || 'audio/webm';
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorderRef.current = rec;
      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          // Capture the init chunk on first arrival — required for every
          // future segment blob (webm/mp4 wants the header in front).
          if (initChunkRef.current === null) {
            initChunkRef.current = e.data;
          }
          chunksRef.current.push({ ts: performance.now(), data: e.data });
          // Cap ring at ~30 s worth of chunks (250 ms × 120) so memory doesn't
          // grow during long silent stretches.
          if (chunksRef.current.length > 120) {
            chunksRef.current = chunksRef.current.slice(-120);
          }
        }
      };
      rec.start(250);

      setState('listening');
      rafRef.current = requestAnimationFrame(tick);
    } catch (e) {
      setError((e as Error).message || 'mic error');
      setState('error');
      cleanup();
    }
  }, [state, cleanup, tick]);

  const stop = useCallback(() => {
    if (speechActiveRef.current) {
      // Force-flush the current speech segment before tearing down.
      emitSegment(performance.now());
    }
    cleanup();
    setState('idle');
  }, [cleanup, emitSegment]);

  return { state, error, start, stop, level };
}
