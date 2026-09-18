import { useEffect, useState } from 'react';

import { ApiError } from '@/lib/api';
import type { RunPart } from '@/lib/run-events';
import { readRunStream, reconnectDelayMs, sleep } from '@/lib/run-stream';

export type RunStreamStatus = 'idle' | 'submitted' | 'streaming' | 'ready' | 'error';

export interface UseRunResult {
  status: RunStreamStatus;
  isReconnecting: boolean;
  aborted: boolean;
  errorText: string | null;
  parts: RunPart[];
}

const IDLE: UseRunResult = { status: 'idle', isReconnecting: false, aborted: false, errorText: null, parts: [] };

/* Follows a run's stream; the server replays on reconnect, so parts restart from empty. */
export function useRun(runId: string | null): UseRunResult {
  const [state, setState] = useState<UseRunResult>(IDLE);

  useEffect(() => {
    if (!runId) {
      setState(IDLE);
      return;
    }
    const ac = new AbortController();
    setState({ ...IDLE, status: 'submitted' });
    void (async () => {
      let attempt = 0;
      while (!ac.signal.aborted) {
        const parts: RunPart[] = [];
        try {
          for await (const batch of readRunStream(runId, ac.signal)) {
            parts.push(...batch);
            setState({ status: 'streaming', isReconnecting: false, aborted: false, errorText: null, parts: [...parts] });
          }
          setState({ status: 'ready', isReconnecting: false, aborted: false, errorText: null, parts });
          return;
        } catch (err) {
          if (ac.signal.aborted) return;
          if (err instanceof ApiError && err.status < 500) {
            setState({ status: 'error', isReconnecting: false, aborted: false, errorText: err.message, parts });
            return;
          }
          setState({ status: 'streaming', isReconnecting: true, aborted: false, errorText: null, parts });
          await sleep(reconnectDelayMs(attempt++), ac.signal);
        }
      }
    })();
    return () => ac.abort();
  }, [runId]);

  return state;
}
