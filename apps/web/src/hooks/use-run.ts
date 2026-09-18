
import { readUIMessageStream, type UIMessage } from 'ai';
import { useEffect, useRef, useState } from 'react';

import { ApiError } from '@/lib/api';
import type { RunParts } from '@/lib/run-parts';
import { openRunStream, reconnectDelayMs, sleep } from '@/lib/run-stream';

export type RunStreamStatus = 'idle' | 'submitted' | 'streaming' | 'ready' | 'error';

export interface UseRunResult {
  status: RunStreamStatus;
  isReconnecting: boolean;
  aborted: boolean;
  errorText: string | null;
  parts: RunParts;
}

const IDLE: UseRunResult = {
  status: 'idle',
  isReconnecting: false,
  aborted: false,
  errorText: null,
  parts: [],
};

export function useRun(runId: string | null): UseRunResult {
  const [state, setState] = useState<UseRunResult>(IDLE);
  const [subscribedRunId, setSubscribedRunId] = useState<string | null>(null);
  const pending = useRef<UseRunResult | null>(null);
  const frame = useRef<number | null>(null);

  if (runId !== subscribedRunId) {
    setSubscribedRunId(runId);
    setState(runId ? { ...IDLE, status: 'submitted' } : IDLE);
  }

  useEffect(() => {
    if (!runId) return;
    const ac = new AbortController();
    const push = (next: UseRunResult) => {
      pending.current = next;
      frame.current ??= requestAnimationFrame(() => {
        frame.current = null;
        if (pending.current && !ac.signal.aborted) setState(pending.current);
      });
    };
    const flushNow = (next: UseRunResult) => {
      pending.current = null;
      if (frame.current !== null) {
        cancelAnimationFrame(frame.current);
        frame.current = null;
      }
      if (!ac.signal.aborted) setState(next);
    };

    void (async () => {
      let message: UIMessage | undefined;
      let lastEventId: string | null = null;
      let aborted = false;
      let errorText: string | null = null;
      let attempt = 0;

      while (!ac.signal.aborted) {
        let conn;
        try {
          conn = await openRunStream(runId, { signal: ac.signal, lastEventId });
        } catch (err) {
          if (ac.signal.aborted) return;
          if (err instanceof ApiError && err.status > 0 && err.status < 500) {
            flushNow({
              status: 'error',
              isReconnecting: false,
              aborted: false,
              errorText: err.message,
              parts: message ? (message.parts as RunParts) : [],
            });
            return;
          }
          push({
            status: 'submitted',
            isReconnecting: true,
            aborted: false,
            errorText: null,
            parts: message ? (message.parts as RunParts) : [],
          });
          await sleep(reconnectDelayMs(attempt++), ac.signal);
          continue;
        }
        attempt = 0;

        try {
          for await (const snapshot of readUIMessageStream({
            message,
            stream: conn.chunks,
            onError: (err) => {
              errorText = err instanceof Error ? err.message : String(err);
            },
          })) {
            message = snapshot;
            push({
              status: 'streaming',
              isReconnecting: false,
              aborted: false,
              errorText: null,
              parts: snapshot.parts as RunParts,
            });
          }
        } catch (err) {
          console.error('useRun: stream dropped', err);
        }
        lastEventId = conn.lastEventId() ?? lastEventId;

        if (ac.signal.aborted) return;
        if (conn.sawDone()) {
          aborted = aborted || sawAbort(message);
          flushNow({
            status: errorText ? 'error' : 'ready',
            isReconnecting: false,
            aborted,
            errorText,
            parts: message ? (message.parts as RunParts) : [],
          });
          return;
        }
        push({
          status: 'streaming',
          isReconnecting: true,
          aborted: false,
          errorText: null,
          parts: message ? (message.parts as RunParts) : [],
        });
        await sleep(reconnectDelayMs(attempt++), ac.signal);
      }
    })();

    return () => {
      ac.abort();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [runId]);

  return state;
}

function sawAbort(message: UIMessage | undefined): boolean {
  if (!message) return false;
  return message.parts.some(
    (p) =>
      p.type === 'data-run' && (p as { data?: { status?: string } }).data?.status === 'cancelled',
  );
}
