import { useMemo, useRef, useState } from 'react';
import { ActionIcon, Tooltip, Loader } from '@mantine/core';
import { IconVolume, IconPlayerStop } from '@tabler/icons-react';
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

// ── Text sanitisation ────────────────────────────────────────────────────────
// Mirror what the backend _sanitize_for_tts() does so that tables / code are
// stripped BEFORE we split into sentences.  Doing it here avoids sending pipe
// characters or code fence lines as TTS input at all.

function sanitizeForSpeech(raw: string): string {
  let t = raw;
  // Remove markdown table blocks: any run of lines starting with "|"
  t = t.replace(/(?:^\|[^\n]*\n?)+/gm, '');
  // Remove fenced code blocks
  t = t.replace(/```[\s\S]*?```/g, ' ');
  // Strip inline markdown
  t = t.replace(/\*\*(.+?)\*\*/gs, '$1');
  t = t.replace(/__(.+?)__/gs, '$1');
  t = t.replace(/\*(.+?)\*/gs, '$1');
  t = t.replace(/_(.+?)_/gs, '$1');
  t = t.replace(/`([^`]+)`/g, '$1');
  // Collapse blank lines
  t = t.replace(/\n{3,}/g, '\n\n');
  return t.trim();
}

// ── Sentence splitter ────────────────────────────────────────────────────────
// Split on sentence-ending punctuation + whitespace, and on blank lines
// (paragraph breaks).  Merge tiny pieces; hard-cap at maxLen characters.

const MAX_CHUNK = 280;
const MIN_CHUNK = 30;

function splitSentences(text: string): string[] {
  // Split at sentence boundaries
  const raw = text.split(/(?<=[.!?…])\s+|\n{2,}/);
  const chunks: string[] = [];
  let cur = '';

  for (const p of raw) {
    const piece = p.trim();
    if (!piece) continue;

    // Hard-split oversized pieces at word boundary
    let remainder = piece;
    while (remainder.length > MAX_CHUNK) {
      const split = remainder.lastIndexOf(' ', MAX_CHUNK);
      const head = (split > 0 ? remainder.slice(0, split) : remainder.slice(0, MAX_CHUNK)).trim();
      remainder = (split > 0 ? remainder.slice(split + 1) : remainder.slice(MAX_CHUNK)).trim();
      if (cur) { chunks.push(cur); cur = ''; }
      chunks.push(head);
    }

    if (!remainder) continue;
    const candidate = cur ? `${cur} ${remainder}` : remainder;
    if (cur && candidate.length > MAX_CHUNK) {
      chunks.push(cur);
      cur = remainder;
    } else {
      cur = candidate;
    }
  }
  if (cur) chunks.push(cur);

  // Merge trailing stub into previous
  if (chunks.length >= 2 && chunks[chunks.length - 1].length < MIN_CHUNK) {
    chunks[chunks.length - 2] += ' ' + chunks.pop()!;
  }

  return chunks.length ? chunks : [text];
}

// ── Component ────────────────────────────────────────────────────────────────

/** Per-message "🔊 Озвучить" button.
 *
 *  Strategy for low-latency playback:
 *   1. Sanitize markdown (strip tables/code) and split into sentence chunks.
 *   2. Fetch TTS for sentence 1 immediately; start playing as soon as it arrives.
 *   3. While sentence N plays, fetch sentence N+1 in parallel (look-ahead).
 *   4. Queue them gaplessly.
 *
 *  This keeps perceived latency to the synthesis time of the FIRST short
 *  sentence (~1–2 s) rather than the whole response.
 */
export function SpeakButton({ tenantId, apiBase, mode, apiKey, authBearer, text }: Props) {
  const api = useMemo(() => {
    const auth: AuthMode | undefined =
      mode === 'admin' ? (authBearer ? { type: 'bearer', token: authBearer } : undefined)
                      : (apiKey ? { type: 'apiKey', apiKey } : undefined);
    return getAiChatApi({ variant: mode === 'admin' ? 'admin' : 'tenant', apiBase, auth });
  }, [mode, apiBase, apiKey, authBearer]);

  const [loading, setLoading]   = useState(false);
  const [playing, setPlaying]   = useState(false);

  // Refs that survive re-renders and are safe to read in async callbacks
  const stoppedRef   = useRef(false);
  const audioRef     = useRef<HTMLAudioElement | null>(null);
  const urlRef       = useRef<string | null>(null);

  const cleanup = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
  };

  const stop = () => {
    stoppedRef.current = true;
    cleanup();
    setPlaying(false);
    setLoading(false);
  };

  const playSentence = (url: string): Promise<void> =>
    new Promise((resolve) => {
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended  = () => resolve();
      audio.onerror  = () => resolve();
      audio.play().catch(() => resolve());
    });

  const handleClick = async () => {
    if (playing || loading) { stop(); return; }
    if (!text?.trim()) return;

    stoppedRef.current = false;
    setLoading(true);

    const clean     = sanitizeForSpeech(text);
    const sentences = splitSentences(clean);

    try {
      // Fetch sentences SEQUENTIALLY — one at a time so TTS GPU isn't split
      // between two requests simultaneously. Prefetch of sentence i+1 starts
      // only after sentence i's audio blob arrives AND starts playing, so the
      // synthesiser works on i+1 while the speaker reads i.
      let prefetchPromise: Promise<Blob> | null = null;

      for (let i = 0; i < sentences.length; i++) {
        if (stoppedRef.current) break;

        // Either use the blob we prefetched during previous playback, or fetch now.
        const blobPromise: Promise<Blob> =
          prefetchPromise ?? api.synthesizeAudio(tenantId, sentences[i]);
        prefetchPromise = null;

        const blob = await blobPromise;
        if (stoppedRef.current) break;

        const url = URL.createObjectURL(blob);
        urlRef.current = url;

        if (i === 0) {
          setLoading(false);
          setPlaying(true);
        }

        // Now that synthesis of i is done and playback is starting,
        // kick off synthesis of i+1 in parallel with playback.
        if (i + 1 < sentences.length) {
          prefetchPromise = api.synthesizeAudio(tenantId, sentences[i + 1]);
        }

        await playSentence(url);
        URL.revokeObjectURL(url);
        urlRef.current = null;
        audioRef.current = null;
      }
    } catch (e) {
      if (!stoppedRef.current) {
        notifications.show({
          title: 'TTS',
          message: (e as Error).message || 'не вдалося синтезувати',
          color: 'red',
        });
      }
    } finally {
      cleanup();
      setLoading(false);
      setPlaying(false);
    }
  };

  if (!text?.trim()) return null;

  return (
    <Tooltip label={playing ? 'Зупинити' : 'Озвучити'}>
      <ActionIcon
        variant="subtle"
        color={playing ? 'red' : 'gray'}
        size="sm"
        onClick={handleClick}
        aria-label="Озвучити відповідь"
      >
        {loading  ? <Loader size={12} /> :
         playing  ? <IconPlayerStop size={14} /> :
                    <IconVolume size={14} />}
      </ActionIcon>
    </Tooltip>
  );
}

export { IconVolume }; // re-export for convenience
