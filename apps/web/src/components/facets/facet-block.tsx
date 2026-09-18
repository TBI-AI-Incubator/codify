import { FacetPill } from './facet-pill';

export function FacetBlock({
  heading,
  options,
  selected,
  onChange,
}: {
  heading: string;
  options: readonly { value: string; label: string; count?: number }[];
  selected?: string;
  onChange: (next: string | undefined) => void;
}) {
  if (options.length === 0) {
    return null;
  }
  return (
    <div className="space-y-2">
      <h2 className="font-tbi-sans text-xs font-semibold uppercase tracking-wide text-ink-60">
        {heading}
      </h2>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const isSel = selected === o.value;
          return (
            <FacetPill
              key={o.value}
              selected={isSel}
              onClick={() => onChange(isSel ? undefined : o.value)}
            >
              {o.label}
              {typeof o.count === 'number' ? (
                <span className="tabular-nums text-current/70">{o.count}</span>
              ) : null}
            </FacetPill>
          );
        })}
      </div>
    </div>
  );
}
