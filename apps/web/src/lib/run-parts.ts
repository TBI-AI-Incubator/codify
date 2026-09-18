
import type {
  AnchorsData, DependencyData, DocMetadataData, Kind, PageData, ResultData,
  RunData, RunStageData, Status, ValidationIssueData,
} from '@codify/core-ts/run_events';

export type RunKind = Kind;
export type RunStatus = Status;


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

export type RunParts = Array<{ type: string; id?: string; data?: unknown }>;

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
