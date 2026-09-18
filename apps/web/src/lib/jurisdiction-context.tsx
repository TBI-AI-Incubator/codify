import { createContext, useContext, useMemo } from 'react';

import { useAuth } from '@/lib/auth-context';
import { FALLBACK_COUNTRY, resolveCountry } from '@/lib/jurisdiction-path';

interface JurisdictionContextValue {
  country: string;
}

export const JurisdictionContext = createContext<JurisdictionContextValue>({
  country: FALLBACK_COUNTRY,
});

export function JurisdictionProvider({ children }: { children: React.ReactNode }) {
  const { jurisdictionPin, jurisdictions } = useAuth();

  const country = useMemo(
    () => resolveCountry(jurisdictionPin, jurisdictions),
    [jurisdictionPin, jurisdictions],
  );

  return <JurisdictionContext value={{ country }}>{children}</JurisdictionContext>;
}

export function useJurisdiction() {
  return useContext(JurisdictionContext);
}
