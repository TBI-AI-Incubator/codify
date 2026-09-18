import i18n from '@/i18n';

export function getI18nLocale(): string {
  return i18n.resolvedLanguage ?? i18n.language ?? 'en-GB';
}

function intlLocale(override?: string): string {
  const locale = override ?? getI18nLocale();
  return locale.includes('-u-') ? locale : `${locale}-u-nu-latn`;
}

const PREFERRED_NUMBERING_SYSTEMS: Record<string, string> = {
  ar: 'arab', // Arabic-Indic: ٠١٢٣٤٥٦٧٨٩
};

export function formatChromeCount(value: number, locale?: string): string {
  const base = locale ?? getI18nLocale();
  const lang = base.split('-')[0]?.toLowerCase() ?? '';
  const system = PREFERRED_NUMBERING_SYSTEMS[lang];
  const tag = system && !base.includes('-u-') ? `${base}-u-nu-${system}` : base;
  return new Intl.NumberFormat(tag).format(value);
}

export function formatNumber(
  value: number,
  locale?: string,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(intlLocale(locale), options).format(value);
}

export function formatShortDate(input: string | Date | number, locale?: string): string {
  return formatDate(
    toDate(input),
    new Intl.DateTimeFormat(intlLocale(locale), {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    }),
  );
}

export function formatLongDate(input: string | Date | number, locale?: string): string {
  return formatDate(
    toDate(input),
    new Intl.DateTimeFormat(intlLocale(locale), {
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    }),
  );
}

export function formatDateTime(input: string | Date | number, locale?: string): string {
  return formatDate(
    toDate(input),
    new Intl.DateTimeFormat(intlLocale(locale), {
      dateStyle: 'medium',
      timeStyle: 'short',
    }),
  );
}

export function formatNumericDate(input: string | Date | number, locale?: string): string {
  return formatDate(
    toDate(input),
    new Intl.DateTimeFormat(intlLocale(locale), {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    }),
  );
}

export function formatRelativeTime(input: string | Date | number, locale?: string): string {
  const ms = toDate(input).getTime();
  if (Number.isNaN(ms)) return '\u2013';
  const seconds = Math.round((ms - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(intlLocale(locale), { numeric: 'auto' });
  const abs = Math.abs(seconds);
  if (abs < 60) return rtf.format(seconds, 'second');
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), 'minute');
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), 'hour');
  return rtf.format(Math.round(seconds / 86400), 'day');
}

function toDate(input: string | Date | number): Date {
  return input instanceof Date ? input : new Date(input);
}

function formatDate(date: Date, fmt: Intl.DateTimeFormat): string {
  return Number.isNaN(date.getTime()) ? '\u2013' : fmt.format(date);
}
