import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function FacetPill({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        'inline-flex min-h-6 items-center gap-1.5 rounded-full border px-2.5 py-1 font-tbi-sans text-xs font-medium transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        selected
          ? 'border-emphasis-strong bg-emphasis-strong text-paper'
          : 'border-ink-20 bg-paper text-ink-80 hover:border-ink-40 hover:bg-paper-sunken',
      )}
    >
      {children}
    </button>
  );
}
