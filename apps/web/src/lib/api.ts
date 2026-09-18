
export const BASE = '/api/proxy';

export interface JsonInit extends Omit<RequestInit, 'headers'> {
  jsonBody?: unknown;
  query?: Record<string, string | number | undefined | null>;
  headers?: Record<string, string>;
  expectedStatuses?: number[];
}

export function buildUrl(path: string, query?: JsonInit['query']): string {
  if (!query) return `${BASE}${path}`;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${BASE}${path}?${qs}` : `${BASE}${path}`;
}

export function buildRequest(
  path: string,
  init: JsonInit,
  accept: string,
): { url: string; init: RequestInit } {
  const { jsonBody, query, headers, expectedStatuses: _expected, ...rest } = init;
  const url = buildUrl(path, query);
  const finalHeaders: Record<string, string> = {
    Accept: accept,
    ...(headers ?? {}),
  };
  let body = rest.body as BodyInit | undefined;
  if (jsonBody !== undefined) {
    finalHeaders['Content-Type'] = 'application/json';
    body = JSON.stringify(jsonBody);
  }
  return { url, init: { ...rest, headers: finalHeaders, body } };
}

export class ApiError extends Error {
  readonly status: number;
  readonly method: string;
  readonly url: string;
  readonly body: string;

  constructor(
    status: number,
    method: string,
    url: string,
    body: string,
  ) {
    super(`${status} ${method} ${url}: ${body}`);
    this.name = 'ApiError';
    this.status = status;
    this.method = method;
    this.url = url;
    this.body = body;
  }
}

export async function fetchJson<T>(path: string, init: JsonInit = {}): Promise<T> {
  const { url, init: req } = buildRequest(path, init, 'application/json');
  const res = await fetch(url, req);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, req.method ?? 'GET', url, text);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
