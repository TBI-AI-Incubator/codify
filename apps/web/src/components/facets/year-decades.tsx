import { useMemo, useState } from 'react';

import { FacetPill } from './facet-pill';

type YearFacet = { value: number; count: number };

export function YearDecadesFacet({
  heading,
  years,
  selected,
  onChange,
}: {
  heading: string;
  years: readonly YearFacet[];
  selected?: number;
  onChange: (next: number | undefined) => void;
}) {
  const decades = useMemo(() => {
    const buckets = new Map<number, { decade: number; count: number; years: YearFacet[] }>();
    for (const y of years) {
      const decade = Math.floor(y.value / 10) * 10;
      const entry = buckets.get(decade) ?? { decade, count: 0, years: [] };
      entry.count += y.count;
      entry.years.push(y);
      buckets.set(decade, entry);
    }
    return [...buckets.values()]
      .map((b) => ({ ...b, years: b.years.slice().sort((a, b) => b.value - a.value) }))
      .sort((a, b) => b.decade - a.decade);
  }, [years]);

  const selectedDecade = selected !== undefined ? Math.floor(selected / 10) * 10 : undefined;
  const [expanded, setExpanded] = useState<number | undefined>(selectedDecade);
  const activeDecade = expanded ?? selectedDecade;
  const activeBucket = decades.find((d) => d.decade === activeDecade);

  if (decades.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2">
      <h2 className="font-tbi-sans text-xs font-semibold uppercase tracking-wide text-ink-60">
        {heading}
      </h2>
      <div className="flex flex-wrap gap-1.5">
        {decades.map((d) => {
          const containsSelected =
            selected !== undefined && Math.floor(selected / 10) * 10 === d.decade;
          const isOpen = activeDecade === d.decade;
          return (
            <FacetPill
              key={d.decade}
              selected={containsSelected || isOpen}
              onClick={() => {
                setExpanded(isOpen ? undefined : d.decade);
                if (containsSelected) onChange(undefined);
              }}
            >
              {`${d.decade}s`}
              <span className="tabular-nums text-current/70">{d.count}</span>
            </FacetPill>
          );
        })}
      </div>
      {activeBucket ? (
        <div className="ms-1 flex flex-wrap gap-1.5 border-s border-ink-20 ps-2">
          {activeBucket.years.map((y) => (
            <FacetPill
              key={y.value}
              selected={selected === y.value}
              onClick={() => onChange(selected === y.value ? undefined : y.value)}
            >
              {y.value}
              <span className="tabular-nums text-current/70">{y.count}</span>
            </FacetPill>
          ))}
        </div>
      ) : null}
    </div>
  );
}
