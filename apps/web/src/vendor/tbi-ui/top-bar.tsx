'use client';

import * as React from 'react';

import { cn } from './utils';

type TopBarProps = React.HTMLAttributes<HTMLElement> & {
  brand?: React.ReactNode;
  breadcrumb?: React.ReactNode;
  search?: React.ReactNode;
  actions?: React.ReactNode;
  avatar?: React.ReactNode;
};

function TopBar({ brand, breadcrumb, search, actions, avatar, className, ...props }: TopBarProps) {
  return (
    <header
      data-slot="top-bar"
      className={cn(
        'sticky top-0 z-40 flex h-14 w-full items-center gap-4 border-b border-ink-20 bg-paper px-4 md:px-6',
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 flex-1 items-center gap-4">
        {brand ? (
          <div data-slot="top-bar-brand" className="flex shrink-0 items-center">
            {brand}
          </div>
        ) : null}
        {breadcrumb ? (
          <div data-slot="top-bar-breadcrumb" className="min-w-0 flex-1">
            {breadcrumb}
          </div>
        ) : null}
      </div>

      {search ? (
        <div
          data-slot="top-bar-search"
          className="hidden min-w-0 max-w-md flex-1 md:flex md:items-center"
        >
          {search}
        </div>
      ) : null}

      <div className="flex shrink-0 items-center gap-2">
        {actions ? (
          <div data-slot="top-bar-actions" className="flex items-center gap-1">
            {actions}
          </div>
        ) : null}
        {avatar ? (
          <div data-slot="top-bar-avatar" className="flex items-center">
            {avatar}
          </div>
        ) : null}
      </div>
    </header>
  );
}

export { TopBar };
export type { TopBarProps };
