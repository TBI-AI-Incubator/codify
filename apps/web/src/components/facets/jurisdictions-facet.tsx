import { CountryFlag } from '../../vendor/tbi-ui';

import { FacetPill } from './facet-pill';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { JurisdictionListItem } from '@/lib/types';

export function JurisdictionsFacet({
  heading,
  options,
  selected,
  onToggle,
}: {
  heading: string;
  options: readonly JurisdictionListItem[];
  selected: readonly string[];
  onToggle: (code: string) => void;
}) {
  const featured = options
    .filter((j) => selected.includes(j.code) || options.indexOf(j) < 4)
    .slice(0, 4);
  const remaining = options.filter((j) => !featured.some((f) => f.code === j.code));

  return (
    <div className="space-y-2">
      <h2 className="font-tbi-sans text-xs font-semibold uppercase tracking-wide text-ink-60">
        {heading}
      </h2>
      <div className="flex flex-wrap gap-1.5">
        {featured.map((j) => {
          const isSel = selected.includes(j.code);
          return (
            <FacetPill key={j.code} selected={isSel} onClick={() => onToggle(j.code)}>
              <CountryFlag code={j.code} size="xs" aria-hidden />
              <span>{j.name}</span>
            </FacetPill>
          );
        })}
      </div>
      {remaining.length > 0 ? (
        <Select value="" onValueChange={(value) => value && onToggle(value)}>
          <SelectTrigger className="w-full" aria-label={heading}>
            <SelectValue placeholder="More jurisdictions…" />
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false} className="min-w-56">
            {remaining.map((j) => (
              <SelectItem key={j.code} value={j.code}>
                {j.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
    </div>
  );
}
