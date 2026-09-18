import { defaultHighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { EditorState } from '@codemirror/state';
import { EditorView, lineNumbers } from '@codemirror/view';
import { useEffect, useRef } from 'react';

import { bluebell } from '@/lib/codemirror-bluebell';

export function BluebellSourceView({
  content,
  onScrollRatio,
}: {
  content: string;
  onScrollRatio?: (ratio: number) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onScrollRatioRef = useRef(onScrollRatio);

  useEffect(() => {
    onScrollRatioRef.current = onScrollRatio;
  }, [onScrollRatio]);

  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: content,
        extensions: [
          lineNumbers(),
          syntaxHighlighting(defaultHighlightStyle),
          bluebell(),
          EditorView.editable.of(false),
          EditorView.theme({
            '&': { height: '100%', fontSize: '0.8125rem' },
            '.cm-scroller': { overflow: 'auto' },
          }),
        ],
      }),
    });
    viewRef.current = view;
    const onScroll = () => {
      const max = Math.max(1, view.scrollDOM.scrollHeight - view.scrollDOM.clientHeight);
      onScrollRatioRef.current?.(view.scrollDOM.scrollTop / max);
    };
    view.scrollDOM.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      view.scrollDOM.removeEventListener('scroll', onScroll);
      view.destroy();
      viewRef.current = null;
    };
  }, [content]);

  return <div ref={hostRef} className="h-full overflow-auto rounded-md border border-ink-20" />;
}
