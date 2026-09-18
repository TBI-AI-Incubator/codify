
export const UNIT_WORDS: Record<string, string> = {
  book: 'Book',
  part: 'Part',
  tit: 'Title',
  ttl: 'Title',
  title: 'Title',
  chp: 'Chapter',
  chapter: 'Chapter',
  sec: 'Section',
  section: 'Section',
  art: 'Article',
  article: 'Article',
  reg: 'Regulation',
  rule: 'Rule',
  para: 'Paragraph',
  subsec: 'Subsection',
  subpara: 'Subparagraph',
  pt: 'Point',
  point: 'Point',
  item: 'Item',
  content: '',
};

export const CONTAINER_KINDS = [
  'book',
  'part',
  'title',
  'chapter',
  'section',
  'article',
  'paragraph',
  'subparagraph',
  'point',
  'item',
] as const;

export type ContainerKind = (typeof CONTAINER_KINDS)[number];

const PREFIX_TO_KIND: Record<string, ContainerKind> = {
  book: 'book',
  part: 'part',
  tit: 'title',
  ttl: 'title',
  title: 'title',
  chp: 'chapter',
  chapter: 'chapter',
  sec: 'section',
  section: 'section',
  art: 'article',
  article: 'article',
  para: 'paragraph',
  subpara: 'subparagraph',
  pt: 'point',
  point: 'point',
  item: 'item',
};

export interface EidContainer {
  prefix: string;
  number: string;
  raw: string;
  kind: ContainerKind | null;
}

export function parseEid(eid: string): EidContainer[] {
  if (!eid) return [];
  return eid.split('__').map((segment): EidContainer => {
    const match = segment.match(/^([a-z]+)(?:_(.*))?$/i);
    const prefix = (match?.[1] ?? segment).toLowerCase();
    const number = match?.[2] ?? '';
    return {
      prefix,
      number,
      raw: segment,
      kind: PREFIX_TO_KIND[prefix] ?? null,
    };
  });
}

export function ancestorChain(eid: string): string[] {
  if (!eid) return [];
  const segs = eid.split('__');
  return segs.map((_, i) => segs.slice(0, i + 1).join('__'));
}

export function parentEid(eid: string): string | null {
  if (!eid) return null;
  const i = eid.lastIndexOf('__');
  return i === -1 ? null : eid.slice(0, i);
}

export function compareEidsDocumentOrder(a: string, b: string): number {
  const as = parseEid(a);
  const bs = parseEid(b);
  const len = Math.max(as.length, bs.length);
  for (let i = 0; i < len; i++) {
    const ai = as[i];
    const bi = bs[i];
    if (!ai) return -1;
    if (!bi) return 1;
    if (ai.prefix !== bi.prefix) {
      const ak = ai.kind ? CONTAINER_KINDS.indexOf(ai.kind) : 999;
      const bk = bi.kind ? CONTAINER_KINDS.indexOf(bi.kind) : 999;
      if (ak !== bk) return ak - bk;
      return ai.prefix.localeCompare(bi.prefix);
    }
    const numCmp = compareNumber(ai.number, bi.number);
    if (numCmp !== 0) return numCmp;
  }
  return 0;
}

function compareNumber(a: string, b: string): number {
  const aChunks = a.split(/[._]/);
  const bChunks = b.split(/[._]/);
  const len = Math.max(aChunks.length, bChunks.length);
  for (let i = 0; i < len; i++) {
    const av = aChunks[i] ?? '';
    const bv = bChunks[i] ?? '';
    const an = Number.parseInt(av, 10);
    const bn = Number.parseInt(bv, 10);
    const aIsNum = !Number.isNaN(an) && /^\d+$/.test(av);
    const bIsNum = !Number.isNaN(bn) && /^\d+$/.test(bv);
    if (aIsNum && bIsNum) {
      if (an !== bn) return an - bn;
      continue;
    }
    const cmp = av.localeCompare(bv);
    if (cmp !== 0) return cmp;
  }
  return 0;
}
