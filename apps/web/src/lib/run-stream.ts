
import type { UIMessageChunk } from 'ai';

import { ApiError } from './api';
import { authHeaders } from './auth';

const BASE = '/api/proxy';
const DONE = '[DONE]';
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000];

export interface RunConnection {
  chunks: ReadableStream<UIMessageChunk>;
  lastEventId: () => string | null;
  sawDone: () => boolean;
}

export async function openRunStream(
  runId: string,
  opts: { signal?: AbortSignal; lastEventId?: string | null } = {},
): Promise<RunConnection> {
  const url = `${BASE}/runs/${runId}/stream`;
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    ...authHeaders(),
  };
  if (opts.lastEventId) headers['Last-Event-ID'] = opts.lastEventId;
  const res = await fetch(url, { headers, signal: opts.signal });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, 'GET', url, text);
  }
  return sseToChunks(res.body);
}

export function sseToChunks(body: ReadableStream<Uint8Array>): RunConnection {
  let lastId: string | null = null;
  let done = false;
  const decoder = new TextDecoder();
  let buf = '';

  const chunks = new ReadableStream<UIMessageChunk>({
    async start(controller) {
      const reader = body.getReader();
      try {
        while (true) {
          const { value, done: eof } = await reader.read();
          if (value) buf += decoder.decode(value, { stream: true });
          let idx = buf.indexOf('\n\n');
          while (idx !== -1) {
            const raw = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            idx = buf.indexOf('\n\n');
            const frame = parseFrame(raw);
            if (!frame) continue;
            if (frame.data === DONE) {
              if (frame.id !== null) lastId = frame.id;
              done = true;
              controller.close();
              return;
            }
            controller.enqueue(JSON.parse(frame.data) as UIMessageChunk);
            if (frame.id !== null) lastId = frame.id;
          }
          if (eof) break;
        }
        controller.close();
      } catch (err) {
        console.error('run-stream: bad frame', err);
        controller.error(err);
      } finally {
        reader.releaseLock();
      }
    },
  });

  return { chunks, lastEventId: () => lastId, sawDone: () => done };
}

function parseFrame(raw: string): { id: string | null; data: string } | null {
  let id: string | null = null;
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (line.startsWith('data: ')) dataLines.push(line.slice(6));
    else if (line.startsWith('data:')) dataLines.push(line.slice(5));
    else if (line.startsWith('id: ')) id = line.slice(4).trim();
    else if (line.startsWith('id:')) id = line.slice(3).trim();
  }
  if (dataLines.length === 0) return null;
  return { id, data: dataLines.join('\n') };
}

export function reconnectDelayMs(attempt: number): number {
  const base = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)] ?? 8000;
  return base / 2 + Math.random() * (base / 2); // full jitter within [base/2, base]
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(t);
        resolve();
      },
      { once: true },
    );
  });
}
