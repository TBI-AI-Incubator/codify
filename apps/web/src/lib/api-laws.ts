import type {
  LawDetail,
  VersionDocument,
  VersionListResponse,
  VersionSource,
} from '@codify/core-ts/api';

import { track } from '@/lib/analytics';
import { BASE, fetchJson } from '@/lib/api';
import { authHeaders } from '@/lib/auth';

export function getLaw(lawId: string): Promise<LawDetail> {
  return fetchJson(`/laws/${encodeURIComponent(lawId)}`);
}

export function listLawVersions(
  lawId: string,
  opts: { cursor?: string; limit?: number } = {},
): Promise<VersionListResponse> {
  return fetchJson(`/laws/${encodeURIComponent(lawId)}/versions`, { query: opts });
}

export function getVersionDocument(versionId: string): Promise<VersionDocument> {
  return fetchJson(`/versions/${encodeURIComponent(versionId)}/document`);
}

export function getVersionSource(
  versionId: string,
  format: 'bluebell' | 'akn-xml' = 'bluebell',
): Promise<VersionSource> {
  return fetchJson(`/laws/versions/${encodeURIComponent(versionId)}/source`, {
    query: { format },
  });
}

export function filenameFromDisposition(disposition: string, fallback: string): string {
  const extended = /filename\*=\s*UTF-8''([^;]+)/i.exec(disposition);
  if (extended?.[1]) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      // Fall back to the plain filename.
    }
  }
  const plain = /filename=\s*"?([^";]+)"?/i.exec(disposition);
  return plain?.[1]?.trim() || fallback;
}

async function downloadFailure(res: Response, fallback: string): Promise<Error> {
  let detail = '';
  try {
    const body: unknown = await res.clone().json();
    if (body && typeof body === 'object') {
      const b = body as { message?: unknown; detail?: unknown; hint?: unknown };
      const message = typeof b.message === 'string' ? b.message : undefined;
      const fastapiDetail = typeof b.detail === 'string' ? b.detail : undefined;
      const hint = typeof b.hint === 'string' ? b.hint : undefined;
      detail = [message ?? fastapiDetail, hint].filter(Boolean).join(' ');
    }
  } catch {
    // Use the HTTP status when the response is not JSON.
  }
  const error = new Error(detail.trim() || `${fallback} (${res.status})`);
  Object.assign(error, { status: res.status });
  return error;
}

async function triggerBlobDownload(res: Response, fallbackName: string): Promise<void> {
  const blob = await res.blob();
  const filename = filenameFromDisposition(
    res.headers.get('content-disposition') ?? '',
    fallbackName,
  );
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function downloadOriginalSource(versionId: string): Promise<void> {
  const url = `${BASE}/laws/versions/${encodeURIComponent(versionId)}/original`;
  const res = await fetch(url, { cache: 'no-store', headers: authHeaders() });
  if (!res.ok) throw await downloadFailure(res, 'Download failed');
  track('law_exported', { format: 'original_source' });
  await triggerBlobDownload(res, 'source.pdf');
}

export type TextFormat = 'txt' | 'bluebell' | 'akn-xml';

const TEXT_FALLBACK_EXT: Record<TextFormat, string> = {
  txt: 'txt',
  bluebell: 'bluebell.txt',
  'akn-xml': 'xml',
};

export async function downloadVersionText(versionId: string, format: TextFormat): Promise<void> {
  const url = `${BASE}/laws/versions/${encodeURIComponent(versionId)}/text?format=${format}`;
  const res = await fetch(url, { cache: 'no-store', headers: authHeaders() });
  if (!res.ok) throw await downloadFailure(res, 'Download failed');
  track('law_exported', { format });
  await triggerBlobDownload(res, `law-${versionId}.${TEXT_FALLBACK_EXT[format]}`);
}
