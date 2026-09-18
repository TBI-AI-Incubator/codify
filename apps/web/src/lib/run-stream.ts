import { ApiError } from './api';
import { partsOf, type RunPart } from './run-events';

const BASE = '/api/proxy';
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000];

/* Every event so far, then each new one, until the server's `end` frame. */
export async function* readRunStream(runId: string, signal?: AbortSignal): AsyncGenerator<RunPart[]> {
  const url = `${BASE}/runs/${encodeURIComponent(runId)}/stream`;
  const res = await fetch(url, { headers: { Accept: 'text/event-stream' }, signal });
  if (!res.ok || !res.body) throw new ApiError(res.status, 'GET', url, await res.text().catch(() => res.statusText));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (value) buf += decoder.decode(value, { stream: true });
      let idx = buf.indexOf('\n\n');
      while (idx !== -1) {
        const frame = parseFrame(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
        idx = buf.indexOf('\n\n');
        if (!frame) continue;
        if (frame.event === 'end') return;
        yield partsOf(frame.event, JSON.parse(frame.data) as Record<string, unknown>);
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(raw: string): { event: string; data: string } | null {
  let event = 'event';
  const data: string[] = [];
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  return data.length ? { event, data: data.join('\n') } : null;
}

export function reconnectDelayMs(attempt: number): number {
  const base = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)] ?? 8000;
  return base / 2 + Math.random() * (base / 2);
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
  });
}
