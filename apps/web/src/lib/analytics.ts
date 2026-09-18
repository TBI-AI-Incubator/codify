export function routePattern(_pathname: string): string {
  return '';
}

export function track(_event: string, _props: Record<string, unknown> = {}): void {}

export function distinctIdFor(_who: { sub: string; email: string | null }): string {
  return '';
}

export function identify(_who: { sub: string; email: string | null }): void {}

export function captureException(_error: unknown, _props: Record<string, unknown> = {}): void {}

export function setCapturing(_on: boolean): void {}

export function resetAnalytics(): void {}

export function reportApiFailure(_status: number, _method: string, _url: string): void {}

export function initAnalytics(_config: unknown): void {}
