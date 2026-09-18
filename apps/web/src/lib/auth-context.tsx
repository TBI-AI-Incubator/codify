import { createContext, useContext, useState } from 'react';

interface Workspace {
  id: string;
  slug: string;
  name: string;
  role: 'admin' | 'editor' | 'viewer';
  jurisdictions: string[];
}

interface AuthState {
  email: string | null;
  sub: string;
  organizations: Workspace[];
  currentOrg: Workspace | null;
  role: 'admin' | 'editor' | 'viewer' | null;
  actualRole: 'admin' | 'editor' | 'viewer' | null;
  viewAs: 'viewer' | null;
  setViewAs: (value: 'viewer' | null) => void;
  jurisdictions: string[];
  platformAdmin: boolean;
  lenses: string[];
  jurisdictionPin: string | null;
  isLoading: boolean;
  isError: boolean;
  preferredLanguage: string | null;
  knownLanguages: string[];
  analyticsOptOut: boolean;
  setAnalyticsOptOut: (optOut: boolean) => Promise<void>;
  signOut: () => void;
  switchOrg: (orgId: string) => Promise<void>;
  setJurisdictionPin: (code: string) => Promise<void>;
  setPreferredLanguage: (locale: string) => Promise<void>;
}

const noop = async () => {};

const PERMISSIVE: AuthState = {
  email: null,
  sub: '',
  organizations: [],
  currentOrg: null,
  role: null,
  actualRole: null,
  viewAs: null,
  setViewAs: () => {},
  jurisdictions: ['*'],
  platformAdmin: true,
  lenses: [],
  jurisdictionPin: null,
  isLoading: false,
  isError: false,
  preferredLanguage: null,
  knownLanguages: [],
  analyticsOptOut: true,
  setAnalyticsOptOut: noop,
  signOut: () => {},
  switchOrg: noop,
  setJurisdictionPin: noop,
  setPreferredLanguage: noop,
};

const AuthContext = createContext<AuthState>(PERMISSIVE);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [jurisdictionPin, setJurisdictionPin] = useState<string | null>(null);

  return (
    <AuthContext.Provider
      value={{
        ...PERMISSIVE,
        jurisdictionPin,
        setJurisdictionPin: async (code) => {
          setJurisdictionPin(code);
        },
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  return useContext(AuthContext);
}
