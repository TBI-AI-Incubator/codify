import { type ReactNode, createContext, useContext, useMemo } from 'react';

import { useJurisdictionPin } from '@/lib/jurisdiction-pin';
import { FALLBACK_COUNTRY, resolveCountry } from '@/lib/jurisdiction-path';

interface JurisdictionContextValue {
  country: string;
}

export const JurisdictionContext = createContext<JurisdictionContextValue>({
  country: FALLBACK_COUNTRY,
});

export function JurisdictionProvider({ children }: { children: ReactNode }) {
  const { jurisdictionPin, jurisdictions } = useJurisdictionPin();

  const country = useMemo(
    () => resolveCountry(jurisdictionPin, jurisdictions),
    [jurisdictionPin, jurisdictions],
  );

  return <JurisdictionContext value={{ country }}>{children}</JurisdictionContext>;
}

export function useJurisdiction() {
  return useContext(JurisdictionContext);
}
