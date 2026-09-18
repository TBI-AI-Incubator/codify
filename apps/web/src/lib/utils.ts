export { cn } from '../vendor/tbi-ui';

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
