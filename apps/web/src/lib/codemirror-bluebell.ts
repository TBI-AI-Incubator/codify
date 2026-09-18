
import {
  StreamLanguage,
  LanguageSupport,
  type StringStream,
  type IndentContext,
} from '@codemirror/language';
import { tags, type Tag } from '@lezer/highlight';


const CONTAINER_MARKERS = new Set([
  'PREFACE',
  'PREAMBLE',
  'BODY',
  'CONCLUSIONS',
  'INTRODUCTION',
  'BACKGROUND',
  'ARGUMENTS',
  'REMEDIES',
  'MOTIVATION',
  'DECISION',
]);

const ATTACHMENT_MARKERS = new Set(['ATTACHMENT', 'APPENDIX', 'SCHEDULE', 'ANNEXURE']);

const HIER_ELEMENTS = new Set([
  'ALINEA',
  'ARTICLE',
  'BOOK',
  'CHAPTER',
  'CLAUSE',
  'DIVISION',
  'INDENT',
  'LEVEL',
  'LIST',
  'PARAGRAPH',
  'PART',
  'POINT',
  'PROVISO',
  'RULE',
  'SECTION',
  'SUBCHAPTER',
  'SUBCLAUSE',
  'SUBDIVISION',
  'SUBLIST',
  'SUBPARAGRAPH',
  'SUBPART',
  'SUBRULE',
  'SUBSECTION',
  'SUBTITLE',
  'TITLE',
  'TOME',
  'TRANSITIONAL',
  'ART',
  'CHAP',
  'PARA',
  'SEC',
  'SUBCHAP',
  'SUBPARA',
  'SUBSEC',
]);

const BLOCK_KEYWORDS = new Set([
  'LONGTITLE',
  'SUBHEADING',
  'CROSSHEADING',
  'BLOCKS',
  'BLOCKLIST',
  'ITEMS',
  'ITEM',
  'BULLETS',
  'TABLE',
  'TR',
  'TH',
  'TC',
  'QUOTE',
  'FOOTNOTE',
  'P',
  'SCENE',
  'NARRATIVE',
  'SUMMARY',
  'FROM',
  'SPEECHGROUP',
  'SPEECH',
  'QUESTION',
  'ANSWER',
  'ADDRESS',
  'ADJOURNMENT',
  'ADMINISTRATIONOFOATH',
  'COMMUNICATION',
  'DEBATESECTION',
  'DECLARATIONOFVOTE',
  'MINISTERIALSTATEMENTS',
  'NATIONALINTEREST',
  'NOTICESOFMOTION',
  'ORALSTATEMENTS',
  'PAPERS',
  'PERSONALSTATEMENTS',
  'PETITIONS',
  'POINTOFORDER',
  'PRAYERS',
  'PROCEDURALMOTIONS',
  'QUESTIONS',
  'RESOLUTIONS',
  'ROLLCALL',
  'WRITTENSTATEMENTS',
]);

function keywordsByLength(set: Set<string>): string[] {
  return [...set].sort((a, b) => b.length - a.length);
}

const HIER_SORTED = keywordsByLength(HIER_ELEMENTS);
const BLOCK_SORTED = keywordsByLength(BLOCK_KEYWORDS);
const CONTAINER_SORTED = keywordsByLength(CONTAINER_MARKERS);
const ATTACHMENT_SORTED = keywordsByLength(ATTACHMENT_MARKERS);


type InlineKind =
  | 'bold'
  | 'italic'
  | 'underline'
  | 'remark'
  | 'ref'
  | 'sup'
  | 'sub'
  | 'footnoteRef'
  | 'standardInline'
  | null;

interface BluebellState {
  indentDepth: number;
  inline: InlineKind;
  braceDepth: number;
  atLineStart: boolean;
  lineKind: 'hier' | 'attachment' | 'container' | 'block' | null;
  afterNum: boolean;
}

