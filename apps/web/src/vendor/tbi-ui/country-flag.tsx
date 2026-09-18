'use client';

import * as React from 'react';
import { CircleFlag as UpstreamCircleFlag } from 'react-circle-flags';
import countries from 'i18n-iso-countries';
import enLocale from 'i18n-iso-countries/langs/en.json';
import { GlobeIcon } from 'lucide-react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from './utils';

let localeRegistered = false;
function ensureLocale() {
  if (localeRegistered) return;
  countries.registerLocale(enLocale as never);
  localeRegistered = true;
}

// reintroduce the third-party request: an eslint rule bans importing
const FLAG_ORIGIN = '/flags/';

export function CircleFlag(
  props: Omit<React.ComponentProps<typeof UpstreamCircleFlag>, 'cdnUrl'>,
) {
  return <UpstreamCircleFlag {...props} cdnUrl={FLAG_ORIGIN} />;
}

const NON_ISO_NAMES: Record<string, string> = {
  eu: 'European Union',
};

type FlagSize = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const sizeToPx: Record<FlagSize, number> = {
  xs: 12,
  sm: 16,
  md: 20,
  lg: 28,
  xl: 40,
};

const flagVariants = cva('inline-flex min-w-0 items-center gap-2 align-middle', {
  variants: {
    size: {
      xs: 'text-[0.625rem]',
      sm: 'text-xs',
      md: 'text-sm',
      lg: 'text-[0.9375rem]',
      xl: 'text-base',
    },
  },
  defaultVariants: { size: 'md' },
});

type CountryFlagProps = Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'> &
  VariantProps<typeof flagVariants> & {
    code: string;
    withName?: boolean;
    nameOverride?: string;
    unknownLabel?: (code: string) => string;
  };

const defaultUnknownLabel = (code: string) => `Unrecognised country code: ${code}`;

function CountryFlag({
  code,
  size,
  withName = false,
  nameOverride,
  unknownLabel = defaultUnknownLabel,
  className,
  ...props
}: CountryFlagProps) {
  ensureLocale();
  const resolved: FlagSize = size ?? 'md';
  const px = sizeToPx[resolved];
  const lower = code.toLowerCase();
  const upper = code.toUpperCase();
  const resolvedName = nameOverride ?? countries.getName(upper, 'en') ?? NON_ISO_NAMES[lower];
  const isKnown = Boolean(resolvedName);
  const fallbackLabel = resolvedName ?? unknownLabel(upper);
  const flagAriaLabel = withName ? undefined : fallbackLabel;
  const flagAriaHidden = withName ? true : undefined;

  return (
    <span
      data-slot="country-flag"
      className={cn(flagVariants({ size: resolved, className }))}
      {...props}
    >
      {isKnown ? (
        <CircleFlag
          countryCode={lower}
          width={String(px)}
          height={String(px)}
          aria-label={flagAriaLabel}
          aria-hidden={flagAriaHidden}
        />
      ) : (
        <span
          role={withName ? 'presentation' : 'img'}
          aria-label={flagAriaLabel}
          aria-hidden={flagAriaHidden}
          className="inline-flex shrink-0 items-center justify-center rounded-full bg-ink-20 text-ink-60"
          style={{ width: px, height: px }}
        >
          <GlobeIcon size={Math.max(8, px - 4)} aria-hidden />
        </span>
      )}
      {withName ? (
        <span className="min-w-0 break-words font-medium tracking-tight text-ink-80">
          {resolvedName ?? upper}
        </span>
      ) : null}
    </span>
  );
}

export { CountryFlag };
export type { CountryFlagProps, FlagSize };
