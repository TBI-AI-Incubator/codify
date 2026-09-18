import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from 'next-themes';
import { type ReactNode, useState } from 'react';

import { JurisdictionPinProvider } from '@/lib/jurisdiction-pin';
import { JurisdictionProvider } from '@/lib/jurisdiction-context';

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache(),
        mutationCache: new MutationCache(),
        defaultOptions: {
          queries: {
            staleTime: 5 * 60 * 1000,
            gcTime: 15 * 60 * 1000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <JurisdictionPinProvider>
          <JurisdictionProvider>{children}</JurisdictionProvider>
        </JurisdictionPinProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
