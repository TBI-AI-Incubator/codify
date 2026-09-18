
import type {
  AnchorsData, DependencyData, DocMetadataData, PageData, ResultData,
  RunData, RunPart, RunStageData, ValidationIssueData,
} from '@/lib/run-events';


export interface CodifyDataParts {
  run: RunData;
  'run-stage': RunStageData;
  page: PageData;
  'doc-metadata': DocMetadataData;
  anchors: AnchorsData;
  'validation-issue': ValidationIssueData;
  result: ResultData;
  dependency: DependencyData;
}

export type RunParts = RunPart[];

export function dataParts<K extends keyof CodifyDataParts>(
  parts: RunParts,
  type: K,
): Array<{ id?: string; data: CodifyDataParts[K] }> {
  const wire = `data-${type}`;
  return parts
    .filter((p) => p.type === wire)
    .map((p) => ({ id: p.id, data: p.data as CodifyDataParts[K] }));
}

export function latestPart<K extends keyof CodifyDataParts>(
  parts: RunParts,
  type: K,
): CodifyDataParts[K] | null {
  const all = dataParts(parts, type);
  return all.length > 0 ? (all[all.length - 1]?.data ?? null) : null;
}
