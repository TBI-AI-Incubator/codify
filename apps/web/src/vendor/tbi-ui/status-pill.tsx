'use client';

import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from './utils';

const pillVariants = cva(
  'inline-flex items-center gap-1 rounded-sm px-2 py-0.5 align-middle font-tbi-sans text-[0.75rem] font-medium leading-none tracking-tight [&_svg]:size-3 [&_svg]:shrink-0',
  {
    variants: {
      tone: {
        teal: 'bg-teal-soft text-teal-strong',
        coral: 'bg-coral-soft text-coral-strong',
        gold: 'bg-gold-soft text-gold-strong',
        emphasis: 'bg-emphasis-soft text-emphasis-strong',
        neutral: 'bg-paper-sunken text-ink-80',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
);

type StatusPillProps = Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'> &
  VariantProps<typeof pillVariants> & {
    label: React.ReactNode;
    icon?: React.ReactNode;
  };

function StatusPill({ tone, label, icon, className, ...props }: StatusPillProps) {
  return (
    <span data-slot="status-pill" className={cn(pillVariants({ tone, className }))} {...props}>
      {icon ? <span aria-hidden>{icon}</span> : null}
      <span>{label}</span>
    </span>
  );
}

export { StatusPill, pillVariants as statusPillVariants };
export type { StatusPillProps };
