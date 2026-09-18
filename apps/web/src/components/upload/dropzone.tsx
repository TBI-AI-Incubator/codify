
import { FileUp } from 'lucide-react';
import { useCallback, useEffect, useRef } from 'react';
import { useDropzone, type DropEvent } from 'react-dropzone';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export interface SelectedPdf {
  file: File;
  relativePath?: string;
}

interface DropzoneProps {
  onFilesSelected: (files: SelectedPdf[], skipped: number) => void;
  onReadOutcome: (outcome: { seq: number; read: string[]; unread: string[] }) => void;
  onReadingChange: (pending: number) => void;
  disabled?: boolean;
}

const isSupported = (name: string) => /\.(pdf|xml|akn|fmx|txt|md)$/i.test(name);

let dropSeq = 0;

function readNames(files: File[]): string[] {
  return [
    ...new Set(files.map((f) => (f as FileWithPath).webkitRelativePath?.split('/')[0] || f.name)),
  ];
}

type FileWithPath = File & { relativePath?: string; webkitRelativePath?: string };

function withRelativePath(file: File, relativePath: string): File {
  Object.defineProperty(file, 'relativePath', { value: relativePath, configurable: true });
  return file;
}

async function walkEntry(entry: FileSystemEntry, prefix: string, out: File[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    out.push(withRelativePath(file, `${prefix}${entry.name}`));
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
        reader.readEntries(resolve, reject),
      );
      if (batch.length === 0) break;
      for (const child of batch) await walkEntry(child, `${prefix}${entry.name}/`, out);
    }
  }
}

function droppedEntries(event: FileSystemFileHandle[] | DropEvent): FileSystemEntry[] {
  if (Array.isArray(event)) return [];
  const dt = 'dataTransfer' in event ? (event as DragEvent).dataTransfer : null;
  if (!dt?.items?.length) return [];
  return Array.from(dt.items)
    .map((i) => (typeof i.webkitGetAsEntry === 'function' ? i.webkitGetAsEntry() : null))
    .filter((e): e is FileSystemEntry => e !== null);
}

function droppedFiles(event: FileSystemFileHandle[] | DropEvent): File[] {
  if (Array.isArray(event)) return [];
  const dt = 'dataTransfer' in event ? (event as DragEvent).dataTransfer : null;
  if (dt) return Array.from(dt.files ?? []);
  const input = (event as Event).target as HTMLInputElement | null;
  return Array.from(input?.files ?? []);
}

export function Dropzone({
  onFilesSelected,
  onReadOutcome,
  onReadingChange,
  disabled = false,
}: DropzoneProps) {
  const { t } = useTranslation();
  const folderInputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback(
    (files: File[]) => {
      const pdfs: SelectedPdf[] = [];
      let skipped = 0;
      for (const f of files as FileWithPath[]) {
        if (isSupported(f.name)) {
          pdfs.push({ file: f, relativePath: f.relativePath ?? f.webkitRelativePath ?? undefined });
        } else {
          skipped += 1;
        }
      }
      if (pdfs.length > 0 || skipped > 0) onFilesSelected(pdfs, skipped);
    },
    [onFilesSelected],
  );

  const addFilesRef = useRef<((files: File[]) => void) | null>(addFiles);
  useEffect(() => {
    addFilesRef.current = addFiles;
  }, [addFiles]);
  const outcomeRef = useRef<DropzoneProps['onReadOutcome'] | null>(onReadOutcome);
  useEffect(() => {
    outcomeRef.current = onReadOutcome;
  }, [onReadOutcome]);
  const readingRef = useRef<((pending: number) => void) | null>(onReadingChange);
  useEffect(() => {
    readingRef.current = onReadingChange;
  }, [onReadingChange]);
  const pendingRef = useRef(0);
  const publish = useCallback((seq: number, files: File[], read: string[], unread: string[]) => {
    addFilesRef.current?.(files);
    outcomeRef.current?.({ seq, read, unread });
  }, []);
  useEffect(
    () => () => {
      addFilesRef.current = null;
      outcomeRef.current = null;
      readingRef.current = null;
    },
    [],
  );

  const getFilesFromEvent = useCallback(
    async (event: FileSystemFileHandle[] | DropEvent): Promise<(DataTransferItem | File)[]> => {
      const isDrop = !Array.isArray(event) && (event as Event).type === 'drop';
      const entries = isDrop ? droppedEntries(event) : [];
      if (entries.length === 0) {
        const files = droppedFiles(event);
        if (files.length > 0) publish((dropSeq += 1), files, readNames(files), []);
        return [];
      }
      const seq = (dropSeq += 1);
      pendingRef.current += 1;
      readingRef.current?.(pendingRef.current);
      const out: File[] = [];
      const read: string[] = [];
      const unread: string[] = [];
      try {
        for (const entry of entries) {
          const found: File[] = [];
          try {
            await walkEntry(entry, '', found);
            out.push(...found);
            read.push(entry.name);
          } catch (err) {
            console.error(
              entry.isDirectory ? 'Failed to read dropped folder' : 'Failed to read dropped file',
              entry.name,
              err,
            );
            unread.push(entry.name);
          }
        }
      } finally {
        pendingRef.current -= 1;
        readingRef.current?.(pendingRef.current);
      }
      publish(seq, out, read, unread);
      return [];
    },
    [publish],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    multiple: true,
    disabled,
    useFsAccessApi: false,
    noPaste: true, // v19 default-on; getFilesFromEvent doesn't read clipboardData.
    getFilesFromEvent,
  });

  return (
    <div
      {...getRootProps()}
      aria-label={t('Document upload area. Drag and drop files or folders, or click to browse.')}
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 text-center transition-all duration-200 sm:p-12',
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
        isDragActive
          ? 'border-primary bg-primary/5 shadow-sm'
          : 'border-muted-foreground/25 hover:border-primary/50 hover:shadow-sm',
      )}
    >
      <input {...getInputProps()} />
      <FileUp
        className={cn(
          'mb-4 h-10 w-10 transition-transform duration-200 motion-reduce:transition-none',
          isDragActive ? 'scale-110 text-primary' : 'text-muted-foreground',
        )}
      />
      <p className="font-medium">
        {isDragActive ? t('Drop them right here') : t('Drop documents or folders here')}
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        {t('click to browse')}
        {' · '}
        <button
          type="button"
          disabled={disabled}
          onClick={(e) => {
            e.stopPropagation();
            folderInputRef.current?.click();
          }}
          className="underline underline-offset-2 hover:text-foreground disabled:no-underline"
        >
          {t('folder')}
        </button>
      </p>
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        aria-hidden
        tabIndex={-1}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          publish((dropSeq += 1), files, readNames(files), []);
          e.target.value = '';
        }}
        {...({ webkitdirectory: '' } as Record<string, string>)}
      />
    </div>
  );
}
