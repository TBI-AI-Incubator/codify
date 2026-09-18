export function lawDisplayName<T extends { title: string; short_title?: string | null }>(
  law: T,
): string {
  return law.short_title?.trim() || law.title;
}

export function lawSubtitle<T extends { title: string; short_title?: string | null }>(
  law: T,
): string | null {
  const name = lawDisplayName(law);
  return name === law.title ? null : law.title;
}

export const HEADING_MAX = 120;

export interface LawHeading {
  heading: string;
  full: string | null;
}

export function lawHeading<T extends { title: string; short_title?: string | null }>(
  law: T,
  displayed: string,
): LawHeading {
  const short = law.short_title?.trim();
  if (short && displayed === short) {
    return { heading: displayed, full: null };
  }
  return cutTitle(displayed);
}

export function cutTitle(text: string): LawHeading {
  const units = graphemes(text);
  if (units.length <= HEADING_MAX) {
    return { heading: text, full: null };
  }
  const cut = units.slice(0, HEADING_MAX).join('');
  const lastSpace = cut.lastIndexOf(' ');
  const head = lastSpace > HEADING_MAX / 2 ? cut.slice(0, lastSpace) : cut;
  return { heading: `${head.replace(/[,;:]$/, '')}…`, full: text };
}

const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });

function graphemes(text: string): string[] {
  return [...segmenter.segment(text)].map((s) => s.segment);
}
