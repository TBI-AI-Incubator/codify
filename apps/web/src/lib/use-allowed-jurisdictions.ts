
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { listJurisdictions } from '@/lib/api-jurisdictions';
import { useAuth } from '@/lib/auth-context';
import type { JurisdictionListItem } from '@codify/core-ts/api';

export function useAllowedJurisdictions(): {
  items: JurisdictionListItem[];
  isPending: boolean;
  isError: boolean;
} {
  const { jurisdictions: allowed } = useAuth();
  const { data, isPending, isError } = useQuery({
    queryKey: ['jurisdictions'],
    queryFn: () => listJurisdictions(),
    staleTime: Infinity,
  });

  const items = useMemo(() => {
    const all = data?.items ?? [];
    const scoped = allowed.includes('*') ? all : all.filter((j) => allowed.includes(j.code));
    return [...scoped].sort((a, b) => a.name.localeCompare(b.name));
  }, [data, allowed]);

  return { items, isPending, isError };
}
