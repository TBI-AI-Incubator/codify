
const PLACEHOLDER = /^draft-/i;

const KEYED_ON = /^[12]\d{3}(?:-\d{2}-\d{2})?$/;

export function numberFromWorkUri(uri: string | null | undefined): string | null {
  const parts = (uri ?? '').split('/').filter(Boolean);
  const keyed = parts.findIndex((p) => KEYED_ON.test(p));
  const rest = keyed >= 0 ? parts.slice(keyed + 1) : parts.slice(-1);
  if (!rest.length) return null;
  const expression = rest.findIndex((p) => p.includes('@'));
  const named = expression >= 0 ? rest.slice(0, expression) : rest;
  if (!named.length) return null;
  const number = named.join('/');
  if (PLACEHOLDER.test(number)) return null;
  return /\d/.test(number) ? number : null;
}
