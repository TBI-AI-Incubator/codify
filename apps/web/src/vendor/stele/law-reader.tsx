'use client';

import * as React from 'react';
import { capitalize, cn } from '../tbi-ui';

import { LegalText } from './legal-text';

type InlineRefNode = { kind: 'ref'; text: string; href?: string };
type InlineTermNode = { kind: 'term'; text: string };
type InlineCitationNode = { kind: 'citation'; text: string; uri: string; href?: string };
type InlineEmphNode = { kind: 'emph'; children: InlineNode[] };
type InlineModNode = { kind: 'mod'; children: InlineNode[] };
type InlineStrongNode = { kind: 'strong'; children: InlineNode[] };
type InlineTextNode = { kind: 'text'; text: string };
type InlineNoteNode = { kind: 'note'; children: InlineNode[]; inferred?: boolean };
type InlineNode =
  | InlineTextNode
  | InlineRefNode
  | InlineTermNode
  | InlineCitationNode
  | InlineEmphNode
  | InlineStrongNode
  | InlineModNode
  | InlineNoteNode;

type DocumentTableCell = {
  content: InlineNode[];
  akn_eid?: string | null;
  header: boolean;
  colspan: number;
  rowspan: number;
};

type DocumentTableRow = {
  akn_eid?: string | null;
  cells: DocumentTableCell[];
};

type DocumentTable = {
  akn_eid: string;
  rows: DocumentTableRow[];
};

type DocumentProvisionBlock = {
  akn_eid: string;
  akn_type: string;
  num: string | null;
  heading: string | null;
  intro: InlineNode[];
  blocks: DocumentProvisionBlock[];
  wrap_up: InlineNode[];
  table?: DocumentTable | null;
};

type DocumentProvision = {
  id: string;
  akn_eid: string;
  akn_type: string;
  text: string;
  position: number;
  num?: string | null;
  blocks?: DocumentProvisionBlock[];
};

type DocumentSection = {
  id: string;
  akn_eid: string;
  akn_type: string;
  title: string | null;
  position: number;
  num?: string | null;
  sections: DocumentSection[];
  provisions: DocumentProvision[];
};

type VersionDocument = {
  version_id: string;
  law_id: string;
  amendment_markers?: readonly [string, string];
  frbr_work_uri: string;
  frbr_expression_uri: string;
  language: string;
  expression_date: string;
  preface?: InlineNode[];
  preface_tables?: DocumentTable[];
  preamble?: InlineNode[];
  preamble_tables?: DocumentTable[];
  conclusions?: InlineNode[];
  sections: DocumentSection[];
  provisions: DocumentProvision[];
};

export type ProvisionState = {
  kind: 'in_force' | 'not_yet_in_force' | 'repealed' | 'text_unapplied' | 'affected';
  date?: string | null;
  action?: string | null;
};

type LawReaderProps = React.HTMLAttributes<HTMLElement> & {
  document: VersionDocument;
  provisionStates?: Record<string, ProvisionState>;
  stateLabel?: (state: ProvisionState) => string;
  flashTarget?: string | null;
  flashNonce?: number;
  onFlashTargetMissing?: (akn_eid: string) => void;
  onActiveChange?: (akn_eid: string | null) => void;
  scrollRootRef?: React.RefObject<HTMLElement | null>;
  localTerms?: Record<string, string>;
  basicUnitTerm?: string | null;
  title?: string;
  chapterLabel?: (num: string) => string;
  enactingFormulaLabel?: React.ReactNode;
  copyCitationLabel?: (citationId: string) => string;
  copyLabel?: (citationId: string) => string;
  copiedLabel?: string;
};

const defaultChapterLabel = (num: string) => `Chapter ${num}`;

const ProvisionStateContext = React.createContext<{
  states: Record<string, ProvisionState>;
  label?: (state: ProvisionState) => string;
}>({ states: {} });

