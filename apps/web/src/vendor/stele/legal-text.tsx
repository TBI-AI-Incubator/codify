'use client';

import * as React from 'react';
import { cn } from '../tbi-ui';

type LegalTextProps = React.HTMLAttributes<HTMLDivElement>;

function LegalText({ className, ...props }: LegalTextProps) {
  return (
    <div
      data-slot="legal-text"
      className={cn('max-w-[72ch] font-tbi-sans text-ink', className)}
      {...props}
    />
  );
}

type ArticleProps = React.HTMLAttributes<HTMLElement> & {
  number: React.ReactNode;
  title?: React.ReactNode;
  level?: 2 | 3 | 4;
};

function Article({ number, title, level = 3, className, children, ...props }: ArticleProps) {
  const Heading = `h${level}` as 'h2' | 'h3' | 'h4';
  return (
    <section
      data-slot="legal-text-article"
      className={cn('mt-10 mb-3 first:mt-0 last:mb-0', className)}
      {...props}
    >
      <Heading className="mb-2 flex flex-wrap items-baseline gap-x-3 font-tbi-sans text-[1.0625rem] font-medium leading-[1.35] tracking-[-0.005em] text-ink">
        <span data-slot="law-eyebrow" className="shrink-0 micro-caps text-emphasis-strong">
          §
        </span>
        <span className="min-w-0 font-legislative break-words text-ink">
          <span className="me-1.5 tabular-nums text-emphasis-strong">
            {number}
            {title ? ' ·' : ''}
          </span>
          {title}
        </span>
      </Heading>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

type ParagraphProps = React.HTMLAttributes<HTMLDivElement> & {
  number?: React.ReactNode;
  citationId?: string;
  copyCitationLabel?: (citationId: string) => string;
  copyLabel?: (citationId: string) => string;
  copiedLabel?: string;
};

function Paragraph({
  number,
  citationId,
  copyCitationLabel,
  copyLabel,
  copiedLabel,
  className,
  children,
  ...props
}: ParagraphProps) {
  return (
    <div
      data-slot="legal-text-paragraph"
      className={cn(
        'group relative grid grid-cols-[2.5rem_minmax(0,72ch)] items-baseline gap-x-2',
        className,
      )}
      {...props}
    >
      <span className="shrink-0 select-none pt-1 text-end font-tbi-mono text-[0.6875rem] tabular-nums text-ink-60">
        {number ?? ''}
      </span>
      <p className="font-legislative text-[0.9375rem] leading-[1.7] text-ink">{children}</p>
      {citationId ? (
        <CitationCopyButton
          citationId={citationId}
          copyCitationLabel={copyCitationLabel}
          copyLabel={copyLabel}
          copiedLabel={copiedLabel}
        />
      ) : null}
    </div>
  );
}

const defaultCopyCitationLabel = (citationId: string) => `Copy citation ${citationId}`;
const defaultCopyLabel = (citationId: string) => `Copy ${citationId}`;

function CitationCopyButton({
  citationId,
  copyCitationLabel = defaultCopyCitationLabel,
  copyLabel = defaultCopyLabel,
  copiedLabel = 'Copied',
}: {
  citationId: string;
  copyCitationLabel?: (citationId: string) => string;
  copyLabel?: (citationId: string) => string;
  copiedLabel?: string;
}) {
  const [copied, setCopied] = React.useState(false);
  const onCopy = React.useCallback(() => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) return;
    void navigator.clipboard.writeText(citationId).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  }, [citationId]);
  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={copyCitationLabel(citationId)}
      title={copied ? copiedLabel : copyLabel(citationId)}
      className="pointer-events-none absolute end-0 top-1 hidden font-tbi-mono text-[0.75rem] leading-none text-ink-40 opacity-0 transition-opacity hover:text-ink group-hover:pointer-events-auto group-hover:inline-block group-hover:opacity-100 focus-visible:pointer-events-auto focus-visible:inline-block focus-visible:opacity-100 focus-visible:outline-none"
    >
      {copied ? '✓' : '¶'}
    </button>
  );
}

