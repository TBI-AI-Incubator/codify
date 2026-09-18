
import * as React from 'react';
import { Popover } from '@base-ui/react/popover';
import { Command } from 'cmdk';
import { Check, Search as SearchIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router';

import { useAuth } from '@/lib/auth-context';
import { countryName } from '@/lib/countries';
import { rescopePath } from '@/lib/jurisdiction-path';
import { useAllowedJurisdictions } from '@/lib/use-allowed-jurisdictions';
import { CircleFlag } from '../vendor/tbi-ui';

interface JurisdictionSwitcherProps {
  currentCode: string;
}

export function JurisdictionSwitcher({ currentCode }: JurisdictionSwitcherProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { setJurisdictionPin } = useAuth();
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const [prevOpen, setPrevOpen] = React.useState(open);

  if (open !== prevOpen) {
    setPrevOpen(open);
    if (!open) setQuery('');
  }

  const { items, isError } = useAllowedJurisdictions();

  const commit = React.useCallback(
    async (code: string) => {
      setOpen(false);
      const target = rescopePath(location, code);
      try {
        await setJurisdictionPin(code);
      } catch {
        navigate(target ?? `/jurisdictions/${code}`);
        return;
      }
      if (target) navigate(target);
    },
    [navigate, setJurisdictionPin, location],
  );

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        title={t('Switch jurisdiction')}
        aria-label={t('Switch jurisdiction, currently {{name}}', {
          name: countryName(currentCode),
        })}
        className="flex w-full items-center gap-2 border-b px-3 py-2 text-sm transition-colors hover:bg-accent group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
      >
        <CircleFlag countryCode={currentCode} height="20" width="20" />
        <span className="flex-1 truncate text-start font-medium group-data-[collapsible=icon]:hidden">
          {countryName(currentCode)}
        </span>
        <span className="micro-caps-sm text-ink-40 group-data-[collapsible=icon]:hidden">
          {t('Switch →')}
        </span>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="right" align="start" sideOffset={8} className="z-[60]">
          <Popover.Popup className="z-[60] flex w-72 flex-col overflow-hidden rounded-md border border-ink-20 bg-paper shadow-popover">
            <Command shouldFilter loop label={t('Switch jurisdiction')}>
              <div className="flex items-center gap-2 border-b border-ink-20 px-3 py-2">
                <SearchIcon className="h-3.5 w-3.5 text-ink-60" aria-hidden />
                <Command.Input
                  value={query}
                  onValueChange={setQuery}
                  placeholder={t('Search country…')}
                  autoFocus
                  className="flex-1 bg-transparent text-sm outline-none placeholder:text-ink-40"
                />
              </div>
              <Command.List className="max-h-72 overflow-y-auto p-1">
                <Command.Empty
                  className={
                    isError
                      ? 'px-3 py-6 text-center text-xs text-coral-strong'
                      : 'px-3 py-6 text-center text-xs text-ink-60'
                  }
                >
                  {isError ? t('Could not load jurisdictions.') : t('No jurisdictions match')}
                </Command.Empty>
                {items.map((item) => {
                  const selected = item.code === currentCode;
                  return (
                    <Command.Item
                      key={item.code}
                      value={`${item.code} ${item.name}`}
                      onSelect={() => void commit(item.code)}
                      className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm aria-selected:bg-accent"
                    >
                      <CircleFlag countryCode={item.code} height="18" width="18" />
                      <span className="flex-1 truncate">{item.name}</span>
                      {selected ? <Check className="h-3.5 w-3.5 text-ink-60" aria-hidden /> : null}
                    </Command.Item>
                  );
                })}
              </Command.List>
            </Command>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
