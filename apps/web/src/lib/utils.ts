export { cn } from '@codify/tbi-ui';

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