type TermProps = React.HTMLAttributes<HTMLSpanElement>;
function Term({ className, ...props }: TermProps) {
  return (
    <em
      data-slot="legal-text-term"
      className={cn('font-legislative italic text-ink', className)}
      {...props}
    />
  );
}

const SAFE_HREF = /^(https?:|mailto:|\/|#|\.\/|\.\.\/)/i;

function safeHref(href: string): string | undefined {
  return SAFE_HREF.test(href.trim()) ? href : undefined;
}

type ReferenceLinkProps = React.AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
};
type ReferenceTextProps = Omit<React.HTMLAttributes<HTMLElement>, 'href'> & {
  href?: undefined;
};
type ReferenceProps = ReferenceLinkProps | ReferenceTextProps;

function Reference(props: ReferenceProps) {
  if (props.href !== undefined) {
    const { href, className, children, ...anchorProps } = props;
    const safe = safeHref(href);
    if (safe === undefined) {
      return (
        <strong
          data-slot="legal-text-reference"
          className={cn('font-legislative font-bold text-ink', className)}
        >
          {children}
        </strong>
      );
    }
    return (
      <a
        data-slot="legal-text-reference"
        href={safe}
        className={cn(
          'font-legislative font-bold text-emphasis underline-offset-2 hover:underline focus-visible:underline',
          className,
        )}
        {...anchorProps}
      >
        {children}
      </a>
    );
  }
  const { className, ...rest } = props;
  return (
    <strong
      data-slot="legal-text-reference"
      className={cn('font-legislative font-bold text-ink', className)}
      {...rest}
    />
  );
}

type CitationBase = {
  uri: string;
  label?: React.ReactNode;
};
type CitationLinkProps = CitationBase &
  React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string };
type CitationTextProps = CitationBase &
  Omit<React.HTMLAttributes<HTMLSpanElement>, 'href'> & { href?: undefined };
type CitationProps = CitationLinkProps | CitationTextProps;

const CITATION_BASE =
  'inline-flex max-w-full flex-wrap items-center gap-1 whitespace-normal rounded-sm bg-paper-sunken px-1.5 py-0.5 align-middle font-tbi-mono text-[0.6875rem] font-medium tracking-tight text-ink-80';
const CITATION_URI = '[overflow-wrap:anywhere]';

function Citation(props: CitationProps) {
  if (props.href !== undefined) {
    const { uri, href, label, className, ...anchorProps } = props;
    const safe = safeHref(href);
    if (safe === undefined) {
      return (
        <span data-slot="legal-text-citation" className={cn(CITATION_BASE, className)}>
          <span className={CITATION_URI}>{uri}</span>
          {label ? <span className="text-ink-60">{label}</span> : null}
        </span>
      );
    }
    return (
      <a
        data-slot="legal-text-citation"
        href={safe}
        className={cn(
          CITATION_BASE,
          'transition-colors hover:bg-emphasis-soft hover:text-emphasis-strong',
          className,
        )}
        {...anchorProps}
      >
        <span className={CITATION_URI}>{uri}</span>
        {label ? <span className="text-ink-60">{label}</span> : null}
      </a>
    );
  }
  const { uri, label, className, ...spanProps } = props;
  return (
    <span data-slot="legal-text-citation" className={cn(CITATION_BASE, className)} {...spanProps}>
      <span className={CITATION_URI}>{uri}</span>
      {label ? <span className="text-ink-60">{label}</span> : null}
    </span>
  );
}

LegalText.Article = Article;
LegalText.Paragraph = Paragraph;
LegalText.Term = Term;
LegalText.Reference = Reference;
LegalText.Citation = Citation;

export { LegalText };
export type {
  LegalTextProps,
  ArticleProps as LegalArticleProps,
  ParagraphProps as LegalParagraphProps,
  TermProps as LegalTermProps,
  ReferenceProps as LegalReferenceProps,
  CitationProps as LegalCitationProps,
};