function startState(): BluebellState {
  return {
    indentDepth: 0,
    inline: null,
    braceDepth: 0,
    atLineStart: true,
    lineKind: null,
    afterNum: false,
  };
}

function copyState(s: BluebellState): BluebellState {
  return { ...s };
}


const KEYWORD_BOUNDARY = new Set([' ', '.', '\n', '{']);

function matchKeyword(stream: StringStream, list: string[]): string | null {
  for (const kw of list) {
    const pos = stream.pos;
    const line = stream.string;
    if (line.startsWith(kw, pos)) {
      const next = line[pos + kw.length];
      if (next === undefined || KEYWORD_BOUNDARY.has(next)) {
        stream.pos = pos + kw.length;
        return kw;
      }
    }
  }
  return null;
}


function token(stream: StringStream, state: BluebellState): string | null {
  if (stream.sol()) {
    state.atLineStart = true;
    state.lineKind = null;
    state.afterNum = false;
  }

  if (state.atLineStart && stream.eatSpace()) {
    if (stream.eol()) state.atLineStart = true; // blank line
    return null;
  }

  const wasLineStart = state.atLineStart;
  state.atLineStart = false;


  if (stream.peek() === '\\') {
    stream.next(); // backslash
    if (!stream.eol()) stream.next(); // escaped char
    return 'escape';
  }

  if (stream.match('**')) {
    state.inline = state.inline === 'bold' ? null : state.inline ? state.inline : 'bold';
    return 'processingInstruction';
  }

  if (stream.match('//')) {
    state.inline = state.inline === 'italic' ? null : state.inline ? state.inline : 'italic';
    return 'processingInstruction';
  }

  if (stream.match('__')) {
    state.inline = state.inline === 'underline' ? null : state.inline ? state.inline : 'underline';
    return 'processingInstruction';
  }

  if (stream.match('}}')) {
    if (state.braceDepth > 0) state.braceDepth--;
    state.inline = null;
    return 'brace';
  }

  if (stream.match('{{')) {
    state.braceDepth++;
    const ch = stream.peek();
    if (ch === '*') state.inline = 'remark';
    else if (ch === '>') state.inline = 'ref';
    else if (ch === '^') state.inline = 'sup';
    else if (ch === '_') state.inline = 'sub';
    else state.inline = 'standardInline';
    return 'brace';
  }

  if (state.braceDepth > 0 && state.inline) {
    if (state.inline === 'remark' && stream.eat('*')) return 'keyword';
    if (state.inline === 'ref' && stream.eat('>')) return 'keyword';
    if (state.inline === 'sup' && stream.eat('^')) return 'keyword';
    if (state.inline === 'sub' && stream.eat('_')) return 'keyword';
    if (stream.match('IMG')) return 'keyword';
    if (stream.match(/^FOOTNOTE(?= )/)) return 'keyword';
    if (stream.match(/^(abbr|def|em|inline|term)(?=[ {}])/)) return 'keyword';
    if (stream.match(/^[-+](?=[ {}])/)) return 'keyword';
  }

  if (state.inline === 'bold') {
    if (!stream.match(/^[^*\\\n{]+/)) stream.next();
    return 'strong';
  }
  if (state.inline === 'italic') {
    if (!stream.match(/^[^/\\\n{]+/)) stream.next();
    return 'emphasis';
  }
  if (state.inline === 'underline') {
    if (!stream.match(/^[^_\\\n{]+/)) stream.next();
    return 'content';
  }
  if (state.inline === 'remark') {
    if (!stream.match(/^[^}\\\n]+/)) stream.next();
    return 'comment';
  }
  if (state.inline === 'ref') {
    if (stream.match(/^[^ }\n]+/)) return 'url';
    if (!stream.match(/^[^}\\\n]+/)) stream.next();
    return 'link';
  }
  if (state.inline === 'sup' || state.inline === 'sub') {
    if (!stream.match(/^[^}\\\n]+/)) stream.next();
    return 'atom';
  }
  if (state.inline === 'standardInline') {
    if (!stream.match(/^[^}\\\n]+/)) stream.next();
    return 'string';
  }


  if (wasLineStart) {
    if (stream.match(/^\.[^ \n|{}.]+/)) return 'className';
    if (stream.match(/^\{[^}]*\}/)) return 'meta';

    const cm = matchKeyword(stream, CONTAINER_SORTED);
    if (cm) {
      state.lineKind = 'container';
      state.indentDepth = stream.indentation();
      return 'heading';
    }

    const am = matchKeyword(stream, ATTACHMENT_SORTED);
    if (am) {
      state.lineKind = 'attachment';
      state.indentDepth = stream.indentation();
      return 'heading';
    }

    const hm = matchKeyword(stream, HIER_SORTED);
    if (hm) {
      state.lineKind = 'hier';
      state.indentDepth = stream.indentation();
      return 'heading';
    }

    const bm = matchKeyword(stream, BLOCK_SORTED);
    if (bm) {
      state.lineKind = 'block';
      state.indentDepth = stream.indentation();
      return 'keyword';
    }

    if (stream.match(/^\([a-zA-Z0-9]+\)(?= )/)) {
      return 'labelName';
    }
  }


  if ((state.lineKind === 'hier' || state.lineKind === 'attachment') && !state.afterNum) {
    if (
      stream.match(/^ +[A-Za-z0-9][A-Za-z0-9.()]*(?= - )/) ||
      stream.match(/^ +[A-Za-z0-9][A-Za-z0-9.()]*$/)
    ) {
      state.afterNum = true;
      return 'labelName';
    }
    if (stream.match(/^ +\([a-zA-Z0-9]+\)/)) {
      state.afterNum = true;
      return 'labelName';
    }
  }

  if (state.lineKind === 'block' && !state.afterNum) {
    if (stream.match(/^ +\([a-zA-Z0-9]+\)/)) {
      state.afterNum = true;
      return 'labelName';
    }
    if (
      stream.match(/^ +[A-Za-z0-9][A-Za-z0-9.()]*(?= - )/) ||
      stream.match(/^ +[A-Za-z0-9][A-Za-z0-9.()]*$/)
    ) {
      state.afterNum = true;
      return 'labelName';
    }
  }

  if (stream.match(' - ')) {
    state.afterNum = true;
    return 'punctuation';
  }

  if (
    stream.match(
      /^(section|article|paragraph|clause|rule|part|chapter|schedule|item|subsection|subparagraph|regulation|order)\s+\d+(\([a-zA-Z0-9]+\))*/i,
    )
  ) {
    return 'link';
  }

  stream.next();
  return null;
}


function indent(state: BluebellState, _textAfter: string, _cx: IndentContext): number | null {
  return state.indentDepth + 2;
}


const bluebellTokenTable: Record<string, Tag | readonly Tag[]> = {
  heading: tags.heading,
  keyword: tags.keyword,
  labelName: tags.labelName,
  link: tags.link,
  url: tags.url,
  strong: tags.strong,
  emphasis: tags.emphasis,
  content: tags.content,
  comment: tags.comment,
  escape: tags.escape,
  brace: tags.brace,
  className: tags.className,
  meta: tags.meta,
  punctuation: tags.punctuation,
  processingInstruction: tags.processingInstruction,
  atom: tags.atom,
  string: tags.string,
};


const bluebellLanguage = StreamLanguage.define<BluebellState>({
  name: 'bluebell',
  startState,
  copyState,
  token,
  indent,
  languageData: {
    closeBrackets: { brackets: ['(', '{', '['] },
  },
  tokenTable: bluebellTokenTable,
});

export function bluebell(): LanguageSupport {
  return new LanguageSupport(bluebellLanguage);
}

export { bluebellLanguage };