function StateMark({ eId }: { eId?: string | null }): React.ReactElement | null {
  const { states, label } = React.useContext(ProvisionStateContext);
  const state = eId ? states[eId] : undefined;
  if (!state || state.kind === 'in_force' || !label) return null;
  return (
    <span
      data-provision-state={state.kind}
      className={cn(
        'inline-flex shrink-0 items-baseline rounded-sm border border-ink-20 px-1.5 py-0.5',
        'text-[0.6875rem] font-medium tracking-tight text-ink-60',
      )}
    >
      {label(state)}
    </span>
  );
}

const DEFAULT_AMENDMENT_MARKERS: readonly [string, string] = ['[', ']'];

const AmendmentMarkers = React.createContext<readonly [string, string]>(DEFAULT_AMENDMENT_MARKERS);

const HEADING_LEVEL: Record<number, 'h2' | 'h3' | 'h4' | 'h5'> = {
  0: 'h2',
  1: 'h2',
  2: 'h3',
  3: 'h4',
};

const HEADING_CLASS: Record<number, string> = {
  0: 'mb-3 text-[1.1875rem] font-bold tracking-[-0.01em] text-ink',
  1: 'mb-2 text-[1.0625rem] font-bold tracking-[-0.005em] text-ink',
  2: 'mb-1.5 text-[0.9375rem] font-semibold text-ink',
  3: 'mb-1 text-[0.875rem] font-semibold text-ink',
};

const SECTION_MT: Record<number, string> = {
  0: 'mt-12',
  1: 'mt-8',
  2: 'mt-6',
  3: 'mt-4',
};

const EYEBROW_BASE = 'shrink-0 self-baseline micro-caps ';

function numberFor(section: DocumentSection): string | null {
  if (section.num) return section.num;
  const tail = section.akn_eid.split('__').pop() ?? section.akn_eid;
  const match = tail.match(/_(\d+[a-z]*)$/i);
  return match && match[1] ? match[1] : null;
}

function gutterLabelFor(
  section: DocumentSection,
  depth: number,
  options: { localTerms?: Record<string, string>; chapterLabel: (num: string) => string },
): string {
  const t = section.akn_type.toLowerCase();
  const num = numberFor(section);
  const local = options.localTerms?.[t];
  if (local) {
    if (t === 'chapter' && depth === 0 && num) return `${local} ${num}`;
    return local;
  }
  if (t === 'chapter' && depth === 0 && num) return options.chapterLabel(num);
  if (t === 'section' || t === 'article') return '§';
  return capitalize(t);
}

function titleNumberFor(section: DocumentSection, depth: number): string | null {
  const num = numberFor(section);
  if (!num) return null;
  const t = section.akn_type.toLowerCase();
  if (t === 'chapter' && depth === 0) return null;
  if (t === 'part') {
    const n = parseInt(num, 10);
    return Number.isFinite(n) ? romanize(n) : num;
  }
  return num;
}

const ROMAN: ReadonlyArray<readonly [number, string]> = [
  [50, 'L'],
  [40, 'XL'],
  [10, 'X'],
  [9, 'IX'],
  [5, 'V'],
  [4, 'IV'],
  [1, 'I'],
];
function romanize(n: number): string {
  if (!Number.isInteger(n) || n < 1 || n > 50) return String(n);
  let out = '';
  let rem = n;
  for (const [val, sym] of ROMAN) {
    while (rem >= val) {
      out += sym;
      rem -= val;
    }
  }
  return out;
}

function Inline({ nodes }: { nodes: InlineNode[] }): React.ReactElement {
  return (
    <>
      {nodes.map((n, i) => (
        <InlineNodeView key={i} node={n} />
      ))}
    </>
  );
}

function AmendedWords({ nodes }: { nodes: InlineNode[] }): React.ReactElement {
  const [open, close] = React.useContext(AmendmentMarkers);
  return (
    <span data-akn-type="mod">
      <span aria-hidden className="font-semibold text-ink-60">
        {open}
      </span>
      <Inline nodes={nodes} />
      <span aria-hidden className="font-semibold text-ink-60">
        {close}
      </span>
    </span>
  );
}

