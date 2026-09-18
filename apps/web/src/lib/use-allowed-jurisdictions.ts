
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { listJurisdictions } from '@/lib/api-jurisdictions';
import { useJurisdictionPin } from '@/lib/jurisdiction-pin';
import type { JurisdictionSummary } from '@/lib/api-jurisdictions';

export function useAllowedJurisdictions(): {
  items: JurisdictionSummary[];
  isPending: boolean;
  isError: boolean;
} {
  const { jurisdictions: allowed } = useJurisdictionPin();
  const { data, isPending, isError } = useQuery({
    queryKey: ['jurisdictions'],
    queryFn: () => listJurisdictions(),
    staleTime: Infinity,
  });

  const items = useMemo(() => {
    const all = data ?? [];
    const scoped = allowed.includes('*') ? all : all.filter((j) => allowed.includes(j.code));
    return [...scoped].sort((a, b) => a.name.localeCompare(b.name));
  }, [data, allowed]);

  return { items, isPending, isError };
}
