import * as React from 'react';

const MOBILE_BREAKPOINT = 768;

const cache = new Map<string, MediaQueryList>();

function listFor(query: string): MediaQueryList {
  let mql = cache.get(query);
  if (!mql) {
    mql = window.matchMedia(query);
    cache.set(query, mql);
  }
  return mql;
}

export function useMediaQuery(query: string): boolean {
  const subscribe = React.useCallback(
    (callback: () => void) => {
      const mql = listFor(query);
      mql.addEventListener('change', callback);
      return () => mql.removeEventListener('change', callback);
    },
    [query],
  );
  const getSnapshot = React.useCallback(() => listFor(query).matches, [query]);
  return React.useSyncExternalStore(subscribe, getSnapshot, () => false);
}

export function useIsMobile() {
  return useMediaQuery(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
}