function InlineNodeView({ node }: { node: InlineNode }): React.ReactNode {
  switch (node.kind) {
    case 'text':
      return node.text;
    case 'ref':
      return node.href ? (
        <LegalText.Reference href={node.href}>{node.text}</LegalText.Reference>
      ) : (
        <LegalText.Reference>{node.text}</LegalText.Reference>
      );
    case 'term':
      return <LegalText.Term>{node.text}</LegalText.Term>;
    case 'citation':
      return node.href ? (
        <LegalText.Citation uri={node.uri} href={node.href} />
      ) : (
        <LegalText.Citation uri={node.uri} />
      );
    case 'emph':
      return (
        <em className="font-legislative italic">
          <Inline nodes={node.children} />
        </em>
      );
    case 'mod':
      return <AmendedWords nodes={node.children} />;
    case 'strong':
      return (
        <strong className="font-legislative font-bold">
          <Inline nodes={node.children} />
        </strong>
      );
    case 'note':
      return (
        <span
          className={`mt-1 block border-t border-border/60 pt-1 text-xs text-muted-foreground ${
            node.inferred ? 'italic' : ''
          }`}
          data-akn-type="authorialNote"
          title={node.inferred ? 'Binding inferred: the marker was not located' : undefined}
        >
          <Inline nodes={node.children} />
        </span>
      );
  }
}

