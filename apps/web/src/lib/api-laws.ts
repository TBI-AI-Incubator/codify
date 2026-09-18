import { BASE, fetchJson } from '@/lib/api';
import type { components } from '@/lib/contract';

export type LawDetail = components['schemas']['LawDetail'];
export type VersionSummary = components['schemas']['VersionSummary'];
export type VersionPage = components['schemas']['VersionPage'];
export type VersionDocument = components['schemas']['VersionDocument'];

export function getLaw(lawId: string): Promise<LawDetail> {
  return fetchJson(`/laws/${encodeURIComponent(lawId)}`);
}

export function listLawVersions(
  lawId: string,
  opts: { cursor?: string; limit?: number } = {},
): Promise<VersionPage> {
  return fetchJson(`/laws/${encodeURIComponent(lawId)}/versions`, { query: opts });
}

export function getVersionDocument(versionId: string): Promise<VersionDocument> {
  return fetchJson(`/versions/${encodeURIComponent(versionId)}/document`);
}

/* The AKN is on the version itself; the browser makes the file. */
export async function downloadAknXml(versionId: string): Promise<void> {
  const version = await fetchJson<components['schemas']['VersionDetail']>(
    `/versions/${encodeURIComponent(versionId)}`,
  );
  const blob = new Blob([version.akn_xml], { type: 'application/xml' });
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = `law-${versionId}.xml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

export { BASE };
