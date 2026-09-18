import { createContext, useContext, useState } from 'react';

/* The one preference the standalone UI keeps: which jurisdiction to open on. */
interface PinState {
  jurisdictionPin: string | null;
  jurisdictions: string[]; // '*' means every shipped jurisdiction
  setJurisdictionPin: (code: string) => void;
}

const PinContext = createContext<PinState>({
  jurisdictionPin: null,
  jurisdictions: ['*'],
  setJurisdictionPin: () => {},
});

export function JurisdictionPinProvider({ children }: { children: React.ReactNode }) {
  const [jurisdictionPin, setJurisdictionPin] = useState<string | null>(() => {
    try {
      return localStorage.getItem('jurisdiction-pin');
    } catch {
      return null;
    }
  });
  const value: PinState = {
    jurisdictionPin,
    jurisdictions: ['*'],
    setJurisdictionPin: (code) => {
      setJurisdictionPin(code);
      try {
        localStorage.setItem('jurisdiction-pin', code);
      } catch {
        // A private window keeps no pin; the session still works.
      }
    },
  };
  return <PinContext.Provider value={value}>{children}</PinContext.Provider>;
}

export function useJurisdictionPin(): PinState {
  return useContext(PinContext);
}
