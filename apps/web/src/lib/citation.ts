
import { UNIT_WORDS } from '@/lib/eid';

const easternDigit = /[\u0660-\u0669\u06f0-\u06f9]/g;
const westernDigits = (value: string): string =>
  value.replace(easternDigit, (d) => String(d.charCodeAt(0) % 16));

export function aknEidLabel(eid: string, opts: { full?: boolean } = {}): string {
  const parts = eid
    .split('__')
    .map((seg) => {
      const m = seg.match(/^([a-z]+)_?(.*)$/i);
      const prefix = m?.[1]?.toLowerCase();
      if (!prefix) return null;
      const word = UNIT_WORDS[prefix];
      if (!word) return null; // unknown or structural-only segment (e.g. `content`)
      const num = m?.[2] ?? '';
      return num ? `${word} ${westernDigits(num.replace(/_/g, '.'))}` : word;
    })
    .filter((x): x is string => Boolean(x));
  if (parts.length === 0) return eid;
  return opts.full ? parts.join(' › ') : (parts[parts.length - 1] ?? eid);
}

export function aknEidLabelRelative(eid: string, scope: string): string {
  const eidParts = eid.split('__');
  const scopeParts = scope ? scope.split('__') : [];
  const below =
    scope && eid.startsWith(scope + '__') ? eidParts.slice(scopeParts.length) : eidParts;
  if (below.length === 0) return aknEidLabel(eid);
  const compact = below.length <= 2 ? below : below.slice(-2);
  return aknEidLabel(compact.join('__'), { full: true });
}

export function formatCitation(frbr: string, lawTitle: string, aknEid: string): string {
  const unit = aknEidLabel(aknEid, { full: true });
  return unit ? `${lawTitle}, ${unit} (${frbr})` : `${lawTitle} (${frbr})`;
}

export function directiveCode(frbr: string): string {
  const segs = frbr.split('/').filter(Boolean);
  const tail = segs.slice(-2).join('/');
  return tail ? `EU ${tail}` : frbr;
}

export interface CitationTarget {
  id: string;
  label: string;
}

export function renderCitations(
  text: string,
  resolve: (n: number) => CitationTarget | undefined,
  maxChips = 3,
): { text: string; unresolved: number; resolved: number } {
  let unresolved = 0;
  const cited = new Set<string>();
  const rendered = text.replace(
    /\[\d+(?:\s*,\s*\d+)*\](?:\s*,\s*\[\d+(?:\s*,\s*\d+)*\])*/g,
    (run) => {
      const indices: number[] = [];
      for (const inner of run.match(/(?<=\[)\d+(?:\s*,\s*\d+)*(?=\])/g) ?? []) {
        for (const part of inner.split(',')) {
          const n = Number.parseInt(part.trim(), 10);
          if (Number.isInteger(n) && n > 0) indices.push(n);
        }
      }
      const seen = new Set<string>();
      const chips: string[] = [];
      const orphans: string[] = [];
      for (const n of indices) {
        const target = resolve(n);
        if (!target) {
          unresolved += 1;
          orphans.push(`[${n}]`);
          continue;
        }
        cited.add(target.id);
        if (seen.has(target.id)) continue;
        seen.add(target.id);
        if (chips.length < maxChips) chips.push(`[${target.label}](cite:${target.id})`);
      }
      const out = [...chips, ...orphans];
      return out.length > 0 ? out.join(', ') : run;
    },
  );
  return { text: rendered, unresolved, resolved: cited.size };
}
