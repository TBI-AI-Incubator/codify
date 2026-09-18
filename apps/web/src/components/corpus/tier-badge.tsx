import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export const TIER_LABEL: Record<number, string> = {
  1: 'AKN native',
  2: 'Structured XML',
  3: 'HTML anchors',
  4: 'PDF body',
  5: 'No online source',
};

export const TIER_DESCRIPTION: Record<number, string> = {
  1: 'Native Akoma Ntoso XML: direct import',
  2: 'Structured XML, not native AKN: schema crosswalk',
  3: 'HTML with per-provision anchors: DOM extraction',
  4: 'PDF body: OCR or text-layer extraction',
  5: 'No publicly accessible statute base',
};

export function tierColorClass(tier: number): string {
  switch (tier) {
    case 1:
      return 'bg-teal-soft text-teal-strong border-teal-strong/20';
    case 2:
      return 'bg-gold-soft text-gold-strong border-gold-strong/20';
    case 3:
      return 'bg-coral-soft text-coral-strong border-coral-strong/20';
    case 4:
      return 'bg-coral text-paper border-coral-strong';
    case 5:
      return 'bg-ink-10 text-ink-80 border-ink-40';
    default:
      return 'bg-paper-sunken text-ink-80 border-ink-20';
  }
}

interface TierBadgeProps {
  tier: number;
  effectiveTier?: number | null;
  className?: string;
}

export function TierBadge({ tier, effectiveTier, className }: TierBadgeProps) {
  const { t } = useTranslation();
  const shown = effectiveTier ?? tier;
  const gated = effectiveTier != null && effectiveTier !== tier;
  const baseDescription = t(TIER_DESCRIPTION[shown] ?? '');
  const description = gated
    ? t('{{description}} (declared Tier {{tier}} requires API token)', {
        description: baseDescription,
        tier,
      })
    : baseDescription;
  const label = TIER_LABEL[shown] ? t(TIER_LABEL[shown]) : t('Tier {{tier}}', { tier: shown });
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium',
        tierColorClass(shown),
        className,
      )}
      title={description}
    >
      {label}
      {gated ? (
        <span aria-hidden className="ms-0.5 opacity-70">
          *
        </span>
      ) : null}
    </span>
  );
}