function ProvisionTable({ table }: { table: DocumentTable }): React.ReactElement {
  return (
    <div className="overflow-x-auto not-italic">
      <table className="w-full border-collapse font-legislative text-[0.9375rem] leading-[1.5]">
        <tbody>
          {table.rows.map((row, ri) => (
            <tr
              id={row.akn_eid ?? undefined}
              key={ri}
              className="border-b border-ink-20 last:border-0"
            >
              {row.cells.map((cell, ci) => {
                const Cell = cell.header ? 'th' : 'td';
                const isCaption = ri === 0 && row.cells.length === 1 && cell.colspan > 1;
                return (
                  <Cell
                    key={ci}
                    id={cell.akn_eid || undefined}
                    data-akn-target={cell.akn_eid ? 'true' : undefined}
                    data-akn-type="p"
                    colSpan={cell.colspan > 1 ? cell.colspan : undefined}
                    rowSpan={cell.rowspan > 1 ? cell.rowspan : undefined}
                    className={cn(
                      'whitespace-pre-line p-2 align-top',
                      cell.header && 'font-semibold text-ink',
                      isCaption && 'text-center',
                    )}
                  >
                    <Inline nodes={cell.content} />
                  </Cell>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProvisionBlock({
  block,
  depth,
  wrapperEid,
}: {
  block: DocumentProvisionBlock;
  depth: number;
  wrapperEid?: string;
}): React.ReactElement {
  const targetEid = block.akn_eid !== wrapperEid ? block.akn_eid : undefined;
  if (block.akn_type === 'table' && block.table) {
    return (
      <div
        id={targetEid || undefined}
        data-akn-target={targetEid ? 'true' : undefined}
        data-akn-type="table"
      >
        <StateMark eId={targetEid} />
        <ProvisionTable table={block.table} />
      </div>
    );
  }
  if (block.akn_type === 'quote') {
    return (
      <blockquote
        id={targetEid || undefined}
        data-akn-target={targetEid ? 'true' : undefined}
        data-akn-type="quote"
        className="ms-6 border-s-2 border-ink-20 ps-4"
      >
        <StateMark eId={targetEid} />
        <div className="space-y-2">
          {block.num ? (
            <p className="font-legislative font-semibold text-[0.9375rem] leading-[1.45] text-ink">
              {block.num}
            </p>
          ) : null}
          {block.heading ? (
            <p className="font-legislative font-semibold text-[0.9375rem] leading-[1.45] text-ink">
              {block.heading}
            </p>
          ) : null}
          {block.intro.length > 0 ? (
            <p className="whitespace-pre-line font-legislative text-[0.9375rem] leading-[1.7] text-ink">
              <Inline nodes={block.intro} />
            </p>
          ) : null}
          {block.blocks.map((b, i) => (
            <ProvisionBlock key={b.akn_eid || i} block={b} depth={0} />
          ))}
          {block.wrap_up.length > 0 ? (
            <p className="whitespace-pre-line font-legislative text-[0.9375rem] leading-[1.7] text-ink">
              <Inline nodes={block.wrap_up} />
            </p>
          ) : null}
        </div>
      </blockquote>
    );
  }
  return (
    <div
      id={targetEid || undefined}
      data-akn-target={targetEid ? 'true' : undefined}
      data-akn-type="provision"
      className={cn('grid grid-cols-[2.5rem_1fr] items-baseline gap-x-2', depth > 0 && 'ms-6')}
    >
      <span className="shrink-0 select-none pt-1 text-end font-tbi-mono text-[0.6875rem] tabular-nums text-ink-60">
        {block.num ?? ''}
      </span>
      <div className="space-y-2">
        <StateMark eId={targetEid} />
        {block.heading ? (
          <p className="font-legislative font-semibold text-[0.9375rem] leading-[1.45] text-ink">
            {block.heading}
          </p>
        ) : null}
        {block.intro.length > 0 ? (
          <p className="whitespace-pre-line font-legislative text-[0.9375rem] leading-[1.7] text-ink">
            <Inline nodes={block.intro} />
          </p>
        ) : null}
        {block.blocks.length > 0 ? (
          <div className="space-y-2">
            {block.blocks.map((b, i) => (
              <ProvisionBlock key={b.akn_eid || i} block={b} depth={depth + 1} />
            ))}
          </div>
        ) : null}
        {block.wrap_up.length > 0 ? (
          <p className="whitespace-pre-line font-legislative text-[0.9375rem] leading-[1.7] text-ink">
            <Inline nodes={block.wrap_up} />
          </p>
        ) : null}
      </div>
    </div>
  );
}

function SectionNode({
  section,
  depth,
  localTerms,
  basicUnitTerm,
  chapterLabel,
  copyLabels,
}: {
  section: DocumentSection;
  depth: number;
  localTerms?: Record<string, string>;
  basicUnitTerm?: string | null;
  chapterLabel: (num: string) => string;
  copyLabels: CopyLabels;
}): React.ReactElement {
  const headingTag = HEADING_LEVEL[Math.min(depth, 3)] ?? 'h3';
  const gutter = gutterLabelFor(section, depth, { localTerms, chapterLabel });
  const titleNumber = titleNumberFor(section, depth);
  const t = section.akn_type.toLowerCase();
  const localTerm = localTerms?.[t];
  const isBasicUnit = !!localTerm && !!basicUnitTerm && localTerm === basicUnitTerm;
  const eyebrowColor = isBasicUnit || t === 'article' ? 'text-emphasis-strong' : 'text-ink-60';

  const mt = SECTION_MT[Math.min(depth, 3)] ?? 'mt-3';

  return (
    <section
      id={section.akn_eid}
      data-akn-target="true"
      data-akn-type="section"
      className={cn(mt, 'first:mt-0')}
    >
      <StateMark eId={section.akn_eid} />
      {React.createElement(
        headingTag,
        {
          className: cn('flex flex-wrap items-baseline gap-x-3', HEADING_CLASS[Math.min(depth, 3)]),
        },
        <>
          <span data-slot="law-eyebrow" className={cn(EYEBROW_BASE, eyebrowColor)}>
            {gutter}
          </span>
          {section.title || titleNumber ? (
            <span className="font-legislative text-ink">
              {titleNumber ? (
                <span className={cn('me-1.5 tabular-nums', eyebrowColor)}>
                  {titleNumber}
                  {section.title ? ' ·' : ''}
                </span>
              ) : null}
              {section.title}
            </span>
          ) : null}
        </>,
      )}
      <div className="space-y-2">
        {section.provisions.map((p) => (
          <ProvisionNode key={p.id} provision={p} copyLabels={copyLabels} />
        ))}
      </div>
      {section.sections.map((child) => (
        <SectionNode
          key={child.id}
          section={child}
          depth={depth + 1}
          localTerms={localTerms}
          basicUnitTerm={basicUnitTerm}
          chapterLabel={chapterLabel}
          copyLabels={copyLabels}
        />
      ))}
    </section>
  );
}

type CopyLabels = {
  copyCitationLabel?: (citationId: string) => string;
  copyLabel?: (citationId: string) => string;
  copiedLabel?: string;
};

function ProvisionNode({
  provision,
  copyLabels,
}: {
  provision: DocumentProvision;
  copyLabels: CopyLabels;
}): React.ReactElement {
  if (provision.blocks && provision.blocks.length > 0) {
    return (
      <div id={provision.akn_eid} data-akn-target="true" data-akn-type="provision">
        <StateMark eId={provision.akn_eid} />
        <div className="space-y-2">
          {provision.blocks.map((b, i) => (
            <ProvisionBlock
              key={b.akn_eid || i}
              block={b}
              depth={0}
              wrapperEid={provision.akn_eid}
            />
          ))}
        </div>
      </div>
    );
  }
  return (
    <div id={provision.akn_eid} data-akn-target="true" data-akn-type="provision">
      <StateMark eId={provision.akn_eid} />
      <LegalText.Paragraph
        number={provision.num ?? undefined}
        citationId={provision.akn_eid}
        copyCitationLabel={copyLabels.copyCitationLabel}
        copyLabel={copyLabels.copyLabel}
        copiedLabel={copyLabels.copiedLabel}
      >
        {provision.text}
      </LegalText.Paragraph>
    </div>
  );
}

const RTL_LANGUAGES = new Set([
  'ara',
  'ar',
  'heb',
  'he',
  'fas',
  'per',
  'fa',
  'urd',
  'ur',
  'pus',
  'snd',
  'yid',
  'ckb',
  'arc',
  'syr',
]);

function isRtlLanguage(language: string): boolean {
  return RTL_LANGUAGES.has(language.toLowerCase());
}

function LawReader({
  document: doc,
  provisionStates,
  stateLabel,
  flashTarget,
  flashNonce,
  onFlashTargetMissing,
  onActiveChange,
  scrollRootRef,
  localTerms,
  basicUnitTerm,
  title,
  chapterLabel = defaultChapterLabel,
  enactingFormulaLabel = 'Enacting formula',
  copyCitationLabel,
  copyLabel,
  copiedLabel,
  className,
  ...rest
}: LawReaderProps) {
  const containerRef = React.useRef<HTMLElement | null>(null);
  const copyLabels: CopyLabels = { copyCitationLabel, copyLabel, copiedLabel };

  React.useEffect(() => {
    if (!flashTarget) return;
    if (typeof window === 'undefined') return;
    const el = window.document.getElementById(flashTarget);
    if (!el) {
      onFlashTargetMissing?.(flashTarget);
      return;
    }
    const reduced =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView?.({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
    el.setAttribute('data-flash', 'true');
    const t = window.setTimeout(() => el.removeAttribute('data-flash'), 2600);
    return () => window.clearTimeout(t);
  }, [flashTarget, flashNonce, onFlashTargetMissing]);

  React.useEffect(() => {
    if (!onActiveChange) return;
    if (typeof window === 'undefined') return;
    const root = containerRef.current;
    if (!root) return;
    const targets = root.querySelectorAll<HTMLElement>('[data-akn-target="true"]');
    if (targets.length === 0) return;

    let lastReported: string | null = null;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .map((e) => e.target as HTMLElement)
          .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
        const next = visible[0]?.id ?? lastReported;
        if (next !== lastReported) {
          lastReported = next ?? null;
          onActiveChange(lastReported);
        }
      },
      { root: scrollRootRef?.current ?? null, rootMargin: '0% 0% -85% 0%', threshold: 0 },
    );
    targets.forEach((t) => observer.observe(t));
    return () => observer.disconnect();
  }, [onActiveChange, doc, scrollRootRef]);

  const stateValue = React.useMemo(
    () => ({ states: provisionStates ?? {}, label: stateLabel }),
    [provisionStates, stateLabel],
  );

  return (
    <ProvisionStateContext.Provider value={stateValue}>
      <AmendmentMarkers.Provider value={doc.amendment_markers ?? DEFAULT_AMENDMENT_MARKERS}>
        <article
          ref={containerRef}
          data-slot="law-reader"
          dir={isRtlLanguage(doc.language) ? 'rtl' : undefined}
          lang={doc.language}
          className={cn('font-tbi-sans', className)}
          {...rest}
        >
          {title ? (
            <h1
              className="mb-4 font-legislative text-[1.5rem] leading-[1.25] tracking-[-0.005em] text-ink [overflow-wrap:anywhere]"
              style={{ fontFeatureSettings: '"liga" 1, "kern" 1' }}
            >
              {title}
            </h1>
          ) : null}
          {doc.preface?.length || doc.preface_tables?.length ? (
            <div
              data-akn-type="preface"
              className="mb-8 space-y-4 font-legislative text-[0.9375rem] italic leading-[1.7] text-ink-70"
            >
              {doc.preface && doc.preface.length > 0 ? (
                <div className="whitespace-pre-line">
                  <Inline nodes={doc.preface} />
                </div>
              ) : null}
              {doc.preface_tables?.map((table) => (
                <ProvisionTable key={table.akn_eid} table={table} />
              ))}
            </div>
          ) : null}
          {doc.preamble?.length || doc.preamble_tables?.length ? (
            <div data-akn-type="preamble" className="mb-10">
              <p className="mb-1.5 micro-caps text-ink-60">{enactingFormulaLabel}</p>
              <div className="space-y-4 font-legislative text-[0.9375rem] leading-[1.7] text-ink">
                {doc.preamble && doc.preamble.length > 0 ? (
                  <div className="whitespace-pre-line">
                    <Inline nodes={doc.preamble} />
                  </div>
                ) : null}
                {doc.preamble_tables?.map((table) => (
                  <ProvisionTable key={table.akn_eid} table={table} />
                ))}
              </div>
            </div>
          ) : null}
          {doc.sections.map((s) => (
            <SectionNode
              key={s.id}
              section={s}
              depth={0}
              localTerms={localTerms}
              basicUnitTerm={basicUnitTerm}
              chapterLabel={chapterLabel}
              copyLabels={copyLabels}
            />
          ))}
          {doc.provisions.length > 0 ? (
            <section className="mt-10">
              {doc.provisions.map((p) => (
                <ProvisionNode key={p.id} provision={p} copyLabels={copyLabels} />
              ))}
            </section>
          ) : null}
          {doc.conclusions && doc.conclusions.length > 0 ? (
            <div
              data-akn-type="conclusions"
              className="mt-12 whitespace-pre-line border-t border-ink-10 pt-6 font-legislative text-[0.9375rem] leading-[1.7] text-ink-70"
            >
              <Inline nodes={doc.conclusions} />
            </div>
          ) : null}
        </article>
      </AmendmentMarkers.Provider>
    </ProvisionStateContext.Provider>
  );
}

export { LawReader };
export type {
  LawReaderProps,
  VersionDocument,
  DocumentSection,
  DocumentProvision,
  DocumentProvisionBlock,
  InlineNode,
};
